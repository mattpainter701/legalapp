# Live demo operations runbook

For the evidence boundary and presentation go/no-go gates, use
[`CUSTOMER_PRESENTATION_CHECKLIST.md`](CUSTOMER_PRESENTATION_CHECKLIST.md).

## Enable the feature

1. Create a synthetic fixture from the API container:

   ```bash
   cd /app
   python scripts/seed_demo_fixture.py --domain lawhand-demo-fixture-v2.invalid
   ```

2. Inspect the fixture in the product. Confirm every person, company, matter,
   document, email address, and phone number is fictional. Never connect cloud
   storage, Teams, SMB, QuickBooks, Stripe, OAuth, or an API key to this tenant.
3. Set these API environment secrets and restart the API processes:

   ```dotenv
   DEMO_MODE_ENABLED=true
   DEMO_ACCESS_CODE=<random value of at least 16 characters>
   DEMO_FIXTURE_TENANT_DOMAIN=lawhand-demo-fixture-v2.invalid
   DEMO_SESSION_TTL_HOURS=72
   DEMO_MESSAGE_QUOTA=20
   DEMO_MAX_ACTIVE=10
   ```

4. In **Platform → AI Provider Routing**, select or create the profile intended
   for demos. Enable **Allow confidential matter context on Standard**, validate
   and activate the route, then choose **Use for demos**. The selected profile
   must contain only targets approved for confidential customer data. This
   assignment applies to newly created demo workspaces.
5. Open `/demo` in a private browser window and complete the smoke test below.
   Passcode changes require an API restart because settings are cached.

### dev1

Step 1 is automatic on dev1. Once `DEMO_MODE_ENABLED=true` is set in the
deployment environment file, `scripts/deploy_dev1.sh` seeds the configured
fixture domain after the health check and fails the deploy if seeding does not
succeed. The seed never overwrites an existing fixture, so a redeploy leaves a
working demo untouched; a rebuilt volume is reseeded without a manual step.

Set the environment keys first: the deploy refuses to start when demo mode is
on with a missing or placeholder access code or no fixture domain, because the
API raises on that combination at import and would otherwise crash-loop behind
the health check. dev1 has no working OAuth provider, so the demo session is
the only way in and this configuration is not optional.

## Salesperson smoke test

- Create a session with the shared code and a test contact identity.
- Confirm the amber demo banner shows `0 of 20` and approximately `72h`.
- Sign out, return to `/demo`, choose **Resume demo**, and enter the same email
  and demo access code. Confirm the same workspace opens with its original
  expiry and usage count. Email alone must never resume a workspace.
- Confirm the conversation list is empty; no prior prospect or fixture chat is
  shown.
- Open the **Northstar Analytics - SaaS Vendor Review** matter and its two
  source documents.
- Ask Standard AI: `Review the MSA and data addendum for customer-side risks,
  cite the source clauses, and propose follow-up tasks for the renewal and
  data-use issues.`
- Confirm the answer cites the private synthetic documents, source links open
  through the authenticated document endpoint, and the banner count advances
  after refresh.
- Repeat with HarborLight or Redwood to confirm separate matter scope and a
  clean, citation-first workflow.
- Open Matters, Tasks, Calendar, Contacts, Billing, and one plugin workflow.
- Confirm Premium requests and live integration endpoints are rejected.
- Confirm Chat reports that approved synthetic matter context is available and
  that private-detail protection is enforced. The privacy control must not be
  switchable off in a disposable demo.
- Never enter prospect, client, employee, or active-case information. The name
  and email used to enter the demo are deleted with the workspace; they are not
  a consented CRM lead record.

## Rotation and fixture releases

- Rotate `DEMO_ACCESS_CODE` whenever it is shared outside the sales team, then
  restart all API workers.
- The fixture seed command is create-only. Publish a changed fixture under a new
  `.invalid` domain, validate it, switch `DEMO_FIXTURE_TENANT_DOMAIN`, and restart.
- Keep the old fixture until every session cloned from it has expired. A demo
  session stores its source fixture ID and purge will never delete that tenant.

## Purge and incident checks

- `demo-session-purge` runs hourly under the scheduler advisory lock.
- A purge requires all three markers: `billing_tier=demo`, a `.demo.invalid`
  domain, and a valid `DemoSession` whose fixture ID differs from its tenant ID.
- Success and failure are recorded in `operator_audit_logs` without prospect
  name or email. A failed purge leaves the tenant inactive and is safe to retry.
- To disable new demos immediately, set `DEMO_MODE_ENABLED=false` and restart.
  Existing sessions remain subject to their original expiry and purge schedule.
- If verification reports survivor rows, do not manually delete the tenant.
  Add the missing dependency/table handling, deploy it, and let the job retry.
