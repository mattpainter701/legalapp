# Zoom Phone Post-Call Webhook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import authoritative inbound Zoom Phone call records immediately after calls complete.

**Architecture:** Add webhook verification and call-history-detail ingestion to the existing Zoom Phone service, then expose unauthenticated Zoom webhook endpoints in the integrations router. Keep manual sync as a backfill path and reuse `CommunicationLog` idempotent upserts.

**Tech Stack:** FastAPI, SQLAlchemy async, httpx, pytest, existing `TenantCredential` and `CommunicationLog` models.

## Global Constraints

- Use only post-call history events for MVP; do not subscribe to live ringing/answered status events.
- Validate Zoom CRC and signatures with `ZOOM_WEBHOOK_SECRET_TOKEN`.
- Prefer tenant-specific webhook URL `/api/integrations/zoom-phone/webhook/{tenant_id}`; also support account-id mapping when stored.
- Import inbound records only and fetch `GET /phone/call_history_detail/{id}` before upsert.
- Keep manual `Sync Zoom` as reconciliation/backfill.

---

### Task 1: Webhook Crypto Helpers

**Files:**
- Modify: `backend/app/services/zoom_phone.py`
- Test: `backend/tests/test_intake_dashboard.py`

**Interfaces:**
- Produces: `zoom_webhook_validation_response(plain_token: str, secret: str | None = None) -> dict[str, str]`
- Produces: `verify_zoom_webhook_signature(body: bytes, timestamp: str | None, signature: str | None, secret: str | None = None, tolerance_seconds: int = 300) -> bool`

- [x] Write failing tests for CRC and signature validation.
- [x] Implement HMAC-SHA256 helpers.
- [x] Run focused tests.

### Task 2: Post-Call Detail Importer

**Files:**
- Modify: `backend/app/services/zoom_phone.py`
- Test: `backend/tests/test_intake_dashboard.py`

**Interfaces:**
- Produces: `extract_zoom_phone_webhook_call_logs(event: dict[str, Any]) -> list[dict[str, Any]]`
- Produces: `fetch_zoom_phone_call_history_detail(db: AsyncSession, *, tenant_id: str, call_history_id: str) -> dict[str, Any]`
- Produces: `import_zoom_phone_webhook_event(db: AsyncSession, *, tenant_id: str, event: dict[str, Any]) -> ZoomPhoneImportResult`

- [x] Write failing tests for extracting completed inbound call-history logs.
- [x] Implement detail fetch and import orchestration.
- [x] Run focused tests.

### Task 3: FastAPI Webhook Endpoint

**Files:**
- Modify: `backend/app/routers/integrations.py`
- Modify: `backend/app/middleware/tenant.py`
- Modify: `backend/app/middleware/rate_limit.py`

**Interfaces:**
- Produces: `POST /api/integrations/zoom-phone/webhook`
- Produces: `POST /api/integrations/zoom-phone/webhook/{tenant_id}`

- [x] Add route handlers for CRC and completed call-history events.
- [x] Exempt webhook path from tenant JWT and rate-limit middleware.
- [x] Return 200 for accepted/unhandled events and 400/401 for invalid payload/signature.

### Task 4: Status Surface, Docs, Verification, Deploy

**Files:**
- Modify: `backend/app/routers/integrations.py`
- Modify: `TASKS.md`
- Modify: `CHANGELOG.md`

- [x] Include webhook URL in Zoom Phone status.
- [x] Update task tracker and changelog.
- [x] Run compile/tests/build as feasible.
- [ ] Commit, push, and deploy to production.
