# LawHand File Share Agent — Setup & Testing Guide

## Prerequisites

- Docker Compose stack running (postgres, redis, backend, frontend)
- Windows Server 2019+ for the MSI, or Python 3.11+ for a source install
- Network access to an SMB/CIFS file share (Windows file server, NAS, Samba)
- Outbound TCP 443 from the agent to the LawHand URL and TCP 445 from the
  agent to each approved file server. No inbound firewall rule is required.
- Admin account on LawHand

## Step 1: Run the Migration

```bash
docker compose exec backend alembic upgrade head
```

For a source checkout with backend dependencies installed on the host, run
`cd backend` and then `alembic upgrade head` instead.

This creates the 5 SMB tables (`smb_agents`, `smb_shares`, `smb_file_index`, `smb_access_log`, `matter_smb_shares`), RLS policies, GIN index on `search_vector`, and adds `smb_folders` JSONB to the `matters` table.

## Step 2: Enable SMB

For development and self-managed base Compose deployments, ensure these are
set in your `.env`:

```env
SMB_ENABLED=true
SMB_PAIRING_CODE_TTL_MIN=10
SMB_MAX_FILE_INDEX_PER_SHARE=250000
SMB_SNIPPET_MAX_CHARS=500
SMB_TASK_POLL_INTERVAL=30
SMB_CONTENT_FETCH_TIMEOUT=120
```

This control-index work does not add a new relay for local-index results or
excerpts. The existing, separately bounded content-fetch flow described below
is unchanged. Any local-index integration remains a separate reviewed change
after the customer approves what result metadata or excerpts may leave the
on-premises agent.

The reviewed Skynet and production Compose overlays pin `SMB_ENABLED=true` for
both the API and scheduler. This prevents a stale inherited host value from
silently disabling file-share retrieval for search and matter context;
disabling it in production
requires an intentional reviewed deployment change.

Restart the backend container after changing `.env`:

```bash
docker compose restart backend
```

## Step 3: Install the Agent

After generating the code in Step 4, use the **Copy command** action beside the
Windows installer in **Administration → File Shares → Agents** and run the
complete block on the Windows machine that can access the approved shares, from
an elevated PowerShell window.
The generated block permits only the LawHand GitHub release path and GitHub's
release CDN hosts, limits redirects and response sizes, validates the manifest,
downloads a version-pinned MSI, verifies SHA-256, and then installs or upgrades
the service. The MSI never receives the one-time pairing code.

For development, `cd agent && pip install -e .[dev]` installs the
`lawhand-agent` CLI from source.

## Step 4: Generate a Pairing Code

As an admin user, call the pairing code endpoint (or use **Administration →
File Shares → Generate Pairing Code**):

```bash
curl -X POST http://localhost:8000/api/v1/smb/pairing-code \
  -H "Authorization: Bearer <ADMIN_JWT_TOKEN>"
```

Response:
```json
{
  "pairing_code": "A7X3-K9M2-Q4TR-8VWP",
  "expires_at": "2026-06-04T15:30:00Z"
}
```

The pairing code expires in 10 minutes (configurable via `SMB_PAIRING_CODE_TTL_MIN`).

## Step 5: Register the Agent

Pairing is deliberately separate from MSI installation. This keeps a bad or
expired code from rolling back an otherwise healthy service installation and
keeps the code out of durable Windows Installer diagnostics. The portal's
copied block performs this step automatically after a fresh install. If you
downloaded and installed the MSI manually, use the generated code in the same
elevated PowerShell window:

```powershell
$pairingCode = 'A7X3-K9M2-Q7RT-W4YZ'
$agent = Join-Path $env:ProgramFiles 'LawHand\Agent\lawhand-agent.exe'
& $agent register --code $pairingCode
if ($LASTEXITCODE -ne 0) { throw "Registration failed with code $LASTEXITCODE" }
Restart-Service 'LawHandAgent'
Get-Service 'LawHandAgent'
```

The production URL is built into the packaged agent, so do not paste a
Markdown-formatted URL or add `--url` for a normal production registration.
For a source install or a local development server, the explicit form remains:

```bash
lawhand-agent register \
  --code A7X3-K9M2-Q7RT-W4YZ \
  --name "Office File Server" \
  --url https://getlawhand.com
```

This exchanges the pairing code for a tenant-bound agent ID and API key. The
MSI protects them under `%ProgramData%\LawHand\Agent`; a per-user source install
uses `~/.clarity-agent/config.toml`. Plain HTTP is rejected except for local
development on `localhost`, `127.0.0.1`, or `::1`.

```toml
saas_url = "http://localhost:8000"
api_key = "<generated-api-key>"
agent_id = "<uuid>"

[smb_credentials]
username = ""
password = ""
domain = ""
```

**Important:** The pairing code is one-time use. Unused reservations expire
after 10 minutes and are removed from the operational agent view; registered
agents remain as auditable records when revoked. The API key is shown only once
during registration.

## Step 6: Configure SMB Credentials

Create the credential in **Administration → File Shares → Credentials**, then
attach it to a share. It is encrypted in the tenant credential vault, delivered
only to that tenant's assigned agent over authenticated HTTPS, and kept in the
agent process memory. Prefer a least-privilege read-only domain account.

The local config credential remains an optional fallback for source installs:

```toml
[smb_credentials]
username = "DOMAIN\\smb_service_account"
password = "your-password"
domain = "DOMAIN"

```

Local fallback credentials are encrypted with a machine-local Fernet key. The
normal MSI setup does not write tenant-vault share passwords to disk.

## Step 7: Add Share via Admin API

Create a share record on the SaaS side:

```bash
curl -X POST 'http://localhost:8000/api/v1/smb/shares?agent_id=<agent_uuid>' \
  -H "Authorization: Bearer <ADMIN_JWT_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "share_path": "\\\\FILESERVER\\LegalDocs",
    "display_name": "Legal Documents",
    "file_extensions": [".pdf", ".docx", ".doc", ".rtf", ".txt"],
    "max_depth": 10
  }'
```

Or use the **File Shares** tab in the admin dashboard.

## Step 8: Start the Agent

```bash
lawhand-agent start
```

The agent will:
1. Send a heartbeat to the SaaS
2. Download the list of configured shares
3. Scan each share (3-tier change detection: dir mtime → file mtime → first-4KB hash)
4. Sync file metadata (path, filename, extension, size, owner, snippet) to the SaaS
5. Maintain a 20-second outbound long poll for near-real-time content tasks
6. Repeat scan every 6 hours (configurable)

## Step 9: Test File Search

In the LawHand chat, ask a question about on-prem files:

> "Find the Acme Corp acquisition agreement on our file server"

The RetrievalPlanner can include `smb` as a source. LawHand searches the
tenant/matter-scoped metadata index first, then asks the agent for full text for
at most the top three hits. That on-demand phase has a 12-second aggregate wait
and a 12,000-character context budget; unavailable files fall back to snippets
and the degraded result is not cached.

`SMB_CONTENT_FETCH_TIMEOUT=120` governs the separate manual/content-status
polling path; it does not extend the RAG request's 12-second aggregate wait.

## Step 10: Test Content Fetch

When the LLM needs the full content of a file:

1. SaaS creates a tenant/file/share/agent-bound content fetch task in Redis
2. Agent polls and picks up the task
3. Agent reads the file from the SMB share
4. Agent posts the content back to the SaaS
5. SaaS validates the exact task binding and injects bounded text into context

You can also manually request content:

```bash
curl -X POST http://localhost:8000/api/v1/smb/files/<file_id>/fetch-content \
  -H "Authorization: Bearer <USER_JWT_TOKEN>"
```

Returns a `task_id` you can poll:

```bash
curl "http://localhost:8000/api/v1/smb/files/<file_id>/content-status?task_id=<task_id>" \
  -H "Authorization: Bearer <USER_JWT_TOKEN>"
```

## Step 11: Bind SMB Shares to Matters

To scope file searches to a specific matter:

```bash
curl -X POST http://localhost:8000/api/v1/smb/matters/<matter_id>/smb-shares \
  -H "Authorization: Bearer <USER_JWT_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "share_id": "<share_uuid>",
    "folder_path": "Acme Litigation Docs"
  }'
```

In the UI: **Matter Detail → File Shares tab → Add Share**

## Upgrade Agents

From version 0.15.0 onward, a tenant admin can use **Administration → File
Shares → Agents → Update**. The API queues only a target version and fixed
manifest identity. The agent independently fetches the official manifest and a
version-pinned GitHub release asset; no portal request can provide an installer
URL or executable path. Agents older than 0.15.0 require one manual bootstrap
upgrade before this portal button works.

### Windows overtop MSI

Run the Windows **Copy command** block from the Agents tab in an elevated
PowerShell prompt. Installing the MSI over the existing product preserves
`C:\ProgramData\LawHand\Agent`; do not uninstall first. When that enrollment
already exists, the block skips pairing and reports that the existing LawHand
registration was preserved.

Portal and CLI auto-updates are supported for the default LocalSystem service.
If `SERVICE_ACCOUNT` was used, use the direct overtop MSI command above. The
late-upgrade schedule reads the existing service identity and leaves its
password in the Service Control Manager; it neither recovers nor logs that
password. Supply both account properties only for a clean install or an
intentional service-identity change.

### Linux command

Version 0.15.0 installs a root-owned systemd path/oneshot updater. The normal
relay process can request only a semantic version; the root helper re-downloads
and verifies the fixed official manifest and versioned tarball, validates every
archive member, performs an atomic replacement, checks service health, and
rolls back on failure.

```bash
lawhand-agent update --check
sudo lawhand-agent update --apply
sudo systemctl status lawhand-agent-update.service
sudo journalctl -u lawhand-agent-update.service --since today
```

For a pre-0.15 packaged Linux agent, download the latest tarball and
`SHA256SUMS.txt`, verify it, extract it, and run `sudo ./install.sh` without
`--code`. That overwrites the binary/units while preserving the existing
`/etc/lawhand-agent` enrollment, encryption key, and ledger. After this one
manual bootstrap, portal updates are available.

```bash
set -euo pipefail
LAWHAND_UPDATE_DIR="$(mktemp -d)"
LAWHAND_RELEASE_BASE="https://github.com/mattpainter701/legalapp/releases/latest/download"
curl --fail --location "$LAWHAND_RELEASE_BASE/lawhand-agent-linux-x86_64.tar.gz" \
  --output "$LAWHAND_UPDATE_DIR/lawhand-agent-linux-x86_64.tar.gz"
curl --fail --location "$LAWHAND_RELEASE_BASE/SHA256SUMS.txt" \
  --output "$LAWHAND_UPDATE_DIR/SHA256SUMS.txt"
(cd "$LAWHAND_UPDATE_DIR" && sha256sum --ignore-missing --check SHA256SUMS.txt)
tar -xzf "$LAWHAND_UPDATE_DIR/lawhand-agent-linux-x86_64.tar.gz" \
  -C "$LAWHAND_UPDATE_DIR"
LAWHAND_INSTALLER="$(find "$LAWHAND_UPDATE_DIR" -mindepth 2 -maxdepth 2 \
  -type f -name install.sh -print -quit)"
test -n "$LAWHAND_INSTALLER"
sudo "$LAWHAND_INSTALLER"
```

The stable URLs always point to the most recently verified published `agent-v*`
tag. They advance to v0.15.1 only after both platform builds, tests, checksums,
and public download probes pass.

## Monitoring

### Agent Status
```bash
# On the agent machine
lawhand-agent status

# On the SaaS
curl http://localhost:8000/api/v1/smb/agents \
  -H "Authorization: Bearer <ADMIN_JWT_TOKEN>"
```

### Agent Logs

The Windows package writes bounded rotating application logs under the
protected machine data directory. Stream or inspect them from an elevated
PowerShell window:

```powershell
Get-Content 'C:\ProgramData\LawHand\Agent\logs\agent.log' -Tail 200 -Wait
```

On Linux, use journald:

```bash
sudo journalctl -u lawhand-agent.service -n 200 -f
```

### SMB Stats (Admin Dashboard)
```bash
curl http://localhost:8000/api/admin/smb/status \
  -H "Authorization: Bearer <ADMIN_JWT_TOKEN>"
```

### Access Log
```bash
curl http://localhost:8000/api/admin/smb/activity?limit=20 \
  -H "Authorization: Bearer <ADMIN_JWT_TOKEN>"
```

## Architektur Summary

```
┌─────────────────┐     HTTPS      ┌──────────────────┐
│  LawHand Agent  │◄──────────────►│  LawHand SaaS    │
│  (on-prem)      │                │  SaaS (cloud)    │
│                 │                │                  │
│  - SMB Scanner  │   POST /sync   │  - smb_agents    │
│  - Task Worker  │   GET /tasks   │  - smb_shares    │
│  - Heartbeat    │   POST /result │  - smb_file_index│
│  - Local SQLite │                │  - smb_access_log│
└────────┬────────┘                └────────┬─────────┘
         │                                  │
    SMB/NTLM                              pgvector
    ┌──────┴───────┐                    tsvector/GIN
    │  File Server │                    (no embeddings!)
    │  \\SERVER\   │
    │  LegalDocs\  │
    └──────────────┘
```

**Key security properties:**
- Each agent API key is bound to one tenant and stored as a SHA-256 digest.
- Share credentials are encrypted in the tenant vault, sent only to the
  assigned agent over TLS, and retained in agent memory; local fallback secrets
  are encrypted on the agent.
- Full content is never written to `smb_file_index`. It is held briefly in the
  Redis task handoff and request context, then expires; metadata and snippets
  are the persistent cache.
- Full audit trail in `smb_access_log`
- Tenant-scoped RLS on all SMB tables
- Agent can be paused/revoked from admin dashboard

This is an application-layer, outbound-only relay, not a general network mesh:
the SaaS cannot open arbitrary sockets into the tenant network, and the agent
can read only configured share roots. The logical isolation now approaches a
per-tenant private link; mTLS device certificates and signed public installers
remain the next controls if a Tailscale-equivalent device identity is required.

## Private firm-memory search rollout

The current shipped slice is a metadata-first search path: the SaaS stores the
filename, bounded opening snippet, file metadata, and a PostgreSQL search
vector. Full text is fetched from the agent only for an authorized, bounded
set of results. It does not search every page of a file, and it does not claim
native Windows-user ACL enforcement.

The planned 4 TB program adds a local full-text sidecar beside the agent. The
index stays in the customer's environment and contains page- or paragraph-
bounded extracted text plus provenance. The current scanner records share,
canonical path, size, modified time, and a first-4-KiB SHA-256 fingerprint; it
does not yet compute a full-file hash or capture native Windows ACLs.
Embeddings are not required. SQLite FTS5/BM25 is the bounded local full-text
control implementation used to validate extraction, scope, ranking, and a
representative corpus; it is not the 4 TB scale target. The proposed scale PoC
uses a durable manifest/queue, isolated Apache Tika extraction, asynchronous
OCR, ACL capture/security trimming, and OpenSearch on local SSD/NVMe while the
original files remain on the HDD-backed file server. That pipeline is not yet
shipped. See
`docs/firm-memory-poc-architecture.md`.

Release 0.15.3 adds an explicitly authorized, matter-scoped relay for bounded
local-search excerpts and metadata. It uses the agent's existing outbound
polling connection and is available through the portal, Chat structured
sources, and user-bound Workspace MCP. Query text is short-lived and is not
application-logged, persisted, or placed in evaluator output. Results use an
opaque same-origin portal link with an authorization recheck and **Copy UNC**;
raw `file://` and `smb://` browser links are not emitted. This does not add a
general inbound network route or make the SaaS an SMB proxy.

The planned rollout is deliberately staged:

1. Inventory the corpus, then measure a stratified 50–200 GB subset and 30–50
   customer-judged queries on the actual SMB path.
2. Enable lexical full-text indexing for approved extensions and inspect
   recall, exact-page rate, latency, and extraction failures.
3. Run extraction in bounded Tika Pipes workers and add asynchronous OCR or
   legacy-format readers only after their sandbox, limits, and retention
   behavior have been reviewed.
4. Consider semantic retrieval later only if the measured query set shows a
   material lexical-recall gap.

### Local index storage and protection

The control index and its retry ledger use the agent's protected
application-data directory, not the share itself. On Windows this is under
`C:\\ProgramData\\LawHand\\Agent`; on Linux it is under the protected
`/var/lib/lawhand-agent` installation data path. The scale PoC places the
active OpenSearch data path and parser/OCR scratch space on dedicated local
SSD/NVMe, never on the SMB share. The service account must be able to read and
write these paths, while ordinary users must not be granted directory access.
Backups, if enabled by the customer, inherit the same encryption, retention,
and legal-hold policy as the agent ledger.

The control index is opt-in. A safe HDD-source starting point is:

```env
CLARITY_LOCAL_INDEX_ENABLED=true
CLARITY_LOCAL_INDEX_PATH=D:\\LawHandIndex\\search-index.db
CLARITY_LOCAL_INDEX_MAX_FILE_MB=25
CLARITY_LOCAL_INDEX_WORKERS=1
```

Before enabling it, an operator must confirm all of the following:

- the index path resolves to protected local SSD/NVMe and not to the SMB share;
- the volume has enough free space for the bounded sample plus SQLite WAL and
  rebuild headroom;
- full-disk encryption, backup, retention, legal-hold, and deletion treatment
  for derived client text have been approved;
- the service identity can write the directory and ordinary users cannot read
  it; and
- the sample roots, indexing window, and source-server I/O throttle have been
  approved by the customer.

Place `CLARITY_LOCAL_INDEX_PATH` on protected SSD/NVMe storage. Begin with one
worker; raise the bounded worker count only after measuring SMB/HDD queueing.
The path must be absolute and must name a file inside a dedicated
subdirectory on a local disk, not the root of a drive or a UNC/network path.
The agent restricts that directory and the database file to the service
identity/system administrators and refuses to enable the index if either ACL
operation fails. These
settings enable the local control index only. They do not enable the separate
SaaS result/excerpt flow.

The index is rebuildable derived data. Never treat it as the authoritative
copy of a case file. Atomic blue/green index switching is a requirement for
the planned scale PoC; it is not implemented by the SQLite control index.

The control index has no customer-facing query endpoint. For a local,
operator-run evaluation, stop writes or use a completed index and run:

```powershell
python -m clarity_agent.poc_query_runner `
  D:\LawHandIndex\search-index.db queries.jsonl --output results.jsonl
```

The query manifest contains query text and share paths and must stay inside the
customer boundary. The result file contains opaque query labels and
pseudonymous document IDs, but it is still sensitive evaluation data. See the
full manifest format and evaluator command in
`docs/firm-memory-poc-architecture.md`.

### Formats, pages, and extraction status

The control implementation recognizes text PDFs, DOCX/DOCM, TXT, and RTF with
a source-byte and extracted-character cap. Its exact manifest states are
`pending`, `running`, `ready`, `unsupported`, and `error`. DOC, WPD, ODT,
password-protected/encrypted files, active Office content, and scanned-image
PDFs remain unsupported or degraded until a reviewed parser/OCR capability is
enabled. The planned scale pipeline may add `partial` and `timed_out`. Any
non-ready state must never appear as authoritative proof of “no match.”

The embedded Python extractor is a control implementation for synthetic or
separately reviewed test files. Its source-byte cap does not bound decompressed
PDF/DOCX size, parser CPU, or parser memory, and a Python thread cannot safely
terminate a hung parser. Do not use it as the messy-corpus PoC extractor. The
customer PoC requires the process-isolated Tika Pipes boundary and limits in
`docs/firm-memory-poc-architecture.md`.

Where the control parser supplies PDF page boundaries, a result carries a page
number and short match-centered passage. Other current formats return a null
page; stable paragraph/block pinpoints are a scale-PoC requirement, not a
shipped capability. The pilot does not promise perfect page numbering or OCR
accuracy.

### Indexing, retry, and deletion lifecycle

Scanning remains resumable and idempotent. The control is reprocessed when its
canonical path, size, modified time, or first-4-KiB fingerprint changes. A
same-size, same-timestamp edit outside that prefix can evade this control
fingerprint; the scale PoC must use a stronger stable identity/full-hash policy.
Temporary parser failures retry with bounded exponential backoff and a
per-share queue limit; permanent failures remain visible for operator repair.
The control handles a rename as delete-plus-add. Preserving identity across
renames requires the future scale pipeline's stable file-ID/full-hash policy.

When a file disappears, the control removes its rows only after a complete,
successful walk; a partial SMB walk must not delete healthy rows. Any future
query service must also make rows immediately ineligible when a share is
disabled, an agent is revoked, or a matter binding is removed. The current
control has no customer-facing query service and may retain derived rows until
the next complete scan or operator-approved rebuild/deletion. Rebuilding or
deleting an eventual scale index must also clear OCR derivatives, caches, and
retry payloads covered by the customer's retention policy.

### Limits and configuration

Every deployment must publish and enforce caps for maximum files per share,
source bytes, extracted characters, page count, OCR pixels, query length,
wildcard expansion, result count, excerpt bytes, queued jobs, and per-user or
per-tenant request rate. The existing metadata path defaults remain
`SMB_MAX_FILE_INDEX_PER_SHARE=250000`, `SMB_SNIPPET_MAX_CHARS=500`, a maximum
of 100 API results, and a 12,000-character aggregate RAG context. A local
full-text pilot must add explicit index/OCR caps rather than silently
reusing these metadata limits.

### Search privacy and authorization

Search is always tenant-bound. Matter searches require a valid matter in the
same tenant, an authorized user, and a matching share/folder binding. Every
fetch task binds the tenant, matter (when present), share, file, canonical
path, and agent; swapping any identifier must fail closed. Folder comparisons
must use normalized separators and path-component boundaries, so `Client-1`
cannot match `Client-10`.

The configured SMB credential defines what the agent can read. Unless an
explicit per-user ACL integration is enabled, LawHand does not mirror native
Windows ACLs. Administrators must use separate least-privilege shares or
accounts for different security boundaries and acknowledge this limitation
before enabling broad firm search. See the canonical controls document:
`docs/private-firm-memory-search.md`.

Indexed text is untrusted evidence. Retrieved passages are delimited and
marked as document content before entering an AI context; instructions inside
a brief, email, filename, or OCR result are never system instructions and
cannot authorize a tool call. Search and fetch activity is auditable without
logging credentials or unrestricted document text.

### Secondary and proprietary sources

Customer-owned historic files and licensed secondary sources are separate
content classes. Do not scrape, bulk-import, train on, or cross-tenant share
Westlaw/Thomson Reuters, Lexis, Wright & Miller, or other restricted content
without a written license/API agreement. The product must disclose which
source classes were searched and preserve source, license, provenance, and
retention metadata. This feature is not a Westlaw replacement and does not
promise comprehensive legal-research coverage.

### Pilot status and rollback

The admin status view should show files discovered, indexed, skipped,
unsupported, failed, retried, deleted, and last successful scan time, plus
queue depth and index size. Record pilot metrics for indexed bytes/files,
extraction coverage, OCR coverage if enabled, P50/P95 query latency,
recall@10, correct-page rate, stale-result revocation time, and authorization
test failures.

To roll back the control, set `CLARITY_LOCAL_INDEX_ENABLED=false`, restart the
agent, preserve the audit trail, and retain or remove the local derived index
according to the customer's retention decision. To rebuild it, stop the
agent, validate the configured root and credentials, choose a new protected
index path, run a complete scan, compare counts and canary queries, then update
the configured path and restart. The planned scale service must implement an
atomic blue/green switch; the control does not. Never delete the original case
files as part of rollback or rebuild.

## Troubleshooting

| Issue | Solution |
|-|-|
| "SMB feature is not enabled" | Set `SMB_ENABLED=true` in `.env` and restart backend |
| Pairing code expired | Generate a new one (they expire in 10 min) |
| Agent can't connect to SMB | Check `smb_credentials` in config.toml, verify network access |
| Connection test succeeds but sync returns HTTP 422 | Upgrade the SaaS compatibility fix or agent to v0.15.1+, then retry **Scan now**; SMB credentials are already working |
| No files in search results | Use **Scan now** in the share admin view, or run `lawhand-agent scan` on the agent |
| Manual content-status polling times out | Increase `SMB_CONTENT_FETCH_TIMEOUT` in `.env`; this does not change the RAG path's 12-second aggregate wait |
| Agent shows as "paused" | Heartbeat missed for 15+ minutes; check agent is running |
| Local index reports unsupported/failed files | Review the per-file status and parser limits; do not infer that an unsupported file has no relevant text |
| Search returns an unexpected matter result | Confirm the matter binding, share root, user access, and the Windows ACL caveat before changing the index |
| Index is stale after a share change | Stop the agent, rebuild to a new protected control-index path, validate it, update the configured path, and restart; do not delete the source files |
