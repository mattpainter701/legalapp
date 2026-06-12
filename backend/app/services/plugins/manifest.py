"""Canonical plugin catalog metadata.

This is the product-facing plugin contract. Prompt templates define how an LLM
skill behaves; this manifest defines what the add-on is, how it maps to matters,
which integrations it can use, and where its workspace lives.
"""

from dataclasses import dataclass, field

from app.services.plugins.prompts import PLUGIN_DISPLAY_NAMES, PLUGIN_SKILLS


@dataclass(frozen=True)
class PluginManifest:
    plugin_name: str
    display_name: str
    category: str
    description: str
    skills: list[str]
    matter_types: list[str] = field(default_factory=list)
    primary_route: str | None = None
    required_integrations: list[str] = field(default_factory=list)
    optional_integrations: list[str] = field(default_factory=list)
    setup_required: bool = True
    supports_matter_assignment: bool = True
    default_entitlement_status: str = "available"


_METADATA: dict[str, dict] = {
    "commercial-legal": {
        "category": "Transactions",
        "description": "Contract review, NDA triage, SaaS agreement analysis, renewal tracking, and commercial playbook enforcement.",
        "matter_types": [
            "commercial",
            "contract",
            "vendor",
            "saas",
            "nda",
            "procurement",
        ],
        "primary_route": "/plugins/commercial/renewals",
        "optional_integrations": [
            "google_drive",
            "onedrive",
            "sharepoint",
            "gmail",
            "outlook",
        ],
    },
    "privacy-legal": {
        "category": "Compliance",
        "description": "DPA review, DSAR responses, privacy impact assessments, and policy monitoring.",
        "matter_types": ["privacy", "data protection", "dsar", "dpa", "compliance"],
        "optional_integrations": ["google_drive", "onedrive", "gmail", "outlook"],
    },
    "litigation-legal": {
        "category": "Disputes",
        "description": "Litigation intake, demand response, legal holds, claim charts, chronology, and portfolio status workflows.",
        "matter_types": [
            "litigation",
            "dispute",
            "claim",
            "lawsuit",
            "demand",
            "subpoena",
        ],
        "primary_route": "/plugins/litigation/matters",
        "optional_integrations": [
            "google_drive",
            "onedrive",
            "sharepoint",
            "gmail",
            "outlook",
            "calendar",
        ],
    },
    "corporate-legal": {
        "category": "Transactions",
        "description": "M&A diligence, entity compliance, board minutes, written consents, and closing checklist workflows.",
        "matter_types": [
            "corporate",
            "m&a",
            "merger",
            "acquisition",
            "entity",
            "governance",
        ],
        "optional_integrations": ["google_drive", "onedrive", "sharepoint"],
    },
    "employment-legal": {
        "category": "People",
        "description": "Termination, classification, hiring, investigation, handbook, wage-hour, and expansion reviews.",
        "matter_types": [
            "employment",
            "labor",
            "termination",
            "classification",
            "investigation",
            "wage-hour",
        ],
        "optional_integrations": ["google_drive", "onedrive", "gmail", "outlook"],
    },
    "product-legal": {
        "category": "Product",
        "description": "Product launch reviews, marketing claims checks, regulatory triage, and feature risk analysis.",
        "matter_types": [
            "product",
            "launch",
            "marketing",
            "claims",
            "feature",
            "regulatory",
        ],
        "optional_integrations": ["google_drive", "onedrive", "sharepoint"],
    },
    "ip-legal": {
        "category": "Intellectual Property",
        "description": "Trademark clearance, FTO, takedowns, open source review, infringement triage, and IP portfolio workflows.",
        "matter_types": [
            "ip",
            "intellectual property",
            "trademark",
            "patent",
            "copyright",
            "open source",
        ],
        "optional_integrations": [
            "google_drive",
            "onedrive",
            "sharepoint",
            "gmail",
            "outlook",
        ],
    },
    "ai-governance-legal": {
        "category": "Compliance",
        "description": "AI use-case triage, vendor AI review, AI inventory, impact assessment, and policy starter workflows.",
        "matter_types": [
            "ai",
            "ai governance",
            "model",
            "vendor ai",
            "impact assessment",
        ],
        "optional_integrations": ["google_drive", "onedrive", "sharepoint"],
    },
    "regulatory-legal": {
        "category": "Compliance",
        "description": "Regulatory gap analysis, policy diffing, comment drafting, monitoring, and feed watcher workflows.",
        "matter_types": ["regulatory", "compliance", "policy", "nprm", "agency"],
        "optional_integrations": ["google_drive", "onedrive", "sharepoint", "calendar"],
    },
    "family-law": {
        "category": "Consumer Practice",
        "description": "Domestic relations workspace: case tracking, parties, children, custody, a jurisdiction-aware child support calculator with reproducible worksheets, support orders, and a payment ledger.",
        "matter_types": ["family", "divorce", "custody", "support", "protective order"],
        "primary_route": "/plugins/domestic/cases",
        "optional_integrations": [
            "google_drive",
            "onedrive",
            "gmail",
            "outlook",
            "calendar",
        ],
    },
    "criminal-defense": {
        "category": "Consumer Practice",
        "description": "Case assessment, discovery review, and motion drafting for criminal defense matters.",
        "matter_types": ["criminal", "criminal defense", "discovery", "motion"],
        "optional_integrations": [
            "google_drive",
            "onedrive",
            "gmail",
            "outlook",
            "calendar",
        ],
    },
    "real-estate": {
        "category": "Property",
        "description": "Lease review, purchase agreement review, title review, and closing workflows.",
        "matter_types": ["real estate", "lease", "purchase", "title", "property"],
        "optional_integrations": ["google_drive", "onedrive", "sharepoint"],
    },
    "trust-estate-legal": {
        "category": "Consumer Practice",
        "description": "Will and trust review, probate checklists, beneficiary letters, estate tax worksheets, and fiduciary accounting.",
        "matter_types": [
            "trust",
            "estate",
            "probate",
            "will",
            "beneficiary",
            "fiduciary",
        ],
        "primary_route": "/plugins/trust-estate/estates",
        "optional_integrations": [
            "google_drive",
            "onedrive",
            "gmail",
            "outlook",
            "calendar",
        ],
    },
    "mediation-legal": {
        "category": "Disputes",
        "description": "Mediation case tracking, party portal, asset schedules, document exchange, proposal negotiation, and session summaries.",
        "skills": [
            "mediation-intake",
            "mediation-brief",
            "settlement-agreement",
            "caucus-summary",
        ],
        "matter_types": [
            "mediation",
            "settlement",
            "dispute resolution",
            "family mediation",
        ],
        "primary_route": "/plugins/mediation/cases",
        "optional_integrations": [
            "google_drive",
            "onedrive",
            "gmail",
            "outlook",
            "calendar",
        ],
    },
}


def list_plugin_manifests() -> list[PluginManifest]:
    names = set(PLUGIN_DISPLAY_NAMES) | set(_METADATA)
    manifests = []
    for plugin_name in sorted(names):
        meta = _METADATA.get(plugin_name, {})
        display_name = PLUGIN_DISPLAY_NAMES.get(
            plugin_name, meta.get("display_name", plugin_name.replace("-", " ").title())
        )
        skills = meta.get("skills", PLUGIN_SKILLS.get(plugin_name, []))
        manifests.append(
            PluginManifest(
                plugin_name=plugin_name,
                display_name=display_name,
                category=meta.get("category", "Practice Area"),
                description=meta.get("description", f"{display_name} workflow add-on."),
                skills=skills,
                matter_types=meta.get("matter_types", []),
                primary_route=meta.get("primary_route"),
                required_integrations=meta.get("required_integrations", []),
                optional_integrations=meta.get("optional_integrations", []),
                setup_required=meta.get("setup_required", True),
                supports_matter_assignment=meta.get("supports_matter_assignment", True),
                default_entitlement_status=meta.get(
                    "default_entitlement_status", "available"
                ),
            )
        )
    return manifests


def get_plugin_manifest(plugin_name: str) -> PluginManifest | None:
    for manifest in list_plugin_manifests():
        if manifest.plugin_name == plugin_name:
            return manifest
    return None


def valid_plugin_names() -> set[str]:
    return {manifest.plugin_name for manifest in list_plugin_manifests()}


def suggest_plugin_for_matter(
    practice_area: str | None = None,
    matter_type: str | None = None,
) -> str | None:
    haystack = " ".join(
        item.lower() for item in (practice_area, matter_type) if item
    ).strip()
    if not haystack:
        return None

    for manifest in list_plugin_manifests():
        for term in manifest.matter_types:
            if term and term.lower() in haystack:
                return manifest.plugin_name
    return None
