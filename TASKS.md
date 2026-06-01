# TASKS.md

## Completed

### CourtListener Public RAG
- [x] Align CourtListener ingest, Jetson embedding, and RAG search around `public_chunks` BGE-384 vectors

### M365 + Google Workspace Integration
- [x] Phase 1: OAuth token persistence (tenant/user token tables, Fernet encryption, token vault, integration connect/disconnect/status API)
- [x] Phase 2: Email agentic pipeline + Calendar sync (M365/Google mail read, LLM classification + draft responses, calendar read/write + deadline sync)
- [x] Phase 3: Document sync for RAG (OneDrive, SharePoint, Google Drive listing + download + ingest into RAG pipeline)
- [x] Phase 4: Gemini + Azure OpenAI LLM providers (added to LLMService with provider routing)
- [x] Phase 5: Admin user sync dashboard (M365/Google Workspace user import via Directory API)

### Auth System
- [x] Add password_hash to User model (005 migration)
- [x] Add company fields to Tenant model (005 migration)
- [x] POST /auth/register endpoint (email/password + company details)
- [x] POST /auth/login endpoint (email/password with bcrypt)
- [x] POST /auth/forgot-password endpoint (reset token generation)
- [x] POST /auth/reset-password endpoint (token + new password)
- [x] Login: is_active check
- [x] Login: scope query by created_at desc + limit(1)
- [x] Registration: reuse existing domain tenant
- [x] JWT: add iat and jti claims
- [x] Logout: token blacklist via Redis (fallback in-process dict)

### Frontend
- [x] SignupPage with company details form
- [x] LoginPage with email/password + forgot password link
- [x] ForgotPasswordPage (token display in dev mode)
- [x] ResetPasswordPage (token + new password form)
- [x] App.jsx routes: /signup, /forgot-password, /reset-password
- [x] api.js: register, login, forgotPassword, resetPassword functions

### Infrastructure
- [x] Add no-bind local Docker Compose mode for engines that cannot mount Windows workspaces
- [x] Fix Sidebar: Array.isArray guards for documents/conversations
- [x] Fix passlib→bcrypt in requirements.txt
- [x] Fix reset token visibility (DEV_MODE check)
- [x] Fix TTL garbage collection for fallback dicts
- [x] Fix CORS origins for hypervisor IP
- [x] Add healthchecks to docker-compose (postgres, redis, backend, frontend)
- [x] Production frontend Dockerfile (multi-stage build + serve)
- [x] Nginx proxy for /health, /docs, /openapi.json, /redoc
- [x] Deploy to hypervisor (172.16.16.202)

### Security
- [x] Regenerate SECRET_KEY on hypervisor
- [x] Set DEV_MODE=false on hypervisor
- [x] Remove credentials from .env
- [x] Set FRONTEND_URL/BACKEND_URL correctly
- [x] PRIMARY_LLM→deepseek-chat

## Pending

### Enhancements
- [ ] Email verification on registration (requires SMTP)
- [ ] Rate limiting on auth endpoints
- [ ] OAuth provider credential setup (Google, Microsoft)
- [ ] Email notifications for password reset (currently dev-mode only)

### Future
- [ ] Production static file serving (nginx directly serves Vite dist)
- [ ] Backup strategy for postgres
- [ ] Monitoring / observability
- [ ] CI/CD pipeline
- [ ] HTTPS certificate automation (Let's Encrypt)
