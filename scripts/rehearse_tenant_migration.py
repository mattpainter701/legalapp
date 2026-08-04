#!/usr/bin/env python3
"""Rehearse candidate Alembic upgrades against synthetic customer data.

CI upgrades a disposable database to the revision immediately before the new
candidate chain, inserts matching records for two tenants, applies the candidate
head, and proves that table/tenant counts and sentinel field values did not fall
or change. When a commit has no new revision, the latest upgrade is rehearsed so
the guard itself remains exercised on every run.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import uuid

from alembic import command
from alembic.config import Config
from alembic.script import Script, ScriptDirectory
import psycopg2
from psycopg2 import sql
from sqlalchemy.engine import make_url

from check_migration_safety import changed_migrations


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


def _alembic() -> tuple[Config, ScriptDirectory]:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    return config, ScriptDirectory.from_config(config)


def _candidate_start(
    scripts: ScriptDirectory, base: str, head: str
) -> tuple[str, list[Script]]:
    additions = {
        (ROOT / path).resolve()
        for status, path, _old_path in changed_migrations(base, head)
        if status.startswith("A")
    }
    candidate_scripts = [
        revision
        for revision in scripts.walk_revisions()
        if Path(revision.path).resolve() in additions
    ]
    if len(candidate_scripts) != len(additions):
        known = {Path(revision.path).resolve() for revision in candidate_scripts}
        missing = sorted(str(path.relative_to(ROOT)) for path in additions - known)
        raise RuntimeError(
            "new migration files are not part of the Alembic graph: "
            + ", ".join(missing)
        )

    if candidate_scripts:
        candidate_ids = {revision.revision for revision in candidate_scripts}
        external_parents = {
            parent
            for revision in candidate_scripts
            for parent in revision._normalized_down_revisions
            if parent not in candidate_ids
        }
        if len(external_parents) != 1:
            raise RuntimeError(
                "candidate migrations must form one forward chain from the deployed head; "
                f"found parents {sorted(external_parents)}"
            )
        return external_parents.pop(), candidate_scripts

    heads = scripts.get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"expected one Alembic head, found {heads}")
    latest = scripts.get_revision(heads[0])
    if latest is None or len(latest._normalized_down_revisions) != 1:
        raise RuntimeError("latest migration does not have one rehearsal predecessor")
    return latest._normalized_down_revisions[0], []


def _connection(database_url: str):
    parsed = make_url(database_url)
    return psycopg2.connect(
        dbname=parsed.database,
        user=parsed.username,
        password=parsed.password,
        host=parsed.host or "localhost",
        port=parsed.port or 5432,
        connect_timeout=10,
    )


def _tenant_counts(cursor) -> dict[tuple[str, str], int]:
    cursor.execute(
        """
        SELECT table_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND column_name = 'tenant_id'
        ORDER BY table_name
        """
    )
    tables = [row[0] for row in cursor.fetchall()]
    counts: dict[tuple[str, str], int] = {}
    for table in tables:
        cursor.execute(
            sql.SQL(
                "SELECT COALESCE(tenant_id::text, '<null>'), count(*)::bigint "
                "FROM {} GROUP BY tenant_id"
            ).format(sql.Identifier(table))
        )
        for tenant_id, count in cursor.fetchall():
            counts[(table, tenant_id)] = count
    return counts


def _selected_rows(cursor, ids: dict[str, list[str]]) -> dict[str, list[tuple]]:
    columns = {
        "tenants": ("id", "name", "domain"),
        "users": ("id", "tenant_id", "email", "full_name"),
        "contacts": ("id", "tenant_id", "first_name", "last_name", "notes"),
        "matters": (
            "id",
            "tenant_id",
            "user_id",
            "slug",
            "matter_name",
            "description",
        ),
        "documents": (
            "id",
            "tenant_id",
            "user_id",
            "filename",
            "content_type",
            "storage_path",
        ),
        "tasks": ("id", "tenant_id", "title", "description", "status"),
    }
    snapshot: dict[str, list[tuple]] = {}
    for table, table_ids in ids.items():
        cursor.execute(
            sql.SQL("SELECT {} FROM {} WHERE id::text = ANY(%s) ORDER BY id").format(
                sql.SQL(", ").join(map(sql.Identifier, columns[table])),
                sql.Identifier(table),
            ),
            (table_ids,),
        )
        snapshot[table] = cursor.fetchall()
    return snapshot


def _seed_customer_data(connection) -> dict[str, list[str]]:
    token = uuid.uuid4().hex
    tenant_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    ids = {
        table: [str(uuid.uuid4()), str(uuid.uuid4())]
        for table in ("tenants", "users", "contacts", "matters", "documents", "tasks")
    }
    ids["tenants"] = tenant_ids

    with connection.cursor() as cursor:
        for index, tenant_id in enumerate(tenant_ids):
            label = "A" if index == 0 else "B"
            cursor.execute(
                "INSERT INTO tenants (id, name, domain) VALUES (%s, %s, %s)",
                (
                    tenant_id,
                    f"Migration Safety Tenant {label}",
                    f"migration-safety-{label.lower()}-{token}.invalid",
                ),
            )
            cursor.execute(
                """
                INSERT INTO users (id, tenant_id, email, full_name)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    ids["users"][index],
                    tenant_id,
                    f"shared-client-{token}@example.invalid",
                    f"Safety Client {label}",
                ),
            )
            cursor.execute(
                """
                INSERT INTO contacts
                    (id, tenant_id, first_name, last_name, notes, created_by_user_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    ids["contacts"][index],
                    tenant_id,
                    "Shared",
                    "Client",
                    f"confidential tenant {label} contact",
                    ids["users"][index],
                ),
            )
            cursor.execute(
                """
                INSERT INTO matters
                    (id, tenant_id, user_id, slug, matter_name, description)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    ids["matters"][index],
                    tenant_id,
                    ids["users"][index],
                    f"shared-matter-{token}",
                    "Shared Migration Rehearsal Matter",
                    f"privileged tenant {label} matter data",
                ),
            )
            cursor.execute(
                """
                INSERT INTO documents
                    (id, tenant_id, user_id, filename, content_type, storage_path)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    ids["documents"][index],
                    tenant_id,
                    ids["users"][index],
                    "shared-client-document.pdf",
                    "application/pdf",
                    f"migration-safety/{label}/confidential.pdf",
                ),
            )
            cursor.execute(
                """
                INSERT INTO tasks
                    (id, tenant_id, title, description, matter_id, created_by_user_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    ids["tasks"][index],
                    tenant_id,
                    "Shared client deadline",
                    f"tenant {label} privileged work product",
                    ids["matters"][index],
                    ids["users"][index],
                ),
            )
    connection.commit()
    return ids


def _assert_preserved(
    before_counts: dict[tuple[str, str], int],
    after_counts: dict[tuple[str, str], int],
    before_rows: dict[str, list[tuple]],
    after_rows: dict[str, list[tuple]],
) -> None:
    losses = [
        (table, tenant_id, before, after_counts.get((table, tenant_id)))
        for (table, tenant_id), before in before_counts.items()
        if after_counts.get((table, tenant_id), -1) < before
    ]
    if losses:
        details = ", ".join(
            f"{table}/{tenant}: {before}->{after}"
            for table, tenant, before, after in losses
        )
        raise RuntimeError(f"tenant row counts decreased during migration: {details}")

    changed = [
        table for table, rows in before_rows.items() if after_rows.get(table) != rows
    ]
    if changed:
        raise RuntimeError(
            "sentinel customer fields changed during migration: " + ", ".join(changed)
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, help="base commit SHA")
    parser.add_argument("--head", default="HEAD", help="candidate commit SHA")
    args = parser.parse_args()

    database_url = os.getenv("MIGRATOR_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        print(
            "::error::MIGRATOR_DATABASE_URL or DATABASE_URL is required",
            file=sys.stderr,
        )
        return 2

    try:
        config, scripts = _alembic()
        starting_revision, candidates = _candidate_start(scripts, args.base, args.head)
        print(f"Upgrading disposable database to rehearsal start {starting_revision}")
        command.upgrade(config, starting_revision)

        with _connection(database_url) as connection:
            ids = _seed_customer_data(connection)
            with connection.cursor() as cursor:
                before_counts = _tenant_counts(cursor)
                before_rows = _selected_rows(cursor, ids)

        print("Applying candidate Alembic head over two-tenant synthetic data")
        command.upgrade(config, "head")

        with _connection(database_url) as connection:
            with connection.cursor() as cursor:
                after_counts = _tenant_counts(cursor)
                after_rows = _selected_rows(cursor, ids)
                cursor.execute(
                    "SELECT version_num FROM alembic_version ORDER BY version_num"
                )
                applied_heads = [row[0] for row in cursor.fetchall()]

        expected_heads = sorted(scripts.get_heads())
        if applied_heads != expected_heads:
            raise RuntimeError(
                f"database revisions {applied_heads} do not match candidate heads {expected_heads}"
            )
        _assert_preserved(before_counts, after_counts, before_rows, after_rows)
    except Exception as exc:  # CI boundary: render a concise GitHub annotation.
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    candidate_names = (
        ", ".join(Path(item.path).name for item in candidates) or "latest revision"
    )
    print(
        f"Migration rehearsal preserved tenant counts and sentinel fields: {candidate_names}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
