from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .database import dict_rows
from .query_embeddings import format_vector_literal
from .control_plane import cadence_seconds, lag_seconds

_CITATION_RE = re.compile(
    r"^\s*(?P<volume>\d+)\s+(?P<reporter>.+?)\s+(?P<page>\d+)\s*$"
)
_SEARCH_TERM_RE = re.compile(r"[A-Za-z0-9]{3,}")
_SEARCH_STOP_WORDS = {
    "about",
    "and",
    "are",
    "case",
    "for",
    "from",
    "handle",
    "have",
    "how",
    "now",
    "out",
    "state",
    "that",
    "the",
    "their",
    "this",
    "what",
    "when",
    "where",
    "which",
    "with",
}


def broad_legal_websearch_query(query: str) -> str:
    """Turn conversational questions into ranked OR recall for legal corpora.

    ``websearch_to_tsquery`` treats ordinary whitespace as AND. A full chat
    question therefore commonly returned zero rows even when the corpus held
    opinions on the controlling issue. Keep meaningful terms, deduplicate them,
    and let ranking plus the jurisdiction filter determine the best passages.
    """
    terms: list[str] = []
    for token in _SEARCH_TERM_RE.findall(query or ""):
        normalized = token.casefold()
        if normalized in _SEARCH_STOP_WORDS or normalized in terms:
            continue
        terms.append(normalized)
    if not terms:
        return query
    return " OR ".join(terms[:16])


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
        promoted_version = "(SELECT version FROM authority_corpus_versions WHERE status = 'promoted' ORDER BY promoted_at DESC NULLS LAST LIMIT 1)"
        filters = [
            f"oc.corpus_version = {promoted_version}",
            f"cl.corpus_version = {promoted_version}",
        ]
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
        if query_embedding is not None and not self._embedding_matches_promoted(
            query_embedding
        ):
            query_embedding = None
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
        # Only searchable, live catalog content may enter either retrieval path.
        # Keeping these predicates in the shared filter list prevents disabled
        # sources and superseded documents from leaking through FTS or dense
        # candidates.
        filters = [
            "s.enabled IS TRUE",
            "d.document_status IN ('current', 'current_with_supplement')",
            "d.corpus_version = (SELECT version FROM authority_corpus_versions WHERE status = 'promoted' ORDER BY promoted_at DESC NULLS LAST LIMIT 1)",
            "c.corpus_version = (SELECT version FROM authority_corpus_versions WHERE status = 'promoted' ORDER BY promoted_at DESC NULLS LAST LIMIT 1)",
        ]
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
        # A provider response is vector-compatible only when it carries the
        # exact promoted model/version/dimension contract.  Mismatches take
        # the keyword branch before any vector SQL is assembled.
        if query_embedding is not None and not self._embedding_matches_promoted(
            query_embedding
        ):
            query_embedding = None
        fts_query = broad_legal_websearch_query(query)
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
            params = [fts_query, fts_query, fts_query, *filter_params, top_k]
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
                fts_query,
                fts_query,
                fts_query,
                candidate_limit,
                top_k,
            ]
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return dict_rows(cur)

    def _embedding_matches_promoted(self, embedding: list[float]) -> bool:
        model = getattr(embedding, "model", None)
        version = getattr(embedding, "version", None)
        dimension = getattr(embedding, "dimension", len(embedding))
        if not model or version is None:
            return False
        with self.conn.cursor() as cur:
            cur.execute("""SELECT embedding_model, embedding_version, embedding_dimension
                           FROM authority_corpus_versions WHERE status='promoted'
                           ORDER BY promoted_at DESC NULLS LAST LIMIT 1""")
            row = cur.fetchone()
        return bool(
            row
            and row[0] == model
            and str(row[1]) == str(version)
            and int(row[2] or 0) == int(dimension) == len(embedding)
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
        fts_query = broad_legal_websearch_query(query)
        sql = f"""
            SELECT oc.chunk_id::text AS chunk_id, oc.opinion_id, oc.cluster_id, oc.chunk_index,
                   cl.case_name,
                   cl.date_filed, oc.court_id, c.full_name AS court_name, oc.content,
                   COALESCE(NULLIF(o.source_url, ''), '/opinion/' || oc.opinion_id::text || '/') AS source_url,
                   COALESCE(cl.citations #>> '{{0,cite}}', cl.citations #>> '{{0}}', '') AS citation,
                   ts_rank_cd(oc.fts, websearch_to_tsquery('english', %s)) AS rank,
                   ts_rank_cd(oc.fts, websearch_to_tsquery('english', %s)) AS keyword_rank,
                   NULL::float AS similarity,
                   'fts' AS search_source
            FROM authority_case_chunks oc
            JOIN authority_case_clusters cl ON cl.corpus_version = oc.corpus_version AND cl.cluster_id = oc.cluster_id
            LEFT JOIN authority_case_opinions o ON o.corpus_version = oc.corpus_version AND o.opinion_id = oc.opinion_id
            LEFT JOIN courts c ON c.court_id = oc.court_id
            WHERE oc.fts @@ websearch_to_tsquery('english', %s)
              AND {' AND '.join(filters)}
            ORDER BY rank DESC, cl.date_filed DESC NULLS LAST
            LIMIT %s
        """
        params = [fts_query, fts_query, fts_query, *filter_params, top_k]
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
        fts_query = broad_legal_websearch_query(query)
        vector = format_vector_literal(query_embedding)
        candidate_limit = min(max(top_k * 4, 20), 200)
        sql = f"""
            WITH filtered AS (
                SELECT oc.chunk_id::text AS chunk_id, oc.opinion_id, oc.cluster_id,
                       oc.chunk_index, cl.case_name, cl.date_filed, oc.court_id,
                       c.full_name AS court_name, oc.content,
                       COALESCE(NULLIF(o.source_url, ''), '/opinion/' || oc.opinion_id::text || '/') AS source_url,
                       COALESCE(cl.citations #>> '{{0,cite}}', cl.citations #>> '{{0}}', '') AS citation,
                       oc.fts, oc.embedding
                FROM authority_case_chunks oc
                JOIN authority_case_clusters cl ON cl.corpus_version = oc.corpus_version AND cl.cluster_id = oc.cluster_id
                LEFT JOIN authority_case_opinions o ON o.corpus_version = oc.corpus_version AND o.opinion_id = oc.opinion_id
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
            fts_query,
            fts_query,
            fts_query,
            candidate_limit,
            top_k,
        ]
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return dict_rows(cur)

    def case_details(
        self, opinion_id: int | None = None, cluster_id: int | None = None
    ) -> dict[str, Any] | None:
        if bool(opinion_id) == bool(cluster_id):
            raise ValueError("exactly one of opinion_id or cluster_id is required")
        where = "o.opinion_id = %s" if opinion_id else "o.cluster_id = %s"
        value = opinion_id or cluster_id
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT o.opinion_id, o.cluster_id, o.source_url, cl.case_name,
                       cl.date_filed, cl.citations, d.docket_number, c.court_id, c.full_name AS court_name
                FROM authority_case_opinions o
                JOIN authority_case_clusters cl ON cl.corpus_version = o.corpus_version AND cl.cluster_id = o.cluster_id
                LEFT JOIN dockets d ON d.docket_id = cl.docket_id
                LEFT JOIN courts c ON c.court_id = d.court_id
                WHERE {where}
                  AND o.corpus_version = (SELECT version FROM authority_corpus_versions WHERE status='promoted' ORDER BY promoted_at DESC NULLS LAST LIMIT 1)
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
                SELECT chunk_id::text AS chunk_id, chunk_index, content
                FROM authority_case_chunks
                WHERE opinion_id = %s
                  AND corpus_version = (SELECT version FROM authority_corpus_versions WHERE status='promoted' ORDER BY promoted_at DESC NULLS LAST LIMIT 1)
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
        result = {key: value for key, value in detail.items() if key != "chunks"}
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
                    """
                    SELECT opinion_id, content
                    FROM authority_case_chunks
                    WHERE chunk_id = %s
                      AND corpus_version = (
                          SELECT version FROM authority_corpus_versions
                          WHERE status='promoted'
                          ORDER BY promoted_at DESC NULLS LAST LIMIT 1
                      )
                    LIMIT 1
                    """,
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
            row
            for row in results
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
                FROM authority_case_citations cit
                JOIN authority_case_clusters cl ON cl.corpus_version = cit.corpus_version AND cl.cluster_id = cit.cited_cluster_id
                LEFT JOIN authority_case_opinions o ON o.corpus_version = cit.corpus_version AND o.opinion_id = cit.cited_opinion_id
                LEFT JOIN authority_case_chunks ch ON ch.corpus_version = cit.corpus_version AND ch.cluster_id = cl.cluster_id
                LEFT JOIN dockets d ON d.docket_id = cl.docket_id
                LEFT JOIN courts c ON c.court_id = d.court_id
                WHERE cit.cited_volume = %s AND cit.cited_reporter = %s AND cit.cited_page = %s
                  AND cit.corpus_version = (SELECT version FROM authority_corpus_versions WHERE status='promoted' ORDER BY promoted_at DESC NULLS LAST LIMIT 1)
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
                """SELECT cit.cited_opinion_id, cit.cited_reporter, cit.cited_volume, cit.cited_page, cit.depth
                   FROM authority_case_citations cit
                   WHERE cit.citing_opinion_id = %s AND cit.corpus_version = (SELECT version FROM authority_corpus_versions WHERE status='promoted' ORDER BY promoted_at DESC NULLS LAST LIMIT 1) LIMIT 100""",
                [opinion_id],
            )
            cited = dict_rows(cur)
            cur.execute(
                """SELECT cit.citing_opinion_id, cit.depth
                   FROM authority_case_citations cit
                   WHERE cit.cited_opinion_id = %s AND cit.corpus_version = (SELECT version FROM authority_corpus_versions WHERE status='promoted' ORDER BY promoted_at DESC NULLS LAST LIMIT 1) LIMIT 100""",
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
                LEFT JOIN authority_case_chunks oc ON oc.court_id = c.court_id
                LEFT JOIN authority_case_clusters cl ON cl.corpus_version = oc.corpus_version AND cl.cluster_id = oc.cluster_id
                WHERE c.court_id = %s
                  AND oc.corpus_version = (SELECT version FROM authority_corpus_versions WHERE status='promoted' ORDER BY promoted_at DESC NULLS LAST LIMIT 1)
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
        promoted = "(SELECT version FROM authority_corpus_versions WHERE status='promoted' ORDER BY promoted_at DESC NULLS LAST LIMIT 1)"
        filters = [f"oc.corpus_version = {promoted}", f"ch.corpus_version = {promoted}"]
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
                       COUNT(DISTINCT ch.chunk_id) AS chunk_count,
                       MIN(oc.date_filed) AS first_date,
                       MAX(oc.date_filed) AS last_date
                FROM courts c
                LEFT JOIN dockets d ON d.court_id = c.court_id
                LEFT JOIN authority_case_clusters oc ON oc.docket_id = d.docket_id AND oc.corpus_version = {promoted}
                LEFT JOIN authority_case_opinions o ON o.corpus_version = oc.corpus_version AND o.opinion_id IN (SELECT opinion_id FROM authority_case_chunks WHERE corpus_version=oc.corpus_version AND cluster_id=oc.cluster_id)
                LEFT JOIN authority_case_chunks ch ON ch.corpus_version = oc.corpus_version AND ch.cluster_id = oc.cluster_id
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
        filters = [
            "(d.docket_number ILIKE %s OR d.case_name ILIKE %s OR oc.case_name ILIKE %s)"
        ]
        promoted = "(SELECT version FROM authority_corpus_versions WHERE status='promoted' ORDER BY promoted_at DESC NULLS LAST LIMIT 1)"
        filters.append(f"oc.corpus_version = {promoted}")
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
                LEFT JOIN authority_case_clusters oc ON oc.docket_id = d.docket_id AND oc.corpus_version = {promoted}
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
            hits = self.search_caselaw(
                query=query, top_k=top_k, query_embedding=query_embedding
            )
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
                citations.append(
                    {
                        "opinion_id": opinion_id,
                        "network": self.citation_network(opinion_id),
                    }
                )
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
                    (SELECT COUNT(*) FROM authority_case_chunks ac
                     WHERE ac.embedding IS NOT NULL AND ac.corpus_version = (
                       SELECT version FROM authority_corpus_versions WHERE status='promoted'
                       ORDER BY promoted_at DESC NULLS LAST LIMIT 1))
                  + (SELECT COUNT(*) FROM legal_document_chunks c
                     JOIN legal_documents d ON d.id=c.document_id
                     WHERE c.embedding IS NOT NULL AND d.corpus_version = (
                       SELECT version FROM authority_corpus_versions WHERE status='promoted'
                       ORDER BY promoted_at DESC NULLS LAST LIMIT 1))
                    AS embedded_chunks
                """
            )
            embedded = dict_rows(cur)
            cur.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM authority_case_chunks ac
                     WHERE ac.embedding IS NULL AND ac.corpus_version = (
                       SELECT version FROM authority_corpus_versions WHERE status='promoted'
                       ORDER BY promoted_at DESC NULLS LAST LIMIT 1))
                  + (SELECT COUNT(*) FROM legal_document_chunks c
                     JOIN legal_documents d ON d.id=c.document_id
                     WHERE c.embedding IS NULL AND d.corpus_version = (
                       SELECT version FROM authority_corpus_versions WHERE status='promoted'
                       ORDER BY promoted_at DESC NULLS LAST LIMIT 1))
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
        status = {
            "ingest_runs": ingest_runs,
            "embedded_chunks": embedded[0]["embedded_chunks"] if embedded else 0,
            "pending_chunks": pending[0]["pending_chunks"] if pending else 0,
            "sources": sources,
            "source_partitions": source_partitions,
        }
        # Keep the established sync_status contract while adding the richer
        # claim-safe projection for deployments that have the control-plane
        # tables.  Old databases still receive the legacy status payload.
        try:
            status["authority_coverage"] = self.authority_coverage()
        except Exception:
            status["authority_coverage"] = None
        return status

    def corpus_status(self) -> dict[str, Any]:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM courts) AS courts,
                    (SELECT COUNT(*) FROM dockets) AS dockets,
                    (SELECT COUNT(*) FROM authority_case_clusters cl WHERE cl.corpus_version = (
                       SELECT version FROM authority_corpus_versions WHERE status='promoted'
                       ORDER BY promoted_at DESC NULLS LAST LIMIT 1)) AS clusters,
                    (SELECT COUNT(*) FROM authority_case_opinions o WHERE o.corpus_version = (
                       SELECT version FROM authority_corpus_versions WHERE status='promoted'
                       ORDER BY promoted_at DESC NULLS LAST LIMIT 1)) AS opinions,
                    (SELECT COUNT(*) FROM authority_case_chunks ch WHERE ch.corpus_version = (
                       SELECT version FROM authority_corpus_versions WHERE status='promoted'
                       ORDER BY promoted_at DESC NULLS LAST LIMIT 1)) AS chunks,
                    (SELECT COUNT(*) FROM authority_case_chunks ch
                     WHERE ch.embedding IS NOT NULL AND ch.corpus_version = (
                       SELECT version FROM authority_corpus_versions WHERE status='promoted'
                       ORDER BY promoted_at DESC NULLS LAST LIMIT 1)) AS embedded_chunks,
                    (SELECT COUNT(*) FROM legal_sources) AS legal_sources,
                    (SELECT COUNT(*) FROM legal_documents) AS legal_documents,
                    (SELECT COUNT(*) FROM legal_document_chunks) AS legal_document_chunks,
                    (SELECT COUNT(*) FROM legal_document_chunks WHERE embedding IS NOT NULL)
                        AS embedded_legal_document_chunks,
                    (SELECT MIN(date_filed) FROM authority_case_clusters cl WHERE cl.corpus_version = (
                       SELECT version FROM authority_corpus_versions WHERE status='promoted'
                       ORDER BY promoted_at DESC NULLS LAST LIMIT 1)) AS first_date,
                    (SELECT MAX(date_filed) FROM authority_case_clusters cl WHERE cl.corpus_version = (
                       SELECT version FROM authority_corpus_versions WHERE status='promoted'
                       ORDER BY promoted_at DESC NULLS LAST LIMIT 1)) AS last_date
                """
            )
            rows = dict_rows(cur)
            cur.execute(
                """
                SELECT source_key, partition_key, expected_coverage, expected_item_count,
                       acquisition_state, snapshot_date, source_release, rows_loaded,
                       chunks_loaded, vectors_loaded, bytes_loaded, first_document_date,
                       last_document_date, upstream_modified_at, last_checked_at,
                       stale_after, gap_reason, owner, metadata, updated_at
                FROM corpus_coverage_ledger
                ORDER BY source_key, partition_key
                """
            )
            coverage_ledger = dict_rows(cur)
        status = rows[0] if rows else {}
        status["coverage"] = self.court_coverage()
        status["coverage_ledger"] = coverage_ledger
        return status

    def authority_coverage(self) -> dict[str, Any]:
        """Return claim-safe, versioned public-authority coverage evidence.

        The projection contains metadata only.  It deliberately never selects
        tenant ids, private document bodies, or query text.
        """
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT version, status, manifest_hash, as_of, created_at,
                       promoted_at, rolled_back_at, rollback_of, reason,
                       embedding_model, embedding_version, embedding_dimension
                FROM authority_corpus_versions
                WHERE status = 'promoted'
                ORDER BY promoted_at DESC NULLS LAST, created_at DESC
                LIMIT 1
            """)
            version_rows = dict_rows(cur)
            version_key = version_rows[0]["version"] if version_rows else ""
            cur.execute("""
                SELECT source_key, display_name, publisher, source_type,
                       jurisdiction, authority_tier, source_tier, official_status,
                       canonical_url, coverage_start, coverage_end, coverage_kind,
                       last_successful_sync_at, item_count, chunk_count,
                       embedded_chunk_count, current_error, rights_decision,
                       geographic_scope, temporal_scope, expected_cadence,
                       completeness_caveats, claim_safe_wording, reviewed_at,
                       reviewed_by
                FROM legal_sources
                WHERE storage_policy <> 'prohibited'
                  AND enabled IS TRUE
                  AND rights_decision IN ('official', 'open', 'licensed')
                  AND reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL
                  AND claim_safe_wording IS NOT NULL
                  AND metadata->>'catalog_schema_version' IS NOT NULL
                  AND metadata->>'implementation_status' IS NOT NULL
                  AND source_key NOT LIKE 'tenant:%'
                  AND source_key NOT LIKE 'firm:%'
                  AND source_key NOT LIKE 'private:%'
                ORDER BY priority, source_key
            """)
            sources = dict_rows(cur)
            cur.execute(
                """
                SELECT cp.source_key, cp.partition_key, cp.corpus_version, cp.cursor_url,
                       cp.status, cp.updated_at, cp.last_successful_harvest_at,
                       cp.retry_count, cp.next_retry_at, cp.dead_letter_at
                FROM authority_harvest_checkpoints cp
                JOIN legal_sources s ON s.source_key = cp.source_key
                WHERE corpus_version = %s
                  AND s.enabled IS TRUE
                  AND s.rights_decision IN ('official', 'open', 'licensed')
                  AND s.reviewed_at IS NOT NULL AND s.reviewed_by IS NOT NULL
                  AND s.claim_safe_wording IS NOT NULL
                  AND s.storage_policy <> 'prohibited'
                  AND s.metadata->>'catalog_schema_version' IS NOT NULL
                  AND s.metadata->>'implementation_status' IS NOT NULL
                  AND s.source_key NOT LIKE 'tenant:%'
                  AND s.source_key NOT LIKE 'firm:%'
                  AND s.source_key NOT LIKE 'private:%'
                ORDER BY source_key, partition_key
            """,
                [version_key],
            )
            partitions = dict_rows(cur)
            cur.execute(
                """
                SELECT source_key, partition_key, source_release AS corpus_version,
                       NULL AS cursor_url, acquisition_state AS status, updated_at,
                       CASE WHEN acquisition_state IN ('complete', 'indexed')
                            THEN last_checked_at ELSE NULL END
                            AS last_successful_harvest_at, 0 AS retry_count,
                       NULL AS next_retry_at, NULL AS dead_letter_at
                FROM corpus_coverage_ledger
                WHERE source_release = %s
                ORDER BY source_key, partition_key
            """,
                [version_key],
            )
            partitions.extend(dict_rows(cur))
            partition_index = {}
            for partition in partitions:
                key = (partition["source_key"], partition["partition_key"])
                existing = partition_index.get(key)
                if existing is None or existing["corpus_version"] == version_key:
                    partition_index[key] = partition
            partitions = list(partition_index.values())
            cur.execute(
                """
                SELECT d.source_key, COUNT(DISTINCT d.id) AS item_count,
                       COUNT(c.id) AS chunk_count,
                       COUNT(c.id) FILTER (WHERE c.embedding IS NOT NULL) AS embedded_chunk_count
                FROM legal_documents d
                LEFT JOIN legal_document_chunks c ON c.document_id=d.id
                WHERE d.corpus_version=%s
                GROUP BY d.source_key
            """,
                [version_key],
            )
            version_counts = {row["source_key"]: row for row in dict_rows(cur)}
            cur.execute(
                """
                SELECT 'courtlistener:ohio-caselaw' AS source_key,
                       COUNT(DISTINCT opinion_id) AS item_count,
                       COUNT(*) AS chunk_count,
                       COUNT(*) FILTER (WHERE embedding IS NOT NULL) AS embedded_chunk_count
                FROM authority_case_chunks
                WHERE corpus_version = %s
            """,
                [version_key],
            )
            case_counts = dict_rows(cur)
            if case_counts and case_counts[0]["chunk_count"]:
                version_counts[case_counts[0]["source_key"]] = case_counts[0]
            cur.execute(
                """
                SELECT corpus_version, audit_kind, thresholds, result, passed,
                       sampled_at, auditor, immutable_hash
                FROM authority_audits
                WHERE corpus_version = %s
                ORDER BY sampled_at DESC, id DESC
                LIMIT 50
            """,
                [version_key],
            )
            audits = dict_rows(cur)
        version = version_rows[0] if version_rows else None
        latest_audits = {}
        for audit in audits:
            latest_audits.setdefault(audit["audit_kind"], audit)
        required_audits = {"release", "completeness", "freshness", "isolation"}
        passed_release = (
            bool(version)
            and required_audits.issubset(latest_audits)
            and all(bool(latest_audits[k]["passed"]) for k in required_audits)
        )
        partition_by_source = {}
        for partition in partitions:
            partition_by_source.setdefault(partition["source_key"], []).append(
                partition
            )
        for source in sources:
            cadence = str(source.get("expected_cadence") or "").lower()
            cadence_window = cadence_seconds(cadence)
            source_partitions = partition_by_source.get(source["source_key"], [])
            successful_syncs = [
                row["last_successful_harvest_at"]
                for row in source_partitions
                if row["last_successful_harvest_at"]
            ]
            # Worst-partition semantics: a fresh partition cannot mask a
            # missing or stale required partition in the same public source.
            last_sync = min(successful_syncs, default=None)
            lag = (
                lag_seconds(last_sync, cadence_window)
                if last_sync and cadence_window
                else None
            )
            failed_partition = any(
                row["status"]
                in {
                    "retryable",
                    "retryable_failure",
                    "quarantined",
                    "dead_letter",
                    "failed",
                }
                for row in source_partitions
            )
            missing_partition = (
                not source_partitions
                or len(successful_syncs) != len(source_partitions)
                or last_sync is None
            )
            source["lag_seconds"] = lag
            if source["source_key"] in version_counts:
                counts = version_counts[source["source_key"]]
                source["item_count"] = counts["item_count"]
                source["chunk_count"] = counts["chunk_count"]
                source["embedded_chunk_count"] = counts["embedded_chunk_count"]
            source["last_successful_sync_at"] = last_sync
            source["stale"] = (
                missing_partition
                or cadence_window is None
                or bool(lag is not None and lag > 0)
            )
            source["status"] = (
                "failed"
                if failed_partition
                else (
                    "unreviewed"
                    if (
                        not source.get("reviewed_at")
                        or source.get("rights_decision")
                        in {"pending_review", "prohibited"}
                        or cadence_window is None
                    )
                    else ("stale" if source["stale"] else "healthy")
                )
            )
            source["claim_state"] = (
                "suppressed"
                if source["status"] != "healthy" or not passed_release
                else (
                    "limited"
                    if source.get("coverage_kind") != "complete"
                    else "supported"
                )
            )
        return {
            "available": bool(version),
            "namespace": "public-authority",
            "corpus_version": version,
            "claim_state": "supported" if passed_release else "suppressed",
            "claim_notice": "Named-source, bounded coverage only; this is not a complete or good-law determination.",
            "sources": sources,
            "partitions": partitions,
            "audits": audits,
            "latest_audits": list(latest_audits.values()),
        }
