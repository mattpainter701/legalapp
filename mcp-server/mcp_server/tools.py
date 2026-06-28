from __future__ import annotations

TOOL_NAMES = [
    "search_caselaw",
    "get_case_details",
    "get_full_opinion",
    "find_similar_cases",
    "search_by_citation",
    "validate_citation",
    "normalize_citation",
    "get_citation_network",
    "get_authority_treatment",
    "search_by_jurisdiction",
    "search_recent_authority",
    "get_court_info",
    "get_court_coverage",
    "search_dockets",
    "export_research_bundle",
    "sync_status",
    "corpus_status",
]


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


def _one_case_identifier_schema(extra_properties: dict | None = None, required: list[str] | None = None) -> dict:
    properties = {"opinion_id": {"type": "integer"}, "cluster_id": {"type": "integer"}}
    if extra_properties:
        properties.update(extra_properties)
    schema = _schema(properties, required or [])
    schema["oneOf"] = [
        {"required": ["opinion_id"], "not": {"required": ["cluster_id"]}},
        {"required": ["cluster_id"], "not": {"required": ["opinion_id"]}},
    ]
    return schema


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
            "inputSchema": _one_case_identifier_schema(),
        },
        {
            "name": "get_full_opinion",
            "description": "Return complete locally loaded opinion text by opinion_id or cluster_id.",
            "inputSchema": _one_case_identifier_schema(
                {"include_chunks": {"type": "boolean", "default": False}}
            ),
        },
        {
            "name": "find_similar_cases",
            "description": "Find factually similar cases from a query, opinion_id, cluster_id, or chunk_id.",
            "inputSchema": _schema(
                {
                    "query": {"type": "string"},
                    "opinion_id": {"type": "integer"},
                    "cluster_id": {"type": "integer"},
                    "chunk_id": {"type": "string"},
                    "jurisdiction": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 8},
                },
                [],
            ),
        },
        {
            "name": "search_by_citation",
            "description": "Look up a case by reporter citation using local citation tables first.",
            "inputSchema": _schema({"citation": {"type": "string"}}, ["citation"]),
        },
        {
            "name": "validate_citation",
            "description": "Parse a user citation, normalize it, and report whether it resolves locally.",
            "inputSchema": _schema({"citation": {"type": "string"}}, ["citation"]),
        },
        {
            "name": "normalize_citation",
            "description": "Normalize a messy reporter citation into canonical volume, reporter, and page fields.",
            "inputSchema": _schema({"citation": {"type": "string"}}, ["citation"]),
        },
        {
            "name": "get_citation_network",
            "description": "Return cases cited by and citing a target opinion.",
            "inputSchema": _schema({"opinion_id": {"type": "integer"}}, ["opinion_id"]),
        },
        {
            "name": "get_authority_treatment",
            "description": "Return local citation treatment signals and history counts for a target opinion.",
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
        {
            "name": "get_court_coverage",
            "description": "Show loaded court/date/count coverage so clients know local corpus limits.",
            "inputSchema": _schema(
                {"court_id": {"type": "string"}, "jurisdiction": {"type": "string"}},
                [],
            ),
        },
        {
            "name": "search_dockets",
            "description": "Search locally loaded CourtListener docket metadata.",
            "inputSchema": _schema(
                {
                    "query": {"type": "string"},
                    "court_id": {"type": "string"},
                    "jurisdiction": {"type": "string"},
                    "date_from": {"type": "string", "format": "date"},
                    "date_to": {"type": "string", "format": "date"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                },
                ["query"],
            ),
        },
        {
            "name": "export_research_bundle",
            "description": "Return selected cases, citations, and snippets in a structured research bundle.",
            "inputSchema": _schema(
                {
                    "query": {"type": "string"},
                    "opinion_ids": {"type": "array", "items": {"type": "integer"}},
                    "cluster_ids": {"type": "array", "items": {"type": "integer"}},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                },
                [],
            ),
        },
        {
            "name": "sync_status",
            "description": "Return ingest and embedding progress for operators and API customers.",
            "inputSchema": _schema({}, []),
        },
        {
            "name": "corpus_status",
            "description": "Return global local corpus counts, embedded chunks, courts, and loaded date range.",
            "inputSchema": _schema({}, []),
        },
    ]
    return {
        "protocolVersion": "2024-11-05",
        "serverInfo": {"name": "clarity-courtlistener", "version": "0.1.0"},
        "capabilities": {"tools": {}},
        "tools": tools,
    }
