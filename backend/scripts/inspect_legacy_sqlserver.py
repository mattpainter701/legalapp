"""Read-only SQL Server inspection and export tooling.

This is intended for one-off customer discovery over VPN/Tailscale. It never
writes to SQL Server. Use a read-only SQL login where possible.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_@$#]*$")
SENSITIVE_RE = re.compile(
    r"(name|phone|email|address|ssn|social|dob|birth|note|memo|comment|description)",
    re.IGNORECASE,
)

TABLE_KEYWORDS = {
    "call": 8,
    "intake": 10,
    "lead": 10,
    "prospect": 9,
    "inquiry": 9,
    "client": 5,
    "contact": 6,
    "phone": 7,
    "history": 4,
    "matter": 3,
    "case": 3,
    "attorney": 3,
}

COLUMN_KEYWORDS = {
    "phone": 8,
    "caller": 8,
    "call": 7,
    "intake": 8,
    "lead": 8,
    "prospect": 8,
    "client": 4,
    "name": 4,
    "attorney": 5,
    "lawyer": 5,
    "date": 3,
    "created": 3,
    "reason": 5,
    "purpose": 5,
    "note": 3,
    "memo": 3,
}

TABS3_KEYWORDS = {
    "client",
    "matter",
    "time",
    "fee",
    "cost",
    "ar",
    "invoice",
    "billing",
    "ledger",
    "trust",
    "practice",
    "pm",
}


@dataclass
class ColumnInfo:
    schema: str
    table: str
    column: str
    data_type: str
    max_length: int | None
    nullable: bool
    ordinal: int


def _require_pyodbc():
    try:
        import pyodbc  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "pyodbc is not installed. Run: py -m pip install -r backend/scripts/requirements-sqlserver.txt"
        ) from exc
    return pyodbc


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _conn_string(args: argparse.Namespace, database: str | None = None) -> str:
    if args.connection_string:
        conn = args.connection_string
    elif os.getenv("LEGACY_SQLSERVER_CONNECTION_STRING"):
        conn = os.environ["LEGACY_SQLSERVER_CONNECTION_STRING"]
    else:
        driver = args.driver or os.getenv("LEGACY_SQLSERVER_DRIVER") or "ODBC Driver 18 for SQL Server"
        server = args.server or os.getenv("LEGACY_SQLSERVER_HOST")
        db = database or args.database or os.getenv("LEGACY_SQLSERVER_DATABASE")
        user = args.username or os.getenv("LEGACY_SQLSERVER_USER")
        password = args.password or os.getenv("LEGACY_SQLSERVER_PASSWORD")
        trust_cert = "yes" if args.trust_server_certificate else os.getenv("LEGACY_SQLSERVER_TRUST_CERT", "yes")
        encrypt = os.getenv("LEGACY_SQLSERVER_ENCRYPT", "yes")
        if not server:
            raise SystemExit("Provide --server or LEGACY_SQLSERVER_HOST.")
        parts = [
            f"DRIVER={{{driver}}}",
            f"SERVER={server}",
            f"Encrypt={encrypt}",
            f"TrustServerCertificate={trust_cert}",
        ]
        if db:
            parts.append(f"DATABASE={db}")
        if user:
            parts.extend([f"UID={user}", f"PWD={password or ''}"])
        else:
            parts.append("Trusted_Connection=yes")
        conn = ";".join(parts) + ";"
    if database and "DATABASE=" not in conn.upper():
        conn += f"DATABASE={database};"
    return conn


def connect(args: argparse.Namespace, database: str | None = None):
    pyodbc = _require_pyodbc()
    return pyodbc.connect(_conn_string(args, database), timeout=args.timeout)


def quote_name(value: str) -> str:
    if not IDENT_RE.match(value):
        raise SystemExit(f"Unsafe SQL identifier: {value!r}")
    return f"[{value}]"


def split_table(value: str) -> tuple[str, str]:
    parts = value.split(".")
    if len(parts) == 1:
        return "dbo", parts[0]
    if len(parts) == 2:
        return parts[0], parts[1]
    raise SystemExit("Table must be TABLE or SCHEMA.TABLE")


def safe_table_ref(value: str) -> str:
    schema, table = split_table(value)
    return f"{quote_name(schema)}.{quote_name(table)}"


def list_databases(args: argparse.Namespace) -> None:
    with connect(args, database=None) as conn:
        rows = conn.cursor().execute(
            """
            SELECT name
            FROM sys.databases
            WHERE state = 0
              AND name NOT IN ('master', 'model', 'msdb', 'tempdb')
            ORDER BY name
            """
        ).fetchall()
    for row in rows:
        print(row.name)


def fetch_columns(conn) -> list[ColumnInfo]:
    rows = conn.cursor().execute(
        """
        SELECT
            TABLE_SCHEMA,
            TABLE_NAME,
            COLUMN_NAME,
            DATA_TYPE,
            CHARACTER_MAXIMUM_LENGTH,
            IS_NULLABLE,
            ORDINAL_POSITION
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA NOT IN ('sys', 'INFORMATION_SCHEMA')
        ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
        """
    ).fetchall()
    return [
        ColumnInfo(
            schema=row.TABLE_SCHEMA,
            table=row.TABLE_NAME,
            column=row.COLUMN_NAME,
            data_type=row.DATA_TYPE,
            max_length=row.CHARACTER_MAXIMUM_LENGTH,
            nullable=row.IS_NULLABLE == "YES",
            ordinal=row.ORDINAL_POSITION,
        )
        for row in rows
    ]


def fetch_row_counts(conn) -> dict[str, int]:
    rows = conn.cursor().execute(
        """
        SELECT
            SCHEMA_NAME(t.schema_id) AS schema_name,
            t.name AS table_name,
            SUM(p.row_count) AS rows_count
        FROM sys.tables t
        JOIN sys.dm_db_partition_stats p ON p.object_id = t.object_id
        WHERE p.index_id IN (0, 1)
        GROUP BY t.schema_id, t.name
        """
    ).fetchall()
    return {f"{row.schema_name}.{row.table_name}": int(row.rows_count or 0) for row in rows}


def score_table(schema: str, table: str, columns: list[ColumnInfo]) -> dict[str, Any]:
    text = f"{schema} {table}".lower()
    score = 0
    reasons: list[str] = []
    for keyword, weight in TABLE_KEYWORDS.items():
        if keyword in text:
            score += weight
            reasons.append(f"table:{keyword}")
    for col in columns:
        col_text = col.column.lower()
        for keyword, weight in COLUMN_KEYWORDS.items():
            if keyword in col_text:
                score += weight
                reasons.append(f"column:{col.column}")
                break
    tabs3_hits = sorted({word for word in TABS3_KEYWORDS if word in text or any(word in c.column.lower() for c in columns)})
    return {"score": score, "reasons": sorted(set(reasons)), "tabs3_practicemaster_hints": tabs3_hits}


def redact_row(row: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in row.items():
        if value is None:
            redacted[key] = None
        elif SENSITIVE_RE.search(key):
            text = str(value)
            redacted[key] = f"<redacted:{len(text)} chars>"
        else:
            redacted[key] = value
    return redacted


def sample_rows(conn, table_ref: str, sample_size: int, redact: bool) -> list[dict[str, Any]]:
    cursor = conn.cursor().execute(f"SELECT TOP {int(sample_size)} * FROM {table_ref}")
    columns = [col[0] for col in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    return [redact_row(row) for row in rows] if redact else rows


def inspect_database(args: argparse.Namespace) -> None:
    with connect(args) as conn:
        columns = fetch_columns(conn)
        row_counts = fetch_row_counts(conn)

        grouped: dict[str, list[ColumnInfo]] = defaultdict(list)
        for col in columns:
            grouped[f"{col.schema}.{col.table}"].append(col)

        tables = []
        for key, cols in grouped.items():
            schema, table = key.split(".", 1)
            scored = score_table(schema, table, cols)
            table_info = {
                "schema": schema,
                "table": table,
                "row_count_estimate": row_counts.get(key, 0),
                "candidate_score": scored["score"],
                "candidate_reasons": scored["reasons"],
                "tabs3_practicemaster_hints": scored["tabs3_practicemaster_hints"],
                "columns": [asdict(col) for col in cols],
            }
            if args.samples and scored["score"] >= args.sample_min_score:
                table_info["sample_rows"] = sample_rows(
                    conn,
                    f"{quote_name(schema)}.{quote_name(table)}",
                    args.samples,
                    redact=not args.unredacted_samples,
                )
            tables.append(table_info)

    tables.sort(key=lambda item: (item["candidate_score"], item["row_count_estimate"]), reverse=True)
    report = {
        "database": args.database,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "table_count": len(tables),
        "candidate_tables": [t for t in tables if t["candidate_score"] > 0][: args.candidate_limit],
        "tables": tables,
    }

    Path(args.out).write_text(json.dumps(report, indent=2, default=_json_default), encoding="utf-8")
    if args.markdown:
        write_markdown_report(report, Path(args.markdown))
    print(f"Wrote schema report: {args.out}")
    if args.markdown:
        print(f"Wrote markdown report: {args.markdown}")


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    lines = [
        f"# SQL Server Inspection: {report.get('database') or '(default database)'}",
        "",
        f"Generated: {report['generated_at']}",
        f"Tables: {report['table_count']}",
        "",
        "## Likely Intake / Call / Practice Data Tables",
        "",
        "| Score | Table | Rows | Reasons | Tabs3/PM hints |",
        "|-:|-|-:|-|-|",
    ]
    for table in report["candidate_tables"]:
        full_name = f"{table['schema']}.{table['table']}"
        lines.append(
            f"| {table['candidate_score']} | `{full_name}` | {table['row_count_estimate']} | "
            f"{', '.join(table['candidate_reasons'])} | {', '.join(table['tabs3_practicemaster_hints'])} |"
        )
    lines.extend(["", "## Candidate Columns", ""])
    for table in report["candidate_tables"]:
        full_name = f"{table['schema']}.{table['table']}"
        lines.extend([f"### `{full_name}`", "", "| Column | Type | Nullable |", "|-|-|-|"])
        for col in table["columns"]:
            length = f"({col['max_length']})" if col["max_length"] else ""
            lines.append(f"| `{col['column']}` | {col['data_type']}{length} | {col['nullable']} |")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_mapping(values: list[str]) -> dict[str, str]:
    mapping = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"Mapping must be target=SourceColumn, got {value!r}")
        target, source = value.split("=", 1)
        target = target.strip()
        source = source.strip()
        if not target or not source:
            raise SystemExit(f"Invalid mapping {value!r}")
        mapping[target] = source
    return mapping


def export_calls(args: argparse.Namespace) -> None:
    canonical = [
        "source_row_id",
        "caller_name",
        "phone",
        "call_date",
        "practice_area",
        "purpose",
        "prior_attorney_name",
        "notes",
    ]
    mapping = parse_mapping(args.map or [])
    table_ref = safe_table_ref(args.table)
    limit = f"TOP {int(args.limit)} " if args.limit else ""
    where = f" WHERE {args.where}" if args.where else ""
    if args.where and (";" in args.where or "--" in args.where or "/*" in args.where):
        raise SystemExit("--where must be a simple predicate without comments or semicolons")

    sql = f"SELECT {limit}* FROM {table_ref}{where}"
    with connect(args) as conn:
        cursor = conn.cursor().execute(sql)
        source_columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()

    out_path = Path(args.out)
    with out_path.open("w", newline="", encoding="utf-8-sig") as handle:
        raw_columns = [col for col in source_columns if col not in set(mapping.values())]
        fieldnames = canonical + [f"raw__{col}" for col in raw_columns]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for idx, row in enumerate(rows, start=1):
            source = dict(zip(source_columns, row))
            output = {key: "" for key in canonical}
            for target, source_col in mapping.items():
                output[target] = source.get(source_col, "")
            if not output["source_row_id"]:
                output["source_row_id"] = str(source.get("ID") or source.get("Id") or source.get("id") or idx)
            for col in raw_columns:
                output[f"raw__{col}"] = source.get(col, "")
            writer.writerow(output)
    print(f"Exported {len(rows)} rows to {out_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only legacy SQL Server inspector/exporter.")
    parser.add_argument("--connection-string", help="Full ODBC connection string; env LEGACY_SQLSERVER_CONNECTION_STRING also works.")
    parser.add_argument("--driver", help="ODBC driver name, default ODBC Driver 18 for SQL Server.")
    parser.add_argument("--server", help="SQL Server host or host,port. Env LEGACY_SQLSERVER_HOST.")
    parser.add_argument("--database", help="Database name. Env LEGACY_SQLSERVER_DATABASE.")
    parser.add_argument("--username", help="SQL login. Env LEGACY_SQLSERVER_USER.")
    parser.add_argument("--password", help="SQL password. Env LEGACY_SQLSERVER_PASSWORD.")
    parser.add_argument("--trust-server-certificate", action="store_true", help="Set TrustServerCertificate=yes.")
    parser.add_argument("--timeout", type=int, default=15)

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list-databases")

    inspect_cmd = sub.add_parser("inspect")
    inspect_cmd.add_argument("--out", default="legacy-sqlserver-schema.json")
    inspect_cmd.add_argument("--markdown", default="legacy-sqlserver-schema.md")
    inspect_cmd.add_argument("--candidate-limit", type=int, default=30)
    inspect_cmd.add_argument("--samples", type=int, default=0, help="Include TOP N sample rows for likely tables.")
    inspect_cmd.add_argument("--sample-min-score", type=int, default=15)
    inspect_cmd.add_argument("--unredacted-samples", action="store_true")

    export_cmd = sub.add_parser("export-calls")
    export_cmd.add_argument("--table", required=True, help="Source table as TABLE or SCHEMA.TABLE.")
    export_cmd.add_argument("--map", action="append", default=[], help="Canonical=SourceColumn mapping; repeatable.")
    export_cmd.add_argument("--where", help="Optional simple WHERE predicate, without the WHERE keyword.")
    export_cmd.add_argument("--limit", type=int, help="Optional TOP N row limit.")
    export_cmd.add_argument("--out", required=True, help="Output CSV for import_legacy_call_records.py.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "list-databases":
        list_databases(args)
    elif args.command == "inspect":
        inspect_database(args)
    elif args.command == "export-calls":
        export_calls(args)
    else:
        raise SystemExit(f"Unknown command {args.command}")


if __name__ == "__main__":
    main()
