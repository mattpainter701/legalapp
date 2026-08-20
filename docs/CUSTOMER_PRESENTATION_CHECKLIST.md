# Customer presentation readiness checklist

This is the evidence checklist for a LawHand customer presentation. It keeps
repository-backed checks separate from operator checks and from checks that
require production credentials or a live provider. A green CI run is not, by
itself, evidence that a live model, mailbox, or Zoom account works.

## Automated evidence

Run these against a disposable environment or CI. Do not point the browser E2E
seed at production.

```bash
# Backend health and public-health response contract
pytest backend/tests/test_health.py

# Demo fixture is synthetic, structured, and metadata-scrubbed
pytest backend/tests/test_demo_fixture_seed.py \
  backend/tests/test_cybersafeadvisor_demo_pack.py \
  backend/tests/test_demo_chat_sources.py

# Demo lifecycle, quota, purge, and tenant isolation
pytest backend/tests/test_demo_lifecycle_db.py

# Browser entry and deterministic chat -> proposal -> task journey
cd frontend
npm run e2e -- e2e/customer-journeys.e2e.js
```

The browser journey in
[`frontend/e2e/customer-journeys.e2e.js`](../frontend/e2e/customer-journeys.e2e.js)
asserts the `/demo` required fields, an access-code error, synthetic-data
disclosures, citation display, proposal review/approval, and movement to the
`In Progress` task board. Its chat, citation, and task responses are intercepted
fixtures. It does **not** prove external model generation, outbound email, or
Zoom delivery; [`frontend/e2e/README.md`](../frontend/e2e/README.md) records
that intentional boundary.

The browser stub mirrors the production API implementation in
[`backend/app/routers/demo.py`](../backend/app/routers/demo.py): an invalid code
returns `401` with `Invalid demo access code`. This remains a deterministic UI
error-state fixture, not a request to production.

For a deployed revision, anonymous checks are limited to these safe routes:

```text
GET /demo             -> Guided demo | LawHand and synthetic-data disclosure
GET /health           -> public health/version metadata
GET /health/llm       -> {"status":"disabled"|"ok"|"degraded"}
GET /health/readiness -> release/operator readiness (inspect only)
GET /api/version      -> deployed commit metadata
```

`/health/llm` deliberately returns HTTP 200 for all three states and never
returns provider URLs, credentials, or exception details. A status of
`disabled` supports a UI-only walkthrough, not a claim that live AI works.

## Operator-required evidence

Capture the output for the exact deployed release; these require host access
and must not be inferred from CI:

```bash
ENV_FILE=.env COMPOSE_FILE=docker-compose.hypervisor.yml \
  bash scripts/prod_env_preflight.sh

bash scripts/rehearse_fresh_host.sh
FRESH_HOST_TOPOLOGY=base-prod bash scripts/rehearse_fresh_host.sh

ENV_FILE=.env COMPOSE_FILE=docker-compose.hypervisor.yml \
  bash scripts/production_check.sh
```

Before presenting a first customer, also retain fresh off-host backup/restore
attestation from [`scripts/backup_db.sh`](../scripts/backup_db.sh) and
[`scripts/restore_rehearsal.sh`](../scripts/restore_rehearsal.sh), prove the
host disk timer acceptance in
[`docs/FIRST_CUSTOMER_PRODUCTION_RUNBOOK.md`](FIRST_CUSTOMER_PRODUCTION_RUNBOOK.md),
and verify the public production-health issue workflow. The strict production
check requires the configured launch tenant and Zoom Phone proof when
`ZOOM_REQUIRED=true` (the default).

## Credentialed gates still blocking a live claim

These are not replaceable with mocked E2E tests:

- After the live-smoke harness is integrated, run `python scripts/demo_live_smoke.py`
  using the operator-held `DEMO_ACCESS_CODE`. Follow `docs/demo_live_smoke.md`
  for the exact assertions. This is an
  operator-only check: use synthetic demo data, never print or commit the code,
  and do not perform provider configuration, credential rotation, or other
  live-provider mutation from the harness.
- Run the `/demo` session with the operator-held access code, then verify the
  seeded Northstar matter, source-document links, quota banner, and expiry.
- Submit the documented review prompt through the real configured LLM and
  verify real citations and a proposal against the synthetic documents.
- Approve the reviewed proposal and verify the persisted task transition; do
  not describe the deterministic fixture as live generation.
- For a first customer launch, complete the real Zoom Phone call -> webhook ->
  exact provider fetch -> Call Intake -> assigned Task path, with assignment
  durability and the intentional `EMAIL_ENABLED=false` behavior verified.
- Rotate exposed production secrets and complete provider reconnect tests before
  calling the installation customer-safe. Do not print or commit secret values.

Until the credentialed gates and the operator gate pass, the safe claim is:
“the customer journey is automated and UI-tested in a disposable environment;
live AI/integration behavior is pending operator verification.”

## Presentation decision

Go for a live customer presentation only when:

1. the exact deployed commit reports readiness `ok` and `/api/version` matches;
2. the disposable CI/browser evidence above is green;
3. the operator preflight, fresh-host/restore evidence, and strict production
   check are green; and
4. the live AI and (where sold) Zoom smoke has been completed with synthetic or
   approved customer data and recorded in the release evidence.

If item 4 is not complete, present the deterministic UI walkthrough and label
the AI/integration portions as pending; do not improvise with customer data.
