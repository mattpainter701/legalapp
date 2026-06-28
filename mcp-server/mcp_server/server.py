from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .database import connect
from .query_embeddings import QueryEmbeddingClient
from .repository import CourtListenerRepository
from .tools import build_tool_manifest

app = FastAPI(title="Clarity CourtListener MCP", version="0.1.0")
query_embedder = QueryEmbeddingClient.from_env()


class ToolCallRequest(BaseModel):
    name: str
    arguments: dict = {}


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
        elif body.name == "search_by_citation":
            result = repo.search_by_citation(args.get("citation", ""))
        elif body.name == "get_citation_network":
            result = repo.citation_network(int(args["opinion_id"]))
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
        else:
            raise HTTPException(status_code=404, detail=f"Unknown tool: {body.name}")
    return {"content": [{"type": "json", "json": result}], "isError": False}
