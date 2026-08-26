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
cd backend
alembic upgrade head
```

This creates the 5 SMB tables (`smb_agents`, `smb_shares`, `smb_file_index`, `smb_access_log`, `matter_smb_shares`), RLS policies, GIN index on `search_vector`, and adds `smb_folders` JSONB to the `matters` table.

## Step 2: Enable SMB in .env

Ensure these are set in your `.env`:

```env
SMB_ENABLED=true
SMB_PAIRING_CODE_TTL_MIN=10
SMB_MAX_FILE_INDEX_PER_SHARE=250000
SMB_SNIPPET_MAX_CHARS=500
SMB_TASK_POLL_INTERVAL=30
SMB_CONTENT_FETCH_TIMEOUT=120
```

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
  "pairing_code": "A7X3K9M2",
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

## Troubleshooting

| Issue | Solution |
|-|-|
| "SMB feature is not enabled" | Set `SMB_ENABLED=true` in `.env` and restart backend |
| Pairing code expired | Generate a new one (they expire in 10 min) |
| Agent can't connect to SMB | Check `smb_credentials` in config.toml, verify network access |
| No files in search results | Use **Scan now** in the share admin view, or run `lawhand-agent scan` on the agent |
| Content fetch timeout | Increase `SMB_CONTENT_FETCH_TIMEOUT` in `.env` |
| Agent shows as "paused" | Heartbeat missed for 15+ minutes; check agent is running |
