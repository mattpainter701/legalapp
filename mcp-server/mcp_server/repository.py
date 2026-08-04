from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .database import dict_rows
from .query_embeddings import format_vector_literal

_CITATION_RE = re.compile(r"^\s*(?P<volume>\d+)\s+(?P<reporter>.+?)\s+(?P<page>\d+)\s*$")


class CourtListenerRepository:
    def __init__(self, conn):
        self.conn = conn

    def _parse_citation(self, citation: str) -> dict[str, str] | None:
        normalized = re.sub(r"\s+", " ", (citation or "").strip())
        match = _CITATION_RE.match(normalized)
        if not match:
            return None
        reporter = match.group("reporter").strip()
        if re.match(r"^(?:[NS]\.[EW]\.|P\.)\s+\d+d$", reporter):
            reporter = reporter.replace(" ", "")
        else:
            reporter = re.sub(r"\s+", " ", reporter)
        return {
            "volume": match.group("volume"),
            "reporter": reporter,
            "page": match.group("page"),
            "canonical": f"{match.group('volume')} {reporter} {match.group('page')}",
        }

    def _search_filters(
        self,
        jurisdiction: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> tuple[list[str], list[Any]]:
        # Caselaw lives in the CourtListener tables, not the versioned legal
        # authority tables. The latter's `d` and `s` aliases are not present
        # in either caselaw query, so those status filters must not leak here.
        filters = ["TRUE"]
        params: list[Any] = []
        if jurisdiction:
            filters.append("(oc.court_id = %s OR c.jurisdiction = %s)")
            params.extend([jurisdiction, jurisdiction])
        if date_from:
            filters.append("cl.date_filed >= %s")
            params.append(date_from)
        if date_to:
            filters.append("cl.date_filed <= %s")
            params.append(date_to)
        return filters, params

    def search_caselaw(
        self,
        query: str,
        top_k: int = 8,
        jurisdiction: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        query_embedding: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        if query_embedding:
            return self._search_hybrid(
                query=query,
                top_k=top_k,
                jurisdiction=jurisdiction,
                date_from=date_from,
                date_to=date_to,
                query_embedding=query_embedding,
            )
        return self._search_fts(
            query=query,
            top_k=top_k,
            jurisdiction=jurisdiction,
            date_from=date_from,
            date_to=date_to,
        )

    def search_legal_authorities(
        self,
        query: str,
        top_k: int = 8,
        jurisdiction: str | None = None,
        source_keys: list[str] | None = None,
        authority_tiers: list[str] | None = None,
        document_types: list[str] | None = None,
        effective_on: str | None = None,
        query_embedding: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        filters = ["TRUE"]
        filter_params: list[Any] = []
        if jurisdiction:
            filters.append("d.jurisdiction = %s")
            filter_params.append(jurisdiction)
        if source_keys:
            filters.append("d.source_key = ANY(%s)")
            filter_params.append(source_keys)
        if authority_tiers:
            filters.append("d.authority_tier = ANY(%s)")
            filter_params.append(authority_tiers)
        if document_types:
            filters.append("d.document_type = ANY(%s)")
            filter_params.append(document_types)
        if effective_on:
            filters.extend(
                [
                    "(d.effective_date IS NULL OR d.effective_date <= %s)",
                    "(d.termination_date IS NULL OR d.termination_date >= %s)",
                ]
            )
            filter_params.extend([effective_on, effective_on])

        where = " AND ".join(filters)
        if not query_embedding:
            sql = f"""
                SELECT c.id::text AS chunk_id, d.id::text AS document_id,
                       d.source_key, s.display_name AS source_name,
                       s.official_status, d.document_type, d.title, d.citation,
                       d.jurisdiction, d.authority_tier, d.document_status,
                       d.publication_date, d.effective_date, d.termination_date,
                       d.canonical_url AS source_url, d.retrieved_at,
                       s.last_successful_sync_at, c.chunk_index, c.content,
                       ts_rank_cd(c.fts, websearch_to_tsquery('english', %s)) AS rank,
                       ts_rank_cd(c.fts, websearch_to_tsquery('english', %s)) AS keyword_rank,
                       NULL::float AS similarity, 'fts' AS search_source
                FROM legal_document_chunks c
                JOIN legal_documents d ON d.id = c.document_id
                JOIN legal_sources s ON s.source_key = d.source_key
                WHERE c.fts @@ websearch_to_tsquery('english', %s)
                  AND {where}
                ORDER BY rank DESC, d.effective_date DESC NULLS LAST
                LIMIT %s
            """
            params = [query, query, query, *filter_params, top_k]
        else:
            vector = format_vector_literal(query_embedding)
            candidate_limit = min(max(top_k * 4, 20), 200)
            sql = f"""
                WITH filtered AS (
                    SELECT c.id::text AS chunk_id, d.id::text AS document_id,
                           d.source_key, s.display_name AS source_name,
                           s.official_status, d.document_type, d.title, d.citation,
                           d.jurisdiction, d.authority_tier, d.document_status,
                           d.publication_date, d.effective_date, d.termination_date,
                           d.canonical_url AS source_url, d.retrieved_at,
                           s.last_successful_sync_at, c.chunk_index, c.content,
                           c.fts, c.embedding
                    FROM legal_document_chunks c
                    JOIN legal_documents d ON d.id = c.document_id
                    JOIN legal_sources s ON s.source_key = d.source_key
                    WHERE {where}
                ),
                dense AS (
                    SELECT *, row_number() OVER (ORDER BY embedding <=> %s::vector) AS dense_rank,
                           1 - (embedding <=> %s::vector) AS similarity,
                           NULL::bigint AS fts_rank, NULL::float AS keyword_rank
                    FROM filtered
                    WHERE embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                ),
                keyword AS (
                    SELECT *, NULL::bigint AS dense_rank, NULL::float AS similarity,
                           row_number() OVER (
                               ORDER BY ts_rank_cd(fts, websearch_to_tsquery('english', %s)) DESC
                           ) AS fts_rank,
                           ts_rank_cd(fts, websearch_to_tsquery('english', %s)) AS keyword_rank
                    FROM filtered
                    WHERE fts @@ websearch_to_tsquery('english', %s)
                    ORDER BY keyword_rank DESC
                    LIMIT %s
                ),
                combined AS (
                    SELECT * FROM dense
                    UNION ALL
                    SELECT * FROM keyword
                )
                SELECT chunk_id, document_id, source_key, source_name,
                       official_status, document_type, title, citation, jurisdiction,
                       authority_tier, document_status, publication_date, effective_date,
                       termination_date, source_url, retrieved_at, last_successful_sync_at,
                       chunk_index, content,
                       COALESCE(MAX(similarity), 0.0) AS similarity,
                       COALESCE(MAX(keyword_rank), 0.0) AS keyword_rank,
                       (
                           COALESCE(0.6 / (60 + MIN(dense_rank)), 0.0) +
                           COALESCE(0.4 / (60 + MIN(fts_rank)), 0.0)
                       ) AS rank,
                       CASE
                           WHEN MIN(dense_rank) IS NOT NULL AND MIN(fts_rank) IS NOT NULL THEN 'hybrid'
                           WHEN MIN(dense_rank) IS NOT NULL THEN 'vector'
                           ELSE 'fts'
                       END AS search_source
                FROM combined
                GROUP BY chunk_id, document_id, source_key, source_name,
                         official_status, document_type, title, citation, jurisdiction,
                         authority_tier, document_status, publication_date, effective_date,
                         termination_date, source_url, retrieved_at, last_successful_sync_at,
                         chunk_index, content
                ORDER BY rank DESC, similarity DESC, effective_date DESC NULLS LAST
                LIMIT %s
            """
            params = [
                *filter_params,
                vector,
                vector,
                vector,
                candidate_limit,
                query,
                query,
                query,
                candidate_limit,
                top_k,
            ]
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return dict_rows(cur)

    def _search_fts(
        self,
        query: str,
        top_k: int,
        jurisdiction: str | None,
        date_from: str | None,
        date_to: str | None,
    ) -> list[dict[str, Any]]:
        filters, filter_params = self._search_filters(jurisdiction, date_from, date_to)
        sql = f"""
            SELECT oc.id::text AS chunk_id, oc.opinion_id, oc.cluster_id, oc.chunk_index,
                   cl.case_name,
                   cl.date_filed, oc.court_id, c.full_name AS court_name, oc.content,
                   COALESCE(NULLIF(o.source_url, ''), '/opinion/' || oc.opinion_id::text || '/') AS source_url,
                   COALESCE(cl.citations #>> '{{0,cite}}', cl.citations #>> '{{0}}', '') AS citation,
                   ts_rank_cd(oc.fts, websearch_to_tsquery('english', %s)) AS rank,
                   ts_rank_cd(oc.fts, websearch_to_tsquery('english', %s)) AS keyword_rank,
                   NULL::float AS similarity,
                   'fts' AS search_source
            FROM opinion_chunks oc
            JOIN opinion_clusters cl ON cl.cluster_id = oc.cluster_id
            LEFT JOIN opinions o ON o.opinion_id = oc.opinion_id
            LEFT JOIN courts c ON c.court_id = oc.court_id
            WHERE oc.fts @@ websearch_to_tsquery('english', %s)
              AND {' AND '.join(filters)}
            ORDER BY rank DESC, cl.date_filed DESC NULLS LAST
            LIMIT %s
        """
        params = [query, query, query, *filter_params, top_k]
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return dict_rows(cur)

    def _search_hybrid(
        self,
        query: str,
        top_k: int,
        jurisdiction: str | None,
        date_from: str | None,
        date_to: str | None,
        query_embedding: list[float],
    ) -> list[dict[str, Any]]:
        filters, filter_params = self._search_filters(jurisdiction, date_from, date_to)
        vector = format_vector_literal(query_embedding)
        candidate_limit = min(max(top_k * 4, 20), 200)
        sql = f"""
            WITH filtered AS (
                SELECT oc.id::text AS chunk_id, oc.opinion_id, oc.cluster_id,
                       oc.chunk_index, cl.case_name, cl.date_filed, oc.court_id,
                       c.full_name AS court_name, oc.content,
                       COALESCE(NULLIF(o.source_url, ''), '/opinion/' || oc.opinion_id::text || '/') AS source_url,
                       COALESCE(cl.citations #>> '{{0,cite}}', cl.citations #>> '{{0}}', '') AS citation,
                       oc.fts, oc.embedding
                FROM opinion_chunks oc
                JOIN opinion_clusters cl ON cl.cluster_id = oc.cluster_id
                LEFT JOIN opinions o ON o.opinion_id = oc.opinion_id
                LEFT JOIN courts c ON c.court_id = oc.court_id
                WHERE {' AND '.join(filters)}
            ),
            dense AS (
                SELECT *,
                       row_number() OVER (ORDER BY embedding <=> %s::vector) AS dense_rank,
                       1 - (embedding <=> %s::vector) AS similarity,
                       NULL::bigint AS fts_rank,
                       NULL::float AS keyword_rank
                FROM filtered
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            ),
            fts AS (
                SELECT *,
                       NULL::bigint AS dense_rank,
                       NULL::float AS similarity,
                       row_number() OVER (
                           ORDER BY ts_rank_cd(fts, websearch_to_tsquery('english', %s)) DESC,
                                    date_filed DESC NULLS LAST
                       ) AS fts_rank,
                       ts_rank_cd(fts, websearch_to_tsquery('english', %s)) AS keyword_rank
                FROM filtered
                WHERE fts @@ websearch_to_tsquery('english', %s)
                ORDER BY keyword_rank DESC, date_filed DESC NULLS LAST
                LIMIT %s
            ),
            combined AS (
                SELECT * FROM dense
                UNION ALL
                SELECT * FROM fts
            )
            SELECT chunk_id, opinion_id, cluster_id, chunk_index, case_name, date_filed,
                   court_id, court_name, content, source_url, citation,
                   COALESCE(MAX(similarity), 0.0) AS similarity,
                   COALESCE(MAX(keyword_rank), 0.0) AS keyword_rank,
                   (
                       COALESCE(0.6 / (60 + MIN(dense_rank)), 0.0) +
                       COALESCE(0.4 / (60 + MIN(fts_rank)), 0.0)
                   ) AS rank,
                   CASE
                       WHEN MIN(dense_rank) IS NOT NULL AND MIN(fts_rank) IS NOT NULL THEN 'hybrid'
                       WHEN MIN(dense_rank) IS NOT NULL THEN 'vector'
                       ELSE 'fts'
                   END AS search_source
            FROM combined
            GROUP BY chunk_id, opinion_id, cluster_id, chunk_index, case_name,
                     date_filed, court_id, court_name, content, source_url, citation
            ORDER BY rank DESC, similarity DESC, date_filed DESC NULLS LAST
            LIMIT %s
        """
        params = [
            *filter_params,
            vector,
            vector,
            vector,
            candidate_limit,
            query,
            query,
            query,
            candidate_limit,
            top_k,
        ]
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return dict_rows(cur)

    def case_details(self, opinion_id: int | None = None, cluster_id: int | None = None) -> dict[str, Any] | None:
        if bool(opinion_id) == bool(cluster_id):
            raise ValueError("exactly one of opinion_id or cluster_id is required")
        where = "o.opinion_id = %s" if opinion_id else "o.cluster_id = %s"
        value = opinion_id or cluster_id
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT o.opinion_id, o.cluster_id, o.type, o.source_url, cl.case_name,
                       cl.date_filed, cl.citations, d.docket_number, c.court_id, c.full_name AS court_name
                FROM opinions o
                JOIN opinion_clusters cl ON cl.cluster_id = o.cluster_id
                LEFT JOIN dockets d ON d.docket_id = cl.docket_id
                LEFT JOIN courts c ON c.court_id = d.court_id
                WHERE {where}
                LIMIT 1
                """,
                [value],
            )
            rows = dict_rows(cur)
            if not rows:
                return None
            detail = rows[0]
            cur.execute(
                """
                SELECT id::text AS chunk_id, chunk_index, content
                FROM opinion_chunks
                WHERE opinion_id = %s
                ORDER BY chunk_index
                """,
                [detail["opinion_id"]],
            )
            detail["chunks"] = dict_rows(cur)
            return detail

    def get_full_opinion(
        self,
        opinion_id: int | None = None,
        cluster_id: int | None = None,
        include_chunks: bool = False,
    ) -> dict[str, Any] | None:
        detail = self.case_details(opinion_id=opinion_id, cluster_id=cluster_id)
        if not detail:
            return None
        chunks = detail.get("chunks") or []
        full_text = "\n\n".join(chunk.get("content") or "" for chunk in chunks).strip()
        result = {
            key: value
            for key, value in detail.items()
            if key != "chunks"
        }
        result["full_text"] = full_text
        result["chunk_count"] = len(chunks)
        if include_chunks:
            result["chunks"] = chunks
        return result

    def find_similar_cases(
        self,
        query: str | None = None,
        opinion_id: int | None = None,
        cluster_id: int | None = None,
        chunk_id: str | None = None,
        top_k: int = 8,
        jurisdiction: str | None = None,
        query_embedding: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        source_opinion_id = opinion_id
        source_text = (query or "").strip()
        if not source_text and chunk_id:
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT opinion_id, content FROM opinion_chunks WHERE id = %s LIMIT 1",
                    [chunk_id],
                )
                rows = dict_rows(cur)
                if rows:
                    source_opinion_id = rows[0].get("opinion_id")
                    source_text = rows[0].get("content") or ""
        if not source_text and (opinion_id or cluster_id):
            detail = self.get_full_opinion(opinion_id=opinion_id, cluster_id=cluster_id)
            if detail:
                source_opinion_id = detail.get("opinion_id")
                source_text = detail.get("full_text") or detail.get("case_name") or ""
        if not source_text:
            raise ValueError("query, opinion_id, cluster_id, or chunk_id is required")
        results = self.search_caselaw(
            query=source_text[:4000],
            top_k=min(top_k + 3, 50),
            jurisdiction=jurisdiction,
            query_embedding=query_embedding,
        )
        filtered = [
            row for row in results
            if not source_opinion_id or row.get("opinion_id") != source_opinion_id
        ]
        return filtered[:top_k]

    def search_by_citation(self, citation: str) -> list[dict[str, Any]]:
        parsed = self._parse_citation(citation)
        if not parsed:
            return []
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT o.opinion_id, o.cluster_id, cl.case_name, cl.date_filed,
                       c.court_id, c.full_name AS court_name
                FROM opinion_citations cit
                JOIN opinion_clusters cl ON cl.cluster_id = cit.cited_cluster_id
                LEFT JOIN opinions o ON o.cluster_id = cl.cluster_id
                LEFT JOIN dockets d ON d.docket_id = cl.docket_id
                LEFT JOIN courts c ON c.court_id = d.court_id
                WHERE cit.cited_volume = %s AND cit.cited_reporter = %s AND cit.cited_page = %s
                LIMIT 20
                """,
                [parsed["volume"], parsed["reporter"], parsed["page"]],
            )
            return dict_rows(cur)

    def normalize_citation(self, citation: str) -> dict[str, Any]:
        parsed = self._parse_citation(citation)
        if not parsed:
            return {
                "input": citation,
                "valid": False,
                "canonical": None,
                "volume": None,
                "reporter": None,
                "page": None,
                "matches": [],
            }
        matches = self.search_by_citation(parsed["canonical"])
        return {
            "input": citation,
            "valid": True,
            "canonical": parsed["canonical"],
            "volume": parsed["volume"],
            "reporter": parsed["reporter"],
            "page": parsed["page"],
            "matches": matches,
        }

    def validate_citation(self, citation: str) -> dict[str, Any]:
        normalized = self.normalize_citation(citation)
        normalized["is_known_locally"] = bool(normalized["matches"])
        normalized["source"] = "local_citation_tables"
        return normalized

    def citation_network(self, opinion_id: int) -> dict[str, list[dict[str, Any]]]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT cited_opinion_id, cited_reporter, cited_volume, cited_page, depth FROM opinion_citations WHERE citing_opinion_id = %s LIMIT 100",
                [opinion_id],
            )
            cited = dict_rows(cur)
            cur.execute(
                "SELECT citing_opinion_id, depth FROM opinion_citations WHERE cited_opinion_id = %s LIMIT 100",
                [opinion_id],
            )
            citing = dict_rows(cur)
            return {"cited": cited, "citing": citing}

    def authority_treatment(self, opinion_id: int) -> dict[str, Any]:
        network = self.citation_network(opinion_id)
        return {
            "opinion_id": opinion_id,
            "cited_authorities": network.get("cited", []),
            "citing_authorities": network.get("citing", []),
            "cited_count": len(network.get("cited", [])),
            "citing_count": len(network.get("citing", [])),
            "positive_signal_count": 0,
            "negative_signal_count": 0,
            "treatment_signal": "unknown",
            "limitations": (
                "Local MVP corpus tracks citation edges but does not classify "
                "positive or negative treatment like a Shepard's service."
            ),
        }

    def court_info(self, court_id: str) -> dict[str, Any] | None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.court_id, c.short_name, c.full_name, c.jurisdiction,
                       COUNT(DISTINCT oc.opinion_id) AS opinion_count,
                       MIN(cl.date_filed) AS first_date,
                       MAX(cl.date_filed) AS last_date
                FROM courts c
                LEFT JOIN opinion_chunks oc ON oc.court_id = c.court_id
                LEFT JOIN opinion_clusters cl ON cl.cluster_id = oc.cluster_id
                WHERE c.court_id = %s
                GROUP BY c.court_id, c.short_name, c.full_name, c.jurisdiction
                """,
                [court_id],
            )
            rows = dict_rows(cur)
            return rows[0] if rows else None

    def court_coverage(
        self,
        court_id: str | None = None,
        jurisdiction: str | None = None,
    ) -> list[dict[str, Any]]:
        filters = ["TRUE"]
        params: list[Any] = []
        if court_id:
            filters.append("c.court_id = %s")
            params.append(court_id)
        if jurisdiction:
            filters.append("c.jurisdiction = %s")
            params.append(jurisdiction)
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT c.court_id, c.short_name, c.full_name, c.jurisdiction,
                       COUNT(DISTINCT d.docket_id) AS docket_count,
                       COUNT(DISTINCT oc.cluster_id) AS cluster_count,
                       COUNT(DISTINCT o.opinion_id) AS opinion_count,
                       COUNT(DISTINCT ch.id) AS chunk_count,
                       MIN(oc.date_filed) AS first_date,
                       MAX(oc.date_filed) AS last_date
                FROM courts c
                LEFT JOIN dockets d ON d.court_id = c.court_id
                LEFT JOIN opinion_clusters oc ON oc.docket_id = d.docket_id
                LEFT JOIN opinions o ON o.cluster_id = oc.cluster_id
                LEFT JOIN opinion_chunks ch ON ch.opinion_id = o.opinion_id
                WHERE {' AND '.join(filters)}
                GROUP BY c.court_id, c.short_name, c.full_name, c.jurisdiction
                ORDER BY opinion_count DESC, c.full_name
                LIMIT 500
                """,
                params,
            )
            return dict_rows(cur)

    def search_dockets(
        self,
        query: str,
        court_id: str | None = None,
        jurisdiction: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        top_k: int = 20,
    ) -> list[dict[str, Any]]:
        filters = ["(d.docket_number ILIKE %s OR d.case_name ILIKE %s OR oc.case_name ILIKE %s)"]
        pattern = f"%{query}%"
        params: list[Any] = [pattern, pattern, pattern]
        if court_id:
            filters.append("d.court_id = %s")
            params.append(court_id)
        if jurisdiction:
            filters.append("c.jurisdiction = %s")
            params.append(jurisdiction)
        if date_from:
            filters.append("oc.date_filed >= %s")
            params.append(date_from)
        if date_to:
            filters.append("oc.date_filed <= %s")
            params.append(date_to)
        params.append(top_k)
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT d.docket_id, d.docket_number, d.case_name, d.court_id, c.full_name AS court_name,
                       c.jurisdiction, COUNT(DISTINCT oc.cluster_id) AS cluster_count,
                       COALESCE(MIN(oc.date_filed), MIN(d.date_filed)) AS first_date,
                       COALESCE(MAX(oc.date_filed), MAX(d.date_filed)) AS last_date,
                       (ARRAY_REMOVE(ARRAY_AGG(DISTINCT oc.case_name), NULL))[1:5] AS case_names
                FROM dockets d
                LEFT JOIN courts c ON c.court_id = d.court_id
                LEFT JOIN opinion_clusters oc ON oc.docket_id = d.docket_id
                WHERE {' AND '.join(filters)}
                GROUP BY d.docket_id, d.docket_number, d.case_name, d.court_id, c.full_name, c.jurisdiction
                ORDER BY last_date DESC NULLS LAST, cluster_count DESC
                LIMIT %s
                """,
                params,
            )
            return dict_rows(cur)

    def export_research_bundle(
        self,
        query: str | None = None,
        opinion_ids: list[int] | None = None,
        cluster_ids: list[int] | None = None,
        top_k: int = 5,
        query_embedding: list[float] | None = None,
    ) -> dict[str, Any]:
        selected_opinion_ids = list(dict.fromkeys(opinion_ids or []))
        selected_cluster_ids = list(dict.fromkeys(cluster_ids or []))
        if query and not selected_opinion_ids and not selected_cluster_ids:
            hits = self.search_caselaw(query=query, top_k=top_k, query_embedding=query_embedding)
            for hit in hits:
                opinion_id = hit.get("opinion_id")
                if opinion_id and opinion_id not in selected_opinion_ids:
                    selected_opinion_ids.append(opinion_id)
        cases = []
        for oid in selected_opinion_ids[:top_k]:
            detail = self.get_full_opinion(opinion_id=oid, include_chunks=True)
            if detail:
                cases.append(detail)
        remaining = max(top_k - len(cases), 0)
        for cid in selected_cluster_ids[:remaining]:
            detail = self.get_full_opinion(cluster_id=cid, include_chunks=True)
            if detail:
                cases.append(detail)
        citations = []
        for case in cases:
            opinion_id = case.get("opinion_id")
            if opinion_id:
                citations.append({"opinion_id": opinion_id, "network": self.citation_network(opinion_id)})
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "query": query,
            "case_count": len(cases),
            "cases": cases,
            "citations": citations,
        }

    def sync_status(self) -> dict[str, Any]:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source, snapshot_date, started_at, completed_at, status,
                       rows_processed, chunks_created, errors
                FROM ingest_runs
                ORDER BY started_at DESC
                LIMIT 10
                """
            )
            ingest_runs = dict_rows(cur)
            cur.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM opinion_chunks WHERE embedding IS NOT NULL)
                  + (SELECT COUNT(*) FROM legal_document_chunks WHERE embedding IS NOT NULL)
                    AS embedded_chunks
                """
            )
            embedded = dict_rows(cur)
            cur.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM opinion_chunks WHERE embedding IS NULL)
                  + (SELECT COUNT(*) FROM legal_document_chunks WHERE embedding IS NULL)
                    AS pending_chunks
                """
            )
            pending = dict_rows(cur)
            cur.execute(
                """
                SELECT source_key, display_name, description, publisher, source_type,
                       jurisdiction, court_id, canonical_url, authority_tier,
                       official_status, ingestion_mode, storage_policy, access_type,
                       license_status, terms_url, sync_frequency, data_format,
                       corpus_table, enabled, priority, coverage_start, coverage_end,
                       coverage_kind,
                       last_attempted_at, last_successful_sync_at, item_count,
                       chunk_count, embedded_chunk_count, parser_version,
                       embedding_model, embedding_version, current_error, metadata
                FROM legal_sources
                ORDER BY source_key
                """
            )
            sources = dict_rows(cur)
            cur.execute(
                """
                SELECT source_key, partition_key, checkpoint_at, cursor_url, status,
                       last_attempted_at, last_successful_sync_at, rows_processed,
                       chunks_created, last_error, metadata
                FROM source_sync_states
                ORDER BY source_key, partition_key
                """
            )
            source_partitions = dict_rows(cur)
        return {
            "ingest_runs": ingest_runs,
            "embedded_chunks": embedded[0]["embedded_chunks"] if embedded else 0,
            "pending_chunks": pending[0]["pending_chunks"] if pending else 0,
            "sources": sources,
            "source_partitions": source_partitions,
        }

    def corpus_status(self) -> dict[str, Any]:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM courts) AS courts,
                    (SELECT COUNT(*) FROM dockets) AS dockets,
                    (SELECT COUNT(*) FROM opinion_clusters) AS clusters,
                    (SELECT COUNT(*) FROM opinions) AS opinions,
                    (SELECT COUNT(*) FROM opinion_chunks) AS chunks,
                    (SELECT COUNT(*) FROM opinion_chunks WHERE embedding IS NOT NULL) AS embedded_chunks,
                    (SELECT COUNT(*) FROM legal_sources) AS legal_sources,
                    (SELECT COUNT(*) FROM legal_documents) AS legal_documents,
                    (SELECT COUNT(*) FROM legal_document_chunks) AS legal_document_chunks,
                    (SELECT COUNT(*) FROM legal_document_chunks WHERE embedding IS NOT NULL)
                        AS embedded_legal_document_chunks,
                    (SELECT MIN(date_filed) FROM opinion_clusters) AS first_date,
                    (SELECT MAX(date_filed) FROM opinion_clusters) AS last_date
                """
            )
            rows = dict_rows(cur)
        status = rows[0] if rows else {}
        status["coverage"] = self.court_coverage()
        return status
