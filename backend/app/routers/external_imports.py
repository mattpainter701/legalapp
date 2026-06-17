"""External import staging endpoints.

The upload edge can be provider-specific, but the staging tables are shared by
Tabs3 and future legal/accounting product imports.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import require_admin
from app.models.external_import import (
    ExternalImportRun,
    ExternalRawRow,
    ExternalSystemConnection,
)
from app.schemas.external_import import (
    ExternalImportReconcileResponse,
    ExternalImportRowsResponse,
    ExternalImportRunResponse,
    ExternalImportTableSummary,
    ExternalImportTablesResponse,
    ExternalRawRowPreview,
)

router = APIRouter(prefix="/api/imports", tags=["external-imports"])

MAX_BUNDLE_BYTES = 512 * 1024 * 1024
SUPPORTED_TABS3_EXPORT_VERSION = "tabs3-export-v1"
VALID_ACCOUNTING_MODES = {"clarity_native", "qbo", "tabs3_reference"}

KEY_FIELD_CANDIDATES: dict[str, list[list[str]]] = {
    "CLIENT": [["CLIENT_ID"], ["Client_ID"]],
    "CMCLIENT": [["Client_ID"]],
    "CONTACT": [["RP_Key"], ["RP_KEY"], ["_SEQUENCE_NO"]],
    "CMRELATE": [["RP_Key"], ["_SEQUENCE_NO"]],
    "BILLTO": [["Client_ID", "Bill_To"], ["CLIENT_ID", "BILL_TO"], ["_SEQUENCE_NO"]],
    "EMPLOYEE": [["EMPLOYEE"], ["Emp_ID"], ["_SEQUENCE_NO"]],
    "CMEMPL": [["Employee"], ["_SEQUENCE_NO"]],
    "CLIENTNOTE": [["CLIENT_ID", "RECORD_TYPE"], ["_SEQUENCE_NO"]],
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _row_checksum(row: dict) -> str:
    return hashlib.sha256(_canonical_json(row).encode("utf-8")).hexdigest()


def _decrypt_bundle(raw: bytes, passphrase: str | None) -> bytes:
    if raw.startswith(b"PK\x03\x04"):
        return raw
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Bundle is neither ZIP nor encrypted JSON") from exc

    if envelope.get("format") != "clarity-tabs3-bundle":
        raise HTTPException(status_code=400, detail="Unsupported encrypted bundle format")
    if not passphrase:
        raise HTTPException(status_code=400, detail="Encrypted bundle requires passphrase")

    try:
        salt = base64.b64decode(envelope["salt"])
        nonce = base64.b64decode(envelope["nonce"])
        ciphertext = base64.b64decode(envelope["ciphertext"])
        iterations = int(envelope.get("iterations", 390000))
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=iterations,
        )
        key = kdf.derive(passphrase.encode("utf-8"))
        return AESGCM(key).decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Unable to decrypt bundle") from exc


def _validate_zip(zip_bytes: bytes) -> zipfile.ZipFile:
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid ZIP payload") from exc
    if "manifest.json" not in zf.namelist():
        raise HTTPException(status_code=400, detail="Bundle missing manifest.json")
    return zf


def _load_manifest(zf: zipfile.ZipFile) -> dict:
    try:
        return json.loads(zf.read("manifest.json").decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid manifest.json") from exc


def _validate_manifest(manifest: dict) -> None:
    if manifest.get("provider") != "tabs3":
        raise HTTPException(status_code=400, detail="Only Tabs3 bundles are supported at this endpoint")
    if manifest.get("export_version") != SUPPORTED_TABS3_EXPORT_VERSION:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported Tabs3 export version: {manifest.get('export_version')}",
        )
    tables = manifest.get("tables")
    if not isinstance(tables, list):
        raise HTTPException(status_code=400, detail="Manifest tables must be a list")
    for table in tables:
        name = table.get("name")
        path = table.get("path")
        if not name:
            raise HTTPException(status_code=400, detail="Manifest table missing name")
        if path and (path.startswith("/") or ".." in path.split("/")):
            raise HTTPException(status_code=400, detail=f"Unsafe table path: {path}")


def _source_row_key(source_table: str, row: dict, line_no: int, key_counts: dict[str, int]) -> str:
    candidates = KEY_FIELD_CANDIDATES.get(source_table.upper(), [])
    candidates += [["_SEQUENCE_NO"], ["SEQNO"], ["SEQ_NO"]]
    for fields in candidates:
        values = []
        for field in fields:
            if field in row and row[field] not in (None, ""):
                values.append(str(row[field]))
            else:
                values = []
                break
        if values:
            key = "|".join(f"{field}={value}" for field, value in zip(fields, values))
            break
    else:
        key = f"line={line_no}|sha={_row_checksum(row)[:16]}"

    seen = key_counts.get(key, 0)
    key_counts[key] = seen + 1
    return key if seen == 0 else f"{key}#dup{seen + 1}"


async def _get_or_create_tabs3_connection(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    manifest: dict,
    accounting_mode: str,
) -> ExternalSystemConnection:
    external_key = "tabs3_odbc"
    result = await db.execute(
        select(ExternalSystemConnection).where(
            ExternalSystemConnection.tenant_id == tenant_id,
            ExternalSystemConnection.provider == "tabs3",
            ExternalSystemConnection.external_key == external_key,
        )
    )
    connection = result.scalar_one_or_none()
    metadata = {
        "dsn": manifest.get("dsn"),
        "export_version": manifest.get("export_version"),
        "host": manifest.get("host"),
    }
    if connection:
        connection.accounting_mode = accounting_mode
        connection.source_metadata = {**(connection.source_metadata or {}), **metadata}
        return connection
    connection = ExternalSystemConnection(
        tenant_id=tenant_id,
        provider="tabs3",
        external_key=external_key,
        display_name="Tabs3 ODBC Export",
        accounting_mode=accounting_mode,
        source_metadata=metadata,
        created_by_user_id=user_id,
    )
    db.add(connection)
    await db.flush()
    return connection


async def _stage_table(
    db: AsyncSession,
    *,
    zf: zipfile.ZipFile,
    manifest_table: dict,
    import_run: ExternalImportRun,
    tenant_id: uuid.UUID,
    warnings: list[str],
) -> ExternalImportTableSummary:
    source_table = manifest_table["name"].upper()
    path = manifest_table.get("path")
    if not path:
        return ExternalImportTableSummary(
            source_table=source_table,
            row_count=0,
            checksum=None,
            manifest_row_count=manifest_table.get("row_count"),
            status="metadata-only",
        )
    if path not in zf.namelist():
        raise HTTPException(status_code=400, detail=f"Bundle missing table file: {path}")

    digest = hashlib.sha256()
    row_count = 0
    key_counts: dict[str, int] = {}
    with zf.open(path, "r") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            digest.update(raw_line)
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid JSON in {source_table} line {line_no}",
                ) from exc
            if not isinstance(row, dict):
                raise HTTPException(
                    status_code=400,
                    detail=f"Expected object row in {source_table} line {line_no}",
                )
            source_key = _source_row_key(source_table, row, line_no, key_counts)
            db.add(
                ExternalRawRow(
                    tenant_id=tenant_id,
                    import_run_id=import_run.id,
                    provider=import_run.provider,
                    source_table=source_table,
                    source_row_key=source_key,
                    row_checksum=_row_checksum(row),
                    row_data=row,
                )
            )
            row_count += 1
            if row_count % 1000 == 0:
                await db.flush()
    await db.flush()

    checksum = digest.hexdigest()
    expected_checksum = manifest_table.get("sha256")
    expected_rows = manifest_table.get("row_count")
    if expected_checksum and checksum != expected_checksum:
        raise HTTPException(
            status_code=400,
            detail=f"Checksum mismatch for {source_table}",
        )
    if expected_rows is not None and row_count != int(expected_rows):
        raise HTTPException(
            status_code=400,
            detail=f"Row count mismatch for {source_table}: expected {expected_rows}, got {row_count}",
        )
    duplicate_count = sum(count - 1 for count in key_counts.values() if count > 1)
    if duplicate_count:
        warnings.append(f"{source_table}: {duplicate_count} duplicate source keys were suffixed")
    return ExternalImportTableSummary(
        source_table=source_table,
        row_count=row_count,
        checksum=checksum,
        manifest_row_count=expected_rows,
    )


@router.post("/tabs3/upload", response_model=ExternalImportRunResponse)
async def upload_tabs3_bundle(
    file: UploadFile = File(...),
    passphrase: str | None = Form(None),
    accounting_mode: str = Form("tabs3_reference"),
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Upload and stage a Tabs3 export bundle.

    This endpoint does not promote canonical records. It only validates the
    bundle and stores immutable raw rows for review/reconciliation.
    """

    if accounting_mode not in VALID_ACCOUNTING_MODES:
        raise HTTPException(status_code=422, detail="Invalid accounting_mode")

    raw = await file.read()
    if len(raw) > MAX_BUNDLE_BYTES:
        raise HTTPException(status_code=413, detail="Import bundle is too large")

    zip_bytes = _decrypt_bundle(raw, passphrase)
    zf = _validate_zip(zip_bytes)
    manifest = _load_manifest(zf)
    _validate_manifest(manifest)

    await set_tenant_context(db, str(admin.tenant_id))
    connection = await _get_or_create_tabs3_connection(
        db,
        tenant_id=admin.tenant_id,
        user_id=admin.id,
        manifest=manifest,
        accounting_mode=accounting_mode,
    )
    warnings = list(manifest.get("schema_warnings") or [])
    import_run = ExternalImportRun(
        tenant_id=admin.tenant_id,
        connection_id=connection.id,
        provider="tabs3",
        source_system=manifest.get("source_system"),
        export_id=manifest.get("export_id"),
        status="staging",
        manifest=manifest,
        warnings=warnings,
        errors=[],
        created_by_user_id=admin.id,
    )
    db.add(import_run)
    await db.flush()

    try:
        table_summaries = []
        for table in manifest.get("tables", []):
            table_summaries.append(
                await _stage_table(
                    db,
                    zf=zf,
                    manifest_table=table,
                    import_run=import_run,
                    tenant_id=admin.tenant_id,
                    warnings=warnings,
                )
            )
        row_counts = {summary.source_table: summary.row_count for summary in table_summaries}
        checksums = {
            summary.source_table: summary.checksum
            for summary in table_summaries
            if summary.checksum
        }
        import_run.status = "staged"
        import_run.row_counts = row_counts
        import_run.checksum_summary = checksums
        import_run.warnings = warnings
        connection.last_import_run_id = import_run.id
        connection.last_import_at = datetime.now(timezone.utc)
        await db.commit()
    except HTTPException as exc:
        import_run.status = "failed"
        import_run.errors = [str(exc.detail)]
        await db.commit()
        raise

    await db.refresh(import_run)
    return ExternalImportRunResponse.model_validate(import_run)


@router.get("/{run_id}", response_model=ExternalImportRunResponse)
async def get_import_run(
    run_id: uuid.UUID,
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(admin.tenant_id))
    run = await db.get(ExternalImportRun, run_id)
    if not run or run.tenant_id != admin.tenant_id:
        raise HTTPException(status_code=404, detail="Import run not found")
    return ExternalImportRunResponse.model_validate(run)


@router.get("/{run_id}/tables", response_model=ExternalImportTablesResponse)
async def list_import_tables(
    run_id: uuid.UUID,
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(admin.tenant_id))
    run = await db.get(ExternalImportRun, run_id)
    if not run or run.tenant_id != admin.tenant_id:
        raise HTTPException(status_code=404, detail="Import run not found")

    manifest_tables = {
        table.get("name", "").upper(): table for table in (run.manifest or {}).get("tables", [])
    }
    rows = (
        await db.execute(
            select(ExternalRawRow.source_table, func.count())
            .where(
                ExternalRawRow.tenant_id == admin.tenant_id,
                ExternalRawRow.import_run_id == run_id,
            )
            .group_by(ExternalRawRow.source_table)
            .order_by(ExternalRawRow.source_table)
        )
    ).all()
    observed_counts = {source_table: count for source_table, count in rows}
    all_tables = sorted(set(observed_counts) | set((run.row_counts or {}).keys()))
    summaries = []
    for source_table in all_tables:
        count = int(observed_counts.get(source_table, (run.row_counts or {}).get(source_table, 0)))
        manifest_table = manifest_tables.get(source_table, {})
        summaries.append(
            ExternalImportTableSummary(
                source_table=source_table,
                row_count=count,
                checksum=(run.checksum_summary or {}).get(source_table),
                manifest_row_count=manifest_table.get("row_count"),
            )
        )
    return ExternalImportTablesResponse(import_run_id=run_id, tables=summaries)


@router.get("/{run_id}/tables/{source_table}/rows", response_model=ExternalImportRowsResponse)
async def preview_import_rows(
    run_id: uuid.UUID,
    source_table: str,
    limit: int = Query(25, ge=1, le=200),
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(admin.tenant_id))
    table = source_table.upper()
    run = await db.get(ExternalImportRun, run_id)
    if not run or run.tenant_id != admin.tenant_id:
        raise HTTPException(status_code=404, detail="Import run not found")
    total = await db.scalar(
        select(func.count())
        .select_from(ExternalRawRow)
        .where(
            ExternalRawRow.tenant_id == admin.tenant_id,
            ExternalRawRow.import_run_id == run_id,
            ExternalRawRow.source_table == table,
        )
    )
    rows = (
        await db.execute(
            select(ExternalRawRow)
            .where(
                ExternalRawRow.tenant_id == admin.tenant_id,
                ExternalRawRow.import_run_id == run_id,
                ExternalRawRow.source_table == table,
            )
            .order_by(ExternalRawRow.created_at.asc(), ExternalRawRow.id.asc())
            .limit(limit)
        )
    ).scalars().all()
    return ExternalImportRowsResponse(
        import_run_id=run_id,
        source_table=table,
        total=total or 0,
        rows=[ExternalRawRowPreview.model_validate(row) for row in rows],
    )


@router.get("/{run_id}/reconcile", response_model=ExternalImportReconcileResponse)
async def reconcile_import_run(
    run_id: uuid.UUID,
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(admin.tenant_id))
    run = await db.get(ExternalImportRun, run_id)
    if not run or run.tenant_id != admin.tenant_id:
        raise HTTPException(status_code=404, detail="Import run not found")

    table_response = await list_import_tables(run_id, admin=admin, db=db)
    total_rows = sum(table.row_count for table in table_response.tables)
    errors = list(run.errors or [])
    warnings = list(run.warnings or [])
    return ExternalImportReconcileResponse(
        import_run_id=run.id,
        status=run.status,
        provider=run.provider,
        export_id=run.export_id,
        table_count=len(table_response.tables),
        total_rows=total_rows,
        tables=table_response.tables,
        warnings=warnings,
        errors=errors,
    )
