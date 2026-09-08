# Public MCP retrieval timeout remediation plan

Date: 2026-09-07

This plan addresses the public CourtListener MCP service's retrieval and coverage timeouts. It is based on bounded live evidence and read-only inspection of the current repository. It does not authorize production database changes, service restarts, or corpus edits.

## Observed behavior

The public backend MCP health endpoint and API catalog returned HTTP 200. Two search tools timed out after approximately 35 seconds, while `corpus_status` and `get_court_coverage` timed out after approximately 20 seconds. Four validation queries were identified and cancelled using their exact PostgreSQL PID and `query_start`. Separate pre-existing activity showed a search running for more than 43 minutes and an embedding update running for more than 44 minutes. The public corpus was estimated at approximately 3.98 million opinions, 17.15 million opinion chunks, 581,896 legal documents, and 12.54 million legal-document chunks.

Health and catalog success establish process/database liveness only; they do not establish that retrieval or coverage queries are bounded, indexed, or current. The deployed MCP service was approximately 11 days older than this worktree. Current-source query and index observations are hypotheses until the deployed image, schema, and migration state are verified.

## Confirmed application weaknesses

`mcp_server/database.py` opens psycopg2 connections without a connection deadline, statement deadline, lock deadline, or identifying `application_name`. The synchronous `/api/mcp/tools/call` handler runs repository queries without a server-side query budget or explicit timeout-to-HTTP mapping.

When the HTTP client or reverse proxy gives up, the synchronous psycopg2 call can continue running in the worker. There is no request-disconnect hook that cancels the PostgreSQL backend query. This allows abandoned work to remain active and compete with embedding updates and later requests.

`/health` executes only `SELECT 1`, so it can remain healthy while search and coverage are blocked. `corpus_status` performs many live aggregate counts and then invokes court coverage again. `get_court_coverage` performs global `COUNT(DISTINCT ...)` joins over courts, dockets, clusters, opinions, and chunks. These are unbounded reporting operations when exposed directly as request-time tools.

The current repository's caselaw retrieval SQL targets snapshot tables such as `authority_case_chunks`. Existing schema text visibly indexes the legacy `opinion_chunks` table, while snapshot-table FTS/vector/join indexes are not proven by source inspection. This is an index hypothesis only: verify the deployed version and live schema before designing or running an index migration.

## API-specific deadline policy

Apply deadlines in a small first remediation PR, with environment-configured values and safe defaults. These are starting points for acceptance testing:

| API/tool class | Connect | Lock | Statement | Timeout behavior |
| --- | ---: | ---: | ---: | --- |
| `/health` | 2 s | 0.5 s | 1 s | 503 degraded, no raw database error |
| `/api/mcp` catalog | 2 s | 0.5 s | 1 s | 503 unavailable |
| `search_caselaw` | 3 s | 1 s | 10 s | 504 retrieval timeout, rollback/close |
| `search_legal_authorities` | 3 s | 1 s | 10 s | 504 retrieval timeout, rollback/close |
| `get_court_coverage` | 3 s | 1 s | 8 s | 504 unavailable or cached response |
| `corpus_status` | 3 s | 1 s | 8 s | cached/degraded status; no open-ended scan |

The HTTP timeout must remain slightly longer than the PostgreSQL statement deadline so the application can return a deliberate error instead of letting the proxy race the database. Every timed-out connection must be rolled back, closed, and removed from future reuse. Add an `application_name` prefix containing the MCP service and operation class, without credentials, query text, tenant data, or user content.

## Disconnect and cancellation behavior

Register request cancellation for the synchronous execution boundary or move database work to an async-compatible cancellation-aware boundary. On disconnect, call PostgreSQL cancellation on the exact connection, wait for acknowledgement, rollback, and close. A timeout handler alone is insufficient if the backend query continues after the client goes away.

Acceptance testing must prove cessation rather than only observing a 504:

1. Run a deliberately blocking test query through an isolated test connection.
2. Disconnect or exceed the API deadline.
3. Assert the API returns within its budget.
4. Poll `pg_stat_activity` using the recorded `application_name` and backend PID until the query disappears, with a short bounded allowance.
5. Assert no matching active query remains and that a subsequent request succeeds.

Use a dedicated test database or transaction-level lock fixture. Do not use the public corpus for this proof.

## Coverage and status design

Move exact corpus totals and per-court ranges to release-time or ingestion-time summary rows keyed by promoted corpus version. `corpus_status` and `get_court_coverage` should read those bounded summaries and report release timestamp, freshness, and completeness state. A stale summary should produce explicit degraded/attention status, not trigger a live full-corpus scan.

Keep `/health` as a cheap liveness endpoint. Add a separate retrieval-readiness surface that performs only bounded dependency checks and reports whether search, coverage summaries, and the promoted release are available. Do not make health dependent on a global count or sample search.

## Deployed-schema and index verification gate

Before proposing indexes, capture the exact deployed MCP image/version, schema migrations, table names, indexes, and PostgreSQL/pgvector versions. Confirm that the running SQL targets the same tables. Compare `EXPLAIN (FORMAT JSON)` plans on a sanitized staging snapshot for each search and coverage operation; do not run `EXPLAIN ANALYZE` against the public corpus without an approved bounded test window.

Only after that gate should a separate migration PR consider:

- GIN FTS indexes on the actual caselaw and legal-document chunk tables;
- partial HNSW/vector indexes on the actual embedding columns;
- composite corpus-version/join-key indexes used by snapshot joins;
- lineage/source-admission indexes matching the deployed predicates.

Each proposed index needs an explain-plan improvement, build-time estimate, storage estimate, and rollback/drop plan. Index creation must be scheduled separately from embedding updates and follow the repository migration protocol.

## Sequenced PRs and acceptance criteria

1. **Deadline and observability PR.** Add connection, lock, and statement budgets; operation-safe `application_name`; structured timeout mapping; and rollback/close tests. Acceptance: every listed API returns within budget under a blocked test query, exposes no raw SQL/credentials, and leaves no active abandoned query.
2. **Cancellation PR.** Add disconnect-aware cancellation and prove backend query cessation with a controlled fixture. Acceptance: client disconnect and server deadline both terminate the query, and a follow-up request succeeds on a fresh connection.
3. **Bounded status PR.** Add release-version summary rows or an equivalent precomputed projection, then switch status/coverage tools to bounded reads. Acceptance: status and coverage remain below deadline at production scale in staging and report freshness/degraded state explicitly.
4. **Deployed-schema/index PR.** After image/schema verification and staging plans, add only indexes proven necessary for deployed SQL. Acceptance: explain plans use intended indexes, build completes in the approved window, search deadlines hold under representative load, and rollback is documented and tested.
5. **Readiness and runbook PR.** Separate liveness from retrieval readiness, alert on timeout rate and abandoned-query count, and document safe operator steps. Acceptance: health can be 200 while readiness is degraded, and public responses state bounded availability without exposing internal errors.

No production index, cancellation, restart, or corpus operation is part of this report.
