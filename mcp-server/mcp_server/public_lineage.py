"""Shared fail-closed public-authority lineage helpers.

The database view is the canonical predicate.  These helpers keep every
operator ingestion path on the same staged/canary release selection contract
and keep caller metadata from impersonating control-plane provenance.
"""

from __future__ import annotations

import os
from typing import Any, Mapping


PROTECTED_AUTHORITY_METADATA_KEYS = frozenset(
    {
        "namespace",
        "public_namespace",
        "source_key",
        "corpus_version",
        "rights_decision",
        "storage_policy",
        "catalog_schema_version",
        "implementation_status",
        "manifest_reference",
        "manifest_sha256",
    }
)


def public_authority_metadata(
    payload: Mapping[str, Any] | None,
    *,
    trusted: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return descriptive metadata with protected provenance overwritten.

    Payload metadata can add harmless source-specific fields, but it can never
    grant a namespace, rights state, catalog lineage, or release identity.
    """

    result = {
        key: value
        for key, value in (payload or {}).items()
        if key not in PROTECTED_AUTHORITY_METADATA_KEYS
    }
    result.update(trusted or {})
    result["namespace"] = "public-authority"
    return result


def require_public_candidate_version(
    conn: Any,
    *,
    source_key: str,
    requested_version: str | None = None,
    error_message: str = (
        "source requires a staged release with current reviewed "
        "public-authority admission"
    ),
) -> str:
    """Resolve one admitted staged/canary version for an exact source.

    Selection is version-bound and consumes ``public_authority_source_lineage``
    rather than reimplementing rights/manifest checks in each adapter.
    """

    requested = (
        os.getenv("AUTHORITY_INGEST_CORPUS_VERSION", "")
        if requested_version is None
        else requested_version
    ).strip()
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT v.version
              FROM authority_corpus_versions v
              JOIN public_authority_source_lineage pas
                ON pas.source_key=%s AND pas.corpus_version=v.version
             WHERE v.status IN ('staged','canary')
               AND (%s='' OR v.version=%s)
             ORDER BY CASE WHEN v.version=%s THEN 0 ELSE 1 END,
                      v.created_at DESC
             LIMIT 1
            """,
            [source_key, requested, requested, requested],
        )
        row = cursor.fetchone()
    if not row:
        raise PermissionError(error_message)
    return str(row[0])
