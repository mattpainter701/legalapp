from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import time
import json
from fastapi import Request
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .database import connect
from .loader import init_schema
from .query_embeddings import QueryEmbeddingClient
from .control_plane import (
    cadence_seconds,
    promote_corpus_version,
    rollback_corpus_version,
    lag_seconds,
    record_audit,
    sampled_audit,
    stage_corpus_version,
)
from .repository import CourtListenerRepository
from .tools import build_tool_manifest

app = FastAPI(title="WellPled CourtListener MCP", version="0.1.0")
query_embedder = QueryEmbeddingClient.from_env()


@app.on_event("startup")
def initialize_public_authority_schema() -> None:
    """Apply the additive public-authority schema before serving requests."""
    init_schema()


def consume_operator_assertion(claims: dict[str, object]) -> None:
    """Atomically consume a signed control assertion in shared storage."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM authority_operator_assertions WHERE expires_at < now()"
            )
            cur.execute(
                """
                INSERT INTO authority_operator_assertions
                  (nonce, credential_id, actor, scope, method, path, body_sha256,
                   issued_at, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s,
                        to_timestamp(%s), to_timestamp(%s))
                ON CONFLICT (nonce) DO NOTHING
                RETURNING nonce
                """,
                [
                    claims["nonce"],
                    claims["credential"],
                    claims["actor"],
                    claims["scope"],
                    claims["method"],
                    claims["path"],
                    claims["body_sha256"],
                    claims["issued"],
                    claims["expires"],
                ],
            )
            consumed = cur.fetchone()
        if consumed is None:
            conn.rollback()
            raise HTTPException(
                status_code=403, detail="replayed signed operator context"
            )
        conn.commit()


class ToolCallRequest(BaseModel):
    name: str
    arguments: dict = Field(default_factory=dict)


class ControlPlaneRequest(BaseModel):
    version: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)
    audit_kind: str | None = None
    manifest_hash: str | None = None
    as_of: str | None = None
    embedding_model: str = "mixedbread-ai/mxbai-embed-large-v1"
    embedding_version: str = "1"
    embedding_dimension: int = 1024


async def operator_identity(
    request: Request,
    value: str = Header(default="", alias="X-Operator-Identity"),
    assertion: str = Header(default="", alias="X-Operator-Assertion"),
) -> str:
    if not value.strip() or not assertion.strip():
        raise HTTPException(
            status_code=403, detail="signed operator context is required"
        )
    try:
        encoded_payload, encoded_signature = assertion.split(".", 1)
        payload = base64.urlsafe_b64decode(
            encoded_payload + "=" * (-len(encoded_payload) % 4)
        )
        signature = base64.urlsafe_b64decode(
            encoded_signature + "=" * (-len(encoded_signature) % 4)
        )
        claims = json.loads(payload.decode())
        actor = str(claims["actor"])
        credential = str(claims["credential"])
        scope = str(claims["scope"])
        method = str(claims["method"])
        route = str(claims["path"])
        issued_i, expires_i = int(claims["issued"]), int(claims["expires"])
        body_hash = str(claims["body_sha256"])
        request_body = json.loads((await request.body()).decode())
        actual_body_hash = hashlib.sha256(
            json.dumps(request_body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    except (KeyError, ValueError, UnicodeError, TypeError, binascii.Error) as exc:
        raise HTTPException(
            status_code=403, detail="invalid signed operator context"
        ) from exc
    signer_secret = os.getenv("MCP_OPERATOR_ASSERTION_SECRET", "")
    if len(signer_secret) < 32:
        raise HTTPException(
            status_code=503, detail="Operator assertion signing is not configured"
        )
    expected = hmac.new(signer_secret.encode(), payload, hashlib.sha256).digest()
    now = int(time.time())
    if (
        not hmac.compare_digest(signature, expected)
        or actor != value.strip()
        or not credential
        or scope != "platform:write"
        or method != request.method
        or route != request.url.path
        or issued_i > now + 5
        or expires_i < now
        or expires_i - issued_i > 60
        or body_hash != actual_body_hash
    ):
        raise HTTPException(
            status_code=403, detail="invalid or expired signed operator context"
        )
    consume_operator_assertion(claims)
    return actor


def require_internal_service_key(
    supplied: str = Header(default="", alias="X-Clarity-Internal-Key"),
) -> None:
    expected = os.getenv("MCP_UPSTREAM_API_KEY", "")
    if len(expected) < 32:
        raise HTTPException(
            status_code=503,
            detail="Private service authentication is not configured",
        )
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401, detail="Invalid internal service credential"
        )


@app.get("/health")
def health():
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
    return {
        "status": "ok",
        "query_embedding": "configured" if query_embedder.url else "disabled",
    }


@app.get("/api/mcp", dependencies=[Depends(require_internal_service_key)])
def manifest():
    return build_tool_manifest()


@app.post(
    "/api/mcp/control/audit", dependencies=[Depends(require_internal_service_key)]
)
def run_control_audit(
    body: ControlPlaneRequest, actor: str = Depends(operator_identity)
):
    if body.audit_kind not in {"release", "completeness", "freshness", "isolation"}:
        raise HTTPException(
            status_code=400,
            detail="audit_kind must be completeness, freshness, or isolation",
        )
    with connect() as conn:
        with conn.cursor() as cur:
            if body.audit_kind == "release":
                cur.execute(
                    """
                    SELECT s.rights_decision, s.reviewed_at,
                           COALESCE(cp.status, l.acquisition_state, 'missing'),
                           GREATEST(COUNT(DISTINCT d.id), COALESCE(MAX(l.rows_loaded), 0))
                    FROM public_authority_source_lineage pas
                    JOIN legal_sources s ON s.source_key=pas.source_key AND pas.corpus_version=%s
                    LEFT JOIN authority_harvest_checkpoints cp
                      ON cp.source_key=s.source_key AND cp.corpus_version=%s
                    LEFT JOIN legal_documents d
                      ON d.source_key=s.source_key AND d.corpus_version=%s
                    LEFT JOIN corpus_coverage_ledger l
                      ON l.source_key=s.source_key AND l.source_release=%s
                    WHERE s.enabled IS TRUE
                      AND s.public_namespace = 'public-authority'
                      AND s.storage_policy <> 'prohibited'
                      AND s.rights_decision IN ('official','open','licensed')
                      AND s.reviewed_by IS NOT NULL
                      AND s.claim_safe_wording IS NOT NULL
                      AND s.metadata->>'catalog_schema_version' IS NOT NULL
                      AND s.metadata->>'implementation_status' IS NOT NULL
                      AND NOT starts_with(s.source_key, 'tenant:')
                      AND NOT starts_with(s.source_key, 'firm:')
                      AND NOT starts_with(s.source_key, 'private:')
                    GROUP BY s.source_key, s.rights_decision, s.reviewed_at,
                             cp.status, l.acquisition_state
                """,
                    [body.version, body.version, body.version, body.version],
                )
                records = [
                    {
                        "ready": row[0] in {"official", "open", "licensed"}
                        and row[1] is not None
                        and row[2] in {"complete", "indexed"}
                        and row[3] > 0
                    }
                    for row in cur.fetchall()
                ]
            elif body.audit_kind == "completeness":
                cur.execute(
                    """
                    SELECT e.source_key, e.partition_key,
                           e.expected_item_count,
                           COALESCE(e.rows_loaded, 0), e.status
                    FROM (
                      SELECT cp.source_key, cp.partition_key,
                             l.expected_item_count, l.rows_loaded, cp.status
                      FROM authority_harvest_checkpoints cp
                      LEFT JOIN corpus_coverage_ledger l
                        ON l.source_key=cp.source_key AND l.partition_key=cp.partition_key
                       AND l.source_release=cp.corpus_version
                      WHERE cp.corpus_version=%s
                      UNION ALL
                      SELECT l.source_key, l.partition_key,
                             l.expected_item_count, l.rows_loaded, l.acquisition_state
                      FROM corpus_coverage_ledger l
                      WHERE l.source_release=%s
                        AND NOT EXISTS (
                          SELECT 1 FROM authority_harvest_checkpoints cp
                          WHERE cp.source_key=l.source_key AND cp.partition_key=l.partition_key
                            AND cp.corpus_version=l.source_release
                        )
                    ) e
                    JOIN public_authority_source_lineage pas
                      ON pas.source_key=e.source_key AND pas.corpus_version=%s
                """,
                    [body.version, body.version, body.version],
                )
                records = [
                    {
                        "expected": max(row[2] or 0, 1),
                        "observed": row[3] if row[4] in {"complete", "indexed"} else 0,
                        "declared": row[2] is not None and row[2] > 0,
                    }
                    for row in cur.fetchall()
                ]
            elif body.audit_kind == "freshness":
                cur.execute(
                    """
                    SELECT COALESCE(cp.last_successful_harvest_at,
                           CASE WHEN l.acquisition_state IN ('complete', 'indexed')
                                THEN l.last_checked_at ELSE NULL END),
                           s.expected_cadence, COALESCE(cp.status, l.acquisition_state)
                    FROM public_authority_source_lineage pas
                    JOIN legal_sources s ON s.source_key=pas.source_key AND pas.corpus_version=%s
                    LEFT JOIN authority_harvest_checkpoints cp
                      ON cp.source_key=s.source_key AND cp.corpus_version=%s
                    LEFT JOIN corpus_coverage_ledger l
                      ON l.source_key=s.source_key AND l.source_release=%s
                    WHERE (cp.source_key IS NOT NULL OR l.source_key IS NOT NULL)
                      AND s.enabled IS TRUE
                      AND s.public_namespace = 'public-authority'
                      AND s.rights_decision IN ('official','open','licensed')
                      AND s.reviewed_at IS NOT NULL AND s.reviewed_by IS NOT NULL
                """,
                    [body.version, body.version, body.version],
                )
                now = datetime.now(timezone.utc)
                records = [
                    {
                        "lag_seconds": (
                            lag_seconds(row[0], cadence, now)
                            if row[0]
                            and cadence is not None
                            and row[2]
                            not in {
                                "failed",
                                "retryable",
                                "retryable_failure",
                                "quarantined",
                                "dead_letter",
                            }
                            else None
                        ),
                        "cadence": row[1],
                    }
                    for row in cur.fetchall()
                    for cadence in [cadence_seconds(row[1])]
                ]
            else:
                cur.execute(
                    """
                    SELECT d.source_key, d.public_namespace,
                           (d.public_namespace IS DISTINCT FROM 'public-authority'
                            OR starts_with(d.source_key, 'tenant:') OR starts_with(d.source_key, 'firm:')
                            OR starts_with(d.source_key, 'private:') OR s.storage_policy = 'prohibited'
                            OR s.public_namespace IS DISTINCT FROM 'public-authority'
                            OR NOT EXISTS (SELECT 1 FROM public_authority_source_lineage pas WHERE pas.source_key=d.source_key AND pas.corpus_version=d.corpus_version)
                            OR s.metadata->>'catalog_schema_version' IS NULL)
                    FROM legal_documents d
                    LEFT JOIN legal_sources s ON s.source_key=d.source_key
                    WHERE d.corpus_version=%s
                    UNION ALL
                    SELECT cl.source_key, cl.public_namespace,
                           (cl.public_namespace IS DISTINCT FROM 'public-authority'
                            OR NOT EXISTS (SELECT 1 FROM public_authority_source_lineage pas WHERE pas.source_key=cl.source_key AND pas.corpus_version=cl.corpus_version))
                    FROM authority_case_clusters cl
                    WHERE cl.corpus_version=%s
                    UNION ALL
                    SELECT cp.source_key, 'public-authority',
                           NOT EXISTS (SELECT 1 FROM public_authority_source_lineage pas
                                       WHERE pas.source_key=cp.source_key
                                         AND pas.corpus_version=cp.corpus_version)
                    FROM authority_harvest_checkpoints cp
                    WHERE cp.corpus_version=%s
                    UNION ALL
                    SELECT l.source_key, 'public-authority',
                           NOT EXISTS (SELECT 1 FROM public_authority_source_lineage pas
                                       WHERE pas.source_key=l.source_key
                                         AND pas.corpus_version=l.source_release)
                    FROM corpus_coverage_ledger l
                    WHERE l.source_release=%s
                """,
                    [body.version, body.version, body.version, body.version],
                )
                records = [
                    {"namespace": row[1], "private": bool(row[2])}
                    for row in cur.fetchall()
                ]
        result = sampled_audit(records, audit_kind=body.audit_kind)
        immutable_hash = record_audit(
            conn,
            corpus_version=body.version,
            audit_kind=body.audit_kind,
            methodology=f"bounded database sample for {body.audit_kind}",
            thresholds={"minimum_completeness": 0.95, "maximum_lag_seconds": 172800},
            result=result,
            passed=bool(result["passed"]),
            auditor=actor,
        )
    return {"version": body.version, "audit": result, "immutable_hash": immutable_hash}


@app.post(
    "/api/mcp/control/stage", dependencies=[Depends(require_internal_service_key)]
)
def stage_control_version(
    body: ControlPlaneRequest, actor: str = Depends(operator_identity)
):
    if not all((body.manifest_hash, body.as_of)):
        raise HTTPException(
            status_code=400, detail="manifest_hash and as_of are required"
        )
    with connect() as conn:
        try:
            stage_corpus_version(
                conn,
                version=body.version,
                manifest_hash=body.manifest_hash,
                as_of=body.as_of,
                actor=actor,
                reason=body.reason,
                embedding_model=body.embedding_model,
                embedding_version=body.embedding_version,
                embedding_dimension=body.embedding_dimension,
            )
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"version": body.version, "status": "staged", "actor": actor}


@app.post(
    "/api/mcp/control/promote", dependencies=[Depends(require_internal_service_key)]
)
def promote_control_version(
    body: ControlPlaneRequest, actor: str = Depends(operator_identity)
):
    with connect() as conn:
        try:
            promote_corpus_version(
                conn, version=body.version, actor=actor, reason=body.reason
            )
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"version": body.version, "status": "promoted", "actor": actor}


@app.post(
    "/api/mcp/control/rollback", dependencies=[Depends(require_internal_service_key)]
)
def rollback_control_version(
    body: ControlPlaneRequest, actor: str = Depends(operator_identity)
):
    with connect() as conn:
        try:
            rollback_corpus_version(
                conn, version=body.version, actor=actor, reason=body.reason
            )
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"version": body.version, "status": "rolled_back", "actor": actor}


@app.post("/api/mcp/tools/call", dependencies=[Depends(require_internal_service_key)])
def call_tool(body: ToolCallRequest):
    try:
        with connect() as conn:
            repo = CourtListenerRepository(conn)
            args = body.arguments
            if body.name == "search_caselaw":
                query = args.get("query", "")
                result = repo.search_caselaw(
                    query=query,
                    top_k=int(args.get("top_k", 8)),
                    jurisdiction=args.get("jurisdiction"),
                    date_from=args.get("date_from"),
                    date_to=args.get("date_to"),
                    query_embedding=query_embedder.embed_query(query),
                )
            elif body.name == "search_legal_authorities":
                query = args.get("query", "")
                result = repo.search_legal_authorities(
                    query=query,
                    top_k=int(args.get("top_k", 8)),
                    jurisdiction=args.get("jurisdiction"),
                    source_keys=args.get("source_keys") or [],
                    authority_tiers=args.get("authority_tiers") or [],
                    document_types=args.get("document_types") or [],
                    effective_on=args.get("effective_on"),
                    query_embedding=query_embedder.embed_query(query),
                )
            elif body.name == "get_case_details":
                result = repo.case_details(
                    args.get("opinion_id"), args.get("cluster_id")
                )
            elif body.name == "get_full_opinion":
                result = repo.get_full_opinion(
                    opinion_id=args.get("opinion_id"),
                    cluster_id=args.get("cluster_id"),
                    include_chunks=bool(args.get("include_chunks", False)),
                )
            elif body.name == "find_similar_cases":
                query = args.get("query", "")
                result = repo.find_similar_cases(
                    query=query,
                    opinion_id=args.get("opinion_id"),
                    cluster_id=args.get("cluster_id"),
                    chunk_id=args.get("chunk_id"),
                    top_k=int(args.get("top_k", 8)),
                    jurisdiction=args.get("jurisdiction"),
                    query_embedding=query_embedder.embed_query(query)
                    if query
                    else None,
                )
            elif body.name == "search_by_citation":
                result = repo.search_by_citation(args.get("citation", ""))
            elif body.name == "validate_citation":
                result = repo.validate_citation(args.get("citation", ""))
            elif body.name == "normalize_citation":
                result = repo.normalize_citation(args.get("citation", ""))
            elif body.name == "get_citation_network":
                result = repo.citation_network(int(args["opinion_id"]))
            elif body.name == "get_authority_treatment":
                result = repo.authority_treatment(int(args["opinion_id"]))
            elif body.name == "get_citator_status":
                result = repo.citator_status()
            elif body.name == "search_by_jurisdiction":
                query = args.get("query", "")
                result = repo.search_caselaw(
                    query=query,
                    top_k=int(args.get("top_k", 8)),
                    jurisdiction=args.get("jurisdiction"),
                    query_embedding=query_embedder.embed_query(query),
                )
            elif body.name == "search_recent_authority":
                query = args.get("query", "")
                result = repo.search_caselaw(
                    query=query,
                    top_k=int(args.get("top_k", 8)),
                    date_from=args.get("date_from"),
                    query_embedding=query_embedder.embed_query(query),
                )
            elif body.name == "get_court_info":
                result = repo.court_info(args.get("court_id", ""))
            elif body.name == "get_court_coverage":
                result = repo.court_coverage(
                    court_id=args.get("court_id"),
                    jurisdiction=args.get("jurisdiction"),
                )
            elif body.name == "search_dockets":
                result = repo.search_dockets(
                    query=args.get("query", ""),
                    court_id=args.get("court_id"),
                    jurisdiction=args.get("jurisdiction"),
                    date_from=args.get("date_from"),
                    date_to=args.get("date_to"),
                    top_k=int(args.get("top_k", 20)),
                )
            elif body.name == "export_research_bundle":
                query = args.get("query", "")
                result = repo.export_research_bundle(
                    query=query,
                    opinion_ids=args.get("opinion_ids") or [],
                    cluster_ids=args.get("cluster_ids") or [],
                    top_k=int(args.get("top_k", 5)),
                    query_embedding=query_embedder.embed_query(query)
                    if query
                    else None,
                )
            elif body.name == "sync_status":
                result = repo.sync_status()
            elif body.name == "corpus_status":
                result = repo.corpus_status()
            elif body.name == "authority_coverage":
                result = repo.authority_coverage()
            else:
                raise HTTPException(
                    status_code=404, detail=f"Unknown tool: {body.name}"
                )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"content": [{"type": "json", "json": result}], "isError": False}
