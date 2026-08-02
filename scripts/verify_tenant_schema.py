#!/usr/bin/env python3
"""Verify the migrated database's tenant-isolation security contract."""

from __future__ import annotations

import os
import sys
import uuid

import psycopg2
from sqlalchemy.engine import make_url


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


def _runtime_role(database_url: str) -> str:
    username = make_url(database_url).username
    if not username:
        raise RuntimeError("runtime database URL does not contain a username")
    return username


def _catalog_violations(connection, runtime_role: str) -> tuple[list[str], int]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT oid, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s",
            (runtime_role,),
        )
        role = cursor.fetchone()
        if role is None:
            raise RuntimeError(f"runtime role does not exist: {runtime_role}")
        runtime_oid, is_superuser, bypasses_rls = role
        if is_superuser or bypasses_rls:
            raise RuntimeError(
                f"runtime role {runtime_role} must be NOSUPERUSER and NOBYPASSRLS"
            )

        cursor.execute(
            """
            SELECT
                c.oid,
                c.relname,
                c.relrowsecurity,
                c.relforcerowsecurity,
                format_type(a.atttypid, a.atttypmod),
                owner.rolname
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            JOIN pg_attribute AS a
              ON a.attrelid = c.oid
             AND a.attname = 'tenant_id'
             AND NOT a.attisdropped
            JOIN pg_roles AS owner ON owner.oid = c.relowner
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
            ORDER BY c.relname
            """
        )
        tenant_tables = cursor.fetchall()
        if not tenant_tables:
            raise RuntimeError("migrated schema contains no tenant-owned tables")

        violations: list[str] = []
        for (
            table_oid,
            table,
            rls_enabled,
            rls_forced,
            tenant_type,
            owner,
        ) in tenant_tables:
            if tenant_type != "uuid":
                violations.append(
                    f"{table}.tenant_id has type {tenant_type}, expected uuid"
                )
            if not rls_enabled:
                violations.append(f"{table} does not ENABLE ROW LEVEL SECURITY")
            if not rls_forced:
                violations.append(f"{table} does not FORCE ROW LEVEL SECURITY")
            if owner == runtime_role:
                violations.append(f"{table} is owned by runtime role {runtime_role}")

            cursor.execute(
                """
                SELECT
                    polname,
                    polroles,
                    COALESCE(pg_get_expr(polqual, polrelid), ''),
                    COALESCE(pg_get_expr(polwithcheck, polrelid), '')
                FROM pg_policy
                WHERE polrelid = %s
                """,
                (table_oid,),
            )
            policies = cursor.fetchall()
            isolating_policy = False
            for _name, roles, using_expression, check_expression in policies:
                expression = f"{using_expression} {check_expression}".lower()
                applies_to_runtime = 0 in roles or runtime_oid in roles
                if (
                    applies_to_runtime
                    and "tenant_id" in expression
                    and "current_setting" in expression
                ):
                    isolating_policy = True
                    break
            if not isolating_policy:
                violations.append(
                    f"{table} lacks a runtime-applicable tenant_id/current_setting RLS policy"
                )
    return violations, len(tenant_tables)


def _sentinel_users(connection) -> list[tuple[str, str, str]]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id::text, tenant_id::text, full_name
            FROM users
            WHERE email LIKE 'shared-client-%@example.invalid'
            ORDER BY tenant_id
            """
        )
        rows = cursor.fetchall()
    if len(rows) != 2 or rows[0][1] == rows[1][1]:
        raise RuntimeError(
            "migration rehearsal did not leave two distinct tenant users"
        )
    return rows


def _set_tenant(cursor, tenant_id: str) -> None:
    cursor.execute(
        """
        SELECT
          set_config('app.current_tenant_id', %s, true),
          set_config('app.tenant_id', %s, true),
          set_config('app.rls_bypass', 'off', true)
        """,
        (tenant_id, tenant_id),
    )


def _verify_effective_isolation(
    owner_connection, runtime_url: str, sentinels: list[tuple[str, str, str]]
) -> None:
    first, second = sentinels
    with _connection(runtime_url) as runtime:
        with runtime.cursor() as cursor:
            _set_tenant(cursor, first[1])
            cursor.execute(
                """
                SELECT id::text, tenant_id::text
                FROM users
                WHERE id::text = ANY(%s)
                """,
                ([first[0], second[0]],),
            )
            visible = cursor.fetchall()
            if visible != [(first[0], first[1])]:
                raise RuntimeError(
                    f"runtime role crossed tenant visibility boundary: {visible}"
                )

            cursor.execute(
                "UPDATE users SET full_name = 'cross-tenant mutation' WHERE id = %s",
                (second[0],),
            )
            if cursor.rowcount != 0:
                raise RuntimeError(
                    "runtime role updated a row belonging to another tenant"
                )

            try:
                cursor.execute(
                    """
                    INSERT INTO users (id, tenant_id, email, full_name)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        str(uuid.uuid4()),
                        second[1],
                        f"cross-tenant-{uuid.uuid4().hex}@example.invalid",
                        "forbidden insert",
                    ),
                )
            except psycopg2.Error:
                runtime.rollback()
            else:
                raise RuntimeError("runtime role inserted a row for another tenant")

    with _connection(runtime_url) as runtime:
        with runtime.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*)
                FROM users
                WHERE id::text = ANY(%s)
                """,
                ([first[0], second[0]],),
            )
            if cursor.fetchone()[0] != 0:
                raise RuntimeError("tenant data is visible without tenant context")

    with owner_connection.cursor() as cursor:
        cursor.execute("SELECT full_name FROM users WHERE id = %s", (second[0],))
        if cursor.fetchone()[0] != second[2]:
            raise RuntimeError("cross-tenant write attempt changed customer data")


def main() -> int:
    owner_url = os.getenv("MIGRATOR_DATABASE_URL") or os.getenv("DATABASE_URL")
    runtime_url = os.getenv("RLS_TEST_DATABASE_URL")
    if not owner_url or not runtime_url:
        print(
            "::error::MIGRATOR_DATABASE_URL/DATABASE_URL and RLS_TEST_DATABASE_URL are required",
            file=sys.stderr,
        )
        return 2

    try:
        runtime_role = _runtime_role(runtime_url)
        with _connection(owner_url) as owner:
            violations, table_count = _catalog_violations(owner, runtime_role)
            if violations:
                raise RuntimeError("; ".join(violations))
            sentinels = _sentinel_users(owner)
            _verify_effective_isolation(owner, runtime_url, sentinels)
    except Exception as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    print(
        f"Tenant schema verified: {table_count} tenant tables enforce FORCE RLS; "
        "cross-tenant reads/writes and no-context reads are blocked."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
