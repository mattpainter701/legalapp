# Live demo smoke check

`scripts/demo_live_smoke.py` is the safe, operator-run complement to the
deterministic Playwright customer journey. It uses the normal public API and
httpOnly session cookies; it does not add a test-only endpoint, log credentials,
or call an external model.

Run it from the repository root with a disposable demo workspace:

```bash
export DEMO_BASE_URL=https://getlawhand.com
export DEMO_ACCESS_CODE='(retrieve through the approved operator secret path)'
python scripts/demo_live_smoke.py
```

The access code is accepted only through `DEMO_ACCESS_CODE`; there is no
command-line access-code option, so it does not appear in shell history or the
process argument list. HTTPS is required for non-loopback hosts. HTTP is allowed
only for local disposable testing (`localhost`, `127.0.0.1`, or `::1`).

The command provisions one normal disposable demo session, verifies
`/api/auth/me`, and confirms that the fixture clone contains synthetic matters,
conversations, or tasks. The access code is sent only in the bootstrap request;
it is never printed. Use a fresh email when repeating a check. The provisioned
workspace follows the configured demo expiry/cap and should not be reused for
customer presentation.

When `DEMO_EMAIL` is omitted, the smoke harness generates a unique address under
`demo.example.com`. This is a subdomain of the RFC-reserved `example.com`
domain, so the identity is synthetic and cannot be confused with a customer
mailbox, while still satisfying the backend's `EmailStr` request validation.
Use `DEMO_EMAIL` only when the operator has an approved disposable address.

## Manual remainder before customer presentation

The script deliberately does not POST a chat message or approve a task. Those
operations consume quota and can enqueue work, so they require a human to inspect
the live response and exact reviewed version. In the disposable workspace from
the smoke run:

1. Open the cloned synthetic matter named in the first-customer runbook.
2. Ask: `Review the MSA and data addendum for customer-side risks, cite the source clauses, and propose follow-up tasks for the renewal and data-use issues.`
3. Confirm every citation opens the expected synthetic source and the proposal's
   approval effect is clear.
4. Approve the exact reviewed proposal once, then open the task board and confirm
   the matching task is `In Progress`.
5. Confirm no email, calendar, Zoom, MCP, or other external delivery occurred.

This manual provider-backed journey remains a presentation gate. The CI browser
suite is deterministic and intentionally intercepts these records; it does not
prove live LLM/provider generation.
