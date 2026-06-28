from __future__ import annotations

TOOL_NAMES = [
    "search_caselaw",
    "get_case_details",
    "search_by_citation",
    "get_citation_network",
    "search_by_jurisdiction",
    "search_recent_authority",
    "get_court_info",
]


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


def build_tool_manifest() -> dict:
    tools = [
        {
            "name": "search_caselaw",
            "description": "Hybrid keyword and semantic search across locally ingested CourtListener case law.",
            "inputSchema": _schema(
                {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 8},
                    "jurisdiction": {"type": "string"},
                    "date_from": {"type": "string", "format": "date"},
                    "date_to": {"type": "string", "format": "date"},
                },
                ["query"],
            ),
        },
        {
            "name": "get_case_details",
            "description": "Return opinion metadata, text chunks, and citations for a CourtListener opinion or cluster.",
            "inputSchema": _schema(
                {"opinion_id": {"type": "integer"}, "cluster_id": {"type": "integer"}},
                [],
            ),
        },
        {
            "name": "search_by_citation",
            "description": "Look up a case by reporter citation using local citation tables first.",
            "inputSchema": _schema({"citation": {"type": "string"}}, ["citation"]),
        },
        {
            "name": "get_citation_network",
            "description": "Return cases cited by and citing a target opinion.",
            "inputSchema": _schema({"opinion_id": {"type": "integer"}}, ["opinion_id"]),
        },
        {
            "name": "search_by_jurisdiction",
            "description": "Search case law constrained to a CourtListener court or jurisdiction.",
            "inputSchema": _schema(
                {
                    "jurisdiction": {"type": "string"},
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 8},
                },
                ["jurisdiction", "query"],
            ),
        },
        {
            "name": "search_recent_authority",
            "description": "Search recent case law after a given filing date.",
            "inputSchema": _schema(
                {
                    "query": {"type": "string"},
                    "date_from": {"type": "string", "format": "date"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 8},
                },
                ["query", "date_from"],
            ),
        },
        {
            "name": "get_court_info",
            "description": "Return local metadata and coverage counts for a CourtListener court.",
            "inputSchema": _schema({"court_id": {"type": "string"}}, ["court_id"]),
        },
    ]
    return {
        "protocolVersion": "2024-11-05",
        "serverInfo": {"name": "clarity-courtlistener", "version": "0.1.0"},
        "capabilities": {"tools": {}},
        "tools": tools,
    }
