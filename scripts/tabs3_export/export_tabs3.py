"""Export Tabs3 / PracticeMaster ODBC tables into a Clarity import bundle.

Runs inside the customer environment because Tabs3 is on-prem. The script only
uses SELECT statements through the vendor-supported ODBC driver.
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import decimal
import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any

try:
    import pyodbc
except ImportError:  # pragma: no cover - exercised on customer workstation
    pyodbc = None

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
except ImportError:  # pragma: no cover - exercised on customer workstation
    AESGCM = None
    PBKDF2HMAC = None
    hashes = None


EXPORT_VERSION = "tabs3-export-v1"

TABLE_GROUPS: dict[str, list[str]] = {
    "core": ["CLIENT", "CONTACT", "BILLTO", "EMPLOYEE", "CLIENTNOTE", "CLIENTCUSTOM"],
    "billing": [
        "FEE",
        "COST",
        "PAYMENT",
        "FUND",
        "LEDGER",
        "LEDGALLOC",
        "ARCHIVE",
        "STMTDET",
        "STMTDETALLOC",
        "STMTTRAK",
    ],
    "rates_codes": [
        "CLIENTRATE",
        "COSTRATE",
        "TCODE",
        "TASKBILLCODE",
        "TASKBUDGET",
        "BILLFREQ",
        "CATEGORY",
    ],
    "trust": ["TRUSTREQUEST", "CLIENT", "BANK", "COMBINEDTRANS", "CONTACT", "RECON"],
    "practicemaster_optional": [
        "CMCLIENT",
        "CMRELATE",
        "CMRELLNK",
        "CMFEE",
        "CMCOST",
        "CMJRNL",
        "CMCAL",
        "CMDOCMGT",
        "CMDOCVSN",
        "CMAUDIT",
        "CMXREF",
    ],
}


def _json_default(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=_json_default)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    if PBKDF2HMAC is None or hashes is None:
        raise RuntimeError("cryptography is required for encrypted exports")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=390000,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def _encrypt_bundle(zip_path: Path, passphrase: str, output_path: Path) -> None:
    if AESGCM is None:
        raise RuntimeError("cryptography is required for encrypted exports")
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(passphrase, salt)
    ciphertext = AESGCM(key).encrypt(nonce, zip_path.read_bytes(), None)
    envelope = {
        "format": "clarity-tabs3-bundle",
        "version": 1,
        "kdf": "pbkdf2-sha256",
        "iterations": 390000,
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    output_path.write_text(json.dumps(envelope), encoding="utf-8")


def _load_schema_tables(schema_path: Path | None) -> dict[str, set[str]]:
    if not schema_path:
        return {}
    raw = json.loads(schema_path.read_text(encoding="utf-8"))
    tables: dict[str, set[str]] = {}
    for table in raw.get("tables", []):
        tables[table["odbc_table"].upper()] = {
            field["name"].upper() for field in table.get("fields", [])
        }
    return tables


def _selected_tables(groups: list[str], explicit_tables: list[str]) -> list[str]:
    seen: set[str] = set()
    tables: list[str] = []
    for group in groups:
        if group not in TABLE_GROUPS:
            raise SystemExit(f"Unknown table group '{group}'. Valid: {', '.join(TABLE_GROUPS)}")
        for table in TABLE_GROUPS[group]:
            key = table.upper()
            if key not in seen:
                seen.add(key)
                tables.append(key)
    for table in explicit_tables:
        key = table.upper()
        if key not in seen:
            seen.add(key)
            tables.append(key)
    return tables


def _connect(args: argparse.Namespace):
    if pyodbc is None:
        raise SystemExit(
            "pyodbc is not installed. Run: python -m pip install -r scripts/tabs3_export/requirements.txt"
        )
    parts = [f"DSN={args.dsn}"]
    if args.user:
        parts.append(f"UID={args.user}")
    if args.password:
        parts.append(f"PWD={args.password}")
    return pyodbc.connect(";".join(parts), readonly=True, autocommit=True)


def _table_columns(cursor, table: str) -> list[str]:
    rows = list(cursor.columns(table=table))
    columns = [row.column_name for row in rows]
    if columns:
        return columns
    # Some ODBC drivers do not expose metadata until the table is queried.
    cursor.execute(f"SELECT * FROM {table}")
    return [col[0] for col in cursor.description or []]


def _row_dict(columns: list[str], row: Any) -> dict[str, Any]:
    return {column: value for column, value in zip(columns, row)}


def _write_table(
    cursor,
    *,
    table: str,
    output_dir: Path,
    row_limit: int | None,
    where_map: dict[str, str],
    schema_tables: dict[str, set[str]],
    schema_warnings: list[str],
) -> dict[str, Any]:
    columns = _table_columns(cursor, table)
    actual = {col.upper() for col in columns}
    expected = schema_tables.get(table.upper())
    if expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if missing:
            schema_warnings.append(f"{table}: missing documented columns: {', '.join(missing[:20])}")
        if extra:
            schema_warnings.append(f"{table}: extra runtime columns: {', '.join(extra[:20])}")

    data_dir = output_dir / "tables"
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / f"{table}.ndjson"

    query = f"SELECT * FROM {table}"
    if where_map.get(table):
        query += f" WHERE {where_map[table]}"

    row_count = 0
    cursor.execute(query)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        while True:
            row = cursor.fetchone()
            if row is None:
                break
            handle.write(_canonical_json(_row_dict(columns, row)) + "\n")
            row_count += 1
            if row_limit is not None and row_count >= row_limit:
                break

    return {
        "name": table,
        "format": "ndjson",
        "path": f"tables/{table}.ndjson",
        "columns": columns,
        "row_count": row_count,
        "sha256": _sha256_file(path),
        "where": where_map.get(table),
        "truncated_at_row_limit": row_limit is not None and row_count >= row_limit,
    }


def _parse_where(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit("--where must use TABLE=SQL_CONDITION")
        table, condition = value.split("=", 1)
        result[table.upper()] = condition.strip()
    return result


def _zip_dir(source_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in source_dir.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(source_dir).as_posix())


def export(args: argparse.Namespace) -> Path:
    if platform.architecture()[0] != "32bit":
        print(
            "WARNING: Tabs3 ODBC installations are commonly 32-bit. "
            "If connection fails, run this with 32-bit Python.",
            file=sys.stderr,
        )

    schema_tables = _load_schema_tables(Path(args.schema_json) if args.schema_json else None)
    tables = _selected_tables(args.groups, args.tables)
    where_map = _parse_where(args.where)
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    export_id = f"tabs3-{dt.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"
    work_dir = Path(tempfile.mkdtemp(prefix=f"{export_id}-"))
    schema_warnings: list[str] = []
    table_entries: list[dict[str, Any]] = []

    try:
        if args.schema_only:
            conn = _connect(args)
            cursor = conn.cursor()
            for table in tables:
                columns = _table_columns(cursor, table)
                table_entries.append(
                    {
                        "name": table,
                        "format": "metadata-only",
                        "path": None,
                        "columns": columns,
                        "row_count": 0,
                        "sha256": None,
                    }
                )
            conn.close()
        else:
            conn = _connect(args)
            cursor = conn.cursor()
            for table in tables:
                print(f"Exporting {table}...", file=sys.stderr)
                try:
                    table_entries.append(
                        _write_table(
                            cursor,
                            table=table,
                            output_dir=work_dir,
                            row_limit=args.row_limit,
                            where_map=where_map,
                            schema_tables=schema_tables,
                            schema_warnings=schema_warnings,
                        )
                    )
                except Exception as exc:
                    if args.continue_on_error:
                        schema_warnings.append(f"{table}: export failed: {exc}")
                    else:
                        raise
            conn.close()

        manifest = {
            "export_version": EXPORT_VERSION,
            "provider": "tabs3",
            "source_system": "tabs3_odbc",
            "export_id": export_id,
            "generated_at": dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            "dsn": args.dsn,
            "schema_only": args.schema_only,
            "groups": args.groups,
            "tables": table_entries,
            "schema_warnings": schema_warnings,
            "row_limit": args.row_limit,
            "host": {
                "machine": platform.node(),
                "python_architecture": platform.architecture()[0],
                "platform": platform.platform(),
            },
        }
        (work_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, default=_json_default), encoding="utf-8"
        )

        zip_path = output_dir / f"{export_id}.zip"
        _zip_dir(work_dir, zip_path)
        if args.passphrase:
            encrypted_path = output_dir / f"{export_id}.tabs3bundle"
            _encrypt_bundle(zip_path, args.passphrase, encrypted_path)
            zip_path.unlink()
            return encrypted_path
        if not args.allow_plaintext:
            zip_path.unlink(missing_ok=True)
            raise SystemExit(
                "Refusing plaintext export. Provide --passphrase or explicitly pass --allow-plaintext for rehearsal."
            )
        return zip_path
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Tabs3 ODBC data for Clarity import")
    parser.add_argument("--dsn", required=True, help="Tabs3 ODBC DSN name")
    parser.add_argument("--user", help="ODBC username if required")
    parser.add_argument("--password", help="ODBC password if required")
    parser.add_argument("--output-dir", default=".", help="Directory for the export bundle")
    parser.add_argument(
        "--schema-json",
        default=str(Path(__file__).resolve().parents[2] / "docs" / "tabs3-odbc-schema.json"),
        help="Optional Clarity schema JSON used for drift warnings",
    )
    parser.add_argument(
        "--groups",
        nargs="+",
        default=["core"],
        help=f"Table groups to export. Valid: {', '.join(TABLE_GROUPS)}",
    )
    parser.add_argument("--tables", nargs="*", default=[], help="Additional explicit ODBC tables")
    parser.add_argument("--row-limit", type=int, help="Max rows per table for rehearsal exports")
    parser.add_argument("--where", action="append", default=[], help="Per-table SQL filter: TABLE=condition")
    parser.add_argument("--schema-only", action="store_true", help="Export metadata only; no customer rows")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue exporting other tables after a table error")
    parser.add_argument("--passphrase", help="Encrypt final bundle with this passphrase")
    parser.add_argument(
        "--allow-plaintext",
        action="store_true",
        help="Allow an unencrypted ZIP. Use only for local rehearsal or redacted data.",
    )
    args = parser.parse_args()
    bundle = export(args)
    print(bundle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
