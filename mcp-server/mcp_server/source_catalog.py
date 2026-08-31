"""Machine-readable registry for public legal authority and ingestion tooling.

The catalog is intentionally policy-aware.  A source being public on the web does
not mean it is approved for automated crawling, local mirroring, embedding, or
redistribution.  Validation rejects unsafe combinations before they reach a
scheduler.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .database import connect
from .loader import init_schema

CATALOG_PATH = Path(__file__).with_name("legal_sources.json")
FRAGMENT_DIR = Path(__file__).with_name("source_fragments")

REQUIRED_SOURCE_FIELDS = {
    "source_key",
    "display_name",
    "description",
    "publisher",
    "source_type",
    "canonical_url",
    "authority_tier",
    "official_status",
    "ingestion_mode",
    "storage_policy",
    "access_type",
    "license_status",
    "sync_frequency",
    "data_format",
    "corpus_table",
    "enabled",
    "priority",
    "coverage_kind",
    "practice_areas",
    "implementation_status",
}

AUTHORITY_TIERS = {
    "binding_primary",
    "persuasive_primary",
    "agency_interpretation",
    "agency_guidance",
    "official_form",
    "operational_record",
    "secondary_metadata",
    "example_only",
}
OFFICIAL_STATUSES = {
    "official_authenticated",
    "official",
    "official_unofficial",
    "aggregator",
    "open_source_tool",
}
INGESTION_MODES = {"bulk", "api", "manifest", "manual", "query_time", "prohibited"}
STORAGE_POLICIES = {
    "mirror",
    "normalized_text",
    "metadata_only",
    "query_cache",
    "prohibited",
}
ACCESS_TYPES = {"open", "api_key", "account", "license_token", "blocked_robots"}
LICENSE_STATUSES = {
    "federal_public_domain",
    "public_domain_dedication",
    "permission_granted",
    "terms_review_required",
    "api_terms",
    "membership_terms",
    "restricted",
    "open_source_license",
}
COVERAGE_KINDS = {"complete", "bounded", "query_time_only", "not_applicable"}


class CatalogValidationError(ValueError):
    pass


def load_catalog(
    path: str | Path | None = None,
    *,
    fragments_dir: str | Path | None = None,
) -> dict[str, Any]:
    catalog_path = Path(path) if path else CATALOG_PATH
    with catalog_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    fragment_root = (
        Path(fragments_dir)
        if fragments_dir
        else (FRAGMENT_DIR if path is None else None)
    )
    fragment_files: list[str] = []
    if fragment_root and fragment_root.exists():
        for fragment_path in sorted(fragment_root.glob("*.json")):
            with fragment_path.open("r", encoding="utf-8") as handle:
                fragment = json.load(handle)
            if fragment.get("schema_version") != 1:
                raise CatalogValidationError(
                    f"{fragment_path.name}: fragment schema_version must be 1"
                )
            sources = fragment.get("sources")
            if not isinstance(sources, list) or not sources:
                raise CatalogValidationError(
                    f"{fragment_path.name}: fragment sources must be a non-empty list"
                )
            payload.setdefault("sources", []).extend(sources)
            fragment_files.append(fragment_path.name)
    if fragment_files:
        payload.setdefault("metadata", {})["fragment_files"] = fragment_files
    validate_catalog(payload)
    return payload


def _require_enum(source: dict[str, Any], field: str, allowed: set[str]) -> None:
    value = source.get(field)
    if value not in allowed:
        raise CatalogValidationError(
            f"{source.get('source_key', '<unknown>')}: {field}={value!r} is invalid"
        )


def validate_catalog(catalog: dict[str, Any]) -> None:
    if catalog.get("schema_version") != 1:
        raise CatalogValidationError("catalog schema_version must be 1")
    if not catalog.get("catalog_updated"):
        raise CatalogValidationError("catalog_updated is required")
    sources = catalog.get("sources")
    if not isinstance(sources, list) or not sources:
        raise CatalogValidationError("catalog sources must be a non-empty list")

    keys: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise CatalogValidationError("each source must be an object")
        missing = sorted(REQUIRED_SOURCE_FIELDS - source.keys())
        if missing:
            raise CatalogValidationError(
                f"{source.get('source_key', '<unknown>')}: missing {', '.join(missing)}"
            )
        key = source["source_key"]
        if not isinstance(key, str) or ":" not in key:
            raise CatalogValidationError(f"invalid source_key {key!r}")
        if key in keys:
            raise CatalogValidationError(f"duplicate source_key {key}")
        keys.add(key)

        parsed_url = urlparse(source["canonical_url"])
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise CatalogValidationError(f"{key}: canonical_url must be an https URL")
        if not isinstance(source["enabled"], bool):
            raise CatalogValidationError(f"{key}: enabled must be boolean")
        if not isinstance(source["priority"], int) or source["priority"] < 0:
            raise CatalogValidationError(
                f"{key}: priority must be a non-negative integer"
            )
        if (
            not isinstance(source["practice_areas"], list)
            or not source["practice_areas"]
        ):
            raise CatalogValidationError(
                f"{key}: practice_areas must be a non-empty list"
            )

        _require_enum(source, "authority_tier", AUTHORITY_TIERS)
        _require_enum(source, "official_status", OFFICIAL_STATUSES)
        _require_enum(source, "ingestion_mode", INGESTION_MODES)
        _require_enum(source, "storage_policy", STORAGE_POLICIES)
        _require_enum(source, "access_type", ACCESS_TYPES)
        _require_enum(source, "license_status", LICENSE_STATUSES)
        _require_enum(source, "coverage_kind", COVERAGE_KINDS)

        if source["access_type"] == "blocked_robots" and source["enabled"]:
            raise CatalogValidationError(
                f"{key}: a robots-blocked source cannot be enabled"
            )
        if (
            source["license_status"] in {"terms_review_required", "restricted"}
            and source["enabled"]
        ):
            raise CatalogValidationError(
                f"{key}: source cannot be enabled until licensing/access review is complete"
            )
        if source["license_status"] == "permission_granted" and not source.get(
            "authorization_basis"
        ):
            raise CatalogValidationError(
                f"{key}: permission-granted sources require authorization_basis"
            )
        if (
            source["ingestion_mode"] == "query_time"
            and source["storage_policy"] == "mirror"
        ):
            raise CatalogValidationError(
                f"{key}: query-time sources cannot be mirrored"
            )
        if source["storage_policy"] in {"mirror", "normalized_text"} and source[
            "corpus_table"
        ] in {None, "none"}:
            raise CatalogValidationError(
                f"{key}: stored sources require a corpus_table"
            )
        if source["source_type"].endswith("_tool") and (
            source["enabled"] or source["corpus_table"] != "none"
        ):
            raise CatalogValidationError(
                f"{key}: tooling entries are not ingestible corpora"
            )


def catalog_summary(catalog: dict[str, Any]) -> dict[str, Any]:
    sources = catalog["sources"]
    policy_holds = [
        source
        for source in sources
        if source["access_type"] == "blocked_robots"
        or source["license_status"] in {"terms_review_required", "restricted"}
    ]
    return {
        "schema_version": catalog["schema_version"],
        "catalog_updated": catalog["catalog_updated"],
        "fragment_files": catalog.get("metadata", {}).get("fragment_files", []),
        "source_count": len(sources),
        "enabled_count": sum(bool(source["enabled"]) for source in sources),
        "by_implementation_status": dict(
            sorted(
                Counter(source["implementation_status"] for source in sources).items()
            )
        ),
        "by_ingestion_mode": dict(
            sorted(Counter(source["ingestion_mode"] for source in sources).items())
        ),
        "policy_hold_source_keys": [source["source_key"] for source in policy_holds],
        "documented_retry_count": sum(
            bool(source.get("retry_action")) for source in sources
        ),
    }


def _source_metadata(catalog: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    metadata = {
        "catalog_schema_version": catalog["schema_version"],
        "catalog_updated": catalog["catalog_updated"],
        "practice_areas": source["practice_areas"],
        "implementation_status": source["implementation_status"],
        "notes": source.get("notes"),
        "authorization_basis": source.get("authorization_basis"),
    }
    # Research fragments may add operational fields without requiring a schema
    # migration.  Keep them in the DB so a blocked crawl's reason and retry route
    # survive catalog seeding and remain visible to operators.
    for field in (
        "retry_action",
        "coverage_notes",
        "acquisition_basis",
        "last_policy_review",
        "user_supplied_urls",
    ):
        if field in source:
            metadata[field] = source[field]
    return metadata


def seed_catalog(conn: Any, catalog: dict[str, Any]) -> int:
    """Upsert catalog-owned fields while preserving operational sync counters."""

    sql = """
        INSERT INTO legal_sources (
            source_key, display_name, description, publisher, source_type,
            jurisdiction, canonical_url, authority_tier, official_status,
            ingestion_mode, storage_policy, access_type, license_status,
            terms_url, sync_frequency, data_format, corpus_table, enabled,
            priority, coverage_kind, parser_version, licensing_notes,
            rights_decision, source_tier, geographic_scope, temporal_scope,
            expected_cadence, completeness_caveats, claim_safe_wording,
            reviewed_at, reviewed_by, review_reason, metadata
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s, %s,
            %s::jsonb
        )
        ON CONFLICT (source_key) DO UPDATE
        SET display_name = EXCLUDED.display_name,
            description = EXCLUDED.description,
            publisher = EXCLUDED.publisher,
            source_type = EXCLUDED.source_type,
            jurisdiction = EXCLUDED.jurisdiction,
            canonical_url = EXCLUDED.canonical_url,
            authority_tier = EXCLUDED.authority_tier,
            official_status = EXCLUDED.official_status,
            ingestion_mode = EXCLUDED.ingestion_mode,
            storage_policy = EXCLUDED.storage_policy,
            access_type = EXCLUDED.access_type,
            license_status = EXCLUDED.license_status,
            terms_url = EXCLUDED.terms_url,
            sync_frequency = EXCLUDED.sync_frequency,
            data_format = EXCLUDED.data_format,
            corpus_table = EXCLUDED.corpus_table,
            enabled = EXCLUDED.enabled,
            priority = EXCLUDED.priority,
            coverage_kind = EXCLUDED.coverage_kind,
            parser_version = EXCLUDED.parser_version,
            licensing_notes = EXCLUDED.licensing_notes,
            metadata = legal_sources.metadata || EXCLUDED.metadata,
            -- Rights and review evidence are operator-owned. Catalog refreshes
            -- may fill them only while no review has been recorded.
            rights_decision = CASE WHEN legal_sources.reviewed_at IS NULL THEN EXCLUDED.rights_decision ELSE legal_sources.rights_decision END,
            source_tier = CASE WHEN legal_sources.reviewed_at IS NULL THEN EXCLUDED.source_tier ELSE legal_sources.source_tier END,
            geographic_scope = CASE WHEN legal_sources.reviewed_at IS NULL THEN EXCLUDED.geographic_scope ELSE legal_sources.geographic_scope END,
            temporal_scope = CASE WHEN legal_sources.reviewed_at IS NULL THEN EXCLUDED.temporal_scope ELSE legal_sources.temporal_scope END,
            expected_cadence = EXCLUDED.expected_cadence,
            completeness_caveats = CASE WHEN legal_sources.reviewed_at IS NULL THEN EXCLUDED.completeness_caveats ELSE legal_sources.completeness_caveats END,
            claim_safe_wording = CASE WHEN legal_sources.reviewed_at IS NULL THEN EXCLUDED.claim_safe_wording ELSE legal_sources.claim_safe_wording END,
            reviewed_at = legal_sources.reviewed_at,
            reviewed_by = legal_sources.reviewed_by,
            review_reason = legal_sources.review_reason,
            updated_at = now()
    """
    with conn.cursor() as cursor:
        for source in catalog["sources"]:
            cursor.execute(
                sql,
                [
                    source["source_key"],
                    source["display_name"],
                    source["description"],
                    source["publisher"],
                    source["source_type"],
                    source.get("jurisdiction"),
                    source["canonical_url"],
                    source["authority_tier"],
                    source["official_status"],
                    source["ingestion_mode"],
                    source["storage_policy"],
                    source["access_type"],
                    source["license_status"],
                    source.get("terms_url"),
                    source["sync_frequency"],
                    source["data_format"],
                    source["corpus_table"],
                    source["enabled"],
                    source["priority"],
                    source["coverage_kind"],
                    source.get("parser_version"),
                    source.get("notes"),
                    source.get("rights_decision") or "pending_review",
                    source.get("source_tier") or source["authority_tier"],
                    json.dumps(
                        source.get("geographic_scope")
                        or (
                            [source["jurisdiction"]]
                            if source.get("jurisdiction")
                            else []
                        )
                    ),
                    json.dumps(
                        source.get("temporal_scope")
                        or {
                            "start": source.get("coverage_start"),
                            "end": source.get("coverage_end"),
                        }
                    ),
                    source.get("expected_cadence") or source["sync_frequency"],
                    source.get("completeness_caveats")
                    or source.get("coverage_notes")
                    or "Bounded source scope; completeness is not established.",
                    source.get("claim_safe_wording"),
                    source.get("reviewed_at"),
                    source.get("reviewed_by"),
                    source.get("review_reason"),
                    json.dumps(_source_metadata(catalog, source)),
                ],
            )
    conn.commit()
    return len(catalog["sources"])


def admit_public_source(
    conn: Any,
    *,
    source_key: str,
    catalog_schema_version: str,
    manifest_reference: str,
    manifest_sha256: str,
    reviewed_by: str,
) -> None:
    """Record the explicit reviewed admission required for public serving.

    This is intentionally separate from catalog seeding: a catalog entry is
    descriptive, while admission is an operator-owned authorization decision.
    """
    catalog_schema_version = str(catalog_schema_version or "").strip()
    manifest_reference = str(manifest_reference or "").strip()
    manifest_sha256 = str(manifest_sha256 or "").strip()
    reviewed_by = str(reviewed_by or "").strip()
    if not catalog_schema_version or not manifest_reference or not reviewed_by:
        raise CatalogValidationError(
            "catalog schema, manifest reference, and reviewer are required"
        )
    if len(manifest_sha256) < 16:
        raise CatalogValidationError("manifest_sha256 must be a stable digest")
    with conn.cursor() as cursor:
        cursor.execute(
            """SELECT rights_decision, reviewed_at, reviewed_by, storage_policy,
                      enabled, metadata->>'catalog_schema_version',
                      metadata->>'implementation_status', claim_safe_wording
               FROM legal_sources WHERE source_key=%s""",
            [source_key],
        )
        source = cursor.fetchone()
        if not source or source[0] not in {"official", "open", "licensed"}:
            raise CatalogValidationError(
                "source is not rights-approved for public admission"
            )
        if (
            not source[1]
            or not source[2]
            or source[3] == "prohibited"
            or source[4] is not True
        ):
            raise CatalogValidationError("source requires independent review evidence")
        if (
            source[5] != catalog_schema_version
            or not str(source[6] or "").strip()
            or not str(source[7] or "").strip()
        ):
            raise CatalogValidationError(
                "source catalog lineage does not match the reviewed admission"
            )
        cursor.execute(
            """SELECT EXISTS (
                 SELECT 1 FROM authority_corpus_versions
                  WHERE manifest_hash=%s
                    AND status IN ('staged', 'canary', 'promoted'))""",
            [manifest_sha256],
        )
        if not cursor.fetchone()[0]:
            raise CatalogValidationError(
                "admission manifest is not a known staged, canary, or promoted release"
            )
        cursor.execute(
            """INSERT INTO citator_public_source_admissions
                 (source_key, catalog_schema_version, manifest_reference,
                  manifest_sha256, reviewed_at, reviewed_by)
               VALUES (%s, %s, %s, %s, now(), %s)
               ON CONFLICT (source_key) DO UPDATE SET
                 catalog_schema_version=EXCLUDED.catalog_schema_version,
                 manifest_reference=EXCLUDED.manifest_reference,
                 manifest_sha256=EXCLUDED.manifest_sha256,
                 reviewed_at=now(), reviewed_by=EXCLUDED.reviewed_by,
                 active=TRUE""",
            [
                source_key,
                catalog_schema_version,
                manifest_reference,
                manifest_sha256,
                reviewed_by,
            ],
        )
        cursor.execute(
            """UPDATE legal_sources
                  SET public_namespace='public-authority',
                      metadata=jsonb_set(metadata, '{manifest_reference}', to_jsonb(%s::text), true)
                WHERE source_key=%s""",
            [manifest_reference, source_key],
        )
        # Candidate snapshots are cloned before a new release can be admitted.
        # Reclassify only rows whose version carries this exact reviewed
        # manifest; the storage triggers re-evaluate the same lineage view.
        cursor.execute(
            """UPDATE legal_documents d
                  SET public_namespace='public-authority'
                 FROM authority_corpus_versions v
                WHERE d.source_key=%s AND d.corpus_version=v.version
                  AND v.manifest_hash=%s
                  AND v.status IN ('staged', 'canary')
                  AND d.public_namespace IS DISTINCT FROM 'public-authority'""",
            [source_key, manifest_sha256],
        )
        cursor.execute(
            """UPDATE authority_case_clusters cl
                  SET public_namespace='public-authority'
                 FROM authority_corpus_versions v
                WHERE cl.source_key=%s AND cl.corpus_version=v.version
                  AND v.manifest_hash=%s
                  AND v.status IN ('staged', 'canary')
                  AND cl.public_namespace IS DISTINCT FROM 'public-authority'""",
            [source_key, manifest_sha256],
        )
    conn.commit()


def _print_sources(catalog: dict[str, Any]) -> None:
    for source in sorted(
        catalog["sources"], key=lambda item: (item["priority"], item["source_key"])
    ):
        print(
            "\t".join(
                [
                    source["source_key"],
                    source["implementation_status"],
                    source["ingestion_mode"],
                    source["storage_policy"],
                    "enabled" if source["enabled"] else "disabled",
                ]
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and seed the legal source catalog"
    )
    parser.add_argument("--catalog", help="Override the bundled catalog path")
    parser.add_argument(
        "--fragments-dir", help="Optional directory of catalog fragments"
    )
    parser.add_argument(
        "--seed", action="store_true", help="Initialize schema and upsert sources"
    )
    parser.add_argument("--list", action="store_true", help="Print catalog sources")
    parser.add_argument("--db-url")
    args = parser.parse_args()

    catalog = load_catalog(args.catalog, fragments_dir=args.fragments_dir)
    if args.list:
        _print_sources(catalog)
    if args.seed:
        init_schema(args.db_url)
        with connect(args.db_url) as conn:
            count = seed_catalog(conn, catalog)
        print(json.dumps({"status": "seeded", "source_count": count}))
        return
    if not args.list:
        print(json.dumps({"status": "valid", **catalog_summary(catalog)}, indent=2))


if __name__ == "__main__":
    main()
