# Clarity Legal

AI-powered legal platform for in-house and boutique legal teams. Multi-tenant SaaS with practice-area intelligence, document RAG, matter management, MCP integration, and an operator console.

---

## What it does

| Capability | Description |
|-|-|
| **Legal Research Chat** | Grounded in uploaded documents + CourtListener public case law via pgvector RAG; confidence-tagged citations |
| **Practice Area Plugins** | 11 workspaces with cold-start profiles, structured skill prompts, dual LLM tiers, and compliance gates |
| **Matter Management** | Litigation, Trust & Estate, and Mediation portfolios with append-only event timelines |
| **Contract Renewal Tracker** | Urgency-rated dashboard with automated weekly email alerts |
| **MCP (Model Context Protocol)** | Connect external AI tools (Claude, Cursor, custom agents) to your legal knowledge base |
| **Platform / Operator Console** | Multi-tenant admin with usage dashboards, tenant CRUD, and platform-key auth |
| **OAuth + Email Auth** | Microsoft 365, Google, or email/password sign-in; unified signup collects firm profile |
| **Multi-Model AI** | DeepSeek V4 Flash (primary), DeepSeek V4 Pro (premium) via OpenCode.ai; Azure OpenAI + Gemini support |
| **Audit & Usage Logging** | Every LLM call logged with tokens, cost, query text, RAG sources, IP, user agent |
| **Billing** | Stripe flat-seat and PAYG metered tiers |

---

## Tech stack

### Backend
| Layer | Technology |
|-|-|
| API | FastAPI + uvicorn, async throughout |
| ORM | SQLAlchemy 2.0 async + asyncpg |
| Database | PostgreSQL 16 + pgvector |
| Cache / Rate limiting | Redis 7 |
| Auth | JWT + Microsoft Entra OAuth2 + Google OAuth2 + email/password |
| Primary LLM | DeepSeek V4 Flash via OpenCode.ai (OpenAI-compatible) |
| Premium LLM | DeepSeek V4 Pro via OpenCode.ai |
| Enterprise AI | Azure OpenAI GPT-4o + Google Gemini 2.0 Flash (optional) |
| Embeddings | OpenAI text-embedding-3-small for tenant docs; BGE-small 384-dim for CourtListener public chunks |
| Task scheduler | APScheduler AsyncIOScheduler |
| Migrations | Alembic (9 migrations) |
| Billing | Stripe Python SDK |
| Multi-tenancy | PostgreSQL Row Level Security enforced at DB layer |

### Frontend
| Layer | Technology |
|-|-|
| Framework | React 18 + Vite 6 |
| Styling | Tailwind CSS (custom brand palette: sage green / warm gold / navy / cream) |
| Fonts | Inter (body) + Source Serif 4 (headings) |
| Icons | Lucide React |
| Routing | React Router v6 |
| HTTP | Axios with Bearer token interceptor |
| Markdown | react-markdown with citation colour-coding and confidence tags |

### Infrastructure
| Component | Technology |
|-|-|
| Containers | Docker Compose (base + override + hypervisor + prod profiles) |
| Reverse proxy | Nginx (HTTP dev config; TLS 1.2/1.3 prod config) |
| SSL | Let's Encrypt via Certbot |
| CI/CD | GitHub Actions — lint, test, build, SSH deploy |

---

## Practice area plugins (11)

| Plugin | Skills |
|-|-|
| **Commercial Legal** | vendor-agreement-review, nda-review, saas-msa-review, renewal-tracker |
| **Privacy Legal** | dpa-review, dsar-response, pia-generation, reg-gap-analysis |
| **Litigation Legal** | matter-intake, portfolio-status, demand-draft, claim-chart, legal-hold |
| **Corporate Legal** | diligence-review, closing-checklist |
| **Employment Legal** | hire-review, termination-review, classification-analysis |
| **Product Legal** | launch-review, marketing-claims-check |
| **IP Legal** | trademark-clearance, fto-analysis, cnd-triage |
| **AI Governance** | use-case-triage, impact-assessment, vendor-ai-review |
| **Regulatory Legal** | reg-gap-analysis, policy-diff, nprm-comment |
| **Trust & Estate** | estate-portfolio, estate-detail, will-trust-review, probate-tracking |
| **Mediation** | case-portfolio, case-detail, intake-brief, settlement-draft |

All skill outputs include confidence tags (`[settled]` / `[verify]` / `[model knowledge]`) and an attorney-review banner.

---

## Routes

| Path | Auth | Page |
|-|-|-|
| `/` | — | Marketing homepage (unauthenticated); Chat (authenticated) |
| `/login` | — | OAuth + email login |
| `/signup` | — | Unified signup (company info + OAuth or email) |
| `/chat` | ✓ | Main research chat |
| `/plugins` | ✓ | Plugin gallery |
| `/plugins/:name` | ✓ | Plugin detail + skills |
| `/plugins/litigation/matters` | ✓ | Litigation portfolio |
| `/plugins/litigation/matters/:id` | ✓ | Matter detail + timeline |
| `/plugins/commercial/renewals` | ✓ | Renewal tracker |
| `/plugins/trust-estate/estates` | ✓ | Trust & Estate portfolio |
| `/plugins/trust-estate/estates/:id` | ✓ | Estate detail + activity log |
| `/plugins/mediation/cases` | ✓ | Mediation case list |
| `/plugins/mediation/cases/:id` | ✓ | Case detail + session log |
| `/admin` | ✓ admin | Tenant admin (users, usage, model settings) |
| `/billing` | ✓ | Stripe billing management |
| `/mcp` | ✓ admin | MCP API keys + tool docs + connection guide |
| `/platform` | platform key | Operator console (multi-tenant admin) |

---

## Getting started (dev)

### Prerequisites
- Docker + Docker Compose
- Git

### Setup

```bash
git clone https://github.com/mattpainter701/legalapp
cd legalapp
```

Copy and edit `.env`:

```env
DEEPSEEK_BASE_URL=https://opencode.ai/zen/go/v1   # or https://api.deepseek.com/v1
DEEPSEEK_API_KEY=sk-...                            # OpenCode.ai or direct DeepSeek key
PRIMARY_LLM=deepseek-v4-flash
PREMIUM_LLM=deepseek-v4-pro
OPENAI_API_KEY=sk-...                              # Optional — embeddings (falls back to deepseek key)
SECRET_KEY=<random-64-char>
```

```bash
docker compose up -d
```

| Service | URL |
|-|-|
| App (via nginx) | http://localhost |
| API docs | http://localhost:8000/docs |

### OAuth setup

Register redirect URIs in your OAuth providers (use the URL your browser accesses — `localhost:8080` via SSH tunnel, or public DNS):

| Provider | Redirect URI |
|-|-|
| Google | `<FRONTEND_URL>/api/auth/google/callback` |
| Microsoft | `<FRONTEND_URL>/api/auth/microsoft/callback` |

Add `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_TENANT_ID` to `.env`.

Note: Google OAuth does not accept private IPs (172.16.x.x). For remote hypervisors, use an SSH tunnel:
```bash
ssh -L 8080:localhost:80 user@hypervisor-ip -N
# Then access http://localhost:8080 and set FRONTEND_URL/BACKEND_URL accordingly
```

---

## Database migrations

| Migration | Description |
|-|-|
| `001_initial_schema` | Core tables, pgvector index, RLS |
| `002_plugins` | Practice profiles, matters, matter events, renewals |
| `003_scheduler` | Scheduler logs |
| `004_audit_log` | Audit columns on usage_records |
| `005`–`007` | Platform admin, MCP keys, model settings |
| `008_estate_mediation` | Trust & Estate + Mediation tables and routes |
| `009_oauth_tokens` | Encrypted tenant/user OAuth token persistence |

### CourtListener public RAG

CourtListener data is staged in `public_chunks` and embedded on one local NVIDIA Jetson connected to PostgreSQL over the same network.

```bash
python scripts/ingest_courtlistener.py --file /data/courtlistener/opinions.json.gz --batch-size 1000
python scripts/jetson_embed_worker.py --worker-id 0 --total-workers 1 --batch-size 64 --db-url "$DATABASE_URL" --loop
psql "$DATABASE_URL" -f scripts/create_public_chunks_index.sql
```

Full operator notes are in `scripts/courtlistener_jetson_pipeline.md`.

---

## Project structure

```
legalapp/
├── backend/
│   ├── app/
│   │   ├── models/          # SQLAlchemy models
│   │   ├── routers/         # FastAPI routers (auth, chat, documents, plugins, admin, billing, mcp, platform)
│   │   ├── services/        # LLM, RAG, embeddings, billing, scheduler
│   │   │   └── plugins/     # 11 practice area prompts + executor
│   │   ├── middleware/       # Tenant context + rate limiter
│   │   ├── schemas/         # Pydantic models
│   │   └── main.py
│   ├── migrations/          # Alembic (001–009)
│   └── tests/
├── frontend/
│   ├── src/
│   │   ├── pages/           # 20 page components
│   │   ├── components/      # Sidebar, ChatMessage, FileUpload, SkillOutput, ColdStartInterview, legalMarkdown
│   │   └── assets/          # Homepage images
│   └── public/
├── nginx/
├── scripts/
├── docker-compose.yml
├── docker-compose.override.yml
├── docker-compose.hypervisor.yml
└── docker-compose.prod.yml
```

---

## License

Private — all rights reserved.
