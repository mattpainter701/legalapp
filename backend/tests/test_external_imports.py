import base64
import io
import json
import uuid
import zipfile

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from sqlalchemy import func, select

from app.models.external_import import (
    ExternalImportRun,
    ExternalRawRow,
    ExternalSystemConnection,
)
from app.routers.external_imports import PROMOTION_CONFIRMATION, _report_hash
from app.models.contact import Contact
from app.models.plugin import Matter
from app.models.external_import import ExternalRecordLink


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _bundle(
    rows_by_table: dict[str, list[dict]], *, corrupt_checksum: bool = False
) -> bytes:
    files: dict[str, bytes] = {}
    tables = []
    for table, rows in rows_by_table.items():
        data = "".join(_canonical_json(row) + "\n" for row in rows).encode("utf-8")
        files[f"tables/{table}.ndjson"] = data
        tables.append(
            {
                "name": table,
                "format": "ndjson",
                "path": f"tables/{table}.ndjson",
                "columns": list(rows[0].keys()) if rows else [],
                "row_count": len(rows),
                "sha256": (
                    "bad"
                    if corrupt_checksum
                    else __import__("hashlib").sha256(data).hexdigest()
                ),
            }
        )
    manifest = {
        "export_version": "tabs3-export-v1",
        "provider": "tabs3",
        "source_system": "tabs3_odbc",
        "export_id": "test-export",
        "dsn": "Tabs3Test",
        "tables": tables,
        "schema_warnings": [],
    }

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for path, data in files.items():
            zf.writestr(path, data)
    return out.getvalue()


def _encrypted_bundle(zip_bytes: bytes, passphrase: str) -> bytes:
    salt = b"1234567890abcdef"
    nonce = b"123456789012"
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=390000,
    )
    key = kdf.derive(passphrase.encode("utf-8"))
    ciphertext = AESGCM(key).encrypt(nonce, zip_bytes, None)
    envelope = {
        "format": "clarity-tabs3-bundle",
        "version": 1,
        "kdf": "pbkdf2-sha256",
        "iterations": 390000,
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    return json.dumps(envelope).encode("utf-8")


@pytest.mark.asyncio
async def test_tabs3_upload_stages_raw_rows_and_summaries(
    client, db_session, test_tenant
):
    bundle = _bundle(
        {
            "CLIENT": [
                {"CLIENT_ID": "100.00", "NAME": "Acme", "CONTACT": "Jane Doe"},
                {"CLIENT_ID": "101.00", "NAME": "Beta", "CONTACT": "John Doe"},
            ],
            "FEE": [{"_SEQUENCE_NO": 1, "CLIENT_ID": "100.00", "HOURS": "1.20"}],
        }
    )

    resp = await client.post(
        "/api/imports/tabs3/upload",
        files={"file": ("tabs3.zip", bundle, "application/zip")},
        data={"accounting_mode": "tabs3_reference"},
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    run_id = uuid.UUID(data["id"])
    assert data["status"] == "staged"
    assert data["row_counts"] == {"CLIENT": 2, "FEE": 1}

    run = await db_session.get(ExternalImportRun, run_id)
    assert run.export_id == "test-export"
    conn = await db_session.get(ExternalSystemConnection, run.connection_id)
    assert conn.accounting_mode == "tabs3_reference"
    assert conn.last_import_run_id == run.id

    raw_rows = (
        (
            await db_session.execute(
                select(ExternalRawRow).where(ExternalRawRow.import_run_id == run_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(raw_rows) == 3
    assert {row.source_table for row in raw_rows} == {"CLIENT", "FEE"}

    tables = await client.get(f"/api/imports/{run_id}/tables")
    assert tables.status_code == 200
    assert {
        item["source_table"]: item["row_count"] for item in tables.json()["tables"]
    } == {
        "CLIENT": 2,
        "FEE": 1,
    }

    preview = await client.get(f"/api/imports/{run_id}/tables/CLIENT/rows")
    assert preview.status_code == 200
    assert preview.json()["total"] == 2
    assert preview.json()["rows"][0]["row_data"]["NAME"] == "Acme"

    reconcile = await client.get(f"/api/imports/{run_id}/reconcile")
    assert reconcile.status_code == 200
    assert reconcile.json()["total_rows"] == 3


@pytest.mark.asyncio
async def test_tabs3_upload_accepts_encrypted_bundle(client):
    zip_bytes = _bundle({"CLIENT": [{"CLIENT_ID": "100.00", "NAME": "Acme"}]})
    encrypted = _encrypted_bundle(zip_bytes, "secret")

    resp = await client.post(
        "/api/imports/tabs3/upload",
        files={"file": ("tabs3.tabs3bundle", encrypted, "application/octet-stream")},
        data={"passphrase": "secret", "accounting_mode": "qbo"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["row_counts"] == {"CLIENT": 1}


@pytest.mark.asyncio
async def test_tabs3_upload_rejects_bad_checksum_without_canonical_records(
    client, db_session
):
    bundle = _bundle(
        {"CLIENT": [{"CLIENT_ID": "100.00", "NAME": "Acme"}]}, corrupt_checksum=True
    )

    resp = await client.post(
        "/api/imports/tabs3/upload",
        files={"file": ("tabs3.zip", bundle, "application/zip")},
    )

    assert resp.status_code == 400
    failed_runs = (
        (
            await db_session.execute(
                select(ExternalImportRun).where(ExternalImportRun.status == "failed")
            )
        )
        .scalars()
        .all()
    )
    assert len(failed_runs) == 1
    assert "Checksum mismatch" in failed_runs[0].errors[0]


@pytest.mark.asyncio
async def test_import_requires_report_bound_approval_and_promotes_idempotently(
    client, db_session, test_tenant
):
    bundle = _bundle(
        {
            "CLIENT": [
                {"CLIENT_ID": "100.00", "NAME": "Acme", "EMAIL": "jane@acme.test"}
            ],
            "MATTER": [
                {
                    "CLIENT_ID": "100.00",
                    "MATTER_NAME": "Acme v. Beta",
                    "DESCRIPTION": "Imported case",
                }
            ],
        }
    )
    uploaded = await client.post(
        "/api/imports/tabs3/upload",
        files={"file": ("tabs3.zip", bundle, "application/zip")},
    )
    assert uploaded.status_code == 200, uploaded.text
    run_id = uuid.UUID(uploaded.json()["id"])
    run = await db_session.get(ExternalImportRun, run_id)
    report_hash = _report_hash(run)

    rejected = await client.post(
        f"/api/imports/{run_id}/approve",
        json={"confirmation": PROMOTION_CONFIRMATION, "report_hash": "stale"},
    )
    assert rejected.status_code == 409

    approved = await client.post(
        f"/api/imports/{run_id}/approve",
        json={"confirmation": PROMOTION_CONFIRMATION, "report_hash": report_hash},
    )
    assert approved.status_code == 200, approved.text

    promoted = await client.post(f"/api/imports/{run_id}/promote")
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["created"] == {"contacts": 1, "matters": 1}
    assert promoted.json()["linked"] == 2

    replay = await client.post(f"/api/imports/{run_id}/promote")
    assert replay.status_code == 200
    assert replay.json()["status"] == "promoted"
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Contact)
            .where(Contact.tenant_id == test_tenant.id)
        )
        == 1
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Matter)
            .where(Matter.tenant_id == test_tenant.id)
        )
        == 1
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(ExternalRecordLink)
            .where(ExternalRecordLink.import_run_id == run_id)
        )
        == 2
    )

    rolled_back = await client.post(f"/api/imports/{run_id}/rollback")
    assert rolled_back.status_code == 200, rolled_back.text
    assert rolled_back.json()["status"] == "rollback_pending"


@pytest.mark.asyncio
async def test_import_reuses_links_across_runs_and_maps_matter_before_client(
    client, db_session
):
    bundle = _bundle(
        {
            "MATTER": [{"CLIENT_ID": "100.00", "MATTER_NAME": "Acme v. Beta"}],
            "CLIENT": [{"CLIENT_ID": "100.00", "NAME": "Acme"}],
        }
    )

    async def promote_once():
        uploaded = await client.post(
            "/api/imports/tabs3/upload",
            files={"file": ("tabs3.zip", bundle, "application/zip")},
        )
        assert uploaded.status_code == 200, uploaded.text
        run_id = uuid.UUID(uploaded.json()["id"])
        run = await db_session.get(ExternalImportRun, run_id)
        approved = await client.post(
            f"/api/imports/{run_id}/approve",
            json={
                "confirmation": PROMOTION_CONFIRMATION,
                "report_hash": _report_hash(run),
            },
        )
        assert approved.status_code == 200, approved.text
        promoted = await client.post(f"/api/imports/{run_id}/promote")
        assert promoted.status_code == 200, promoted.text
        return run_id, promoted.json()

    first_run, first = await promote_once()
    second_run, second = await promote_once()
    replay = await client.post(f"/api/imports/{second_run}/promote")
    assert replay.status_code == 200, replay.text
    assert replay.json() == second
    assert first["created"] == {"contacts": 1, "matters": 1}
    assert second["created"] == {"contacts": 0, "matters": 0}
    assert second["linked"] == 0
    assert second["skipped"] == 2
    assert await db_session.scalar(select(func.count()).select_from(Contact)) == 1
    assert await db_session.scalar(select(func.count()).select_from(Matter)) == 1
    assert (
        await db_session.scalar(select(func.count()).select_from(ExternalRecordLink))
        == 2
    )
    assert first_run != second_run


@pytest.mark.asyncio
async def test_import_rejects_conflicting_identity_matches_without_writes(
    client, db_session, test_tenant, test_user
):
    db_session.add_all(
        [
            Contact(
                tenant_id=test_tenant.id,
                client_number="100.00",
                email="one@example.test",
                first_name="One",
                contact_type="client",
            ),
            Contact(
                tenant_id=test_tenant.id,
                client_number="200.00",
                email="two@example.test",
                first_name="Two",
                contact_type="client",
            ),
        ]
    )
    await db_session.commit()
    bundle = _bundle(
        {
            "CLIENT": [
                {"CLIENT_ID": "100.00", "NAME": "Conflict", "EMAIL": "two@example.test"}
            ]
        }
    )
    uploaded = await client.post(
        "/api/imports/tabs3/upload",
        files={"file": ("tabs3.zip", bundle, "application/zip")},
    )
    run_id = uuid.UUID(uploaded.json()["id"])
    run = await db_session.get(ExternalImportRun, run_id)
    assert (
        await client.post(
            f"/api/imports/{run_id}/approve",
            json={
                "confirmation": PROMOTION_CONFIRMATION,
                "report_hash": _report_hash(run),
            },
        )
    ).status_code == 200
    promoted = await client.post(f"/api/imports/{run_id}/promote")
    assert promoted.status_code == 422
    failed = await db_session.get(ExternalImportRun, run_id)
    assert failed.status == "promotion_failed"
    assert "different contacts" in failed.errors[0]
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(ExternalRecordLink)
            .where(ExternalRecordLink.import_run_id == run_id)
        )
        == 0
    )


@pytest.mark.asyncio
async def test_import_rejects_anonymous_client_without_canonical_writes(
    client, db_session
):
    bundle = _bundle({"CLIENT": [{"NAME": "No stable identity"}]})
    uploaded = await client.post(
        "/api/imports/tabs3/upload",
        files={"file": ("tabs3.zip", bundle, "application/zip")},
    )
    run_id = uuid.UUID(uploaded.json()["id"])
    run = await db_session.get(ExternalImportRun, run_id)
    assert (
        await client.post(
            f"/api/imports/{run_id}/approve",
            json={
                "confirmation": PROMOTION_CONFIRMATION,
                "report_hash": _report_hash(run),
            },
        )
    ).status_code == 200
    promoted = await client.post(f"/api/imports/{run_id}/promote")
    assert promoted.status_code == 422
    failed = await db_session.get(ExternalImportRun, run_id)
    assert failed.status == "promotion_failed"
    assert "neither CLIENT_ID nor email" in failed.errors[0]
    assert await db_session.scalar(select(func.count()).select_from(Contact)) == 0
    assert await db_session.scalar(select(func.count()).select_from(Matter)) == 0
    assert (
        await db_session.scalar(select(func.count()).select_from(ExternalRecordLink))
        == 0
    )


@pytest.mark.asyncio
async def test_import_missing_matter_name_persists_failure_after_rollback(
    client, db_session
):
    bundle = _bundle(
        {
            "CLIENT": [{"CLIENT_ID": "100.00", "NAME": "Acme"}],
            "MATTER": [{"CLIENT_ID": "100.00"}],
        }
    )
    uploaded = await client.post(
        "/api/imports/tabs3/upload",
        files={"file": ("tabs3.zip", bundle, "application/zip")},
    )
    run_id = uuid.UUID(uploaded.json()["id"])
    run = await db_session.get(ExternalImportRun, run_id)
    assert (
        await client.post(
            f"/api/imports/{run_id}/approve",
            json={
                "confirmation": PROMOTION_CONFIRMATION,
                "report_hash": _report_hash(run),
            },
        )
    ).status_code == 200
    promoted = await client.post(f"/api/imports/{run_id}/promote")
    assert promoted.status_code == 422
    failed = await db_session.get(ExternalImportRun, run_id)
    assert failed.status == "promotion_failed"
    assert "matter row has no name" in failed.errors[0]
    assert await db_session.scalar(select(func.count()).select_from(Contact)) == 0
    assert await db_session.scalar(select(func.count()).select_from(Matter)) == 0
    assert (
        await db_session.scalar(select(func.count()).select_from(ExternalRecordLink))
        == 0
    )
