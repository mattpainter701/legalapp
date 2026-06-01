# Clarity Legal — platform.clarity.legal

AI-powered legal platform for in-house and boutique legal teams. Multi-tenant SaaS with practice-area intelligence, document RAG, matter management, and a Microsoft Word add-in.

---

## What it does

| Capability | Description |
|---|---|---|
| **Legal Research Chat** | Ask legal questions; answers are grounded in your uploaded documents + public case law via pgvector RAG |
| **Practice Area Plugins** | 9 specialised workspaces with cold-start interviews, structured skill prompts, dual LLM tiers, and hard compliance gates |
| **Matter Management** | Litigation portfolio tracker with conflicts check, legal-hold flag, key dates, and append-only event timeline |
| **Contract Renewal Tracker** | Urgency-rated renewal dashboard with automated weekly email alerts |
| **Scheduled Legal Agents** | 4 background agents run weekly: renewal watcher, regulatory monitor, docket watcher, portfolio status digest |
| **M365 & Google Workspace** | OAuth-connected email reading + LLM triage/drafting, calendar sync with deadline push, document sync from OneDrive/SharePoint/Google Drive into RAG, admin user import |
| **Multi-Model AI** | DeepSeek V4 Flash (primary), Claude Opus 4 (premium), Azure OpenAI GPT-4o, Google Gemini 2.0 Flash |
| **Audit & Usage Logging** | Every LLM call is logged with tokens, cost, query text, RAG sources, IP, and user agent |
| **Microsoft Word Add-in** | Chat + Practice Tools panel directly inside Word via Office.js |
| **Billing** | Stripe flat-seat and PAYG metered tiers; 10× PAYG markup on base model cost |

---

## Tech stack

### Backend
| Layer | Technology |
|---|---|
| API | FastAPI 0.115 + uvicorn, async throughout |
| ORM | SQLAlchemy 2.0 async + asyncpg |
| Database | PostgreSQL 16 + pgvector (cosine IVFFLAT, lists=100) |
| Cache / Rate limiting | Redis 7 |
| Auth | JWT (python-jose) + Microsoft Entra OAuth2 + Google OAuth2 |
| Primary LLM | DeepSeek V4 Flash (`deepseek-chat` alias, OpenAI-compatible) |
| Premium LLM | Claude Opus 4 (`claude-opus-4-8`, Anthropic SDK) |
| Enterprise AI | Azure OpenAI GPT-4o + Google Gemini 2.0 Flash |
| Embeddings | OpenAI `text-embedding-3-small` (1536-dim, Phase 1) → BAAI/bge-small-en-v1.5 (384-dim, Phase 2 via Jetson Orin cluster) |
| Task scheduler | APScheduler AsyncIOScheduler |
| Migrations | Alembic (9 migrations, chained) |
| Email | aiosmtplib async SMTP; Slack webhook via httpx |
| Billing | Stripe Python SDK |
| Multi-tenancy | PostgreSQL Row Level Security (RLS) — enforced at DB layer |

### Frontend
| Layer | Technology |
|---|---|
| Framework | React 18 + Vite 6 |
| Styling | Tailwind CSS |
| Routing | React Router v6 |
| HTTP | Axios with Bearer token interceptor |
| Markdown | react-markdown (citation colour-coding) |
| PWA | Web app manifest for desktop install |

### Infrastructure
| Component | Technology |
|---|---|
| Containers | Docker Compose (dev override + prod override pattern) |
| Reverse proxy | Nginx (HTTP-only dev config; TLS 1.2/1.3 + HSTS prod config) |
| SSL | Let's Encrypt via Certbot (webroot mode, auto-renewal cron) |
| CI | GitHub Actions — ruff lint, pytest, Vite build |
| CD | GitHub Actions SSH deploy to VPS |
| Edge embedding | 3× Jetson Orin workers (Phase 2) |

---

## Practice area plugins

Each plugin has a cold-start interview that builds a persistent practice profile, then gates skills against that profile.

| Plugin | Skills |
|---|---|
| **Commercial Legal** | vendor-agreement-review, nda-review, saas-msa-review, renewal-tracker |
| **Privacy Legal** | dpa-review, dsar-response, pia-generation, reg-gap-analysis |
| **Litigation Legal** | matter-intake, portfolio-status, demand-draft, claim-chart, legal-hold |
| **Corporate Legal** | diligence-review, closing-checklist |
| **Employment Legal** | hire-review, termination-review, classification-analysis |
| **Product Legal** | launch-review, marketing-claims-check |
| **IP Legal** | trademark-clearance, fto-analysis, cnd-triage |
| **AI Governance** | use-case-triage, impact-assessment, vendor-ai-review |
| **Regulatory Legal** | reg-gap-analysis, policy-diff, nprm-comment |

All skill outputs include citation tags (`[settled]` / `[verify]` / `[model knowledge]`), dual severity ratings, and a mandatory attorney-review banner.

---

## Multi-tenancy model

- Tenant is auto-created on first OAuth login, keyed by email domain
- PostgreSQL RLS enforces tenant isolation at the database level — not just application logic
- Public case law is accessible to all tenants via sentinel UUID `00000000-0000-0000-0000-000000000001`
- Billing tiers: `flat` (per-seat) and `payg` (10× token markup)

---

## Getting started (dev)

### Prerequisites
- Docker + Docker Compose
- `openssl` (for key generation)

### Setup

```bash
git clone https://github.com/mattpainter701/legalapp
cd legalapp
make setup          # copies .env.example → .env
```

Edit `.env` and fill in at minimum:

```env
ANTHROPIC_API_KEY=sk-ant-...       # Claude Opus (premium LLM)
DEEPSEEK_API_KEY=...               # Primary LLM
OPENAI_API_KEY=sk-...              # Embeddings
SECRET_KEY=<openssl rand -hex 32>
```

```bash
make dev-build      # build images and start (first time)
make dev            # start (subsequent runs)
```

| Service | URL |
|---|---|
| Frontend | http://localhost:3000 |
| API | http://localhost:8000 |
| API docs | http://localhost:8000/docs |
| Nginx (optional) | http://localhost:80 |

### Dev login (no OAuth required)

```bash
curl -X POST http://localhost:8000/api/dev/login \
  -H 'Content-Type: application/json' \
  -d '{"email": "you@yourfirm.com"}'
```

Paste the returned `access_token` into browser localStorage as `token`.

### OAuth (Google / Microsoft 365)

Register redirect URIs in your OAuth app:
- Google Cloud Console → `http://localhost:8000/api/auth/google/callback`
- Azure App Registration → `http://localhost:8000/api/auth/microsoft/callback`

Add `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET` to `.env`.

### Useful make targets

```bash
make migrate        # run alembic migrations
make test           # run pytest suite
make lint           # ruff check
make shell-db       # psql into postgres
make shell-backend  # bash into backend container
make logs           # tail all logs
```

---

## Production deployment

### First deploy (VPS)

```bash
# 1. Point DNS: platform.clarity.legal → VPS IP

# 2. SSH onto VPS, clone repo, create .env.prod
cp .env.prod.example .env.prod
# fill in real values

# 3. Provision TLS certificate
bash nginx/init-letsencrypt.sh platform.clarity.legal admin@clarity.legal

# 4. Start full stack
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Auto-renewal runs every Monday at 03:00 via cron (installed by `init-letsencrypt.sh`).

### CI/CD

Push to `main` triggers:
1. `ci.yml` — lint (ruff) + test (pytest with postgres+redis services) + frontend build
2. `deploy.yml` — SSH deploy to VPS: `git pull` → `docker compose build` → `alembic upgrade head` → `docker compose up -d` → health check → Slack notify

Required GitHub secrets: `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_APP_DIR`, `SLACK_WEBHOOK_URL`.

### On-prem → VPS data sync

```bash
make sync-public-db   # pg_dump public case-law chunks from on-prem, rsync to VPS, pg_restore
```

---

## Project structure

```
legalapp/
├── backend/
│   ├── app/
│   │   ├── models/          # SQLAlchemy models (tenant, user, document, conversation, plugin, scheduler, oauth)
│   │   ├── routers/         # FastAPI routers (auth, chat, documents, plugins, admin, billing, scheduler, dev, integrations, email_agent, document_sync, user_sync)
│   │   ├── services/        # LLM, RAG, embeddings, billing, scheduler, email, plugins, token_vault, email_agent, calendar_sync, document_sync, user_sync, microsoft_mail, google_mail
│   │   │   └── plugins/     # 9 practice area prompts + executor
│   │   ├── middleware/       # Tenant context (JWT) + Redis rate limiter
│   │   ├── schemas/         # Pydantic request/response models
│   │   └── main.py          # App factory, lifespan, middleware wiring
│   ├── migrations/          # Alembic migrations (001–009)
│   └── tests/               # pytest-asyncio suite
├── frontend/
│   ├── src/
│   │   ├── pages/           # Chat, Login, Plugins, PluginDetail, Matters, Renewals, Admin
│   │   └── components/      # Sidebar, SkillOutput, ColdStartInterview, ChatMessage
│   └── public/              # PWA manifest
├── nginx/
│   ├── nginx.conf           # Production (TLS, rate limiting, security headers)
│   ├── nginx.dev.conf       # Development (HTTP only, no certs needed)
│   ├── init-letsencrypt.sh  # First-time cert provisioning
│   └── renew-cert.sh        # Renewal (called by cron)
├── scripts/
│   ├── ingest_courtlistener.py   # Bulk CourtListener case law ingestion
│   ├── jetson_embed_worker.py    # Jetson Orin embedding worker (Phase 2)
│   ├── sync_to_vps.sh            # On-prem → VPS public chunk sync
│   └── deploy_prod.sh            # Production deploy script
├── word-addin/              # Office.js task pane (Chat + Practice Tools tabs)
├── docker-compose.yml       # Base services (postgres, redis, backend, frontend, nginx)
├── docker-compose.override.yml  # Dev: hot reload, HTTP nginx, exposed DB ports
├── docker-compose.prod.yml  # Prod: restart policies, resource limits, no exposed ports
└── Makefile                 # Dev/prod/deploy/test shortcuts
```

---

## Database migrations

| Migration | Description |
|---|---|
| `001_initial_schema` | Core tables (tenants, users, documents, chunks, conversations, messages, usage_records), pgvector IVFFLAT index, RLS policies |
| `002_plugins` | Practice profiles, matters, matter events, renewals + RLS |
| `003_scheduler` | Scheduler logs |
| `004_audit_log` | Audit columns on usage_records (operation_type, query_text, rag_chunks_retrieved, rag_source_ids, ip_address, user_agent) |
| `005_password_and_company` | password_hash to users; company fields to tenants |
| `006_public_chunks` | Public case-law chunks with BGE-384 embeddings |
| `007_tenant_api_key` | API key column on tenants (MCP auth) |
| `008_estate_mediation` | Estate planning + mediation case tables |
| `009_oauth_tokens` | Tenant credentials + user OAuth tokens with Fernet encryption |

---

## License

Private — all rights reserved.
