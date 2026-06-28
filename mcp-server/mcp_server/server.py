from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .database import connect
from .query_embeddings import QueryEmbeddingClient
from .repository import CourtListenerRepository
from .tools import build_tool_manifest

app = FastAPI(title="Clarity CourtListener MCP", version="0.1.0")
query_embedder = QueryEmbeddingClient.from_env()


class ToolCallRequest(BaseModel):
    name: str
    arguments: dict = Field(default_factory=dict)


@app.get("/health")
def health():
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
    return {
        "status": "ok",
        "query_embedding": "configured" if query_embedder.url else "disabled",
    }


@app.get("/api/mcp")
def manifest():
    return build_tool_manifest()


@app.post("/api/mcp/tools/call")
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
            elif body.name == "get_case_details":
                result = repo.case_details(args.get("opinion_id"), args.get("cluster_id"))
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
                    query_embedding=query_embedder.embed_query(query) if query else None,
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
                    query_embedding=query_embedder.embed_query(query) if query else None,
                )
            elif body.name == "sync_status":
                result = repo.sync_status()
            elif body.name == "corpus_status":
                result = repo.corpus_status()
            else:
                raise HTTPException(status_code=404, detail=f"Unknown tool: {body.name}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"content": [{"type": "json", "json": result}], "isError": False}
