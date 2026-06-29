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
- Main backend uses `MCP_SERVER_URL=http://courtlistener-mcp:8021` and exposes
  CourtListener through `/api/mcp`.
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

MVP/test-hardware constraint: do not attempt a full CourtListener corpus sync
on the current hypervisor. Load bounded regional/specialty batches only after
checking projected Docker-volume growth.

## Start And Health Checks

Use the `legalapp` Compose project name so volume names match production:

```bash
cd /home/varta/legalapp
docker compose -p legalapp -f docker-compose.courtlistener-mcp.yml up -d courtlistener-db courtlistener-mcp
docker ps --filter name=legalapp-courtlistener --format "{{.Names}} {{.Status}}"
curl -sf http://127.0.0.1:8021/health
curl -sf http://127.0.0.1:8021/api/mcp
```

Main app health:

```bash
curl -sf http://localhost/health
curl -s -o /dev/null -w "%{http_code}\n" https://legalapp.perevagagroup.com/health
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

Current MVP filter behavior:

- State focus: ND, MT, MN, SD.
- Specialty: U.S. Tax Court and regional bankruptcy/BAP courts.
- SCOTUS included.
- Default keeps published/precedential clusters.
- Use `--include-unpublished` only for a deliberate expansion.

## Current Corpus Checkpoint

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

Checks:

```bash
curl -sf http://<jetson-lan-host>:8031/health
curl -sf http://127.0.0.1:8021/health
```

`courtlistener-mcp` reports `"query_embedding":"configured"` when the env is
present. The first query after model load can take about 18 seconds while the
model warms up; later calls should be faster. The service is LAN-only and must
not be exposed publicly. Current operational gap: it is launched by `nohup`, so
the next hardening pass should make it a systemd service or Jetson-side Compose
service.

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
payload = {"name": "search_caselaw", "arguments": {"query": "Appaloosa NDIC", "top_k": 3}}
req = urllib.request.Request(
    "http://127.0.0.1:8021/api/mcp/tools/call",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
print(json.load(urllib.request.urlopen(req, timeout=30)))
PY
```

Main app MCP tool call requires auth. Do not rotate a tenant MCP API key just
for testing; the raw key is shown only once.

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
- Convert the Jetson query embedding `nohup` process into systemd or a
  Jetson-side Compose service.
- Run a larger citation import pass with the fixed local-endpoint filter.
- Expand the state/specialty corpus once disk projections still stay under the
  Docker-volume budget.
- Re-run retrieval sanity checks against CourtListener web/API results after
  each major corpus expansion.
