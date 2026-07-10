# CourtListener MCP + Jetson Embeddings

## Topology

- The hypervisor named by `HYPERVISOR_HOST` runs `courtlistener-db` and `courtlistener-mcp` via `docker-compose.courtlistener-mcp.yml`.
- CourtListener data volumes stay under Docker storage on the hypervisor, not the root filesystem.
- Jetsons are stateless embedding workers. They connect to `courtlistener-db` over LAN and update `opinion_chunks.embedding`.
- Do not mount a Jetson SSD as the live Postgres data directory. Use it only for temporary bulk staging if needed.

## Start The MCP Stack

Production on `skynet` must use the `legalapp` Compose project name so it
reuses the existing `legalapp_courtlistener_*` volumes and network:

```powershell
docker --context skynet compose -p legalapp -f docker-compose.courtlistener-mcp.yml up -d --build courtlistener-db courtlistener-mcp
docker --context skynet compose -p legalapp -f docker-compose.courtlistener-mcp.yml --profile loader run --rm courtlistener-loader
```

Set the main backend to proxy MCP calls:

```env
MCP_SERVER_URL=http://courtlistener-mcp:8021
MCP_UPSTREAM_API_KEY=<same-32+-character-secret-on-backend-and-sidecar>
MCP_PRODUCT_ENABLED=false
```

Endpoint contract:

- Public `/api/mcp` is the official SDK-backed Streamable HTTP endpoint and is
  unavailable while `MCP_PRODUCT_ENABLED=false`. Optional metadata lives at
  `/api/mcp/manifest` and is also hidden while disabled.
- The private sidecar requires `X-Clarity-Internal-Key` matching
  `MCP_UPSTREAM_API_KEY`. Public clients never receive this credential.
- The backend compatibility adapter accepts an application JWT or a scoped
  `X-MCP-API-Key`; legacy `X-API-Key` is rejected.
- Legacy `GET/POST /api/mcp/api-key` issuance returns HTTP 410. Tenant admins
  use `/api/mcp/product` and `/api/mcp/product-keys`; key creation remains
  unavailable until product, tenant, entitlement, billing and Stripe gates pass.
- Do not rotate a tenant MCP API key just for smoke tests. The raw key is shown
  only once on creation, so a live `X-MCP-API-Key` smoke requires the current
  key from the tenant admin or an intentional key rotation.

On the hypervisor, `/home/varta/legalapp/.env` is the production credential
source of truth. Back it up before editing, then recreate only the backend so it
picks up the MCP URL:

```powershell
docker --context skynet run --rm -v /var/run/docker.sock:/var/run/docker.sock -v /home/varta/legalapp:/home/varta/legalapp -w /home/varta/legalapp docker:27-cli sh -c "docker compose -p legalapp -f docker-compose.hypervisor.yml up -d backend"
```

## Stage And Load CourtListener Bulk Data

The release corpus should start with the regional/specialty MVP, not the whole
CourtListener corpus:

- State focus: North Dakota, Montana, Minnesota, and South Dakota.
- Always include SCOTUS because it is small and controlling.
- Specialty focus: tax, immigration administrative authority, and regional
  bankruptcy courts/BAPs for the 8th/9th Circuit footprint.
- Default cluster filter keeps published/precedential authority. Add
  `--include-unpublished` only for a later expansion pass.

The loader supports staged, resumable steps:

```powershell
docker compose -f docker-compose.courtlistener-mcp.yml --profile loader run --rm courtlistener-loader python -m mcp_server.loader --stage-latest
docker compose -f docker-compose.courtlistener-mcp.yml --profile loader run --rm courtlistener-loader python -m mcp_server.loader --load-mvp --mvp-states ND,MT,MN,SD --limit 1000
docker compose -f docker-compose.courtlistener-mcp.yml --profile loader run --rm courtlistener-loader python -m mcp_server.loader --chunk-opinions --limit 1000
```

Use `--load-staged` only for a lab database where the full corpus is intended.
Remove `--limit` only after disk growth and row counts look sane.

## Launch Jetson Embedding Workers

Each Jetson needs this repo checkout, Python dependencies, CUDA/PyTorch, and access to the vector DB URL.

```powershell
$env:JETSON_HOSTS="<jetson-a> <jetson-b> <jetson-c>"
# Or set indexed hosts instead:
$env:JETSON_0_HOST="<jetson-a>"
$env:JETSON_1_HOST="<jetson-b>"
$env:JETSON_2_HOST="<jetson-c>"
$env:JETSON_3_HOST="<jetson-d>"
$env:JETSON_USER="jetson"
$env:JETSON_3_USER="<only-if-different>"
$env:VECTORDB_URL="postgresql://courtlistener:<password>@<hypervisor-host>:5432/courtlistener"
$env:BATCH_SIZE="32"
docker compose -f docker-compose.courtlistener-mcp.yml --profile embedding run --rm embedding-dispatcher
```

For Jetson 3 on the wired/testlab network, the working path is SSD-backed and
uses an SSH reverse tunnel because the Jetson cannot initiate traffic to the
hypervisor DB subnet:

```env
JETSON_3_HOST=192.168.1.203
JETSON_3_USER=<jetson-user>
JETSON_SCRIPT_DIR=/data/legalapp-embeddings/scripts
JETSON_DB_REVERSE_TUNNEL=true
JETSON_DB_TUNNEL_REMOTE_PORT_BASE=15434
```

The Jetson worker files live under `/data/legalapp-embeddings`:

- `/data/legalapp-embeddings/app` — copied MCP worker package.
- `/data/legalapp-embeddings/scripts/jetson_embed_worker.py` — wrapper that adds `/data/pip_packages` and the MCP package to `sys.path`.
- `/data/legalapp-embeddings/model-cache` — Hugging Face/SentenceTransformer cache.
- `/data/legalapp-embeddings/logs` and `~/clarity-legal-logs` — worker logs.

Automation uses SSH keys. Keep any temporary Jetson passwords in local env only
for manual setup. The dispatcher can consume indexed password variables such as
`JETSON_3_PASSWORD` through `sshpass`, but SSH keys are still the steady-state
target and passwords must not be committed.

Jetson workers require LAN reachability to the hypervisor DB bind:

```powershell
Test-NetConnection <jetson-ip> -Port 22
docker --context skynet run --rm --network host alpine:3.20 sh -c "ip route get <jetson-ip>; nc -vz -w 5 <jetson-ip> 22"
```

The legacy launcher remains available:

```bash
JETSON_HOSTS="<jetson-a> <jetson-b> <jetson-c>" \
VECTORDB_URL="postgresql://courtlistener:<password>@<hypervisor-host>:5432/courtlistener" \
bash scripts/trigger_jetson_workers.sh
```

## Verify

```sql
SELECT COUNT(*) FROM opinion_chunks;
SELECT COUNT(*) FROM opinion_chunks WHERE embedding IS NOT NULL;
SELECT COUNT(*) FROM opinion_chunks WHERE embedding IS NULL;
SELECT vector_dims(embedding), COUNT(*) FROM opinion_chunks WHERE embedding IS NOT NULL GROUP BY 1;
```

Smoke the MCP endpoint:

```powershell
Invoke-RestMethod http://localhost:8021/health
$headers = @{ "X-Clarity-Internal-Key" = $env:MCP_UPSTREAM_API_KEY }
Invoke-RestMethod http://localhost:8021/api/mcp -Headers $headers
```

2026-06-25 smoke result: Jetson 3 embedded all 237 staged MVP smoke chunks with
`mixedbread-ai/mxbai-embed-large-v1`, `embedding_version=1`, `vector_dims=1024`.

2026-06-26 production chat smoke result:

- `https://legalapp.perevagagroup.com/health` returned 200 with database connected.
- Backend `/api/mcp` proxied to `clarity-courtlistener` and live
  `search_caselaw` returned CourtListener chunks.
- `POST /api/conversations` returned 201 under a real user token.
- `POST /api/conversations/{id}/messages` with
  `content="parental rights methamphetamine"` and `include_public=true` returned
  201, stored one source citation, and tagged `context_used` with
  `courtlistener:9b3eb47b-b1ae-4bd6-bb8f-9d38133c0653`.
- The same chat path with `include_public=false` returned 201 and stored no
  CourtListener context.
- Frontend streaming chat refreshes the persisted conversation after
  `[STREAM_COMPLETE]` so the source ledger receives the stored MCP citations
  without requiring a manual reload.

2026-06-26 historical production endpoint smoke result (superseded by the
current product flag, dedicated upstream credential, and official protocol):

- `POST /api/mcp/tools/call` without credentials returned 401 and did not proxy.
- `GET /api/mcp` returned `clarity-courtlistener` with 7 tools.
- Authenticated `POST /api/mcp/tools/call` returned 200, `isError=false`, and a
  JSON CourtListener hit for `Matter of A.D. and K.D. YINC`.
- Admin `GET /api/mcp/api-key` returned 200 and surfaced the same 7 live
  CourtListener tools for the tenant MCP configuration UI.
- Chat `POST /api/conversations/{id}/messages` returned 201, one CourtListener
  source, and persisted `context_used` as
  `courtlistener:9b3eb47b-b1ae-4bd6-bb8f-9d38133c0653`.

2026-06-26 MVP corpus expansion:

- S3 staging remains cached in `legalapp_courtlistener_bulk` at about 58 GB of
  compressed source archives.
- CourtListener Postgres grew to about 139 MB logical DB size after expanding
  to 50,000 dockets, 2,103 clusters, 500 opinions, and 5,024 chunks.
- Chunk distribution after expansion:
  - Montana Supreme Court: 269 opinions, 2,405 chunks.
  - Supreme Court of Minnesota: 108 opinions, 700 chunks.
  - Supreme Court of the United States: 57 opinions, 951 chunks.
  - North Dakota Supreme Court: 39 opinions, 506 chunks.
  - U.S. Tax Court: 13 opinions, 291 chunks.
  - South Dakota Supreme Court: 9 opinions, 106 chunks.
  - Minnesota Court of Appeals: 2 opinions, 30 chunks.
  - Eighth Circuit BAP: 2 opinions, 11 chunks.
  - Ninth Circuit BAP: 1 opinion, 24 chunks.
- Jetson 3 reverse-tunnel embedding is running against the new chunks. The first
  stable check after launch showed 557 embedded and 4,467 unembedded chunks.
- Citation-map import is deferred for this expansion. The loader now filters
  citation-map rows to require both local opinion endpoints, but the live
  citation table should not be treated as ready until a dedicated citation pass
  completes.

Expanded-corpus search prompts that should return hits:

- `North Dakota workers compensation Boechler` -> `WSI v. Boechler, PC`.
- `Texas New Mexico water dispute` -> `Texas v. New Mexico`.
- `Hardman Moore` -> `In re Marriage of Hardman and Moore`.
- `public defender` -> `Office of State Pub. Defender v. Fagenstrom`.
- `City Helena Parsons` -> `City of Helena v. Parsons`.
- `Wickham` -> `State v. Wickham`.
- `Appaloosa NDIC` -> `Blue Appaloosa v. NDIC`.
- `Tax Court deficiency` -> Tax Court cases such as `Nichols v. Commissioner`.
