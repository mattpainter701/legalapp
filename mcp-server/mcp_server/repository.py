from __future__ import annotations

import re
from typing import Any

from .database import dict_rows
from .query_embeddings import format_vector_literal

_CITATION_RE = re.compile(r"^\s*(?P<volume>\d+)\s+(?P<reporter>.+?)\s+(?P<page>\d+)\s*$")


class CourtListenerRepository:
    def __init__(self, conn):
        self.conn = conn

    def _search_filters(
        self,
        jurisdiction: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> tuple[list[str], list[Any]]:
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
                   ts_rank_cd(oc.fts, websearch_to_tsquery('english', %s)) AS rank,
                   ts_rank_cd(oc.fts, websearch_to_tsquery('english', %s)) AS keyword_rank,
                   NULL::float AS similarity,
                   'fts' AS search_source
            FROM opinion_chunks oc
            JOIN opinion_clusters cl ON cl.cluster_id = oc.cluster_id
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
                       c.full_name AS court_name, oc.content, oc.fts, oc.embedding
                FROM opinion_chunks oc
                JOIN opinion_clusters cl ON cl.cluster_id = oc.cluster_id
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
                   court_id, court_name, content,
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
                     date_filed, court_id, court_name, content
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
        if not opinion_id and not cluster_id:
            raise ValueError("opinion_id or cluster_id is required")
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

    def search_by_citation(self, citation: str) -> list[dict[str, Any]]:
        match = _CITATION_RE.match(citation)
        if not match:
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
                [match.group("volume"), match.group("reporter"), match.group("page")],
            )
            return dict_rows(cur)

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
