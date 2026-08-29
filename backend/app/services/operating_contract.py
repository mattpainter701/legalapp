"""The customer operating contract and its evidence boundaries.

This is deliberately a small, versioned registry rather than prose duplicated
across marketing, runbooks, and support responses.  ``status`` is a claim
boundary: only ``implemented`` and ``verified`` may be described as available.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

# The registry is intentionally one control per literal record so reviewers can
# compare each customer claim with its boundary and evidence in one place.
# fmt: off
CONTRACT_VERSION = "2026-08-28.1"

# Public-safe evidence paths.  Do not add secrets, hostnames, customer data, or
# operator-only procedures to this registry.
_CONTROLS: tuple[dict[str, Any], ...] = (
    {
        "id": "topology",
        "title": "Supported production topology",
        "status": "verified",
        "claim": "Single hosted application deployment with customer-authorized cloud storage and a controlled research gateway is supported.",
        "boundary": "Multi-region, active-active, and provider-specific availability are not promised.",
        "evidence": ["docs/FIRST_CUSTOMER_PRODUCTION_RUNBOOK.md", "backend/app/main.py:/api/version"],
    },
    {
        "id": "service-objectives",
        "title": "Service objectives",
        "status": "policy-committed",
        "claim": "We measure release health, API version identity, backup freshness, and restore readiness.",
        "boundary": "These are operational objectives, not an uptime SLA, RPO/RTO warranty, or service credit commitment.",
        "evidence": ["docs/FIRST_CUSTOMER_PRODUCTION_RUNBOOK.md", ".github/workflows/production-health.yml"],
    },
    {
        "id": "support",
        "title": "Support and escalation",
        "status": "policy-committed",
        "claim": "Support is handled through the designated customer support channel during agreed business hours; security and data-integrity reports are escalated immediately.",
        "boundary": "Exact hours, response targets, and severity commitments are order-form terms, not a universal SLA.",
        "evidence": ["docs/FIRST_CUSTOMER_PRODUCTION_RUNBOOK.md"],
    },
    {
        "id": "status-incidents",
        "title": "Status and incident communication",
        "status": "implemented",
        "claim": "Operational incidents are communicated with impact, current state, mitigation, and resolution follow-up; public health checks expose only sanitized readiness.",
        "boundary": "Incident updates are factual and may be incomplete while investigation continues; no incident-free claim is made.",
        "evidence": [".github/workflows/production-health.yml", "docs/GITHUB_DEPLOY_RUNNER.md"],
    },
    {
        "id": "backup-restore",
        "title": "Backup and restore",
        "status": "verified",
        "claim": "Encrypted off-host backups and isolated restore rehearsals are required by the production runbook.",
        "boundary": "A backup is not a guarantee against every provider, operator, or customer-cloud failure; restore evidence is time-bound.",
        "evidence": ["docs/BACKUP_DISASTER_RECOVERY.md", "scripts/restore_rehearsal.sh", ".github/workflows/skynet-dr-rehearsal.yml"],
    },
    {
        "id": "tenant-export",
        "title": "Tenant export",
        "status": "provider-dependent",
        "claim": "Tenant-scoped records can be exported through existing product export paths and customer-authorized cloud sources.",
        "boundary": "Completeness depends on connected providers and requested record classes; a universal one-click export is not promised.",
        "evidence": ["backend/app/services/document_export.py", "docs/task-and-customer-data-lifecycle.md"],
    },
    {
        "id": "onboarding-migration",
        "title": "Onboarding and migration acceptance receipt",
        "status": "implemented",
        "claim": "Tenant administrators can record versioned agreement acceptance with signer, authority, timestamp, and document hash evidence; migration safety is checked before release.",
        "boundary": "Customer-specific import scope and acceptance criteria remain part of the written onboarding record.",
        "evidence": ["docs/agreements-retention-operations.md", "scripts/rehearse_tenant_migration.py", "backend/app/routers/compliance.py"],
    },
    {
        "id": "offboarding-deletion",
        "title": "Offboarding and deletion",
        "status": "implemented",
        "claim": "Retention actions are tenant-scoped, audited, legal-hold aware, and fail closed for protected records.",
        "boundary": "Matter records, agreement evidence, and provider-held data are not automatically deleted by the narrow chat-attachment cleanup path.",
        "evidence": ["docs/agreements-retention-operations.md", "backend/app/services/compliance.py"],
    },
    {
        "id": "privacy-terms",
        "title": "DPA and BAA applicability",
        "status": "planned",
        "claim": "Applicability is assessed and documented per customer and processing role before contracting.",
        "boundary": "No universal DPA or BAA coverage is asserted by this product contract.",
        "evidence": ["docs/customer-data-scale-roadmap-2026-08-27.md"],
    },
    {
        "id": "subprocessors",
        "title": "Subprocessors",
        "status": "provider-dependent",
        "claim": "Connected cloud, communications, payment, signing, and model providers are disclosed and reviewed before customer use.",
        "boundary": "Provider availability, terms, region, retention, and DPA status must be confirmed for the selected configuration.",
        "evidence": ["docs/AI_SBOM_DLP_RISK_PLAN.md", "docs/customer-data-scale-roadmap-2026-08-27.md"],
    },
    {
        "id": "security-review",
        "title": "Security-review packet",
        "status": "implemented",
        "claim": "The repository provides version, migration-safety, backup, restore, dependency, and tenant-isolation evidence suitable for a security-review packet.",
        "boundary": "Packet contents are evidence snapshots; they are not a certification or independent audit opinion.",
        "evidence": [".github/workflows/ci.yml", "scripts/generate_sbom_inventory.py", "docs/credential_security_operations.md"],
    },
    {
        "id": "penetration-testing",
        "title": "Penetration testing",
        "status": "planned",
        "claim": "External penetration testing is scheduled as a recurring security program control.",
        "boundary": "No current external penetration-test attestation is claimed here.",
        "evidence": ["docs/AI_SBOM_DLP_RISK_PLAN.md"],
    },
    {
        "id": "certification-roadmap",
        "title": "Certification roadmap",
        "status": "planned",
        "claim": "SOC 2 and related assurance work remain roadmap items subject to scope, budget, and independent assessment.",
        "boundary": "LawHand does not claim SOC 2, ISO 27001, HIPAA, or other certification attainment in this contract.",
        "evidence": ["docs/AI_SBOM_DLP_RISK_PLAN.md"],
    },
)


def operating_contract() -> dict[str, Any]:
    """Return a detached, public-safe snapshot of the operating contract."""

    return {
        "schema": "lawhand.operating-contract",
        "version": CONTRACT_VERSION,
        "claim_statuses": [
            "implemented",
            "verified",
            "policy-committed",
            "provider-dependent",
            "planned",
            "unavailable",
        ],
        "controls": deepcopy(list(_CONTROLS)),
        "truth_rule": "Only implemented and verified controls are product capabilities; policy, provider, planned, and unavailable states are not promises.",
    }


def validate_operating_contract(contract: dict[str, Any] | None = None) -> list[str]:
    """Validate the registry shape and reject misleading claims."""

    value = contract or operating_contract()
    errors: list[str] = []
    allowed = set(value.get("claim_statuses", []))
    if value.get("schema") != "lawhand.operating-contract":
        errors.append("invalid schema")
    if not value.get("version"):
        errors.append("missing version")
    controls = value.get("controls")
    if not isinstance(controls, list) or not controls:
        return errors + ["controls must be non-empty"]
    seen: set[str] = set()
    for control in controls:
        control_id = control.get("id")
        if not control_id or control_id in seen:
            errors.append(f"duplicate or missing control id: {control_id}")
        seen.add(control_id)
        if control.get("status") not in allowed:
            errors.append(f"invalid status for {control_id}")
        if not control.get("claim") or not control.get("boundary"):
            errors.append(f"claim boundary missing for {control_id}")
        if not control.get("evidence"):
            errors.append(f"evidence missing for {control_id}")
    return errors
# fmt: on
