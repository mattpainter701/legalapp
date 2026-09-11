"""Keep tenant RLS policies failing closed when the tenant setting is empty.

``set_tenant_context`` writes transaction-local settings, so any commit inside
a unit of work clears them, and within the same session the setting then reads
as ``''`` rather than NULL. Thirty-nine policies compared ``tenant_id`` with
``current_setting(...)::uuid`` directly, and ``''::uuid`` raises ``invalid input
syntax for type uuid: ""``. A lost tenant context therefore became a hard
error instead of a query that simply matches no rows.

Production hit exactly this on 2026-09-11: an OAuth token refresh committed in
the middle of a cloud sync, and every later write to ``cloud_metadata_index``
raised. The caller is fixed separately; this makes the policies themselves
robust, using the ``NULLIF(..., '')::uuid`` form migrations 076 and 162 already
use.

Access is unchanged. A matching tenant still matches, and an empty or absent
setting still yields no rows and rejects writes. Only the failure mode moves
from an exception to failing closed.

Each policy is altered in place, so its command, roles and permissive mode are
untouched. The upgrade rewrites a policy only when its live expression is
exactly the known unsafe form, skips one that is already normalized, and
refuses to guess about anything else. The list matches both dev1 and
production as of 2026-09-11.

Revision ID: 173_rls_tenant_guc_nullif
Revises: 172_case_continuance
"""

import sqlalchemy as sa
from alembic import op


revision = "173_rls_tenant_guc_nullif"
down_revision = "172_case_continuance"
branch_labels = None
depends_on = None

CURRENT = "app.current_tenant_id"
LEGACY = "app.tenant_id"

# (table, policy, tenant setting, policy has a WITH CHECK clause)
POLICIES = (
    ("api_access_logs", "api_access_logs_tenant_isolation", CURRENT, True),
    (
        "child_support_calculations",
        "child_support_calculations_tenant_isolation",
        CURRENT,
        False,
    ),
    ("cloud_metadata_index", "tenant_isolation_cloud_metadata", CURRENT, False),
    ("custody_arrangements", "custody_arrangements_tenant_isolation", CURRENT, False),
    (
        "document_template_previews",
        "document_template_previews_tenant_isolation",
        LEGACY,
        True,
    ),
    ("document_templates", "tenant_isolation_document_templates", CURRENT, False),
    ("domestic_cases", "domestic_cases_tenant_isolation", CURRENT, False),
    ("domestic_children", "domestic_children_tenant_isolation", CURRENT, False),
    ("domestic_deadlines", "domestic_deadlines_tenant_isolation", CURRENT, False),
    ("domestic_events", "domestic_events_tenant_isolation", CURRENT, False),
    ("domestic_parties", "domestic_parties_tenant_isolation", CURRENT, False),
    (
        "estate_accounting_entries",
        "estate_accounting_entries_tenant_isolation",
        CURRENT,
        False,
    ),
    ("estate_assets", "estate_assets_tenant_isolation", CURRENT, False),
    ("estate_beneficiaries", "estate_beneficiaries_tenant_isolation", CURRENT, False),
    ("estate_deadlines", "estate_deadlines_tenant_isolation", CURRENT, False),
    ("estate_distributions", "estate_distributions_tenant_isolation", CURRENT, False),
    ("estate_fiduciaries", "estate_fiduciaries_tenant_isolation", CURRENT, False),
    ("estate_liabilities", "estate_liabilities_tenant_isolation", CURRENT, False),
    ("external_import_runs", "external_import_runs_tenant_isolation", CURRENT, True),
    ("external_raw_rows", "external_raw_rows_tenant_isolation", CURRENT, True),
    ("external_record_links", "external_record_links_tenant_isolation", CURRENT, True),
    (
        "external_system_connections",
        "external_system_connections_tenant_isolation",
        CURRENT,
        True,
    ),
    ("legacy_call_records", "legacy_call_records_tenant_isolation", CURRENT, True),
    ("matter_assignments", "tenant_isolation_matter_assignments", CURRENT, False),
    ("matter_documents", "matter_documents_tenant_isolation", CURRENT, True),
    ("matter_notes", "tenant_isolation_matter_notes", CURRENT, False),
    ("matter_parties", "matter_parties_tenant_isolation", CURRENT, True),
    ("office_action_runs", "office_action_runs_tenant_isolation", LEGACY, True),
    (
        "partner_assignment_log",
        "partner_assignment_log_tenant_isolation",
        CURRENT,
        True,
    ),
    (
        "partner_rotation_state",
        "partner_rotation_state_tenant_isolation",
        CURRENT,
        True,
    ),
    ("plan_upgrade_requests", "plan_upgrade_requests_tenant_isolation", CURRENT, True),
    ("prompt_overrides", "tenant_isolation_prompt_overrides", CURRENT, False),
    ("retainer_transactions", "tenant_isolation_retainer_transactions", CURRENT, False),
    ("retainers", "tenant_isolation_retainers", CURRENT, False),
    ("roles", "roles_tenant_isolation", CURRENT, True),
    ("support_orders", "support_orders_tenant_isolation", CURRENT, False),
    ("support_payments", "support_payments_tenant_isolation", CURRENT, False),
    ("tenant_oauth_apps", "tenant_oauth_apps_tenant_isolation", CURRENT, True),
    ("user_roles", "user_roles_tenant_isolation", CURRENT, True),
)


def _rendered_unsafe(setting: str) -> str:
    """The unsafe predicate exactly as PostgreSQL deparses it in pg_policies."""
    return f"(tenant_id = (current_setting('{setting}'::text, true))::uuid)"


def _predicate(setting: str, *, safe: bool) -> str:
    if safe:
        return f"tenant_id = NULLIF(current_setting('{setting}', true), '')::uuid"
    return f"tenant_id = current_setting('{setting}', true)::uuid"


def _live_expressions(table: str, policy: str) -> tuple[str, str | None]:
    row = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT qual, with_check FROM pg_policies "
                "WHERE schemaname = 'public' "
                "AND tablename = :table AND policyname = :policy"
            ),
            {"table": table, "policy": policy},
        )
        .first()
    )
    if row is None:
        raise RuntimeError(f"RLS policy {policy} on {table} is missing")
    return row[0], row[1]


def _alter(
    table: str, policy: str, setting: str, has_check: bool, *, safe: bool
) -> None:
    predicate = _predicate(setting, safe=safe)
    clause = f"USING ({predicate})"
    if has_check:
        clause += f" WITH CHECK ({predicate})"
    op.execute(f"ALTER POLICY {policy} ON {table} {clause}")


def upgrade():
    unsafe_by_setting = {
        setting: _rendered_unsafe(setting) for setting in (CURRENT, LEGACY)
    }
    for table, policy, setting, has_check in POLICIES:
        qual, check = _live_expressions(table, policy)
        live = [qual, check] if has_check else [qual]
        if all(expression and "NULLIF" in expression for expression in live):
            continue
        unsafe = unsafe_by_setting[setting]
        expected_check = unsafe if has_check else None
        if qual != unsafe or check != expected_check:
            raise RuntimeError(
                f"RLS policy {policy} on {table} has an unexpected expression; "
                f"review it before normalizing: USING {qual!r} WITH CHECK {check!r}"
            )
        _alter(table, policy, setting, has_check, safe=True)


def downgrade():
    for table, policy, setting, has_check in POLICIES:
        _alter(table, policy, setting, has_check, safe=False)
