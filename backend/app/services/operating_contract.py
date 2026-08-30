"""Versioned, public-safe source of truth for LawHand operating trust.

The registry distinguishes an implemented workflow from the state of an audit,
provider agreement, certification, or customer-specific contractual promise.
No item in this module is evidence that a provider feature or external
attestation has been obtained.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

CONTRACT_VERSION = "2026-08-29.1"
SUPPORT_POLICY_VERSION = "2026-08-29.1"
SUBPROCESSOR_REGISTRY_VERSION = "2026-08-29.1"
ASSURANCE_ROADMAP_VERSION = "2026-08-29.1"


_SERVICE_OBJECTIVES: dict[str, Any] = {
    "version": CONTRACT_VERSION,
    "kind": "operational-objectives-not-sla",
    "objectives": [
        {
            "id": "public-health-probe",
            "measure": "Production readiness, version identity, public application, API, and TLS are probed on a ten-minute schedule.",
            "target": "Each scheduled probe completes or produces an operator-visible failure.",
            "evidence_state": "implemented",
        },
        {
            "id": "backup-freshness",
            "measure": "The production health gate checks encrypted off-host database and uploads-backup freshness.",
            "target": "The configured production backup freshness threshold is not exceeded.",
            "evidence_state": "verified-by-production-health-gate",
        },
        {
            "id": "restore-readiness",
            "measure": "An isolated restore rehearsal validates database integrity, tenant access, file sampling, and version evidence.",
            "target": "A current passing rehearsal exists before a release is represented as restore-verified.",
            "evidence_state": "verified-by-rehearsal-gate",
        },
        {
            "id": "incident-updates",
            "measure": "Public incident updates follow the investigating, identified, monitoring, and resolved lifecycle.",
            "target": "Material state changes are published without exposing customer data or exploitable topology details.",
            "evidence_state": "implemented",
        },
    ],
    "exclusions": [
        "No uptime percentage, RPO, RTO, service credit, or damages remedy is promised by these objectives.",
        "Customer-controlled identity, cloud storage, networks, integrations, maintenance, misuse, force majeure, and provider-wide failures are excluded unless written customer terms say otherwise.",
        "A passing probe or backup check is time-bound evidence, not a guarantee of future availability or recoverability.",
    ],
}


_SUPPORT_POLICY: dict[str, Any] = {
    "version": SUPPORT_POLICY_VERSION,
    "coverage": {
        "standard_hours": "Monday-Friday, 08:00-17:00 America/Chicago",
        "exceptions": "No automatic holiday exclusion is claimed; signed customer terms may define additional closures or coverage.",
        "after_hours": "S1 reports use the emergency channel identified in the customer order form; other requests enter the next covered period.",
    },
    "objective_boundary": "Acknowledgement and escalation targets are operating objectives, not an SLA, warranty, or service-credit promise unless incorporated into signed customer terms.",
    "severities": [
        {
            "severity": "S1",
            "definition": "Confirmed or credibly suspected confidentiality breach, destructive data-integrity event, or production-wide unavailability with no safe workaround.",
            "acknowledgement_objective_minutes": 60,
            "initial_owner": "incident commander",
            "escalation": "Route immediately to operations; include security lead for confidentiality or integrity impact; escalate to executive owner if not acknowledged in 30 minutes.",
        },
        {
            "severity": "S2",
            "definition": "Material production degradation or blocked critical workflow affecting multiple authorized users with no reasonable workaround.",
            "acknowledgement_objective_minutes": 240,
            "initial_owner": "support lead",
            "escalation": "Escalate to operations if not acknowledged within two covered hours or if impact broadens.",
        },
        {
            "severity": "S3",
            "definition": "Non-critical defect with a workaround, isolated integration problem, or question requiring investigation.",
            "acknowledgement_objective_minutes": 480,
            "initial_owner": "support",
            "escalation": "Escalate to support lead when the acknowledgement objective is missed.",
        },
        {
            "severity": "S4",
            "definition": "How-to request, cosmetic issue, or non-urgent enhancement feedback.",
            "acknowledgement_objective_minutes": 960,
            "initial_owner": "support",
            "escalation": "Review in the normal support queue and escalate when the acknowledgement objective is missed.",
        },
    ],
    "workflow": [
        "Record a tenant-scoped request without secrets or unnecessary customer content.",
        "Classify severity and snapshot this policy version and acknowledgement clock.",
        "Acknowledge, escalate, mitigate, and resolve through the audited state machine.",
        "Open a sanitized public incident for material shared-service impact; keep tenant-specific details out of public updates.",
        "Record a resolution summary and any follow-up separately from unsupported root-cause speculation.",
    ],
}


def _provider(name: str, purpose: str, data: list[str], use: str) -> dict[str, Any]:
    return {
        "name": name,
        "purpose": purpose,
        "data_categories": data,
        "region": "Customer, provider, and selected deployment configuration.",
        "use_status": use,
        "terms_state": "contract-specific-review-required",
        "dpa_state": "customer-configuration-review-required",
        "baa_state": "not-asserted-by-this-registry",
    }


_SUBPROCESSORS: tuple[dict[str, Any], ...] = (
    _provider("IONOS", "Production compute, network, and backup infrastructure for the supported hosted topology.", ["encrypted application data", "service metadata", "backup data"], "production-topology-provider"),
    _provider("Cloudflare", "Optional edge, DNS, and protected connectivity services in an approved deployment configuration.", ["network metadata", "encrypted transit data"], "deployment-dependent"),
    _provider("Microsoft", "Customer-authorized identity, Microsoft 365 storage, email, and Teams integrations.", ["identity metadata", "customer-selected files", "communications metadata"], "customer-enabled-only"),
    _provider("Google", "Customer-authorized identity, Workspace storage, and optional model services.", ["identity metadata", "customer-selected files", "authorized prompt context"], "customer-enabled-only"),
    _provider("OpenAI", "Optional model inference selected through tenant model-provider configuration.", ["minimum authorized prompt context"], "customer-enabled-only"),
    _provider("Anthropic", "Optional model inference selected through tenant model-provider configuration.", ["minimum authorized prompt context"], "customer-enabled-only"),
    _provider("Stripe", "Subscription and payment processing.", ["billing contact", "subscription metadata", "payment-provider identifiers"], "billing-path-provider"),
    _provider("Dropbox Sign", "Optional customer-authorized electronic signature workflow.", ["signer identity", "customer-selected documents", "signature evidence"], "customer-enabled-only"),
    _provider("Zoom", "Optional customer-authorized phone and meeting integrations.", ["communications metadata", "customer-authorized call content"], "customer-enabled-only"),
    _provider("Twilio", "Optional communications delivery where configured.", ["contact routing data", "message or call metadata"], "configuration-dependent"),
)


_ASSURANCE_PROGRAM: dict[str, Any] = {
    "version": ASSURANCE_ROADMAP_VERSION,
    "dpa_baa": {
        "dpa_applicability": "A DPA is evaluated before LawHand processes personal data for a customer as a processor; applicability, parties, transfer terms, and selected providers must be recorded in signed customer terms.",
        "baa_applicability": "A BAA is evaluated only when a customer proposes regulated protected health information use and every required service/provider is eligible. No BAA or HIPAA-ready deployment is represented as currently available by this registry.",
        "evidence_state": "customer-contract-dependent",
    },
    "penetration_testing": {
        "cadence": "Target annual external application and infrastructure penetration test, plus material-scope-change review.",
        "owner_role": "Security lead",
        "next_review_window": "Not scheduled",
        "latest_completed_evidence": None,
        "evidence_state": "planned-not-attained",
        "claim_boundary": "No completed external penetration test or attestation is claimed until a dated, scoped report is entered into the evidence record.",
    },
    "certification_roadmap": [
        {"framework": "SOC 2 Type I", "state": "planned", "next_gate": "Approve scope, control owners, and independent assessor.", "target_date": None, "attained": False},
        {"framework": "SOC 2 Type II", "state": "unavailable-until-type-i-and-observation-period", "next_gate": "Complete prerequisite scope and evidence period.", "target_date": None, "attained": False},
        {"framework": "ISO 27001", "state": "not-scheduled", "next_gate": "Business decision and formal ISMS scope.", "target_date": None, "attained": False},
        {"framework": "HIPAA / BAA-supported offering", "state": "not-available", "next_gate": "Counsel review, provider eligibility, control mapping, and signed BAAs.", "target_date": None, "attained": False},
    ],
}


_CONTROLS: tuple[dict[str, Any], ...] = (
    {"id": "topology", "title": "Supported production topology", "status": "verified", "claim": "A version-identified single hosted application deployment with encrypted off-host backups, customer-authorized cloud storage, and a controlled research gateway is supported.", "boundary": "Multi-region, active-active, and provider-specific availability are not promised.", "evidence": ["production-topology-runbook", "release-version-identity"]},
    {"id": "service-objectives", "title": "Service objectives", "status": "implemented", "claim": "A versioned set of measurable production health, backup freshness, restore readiness, and incident communication objectives is published.", "boundary": "Objectives are not an uptime SLA, RPO/RTO warranty, damages remedy, or service-credit commitment.", "evidence": ["service-objectives-snapshot", "production-health-gate", "restore-rehearsal-gate"]},
    {"id": "support", "title": "Support and escalation", "status": "implemented", "claim": "Support hours, S1-S4 definitions, acknowledgement objectives, ownership, and escalation paths are versioned and bound to an audited request workflow.", "boundary": "Targets are operating objectives unless signed customer terms expressly incorporate them.", "evidence": ["support-policy-snapshot", "tenant-support-workflow"]},
    {"id": "status-incidents", "title": "Status and incident communication", "status": "implemented", "claim": "Operators can publish sanitized incidents through an append-only investigating, identified, monitoring, and resolved lifecycle exposed by the public status API.", "boundary": "Updates contain confirmed public-safe facts and may be incomplete during investigation; no incident-free history is asserted.", "evidence": ["public-status-api", "immutable-incident-update-ledger"]},
    {"id": "backup-restore", "title": "Backup and restore", "status": "verified", "claim": "Encrypted off-host backups and isolated restore rehearsals are enforced by production checks and runbooks.", "boundary": "Evidence is time-bound and does not guarantee recovery from every provider, operator, or customer-cloud failure.", "evidence": ["backup-restore-runbook", "restore-rehearsal-gate"]},
    {"id": "tenant-export", "title": "Tenant export", "status": "implemented", "claim": "Tenant administrators can inventory every current tenant-id database table and recorded file provider, account for each category through a product export or bounded evidence summary, and bind the resulting artifact by SHA-256.", "boundary": "Secret values are never exported; connected-provider availability, immutable evidence retention, and artifact format remain explicit recorded limitations.", "evidence": ["tenant-export-reconciliation", "immutable-lifecycle-receipt"]},
    {"id": "onboarding-migration", "title": "Onboarding and migration acceptance receipt", "status": "implemented", "claim": "An authorized tenant administrator can sign immutable onboarding and BK28 migration receipts with scope, source manifest counts, reconciliation, artifact evidence, and outcome.", "boundary": "A receipt accepts only its recorded scope and does not cure source-system omissions or warnings.", "evidence": ["agreement-acceptance-ledger", "bk28-import-run", "immutable-lifecycle-receipt"]},
    {"id": "offboarding-deletion", "title": "Offboarding and deletion", "status": "implemented", "claim": "A non-destructive offboarding workflow snapshots inventory and legal holds, requires two distinct operator approvals, records provider and backup disposition, and emits immutable proof.", "boundary": "The workflow never deletes tenant data itself; deletion execution requires separately authorized provider and data-store actions and is blocked while a legal hold is active.", "evidence": ["offboarding-approval-workflow", "legal-hold-gate", "immutable-deletion-receipt"]},
    {"id": "privacy-terms", "title": "DPA and BAA applicability", "status": "policy-committed", "claim": "The security packet states how DPA and BAA applicability must be evaluated and recorded before applicable processing.", "boundary": "No universal DPA, BAA, HIPAA-ready configuration, or legal conclusion is asserted.", "evidence": ["assurance-program-snapshot"]},
    {"id": "subprocessors", "title": "Subprocessors", "status": "implemented", "claim": "A named, versioned registry records purpose, data categories, region boundary, use status, terms state, and DPA/BAA evidence state.", "boundary": "Configuration-dependent entries are not represented as active, and the registry does not substitute for current provider contracts.", "evidence": ["named-subprocessor-registry"]},
    {"id": "security-review", "title": "Security-review packet", "status": "implemented", "claim": "A downloadable, content-addressed packet exports the operating contract, objectives, support policy, subprocessors, assurance state, and sanitized evidence catalogue.", "boundary": "The packet is a point-in-time first-party evidence snapshot, not a certification or independent audit opinion.", "evidence": ["public-security-review-packet"]},
    {"id": "penetration-testing", "title": "Penetration testing", "status": "planned", "claim": "The assurance roadmap records an annual target cadence, owner role, scheduling state, and completed-evidence field.", "boundary": "The current evidence state is planned-not-attained and no completed external test is claimed.", "evidence": ["assurance-program-snapshot"]},
    {"id": "certification-roadmap", "title": "Certification roadmap", "status": "planned", "claim": "A versioned roadmap records explicit next gates and attainment flags for SOC 2, ISO 27001, and a BAA-supported offering.", "boundary": "No certification, audit report, HIPAA compliance, or target completion date is claimed.", "evidence": ["assurance-program-snapshot"]},
)


def service_objectives() -> dict[str, Any]:
    return deepcopy(_SERVICE_OBJECTIVES)


def support_policy() -> dict[str, Any]:
    return deepcopy(_SUPPORT_POLICY)


def support_severity(severity: str) -> dict[str, Any]:
    normalized = severity.upper()
    for item in _SUPPORT_POLICY["severities"]:
        if item["severity"] == normalized:
            return deepcopy(item)
    raise ValueError("unsupported support severity")


def subprocessor_registry() -> dict[str, Any]:
    return {"schema": "lawhand.subprocessors", "version": SUBPROCESSOR_REGISTRY_VERSION, "entries": deepcopy(list(_SUBPROCESSORS)), "boundary": "Use status describes supported configuration paths, not proof that a provider is active for a particular tenant."}


def assurance_program() -> dict[str, Any]:
    return deepcopy(_ASSURANCE_PROGRAM)


def operating_contract() -> dict[str, Any]:
    return {"schema": "lawhand.operating-contract", "version": CONTRACT_VERSION, "claim_statuses": ["implemented", "verified", "policy-committed", "provider-dependent", "planned", "unavailable"], "controls": deepcopy(list(_CONTROLS)), "truth_rule": "Only implemented and verified controls are product capabilities; policy, provider, planned, and unavailable states are not attainment claims."}


def security_review_packet() -> dict[str, Any]:
    """Build a deterministic, public-safe, content-addressed evidence packet."""
    content = {
        "schema": "lawhand.security-review-packet",
        "version": CONTRACT_VERSION,
        "operating_contract": operating_contract(),
        "service_objectives": service_objectives(),
        "support_policy": support_policy(),
        "subprocessors": subprocessor_registry(),
        "assurance_program": assurance_program(),
        "evidence_catalogue": [
            {"id": "release-version-identity", "state": "implemented", "description": "Runtime metadata binds version and commit identity."},
            {"id": "tenant-isolation", "state": "verified-by-security-gates", "description": "Tenant data tables use forced row-level security and least-privilege runtime checks."},
            {"id": "migration-safety", "state": "verified-by-release-gate", "description": "Schema history and migration safety are checked before release."},
            {"id": "backup-and-restore", "state": "verified-by-production-gates", "description": "Backup freshness and isolated restore rehearsal evidence are release inputs."},
            {"id": "customer-lifecycle", "state": "implemented", "description": "Append-only receipts bind onboarding, import reconciliation, exports, and offboarding evidence."},
        ],
        "packet_boundary": "This first-party snapshot contains no secrets, customer data, internal hostnames, exploitable topology details, audit opinion, or certification claim.",
    }
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    content["sha256"] = hashlib.sha256(canonical).hexdigest()
    return content


def validate_operating_contract(contract: dict[str, Any] | None = None) -> list[str]:
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
    for item in assurance_program()["certification_roadmap"]:
        if item["attained"] and item["state"] not in {"implemented", "verified"}:
            errors.append(f"false certification attainment: {item['framework']}")
    return errors
