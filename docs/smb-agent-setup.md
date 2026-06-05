# SMB File Share Relay Agent — Setup & Testing Guide

## Prerequisites

- Docker Compose stack running (postgres, redis, backend, frontend)
- Python 3.11+ on the machine that will run the agent
- Network access to an SMB/CIFS file share (Windows file server, NAS, Samba)
- Admin account on Clarity Legal SaaS

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
SMB_MAX_FILE_INDEX_PER_SHARE=500
SMB_SNIPPET_MAX_CHARS=500
SMB_TASK_POLL_INTERVAL=30
SMB_CONTENT_FETCH_TIMEOUT=120
```

Restart the backend container after changing `.env`:

```bash
docker compose restart backend
```

## Step 3: Install the Agent

On the machine that has access to your SMB file shares:

```bash
cd agent
pip install -e .
```

This installs `clarity-agent` as a CLI command with its dependencies (smbprotocol, httpx, pypdf, python-docx, aiosqlite, cryptography).

## Step 4: Generate a Pairing Code

As an admin user, call the pairing code endpoint:

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

Alternatively, use the admin UI **File Shares** tab in **Admin → File Shares → Generate Pairing Code**.

## Step 5: Register the Agent

On the agent machine:

```bash
clarity-agent register \
  --code A7X3K9M2 \
  --name "Office File Server" \
  --url http://localhost:8000
```

This exchanges the pairing code for an API key and saves it to `~/.clarity-agent/config.toml`:

```toml
saas_url = "http://localhost:8000"
api_key = "<generated-api-key>"
agent_id = "<uuid>"

[smb_credentials]
username = ""
password = ""
domain = ""
```

**Important:** The pairing code is one-time use. The API key is shown only once during registration.

## Step 6: Configure SMB Credentials

Edit `~/.clarity-agent/config.toml` to add your SMB credentials:

```toml
[smb_credentials]
username = "DOMAIN\\smb_service_account"
password = "your-password"
domain = "DOMAIN"

[[shares]]
server = "FILESERVER"
share = "LegalDocs"
display_name = "Legal Documents"
file_extensions = [".pdf", ".docx", ".doc", ".rtf", ".txt"]
max_depth = 10
```

The agent encrypts SMB credentials with Fernet (a machine-specific key stored in `~/.clarity-agent/.key`).

## Step 7: Add Share via Admin API

Create a share record on the SaaS side:

```bash
curl -X POST http://localhost:8000/api/v1/smb/shares \
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
clarity-agent start
```

The agent will:
1. Send a heartbeat to the SaaS
2. Download the list of configured shares
3. Scan each share (3-tier change detection: dir mtime → file mtime → first-4KB hash)
4. Sync file metadata (path, filename, extension, size, owner, snippet) to the SaaS
5. Poll for content fetch tasks every 30 seconds
6. Repeat scan every 6 hours (configurable)

## Step 9: Test File Search

In the Clarity Legal chat, ask a question about on-prem files:

> "Find the Acme Corp acquisition agreement on our file server"

The RetrievalPlanner will detect "file server" and include "smb" as a source. The system will search `smb_file_index` using tsvector full-text search and return matching files with snippets.

## Step 10: Test Content Fetch

When the LLM needs the full content of a file:

1. SaaS creates a content fetch task in Redis
2. Agent polls and picks up the task
3. Agent reads the file from the SMB share
4. Agent posts the content back to the SaaS
5. SaaS injects the content into the LLM context

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
curl -X POST http://localhost:8000/api/v1/matters/<matter_id>/smb-shares \
  -H "Authorization: Bearer <USER_JWT_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "share_id": "<share_uuid>",
    "display_label": "Acme Litigation Docs",
    "auto_scan": true
  }'
```

In the UI: **Matter Detail → File Shares tab → Add Share**

## Monitoring

### Agent Status
```bash
# On the agent machine
clarity-agent status

# On the SaaS
curl http://localhost:8000/api/v1/smb/agents \
  -H "Authorization: Bearer <ADMIN_JWT_TOKEN>"
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
│  Clarity Agent  │◄──────────────►│  Clarity Legal   │
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
- SMB credentials NEVER leave the agent machine (stored encrypted locally)
- File content is ephemeral in RAM only — never persisted in the SaaS database
- Full audit trail in `smb_access_log`
- Agent API key hashed with SHA-256 (like password hashing)
- Tenant-scoped RLS on all SMB tables
- Agent can be paused/revoked from admin dashboard

## Troubleshooting

| Issue | Solution |
|-|-|
| "SMB feature is not enabled" | Set `SMB_ENABLED=true` in `.env` and restart backend |
| Pairing code expired | Generate a new one (they expire in 10 min) |
| Agent can't connect to SMB | Check `smb_credentials` in config.toml, verify network access |
| No files in search results | Run `clarity-agent scan --share-path "\\\\server\\share"` first |
| Content fetch timeout | Increase `SMB_CONTENT_FETCH_TIMEOUT` in `.env` |
| Agent shows as "paused" | Heartbeat missed for 15+ minutes; check agent is running |