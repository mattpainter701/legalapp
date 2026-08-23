# CourtListener MCP Operations Handoff

This is the operator handoff for the LegalApp CourtListener MCP stack. It
captures the live topology, data-loading workflow, embedding workflow, current
state, and failure modes discovered during the first production bring-up.

## Source Of Truth

- Architecture/spec: `docs/legal_rag.md`
- Build/runbook: `docs/courtlistener_mcp_jetson.md`
- Compose stack: `docker-compose.courtlistener-mcp.yml`
- MCP package: `mcp-server/`
- Main backend proxy: `backend/app/routers/mcp.py`
- Production app env: `/home/varta/legalapp/.env` on the hypervisor
- Keep concrete hostnames, IPs, usernames, and passwords in env files only.
  Do not copy secrets into docs, memory, or task files.

## Live Topology

- Main LegalApp stack runs separately from the CourtListener stack.
- CourtListener stack services:
  - `courtlistener-db`: pgvector Postgres, public legal data only.
  - `courtlistener-mcp`: REST MCP server on port 8021 inside the Docker network.
  - `courtlistener-loader`: one-shot S3 loader/chunker.
  - `courtlistener-sync`: placeholder for later low-volume REST sync.
  - `embedding-dispatcher`: launches Jetson embedding workers over SSH.
- Main backend uses `MCP_SERVER_URL=http://courtlistener-mcp:8021` with a
  dedicated `MCP_UPSTREAM_API_KEY`. Internal chat can query the private engine;
  public `/api/mcp` remains unavailable while `MCP_PRODUCT_ENABLED=false`.
- Jetsons are embedding workers only. Do not mount Jetson storage as the live
  Postgres data directory.

## Volumes And Space

Production volumes currently used:

- `legalapp_courtlistener_pgdata`: CourtListener Postgres data.
- `legalapp_courtlistener_bulk`: staged compressed CourtListener S3 snapshot.

Current checkpoint from 2026-06-26:

- Postgres logical DB size: about 164 MB after expanding to 500 opinions,
  5,024 chunks, and vector index metadata.
- Postgres Docker volume: small relative to the raw cache.
- S3 staging volume: about 58 GB compressed archives.
- Docker LV had about 1.2 TB free during bring-up.

The 58 GB staged archive is not the live searchable corpus. It is compressed
source input. Searchable rows exist only after the loader imports opinions and
`--chunk-opinions` creates `opinion_chunks`.

Do not equate available space with permission to run an unbounded national
import. The current scale target is the published federal appellate profile,
with explicit row ceilings and database-size checks before and after each
stage. The full raw snapshot remains larger and noisier than the first useful
research corpus.

## Start And Health Checks

Use the `legalapp` Compose project name so volume names match production:

```bash
cd /home/varta/legalapp
docker compose -p legalapp -f docker-compose.courtlistener-mcp.yml up -d courtlistener-db courtlistener-mcp
docker ps --filter name=legalapp-courtlistener --format "{{.Names}} {{.Status}}"
curl -sf http://127.0.0.1:8021/health
curl -sf -H "X-Clarity-Internal-Key: $MCP_UPSTREAM_API_KEY" \
  http://127.0.0.1:8021/api/mcp
```

Main app health:

```bash
curl -sf http://localhost/health
curl -s -o /dev/null -w "%{http_code}\n" https://getlawhand.com/health
```

## Data Loading Workflow

Preferred staged flow:

```bash
cd /home/varta/legalapp

docker compose -p legalapp -f docker-compose.courtlistener-mcp.yml --profile loader run --rm \
  courtlistener-loader python -m mcp_server.loader --stage-latest

docker run --rm --network legalapp_default \
  -v legalapp_courtlistener_bulk:/data/courtlistener \
  -e VECTORDB_URL=postgresql://courtlistener:<password>@courtlistener-db:5432/courtlistener \
  legalapp-courtlistener-loader:latest \
  python -m mcp_server.loader --load-mvp --mvp-states ND,MT,MN,SD \
    --docket-limit 50000 --cluster-limit 2000 --opinion-limit 500 --citation-limit 0

docker run --rm --network legalapp_default \
  -e VECTORDB_URL=postgresql://courtlistener:<password>@courtlistener-db:5432/courtlistener \
  legalapp-courtlistener-loader:latest \
  python -m mcp_server.loader --chunk-opinions --limit 1000000
```

Use direct `docker run` for long loader jobs after the image exists. During
bring-up, `docker compose run` was convenient but created project/orphan noise.
Avoid `--remove-orphans` with the CourtListener compose file because the main
LegalApp services are not defined there and must not be removed.

The reverse is also true: do not run the main `docker-compose.hypervisor.yml`
deploy with `--remove-orphans` unless the CourtListener compose file is included
in the same command. `courtlistener-db` and `courtlistener-mcp` are sidecar
services in the same `legalapp` project/network, so a main-stack-only orphan
cleanup removes them and breaks `MCP_SERVER_URL=http://courtlistener-mcp:8021`.
If that happens, confirm `COURTLISTENER_DB_PASSWORD` exists in
`/home/varta/legalapp/.env`, then restart the sidecar with:

```bash
cd /home/varta/legalapp
docker compose -p legalapp -f docker-compose.courtlistener-mcp.yml --env-file .env up -d courtlistener-db courtlistener-mcp
```

Official non-case-law authorities run in the same private pgvector database through a
separate profile. Configure `LEGAL_SOURCE_USER_AGENT`, the Ohio crawler contact and
authorization-basis variables, then start the overlap-protected daily scheduler:

```bash
docker compose -p legalapp -f docker-compose.courtlistener-mcp.yml --env-file .env \
  --profile authority-sync up -d --build legal-authority-sync
docker compose -p legalapp -f docker-compose.courtlistener-mcp.yml logs -f legal-authority-sync
```

For a bounded preflight before enabling the scheduler, run an adapter with `--preview`
inside the image. Production syncs seed the source catalog and schema, upsert by stable
source identity, invalidate changed chunks for re-embedding, checkpoint interrupted
runs, retain prior statute/regulation releases as `superseded`, and expose only current
authority versions through normal retrieval.

Current MVP filter behavior:

- State focus: ND, MT, MN, SD.
- Specialty: U.S. Tax Court and regional bankruptcy/BAP courts.
- SCOTUS included.
- Default keeps published/precedential clusters.
- Use `--include-unpublished` only for a deliberate expansion.

## Current Corpus Checkpoint

## Corpus Coverage Inventory

`corpus_status` now includes a `coverage_ledger` in addition to global counts
and per-court coverage. Each CourtListener court partition records its observed
opinions, chunks, embedded vectors, date range, and acquisition state. Official
authority source partitions continue to be tracked by `legal_sources` and
`source_sync_states`.

Use this inventory to answer whether a court, title, agency source, or time
period is loaded, partial, stale, or intentionally absent. A successful sync is
not itself evidence of complete coverage; consult the declared source partition
and its observed counts.

## Guarded Bulk Expansion

Never run `--load-staged` against production. Use `--load-mvp` with the
existing coverage profile and optional repeatable `--court-id` values, plus a
logical database ceiling:

```bash
python -m mcp_server.loader --load-mvp \
  --coverage-profile national-priority \
  --court-id ca8 \
  --max-database-gb 350
```

The loader checks the PostgreSQL logical database size as it processes the
tranche and stops before the configured ceiling. Start with a bounded
opinion/chunk tranche, wait for the Jetson embedding queue to drain, inspect
`corpus_status`, then raise the budget only after storage and retrieval checks
pass.

### High-throughput bulk settings

The loader batches 500 database writes by default and commits each batch. It
also runs `lbzip2` with eight decompression threads by default. These are
throughput defaults, not a reason to launch duplicate loaders. On Skynet they
use otherwise-idle CPU without rescanning the same archive. Tune only after
observing host pressure:

```bash
COURTLISTENER_DECOMPRESS_THREADS=8
COURTLISTENER_WRITE_BATCH_SIZE=500
```

Do not start multiple loaders over the same bulk CSV to chase throughput: each
worker would rescan the entire compressed archive and contend on the same
Postgres tables. Parallelize only independent source partitions after their
parent docket/cluster stage has completed.

As of 2026-06-26 after expansion:

- `dockets`: 50,000
- `opinion_clusters`: 2,103
- `opinions`: 500
- `opinion_chunks`: 5,024
- `opinion_citations`: 5,228 local citation edges after the bounded
  citation-map pass.
- Embeddings: 5,024 embedded, 0 unembedded, all 1024-dim
  `mixedbread-ai/mxbai-embed-large-v1` vectors.
- Vector index: `ix_opinion_chunks_embedding_hnsw` on
  `opinion_chunks.embedding vector_cosine_ops` where embedding is present.

Chunk distribution after expansion:

- Montana Supreme Court: 269 opinions, 2,405 chunks.
- Supreme Court of Minnesota: 108 opinions, 700 chunks.
- SCOTUS: 57 opinions, 951 chunks.
- North Dakota Supreme Court: 39 opinions, 506 chunks.
- U.S. Tax Court: 13 opinions, 291 chunks.
- South Dakota Supreme Court: 9 opinions, 106 chunks.
- Minnesota Court of Appeals: 2 opinions, 30 chunks.
- Eighth Circuit BAP: 2 opinions, 11 chunks.
- Ninth Circuit BAP: 1 opinion, 24 chunks.

Count and size checks:

```sql
SELECT COUNT(*) FROM dockets;
SELECT COUNT(*) FROM opinion_clusters;
SELECT COUNT(*) FROM opinions;
SELECT COUNT(*) FROM opinion_chunks;
SELECT COUNT(*) FROM opinion_chunks WHERE embedding IS NOT NULL;
SELECT COUNT(*) FROM opinion_chunks WHERE embedding IS NULL;
SELECT vector_dims(embedding), COUNT(*)
FROM opinion_chunks
WHERE embedding IS NOT NULL
GROUP BY 1;
SELECT indexname FROM pg_indexes WHERE tablename = 'opinion_chunks' ORDER BY 1;
SELECT pg_size_pretty(pg_database_size(current_database())) AS db_size;
```

## Embedding Workflow

Jetson worker expectations:

- Model: `mixedbread-ai/mxbai-embed-large-v1`
- Dimensions: 1024
- Batch size: 32 until benchmarking proves higher is safe
- Target table: `opinion_chunks`
- Worker files on Jetson: `/data/legalapp-embeddings`
- Logs: `~/clarity-legal-logs/courtlistener_worker_<n>.log`

Hypervisor `.env` needs these variables for dispatcher launch:

```env
JETSON_3_HOST=<jetson-host-or-ip>
JETSON_3_USER=<jetson-user>
JETSON_3_PASSWORD=<temporary-password-if-ssh-key-not-ready>
JETSON_SCRIPT_DIR=/data/legalapp-embeddings/scripts
JETSON_DB_REVERSE_TUNNEL=true
JETSON_DB_TUNNEL_REMOTE_PORT_BASE=15434
BATCH_SIZE=32
```

Preferred scheduled launch:

```bash
cd /home/varta/legalapp
docker compose -f docker-compose.courtlistener-mcp.yml --profile embedding-scheduler up -d embedding-scheduler
docker compose -f docker-compose.courtlistener-mcp.yml logs -f embedding-scheduler
```

MVP runtime posture: the scheduler is built and validated but should normally
remain stopped on current test hardware. Start it intentionally only after a
bounded import creates new `embedding IS NULL` chunks, then stop it again after
the queue drains.

```bash
cd /home/varta/legalapp
docker compose -p legalapp -f docker-compose.courtlistener-mcp.yml --profile embedding-scheduler stop embedding-scheduler
docker compose -p legalapp -f docker-compose.courtlistener-mcp.yml --profile embedding-scheduler rm -f embedding-scheduler
```

Scheduler behavior:

- Runs every `EMBEDDING_SCHEDULER_INTERVAL_SECONDS` seconds, default 900.
- After a failed SSH/worker session, retries with capped exponential backoff.
  `EMBEDDING_SCHEDULER_RETRY_INITIAL_SECONDS` defaults to 60 and
  `EMBEDDING_SCHEDULER_RETRY_MAX_SECONDS` defaults to 900.
- SSH sessions use connection timeout and server-alive probes so a dead Jetson
  session returns control to the scheduler instead of hanging indefinitely.
- Scheduler errors redact database URL credentials and password assignments.
  Do not add raw command arguments to scheduler or dispatcher exception logs.
- Schedules embeddings for chunks already present in `opinion_chunks`; it does
  not download or import new CourtListener bulk/API data.
- Uses `SCHEDULER_DB_URL` for its own lock/count queries inside Docker and
  passes `VECTORDB_URL`/`EMBEDDING_WORKER_DB_URL` to Jetson workers.
- Runs with host networking so reverse-tunnel dispatch can reach the same
  host-bound CourtListener DB port used by manual dispatcher runs.
- Takes Postgres advisory lock `EMBEDDING_SCHEDULER_LOCK_ID`, default
  `2026062901`, so duplicate scheduler containers or manual runs do not launch
  overlapping Jetson workers.
- Counts `opinion_chunks WHERE embedding IS NULL`.
- Skips when the count is below `EMBEDDING_SCHEDULER_MINIMUM_UNEMBEDDED`,
  default 1.
- Launches the existing Jetson dispatcher when there is queued work, then sleeps
  and checks again.

Manual one-shot dispatcher launch for bounded backfills or recovery:

```bash
docker run -d --name legalapp-embedding-dispatcher-expand \
  --network host \
  --env-file /home/varta/legalapp/.env \
  -e VECTORDB_URL=postgresql://courtlistener:<password>@<hypervisor-db-host>:5434/courtlistener \
  legalapp-courtlistener-loader:latest \
  python -m mcp_server.dispatcher --reverse-tunnel
```

Monitor progress:

```bash
docker compose -f /home/varta/legalapp/docker-compose.courtlistener-mcp.yml ps embedding-scheduler
docker ps --filter name=legalapp-embedding-dispatcher-expand --format "{{.Names}} {{.Status}}"
docker exec -i legalapp-courtlistener-db-1 psql -U courtlistener -d courtlistener -P pager=off -c \
  "SELECT COUNT(*) chunks,
          COUNT(*) FILTER (WHERE embedding IS NOT NULL) embedded,
          COUNT(*) FILTER (WHERE embedding IS NULL) unembedded
   FROM opinion_chunks;"
```

If embedding stalls:

- Check scheduler and dispatcher container logs.
- Check Jetson SSH reachability from the hypervisor, not from the workstation.
- Check Jetson worker logs in `~/clarity-legal-logs`.
- Confirm the reverse tunnel is active if the Jetson cannot initiate DB traffic.
- Confirm `VECTORDB_URL` points to the externally bound CourtListener DB port
  for dispatcher launch, and to the tunnel local port for the worker command.
- Confirm the scheduler is not reporting `lock_held`; if it is, another
  scheduler/dispatcher run is active or a stale advisory lock is held by a live
  connection.

## Query Embedding Service

Runtime hybrid search needs a query vector for each search prompt. That is
served by a small FastAPI app on the Jetson, using the same mxbai model as the
chunk worker.

Hypervisor `.env` keys:

```env
MCP_QUERY_EMBEDDING_URL=http://<jetson-lan-host>:8031/embed
MCP_QUERY_EMBEDDING_MODEL=mixedbread-ai/mxbai-embed-large-v1
MCP_QUERY_EMBEDDING_TIMEOUT_SECONDS=20
```

Jetson service command used during bring-up:

```bash
PYTHONPATH=/data/pip_packages:/data/legalapp-embeddings/app \
HF_HOME=/data/legalapp-embeddings/model-cache \
TRANSFORMERS_CACHE=/data/legalapp-embeddings/model-cache \
/data/legalapp-embeddings/venv/bin/python3 -m uvicorn \
  mcp_server.embedding_service:app --host 0.0.0.0 --port 8031
```

After the regional canary, expand from the already-staged snapshot without
downloading it again:

```bash
docker exec -i legalapp-courtlistener-db-1 psql -U courtlistener -d courtlistener -P pager=off -c \
  "SELECT pg_size_pretty(pg_database_size(current_database())) AS database_size,
          (SELECT COUNT(*) FROM opinions) AS opinions,
          (SELECT COUNT(*) FROM opinion_chunks) AS chunks;"

docker run --rm --network legalapp_default \
  -v legalapp_courtlistener_bulk:/data/courtlistener \
  -e VECTORDB_URL=postgresql://courtlistener:<password>@courtlistener-db:5432/courtlistener \
  legalapp-courtlistener-loader:latest \
  python -m mcp_server.loader --load-mvp \
    --coverage-profile federal-appellate --mvp-states ND,MT,MN,SD \
    --docket-limit 500000 --cluster-limit 200000 --opinion-limit 200000 \
    --citation-limit 2000000

docker run --rm --network legalapp_default \
  -e VECTORDB_URL=postgresql://courtlistener:<password>@courtlistener-db:5432/courtlistener \
  legalapp-courtlistener-loader:latest \
  python -m mcp_server.loader --chunk-opinions --limit 250000
```

`federal-appellate` adds SCOTUS and all federal circuits to the existing
regional, Tax Court, immigration, and bankruptcy selection. Use
`national-priority` only as a later explicit batch; it also adds the configured
major state and D.C. courts. `--court-id` and `COURTLISTENER_EXTRA_COURT_IDS`
support reviewed additions without changing source code. A numeric limit of
zero now means skip that table, and repeat runs count newly inserted/changed
rows instead of consuming the limit on unchanged rows.

Production Jetsons should run the checked-in systemd template instead of
`nohup`. Replace `<jetson-user>` with the account that owns the SSD checkout:

```bash
sudo install -o root -g root -m 0644 \
  deploy/jetson/lawhand-query-embedding@.service \
  /etc/systemd/system/lawhand-query-embedding@.service
sudo systemctl daemon-reload
sudo systemctl enable --now lawhand-query-embedding@<jetson-user>.service
sudo systemctl status lawhand-query-embedding@<jetson-user>.service
journalctl -u lawhand-query-embedding@<jetson-user>.service -n 100 --no-pager
```

The unit restarts an unexpectedly exited process after ten seconds and waits for
the network before starting. It expects the existing
`/data/legalapp-embeddings` layout.
Keep port 8031 restricted to the trusted LAN/firewall zone.

Checks:

```bash
curl -sf http://<jetson-lan-host>:8031/health
curl -sf http://127.0.0.1:8021/health
```

`courtlistener-mcp` reports `"query_embedding":"configured"` when the env is
present. The first query after model load can take about 18 seconds while the
model warms up; later calls should be faster. The service is LAN-only and must
not be exposed publicly.

## Scheduler Credential Rotation

Scheduler/dispatcher exceptions no longer include the worker database URL, but
any credential previously emitted by container logs must be treated as exposed.
Rotate it only as a coordinated post-deploy operation:

1. Back up the production `.env` and generate a new high-entropy password.
2. Update the Postgres role password and `COURTLISTENER_DB_PASSWORD` together.
3. Recreate `courtlistener-mcp`, loaders, and `embedding-scheduler` so every
   client receives the new value; never print the value in a shell transcript.
4. Verify MCP health, scheduler lock/count access, and one bounded Jetson worker
   connection before revoking the old operational session.
5. Inspect fresh scheduler logs and confirm no URL credentials are present.

## MCP Search Behavior

`search_caselaw` now uses hybrid search when `MCP_QUERY_EMBEDDING_URL` is
configured:

- Query text is embedded by the Jetson query embedding service.
- pgvector cosine distance ranks dense semantic matches.
- PostgreSQL `websearch_to_tsquery` ranks keyword matches.
- Results are fused with weighted reciprocal-rank scoring.
- The response includes `search_source` (`hybrid`, `vector`, or `fts`) plus
  `similarity` for user-facing relevance.

If the Jetson query embedder is unavailable or times out, the MCP server fails
closed to FTS search so chat can still return public authority. FTS now uses
`websearch_to_tsquery`, which is friendlier for natural-language prompts than
the older strict `plainto_tsquery` behavior.

Expanded-corpus test prompts:

- `North Dakota workers compensation Boechler`
- `Texas New Mexico water dispute`
- `Hardman Moore`
- `public defender`
- `City Helena Parsons`
- `Wickham`
- `Appaloosa NDIC`
- `Tax Court deficiency`

Raw MCP smoke:

```bash
python3 - <<'PY'
import json, urllib.request
import os
payload = {"name": "search_caselaw", "arguments": {"query": "Appaloosa NDIC", "top_k": 3}}
req = urllib.request.Request(
    "http://127.0.0.1:8021/api/mcp/tools/call",
    data=json.dumps(payload).encode(),
    headers={
        "Content-Type": "application/json",
        "X-Clarity-Internal-Key": os.environ["MCP_UPSTREAM_API_KEY"],
    },
    method="POST",
)
print(json.load(urllib.request.urlopen(req, timeout=30)))
PY
```

The private sidecar smoke requires the server-to-server key. Public product
smokes must remain off for the first-customer launch. When a later release is
approved, use an existing scoped product key; raw keys are shown only once.

## Known Pitfalls

- Citation import for a trimmed corpus must not insert citation-map edges where
  either opinion endpoint is absent. The loader requires both local opinion IDs
  before inserting citation edges.
- `opinion_citations` has a bounded local citation-map pass, not full
  CourtListener citation coverage. Treat citation lookup/network quality as MVP
  smoke coverage until a larger citation import is deliberately run.
- The raw S3 archives are huge. Do not assume the 58 GB staged cache equals
  searchable DB data.
- The `opinions` archive is the slow stage; it is about 50 GB compressed and is
  streamed with `lbzip2`.
- Counts may not move until a loader stage commits.
- The Jetson query embedding service is required for vector/hybrid ranking but
  the MCP server falls back to FTS if it is unavailable.
- Keep CourtListener Postgres on Docker storage. Do not move the live database
  to Jetson SSD or NFS/SMB.
- Keep the main app and CourtListener compose files separate. Avoid destructive
  cleanup commands from the wrong compose file.
- If the remote checkout is missing `mcp-server/` or
  `docker-compose.courtlistener-mcp.yml`, sync them before trying to operate the
  side stack. This happened during bring-up.

## Recovery Commands

Recreate only the side stack:

```bash
cd /home/varta/legalapp
docker compose -p legalapp -f docker-compose.courtlistener-mcp.yml up -d courtlistener-db courtlistener-mcp
```

Check data survived:

```bash
docker exec -i legalapp-courtlistener-db-1 psql -U courtlistener -d courtlistener -P pager=off -c \
  "SELECT COUNT(*) FROM opinion_chunks;
   SELECT COUNT(*) FROM opinion_chunks WHERE embedding IS NOT NULL;"
```

Remove failed one-shot loader containers after capturing logs:

```bash
docker logs <loader-container-name> > /tmp/<loader-container-name>.log
docker rm <loader-container-name>
```

Do not prune volumes unless the intent is to delete the corpus/staged S3 cache.

## Next Work

- Add Jetson 1 and Jetson 2 env/SSH keys and relaunch dispatcher with all
  available workers.
- Install and enable the checked-in Jetson query-embedding systemd unit.
- Run a larger citation import pass with the fixed local-endpoint filter.
- Expand the state/specialty corpus once disk projections still stay under the
  Docker-volume budget.
- Re-run retrieval sanity checks against CourtListener web/API results after
  each major corpus expansion.
