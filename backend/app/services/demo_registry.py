"""Authoritative clone/purge decisions for tenant-scoped demo data.

Cloning is intentionally a subset of purging. Any new model table carrying a
``tenant_id`` column must be classified here before the metadata coverage test
will pass.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DemoTablePolicy:
    table: str
    clone: bool
    purge: bool = True


# Synthetic business content needed for the guided demo. Implementations must
# still remap every PK/FK/embedded identifier and run table-specific file hooks.
_CLONE_TABLES = {
    "child_support_calculations",
    "chat_artifacts",
    "chunks",
    "communication_logs",
    "contacts",
    "conversations",
    "custody_arrangements",
    "document_templates",
    "documents",
    "domestic_cases",
    "domestic_children",
    "domestic_deadlines",
    "domestic_events",
    "domestic_parties",
    "estate_accounting_entries",
    "estate_assets",
    "estate_beneficiaries",
    "estate_deadlines",
    "estate_distributions",
    "estate_fiduciaries",
    "estate_liabilities",
    "estates",
    "expenses",
    "invoices",
    "leads",
    "matter_assignments",
    "matter_document_revisions",
    "matter_documents",
    "matter_events",
    "matter_notes",
    "matter_parties",
    "matters",
    "mediation_assets",
    "mediation_cases",
    "mediation_documents",
    "mediation_parties",
    "mediation_proposals",
    "messages",
    "plugin_skill_runs",
    "payments",
    "practice_profiles",
    "prompt_overrides",
    "renewals",
    "retainer_transactions",
    "retainers",
    "scheduled_events",
    "support_orders",
    "support_payments",
    "task_events",
    "tasks",
    "time_entries",
    "trust_accounts",
    "trust_bank_accounts",
    "trust_reconciliations",
    "trust_transactions",
}


# Purge-only tables contain runtime state, identity, credentials, integrations,
# ephemeral work, or audit evidence. A demo may create rows in these tables even
# though fixture rows must never be copied into a disposable tenant.
_PURGE_ONLY_TABLES = {
    "api_access_logs",
    "background_ai_usage_reservations",
    "client_portal_invites",
    "cloud_metadata_index",
    "conflict_checks",
    "demo_sessions",
    "demo_usage_reservations",
    "document_integrity_events",
    "document_storage_operations",
    "document_template_previews",
    "durable_jobs",
    "engagement_packets",
    "error_logs",
    "external_import_runs",
    "external_raw_rows",
    "external_record_links",
    "external_system_connections",
    "generated_artifact_revisions",
    "generated_artifacts",
    "inbound_email_aliases",
    "inbound_emails",
    "intake_call_drafts",
    "intake_forms",
    "intake_submissions",
    "integration_sync_runs",
    "legacy_call_records",
    "lead_appointments",
    "lead_channel_consents",
    "lead_funnel_events",
    "matter_smb_shares",
    "mcp_product_keys",
    "mcp_usage_events",
    "mediation_invites",
    "office_action_runs",
    "offboarding_approvals",
    "offboarding_cases",
    "partner_assignment_log",
    "partner_rotation_state",
    "plan_upgrade_requests",
    "portal_invoice_downloads",
    "prospect_contact_events",
    "prospect_follow_through",
    "prospect_follow_through_events",
    "qbo_integrations",
    "qbo_item_mappings",
    "retention_actions",
    "retention_policies",
    "customer_lifecycle_receipts",
    "roles",
    "scheduler_logs",
    "support_requests",
    "signature_requests",
    "signature_signers",
    "smb_access_log",
    "smb_agents",
    "smb_credentials",
    "smb_file_index",
    "smb_shares",
    "task_automation_runs",
    "teams_channel_links",
    "teams_notification_settings",
    "teams_voice_settings",
    "tenant_agreement_acceptances",
    "tenant_credentials",
    "tenant_oauth_apps",
    "tenant_plugin_entitlements",
    "tenant_settings",
    "tenant_plugin_setups",
    "usage_records",
    "user_memories",
    "user_oauth_tokens",
    "user_roles",
    "users",
    "workspace_mcp_audit_events",
    "workspace_mcp_grants",
}


_OVERLAP = _CLONE_TABLES & _PURGE_ONLY_TABLES
if _OVERLAP:
    raise RuntimeError(f"Demo tables have conflicting policies: {sorted(_OVERLAP)}")


DEMO_TABLE_REGISTRY: dict[str, DemoTablePolicy] = {
    table: DemoTablePolicy(table=table, clone=True) for table in sorted(_CLONE_TABLES)
} | {
    table: DemoTablePolicy(table=table, clone=False)
    for table in sorted(_PURGE_ONLY_TABLES)
}


SENSITIVE_NEVER_CLONE = frozenset(
    {
        "external_system_connections",
        "inbound_email_aliases",
        "inbound_emails",
        "mcp_product_keys",
        "smb_agents",
        "smb_credentials",
        "smb_shares",
        "teams_channel_links",
        # Carries the Entra directory binding and the notification clientState
        # secret — never cloned into a demo tenant.
        "teams_voice_settings",
        "tenant_credentials",
        "tenant_oauth_apps",
        "tenant_plugin_setups",
        "user_oauth_tokens",
    }
)
