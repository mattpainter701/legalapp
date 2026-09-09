# Reducing IONOS release downtime

Planning document for collaboration. Nothing here is implemented. It proposes
sequenced work and records the open questions that need a decision before any
of it is built.

Companion to `docs/IONOS_CUTOVER_RUNBOOK.md`, whose current-state warning
already names this gap:

> `stage` runs the public production Compose deployment with
> `up -d --force-recreate`. It can interrupt customer traffic while services
> rebuild and restart. It is therefore a maintenance operation today, not a
> private candidate stage or zero-downtime deployment.

## Goal and constraints

The firm accepts a short maintenance window. It does not accept a silent
multi-minute outage, and it wants people warned before one starts so they do
not begin work they are about to lose.

- **Acceptable:** a brief, clearly-communicated interruption.
- **Not acceptable:** the current window, or a bare connection failure with no
  explanation.
- **Constraint:** one IONOS Cube M host with local PostgreSQL and Redis. A
  second always-on host is not currently available, so a full blue/green edge
  is out of scope here (see *Deliberately out of scope*).

## What actually happened on the last release

Measured from run
[34398387631](https://github.com/mattpainter701/legalapp/actions/runs/34398387631),
`b0e75ac9`, IONOS `stage`. The whole operation took 10m34s, but the public
ingress outage was the narrower window between nginx being torn down and
nginx accepting traffic again:

| Time (UTC) | Event |
| --- | --- |
| 20:03:59 | `nginx` recreated — **public ingress stops** |
| 20:04:09 | `postgres`, `redis`, `litellm-postgres` restarted (unchanged images) |
| 20:04:20 | core `migrator` starts |
| 20:04:21 | `litellm-migrator` starts |
| 20:05:23 | `litellm-migrator` exits (62s) |
| 20:06:25 | `litellm-schema-migrator` exits (62s) |
| 20:06:26 | core `migrator` exits (2m06s) |
| 20:09:05 | `litellm` healthy (2m40s) |
| 20:09:38 | `backend` healthy (33s) |
| 20:09:45 | `frontend` healthy, `nginx` starts — **ingress restored** |

**Ingress outage: ~5m46s.** The LiteLLM chain — two serial one-shot migrators
plus the proxy's own startup — accounts for **4m44s of it, about 82%**.

## Root causes

Three separate causes, which is why there is no single fix.

### 1. nginx cannot survive a backend restart, so it is torn down with everything else

`nginx/nginx.conf:261-263` declares static upstreams and the file has **no
`resolver` directive**:

```nginx
upstream backend  { server backend:8000;  keepalive 32; }
upstream frontend { server frontend:3000; keepalive 16; }
upstream office_addin { server office-addin:3001; keepalive 8; }
```

nginx resolves those names **once at startup**. If a backend container is
replaced and comes back on a new address, nginx keeps proxying to the old one
until it is reloaded. That is why nginx carries
`depends_on: {backend, frontend, office-addin}: condition: service_healthy` —
without it, nginx would start against upstreams that do not resolve and crash.

The consequence: nginx is structurally forced to wait behind the entire
application health chain, and there is nothing left running to answer a
request or explain the outage.

### 2. The public ingress is gated behind LiteLLM, which the application does not require

`backend` declares `depends_on: litellm: condition: service_healthy`, and
nginx in turn waits on `backend`. So the public site waits on the AI proxy.

The application does not need it. `LITELLM_ENABLED` defaults to `False`
(`backend/app/config.py:220`) and `backend/app/main.py:246` treats that as a
supported mode, logging that AI features are disabled and continuing to serve.
dev1 runs this way permanently.

The compose healthcheck comment is the sharpest statement of the risk
(`docker-compose.hypervisor.yml`):

> A cold start can spend more than seven minutes loading the reviewed model
> registry after Prisma is ready.

`start_period: 480s`. We measured 2m40s; the documented worst case is over
seven minutes. Today that would all be public downtime.

### 3. Services that did not change are recreated anyway

`scripts/deploy_prod.sh:290` is:

```bash
"${compose[@]}" up -d --force-recreate
```

With no service arguments, `--force-recreate` applies to **every** service in
the project, including `postgres`, `redis` and `litellm-postgres`. The script
already computes the set that actually ships a release at line 96
(`release_services`), and already compares per-service image IDs at line 224,
so the information needed to narrow this is present and unused.

Bouncing the production database on every release is unnecessary time and
unnecessary risk.

## Proposed workstreams

Ordered by cost. Each is independently shippable; the estimates are from the
single measured run above and should be treated as indicative.

### A. Stop recreating unchanged services *(small)*

Scope `--force-recreate` to `release_services` rather than the whole project.

- **Effect:** removes the needless PostgreSQL/Redis bounce, ~20s and a class of
  risk that has nothing to do with the release.
- **Risk:** low. Needs a check that a changed `postgres` image still gets
  picked up when it genuinely changes.

### B. Stop gating public ingress on LiteLLM *(small–medium, biggest time win)*

Relax `backend`'s dependency on `litellm` from `service_healthy` to
`service_started`, and let AI endpoints degrade until the proxy is live.

- **Effect:** removes 2m40s (measured) to 8m (documented worst case) from the
  critical path. Backend would instead be gated by core migrations.
- **Estimated ingress outage after A+B: ~3m.**
- **Open question:** what should AI endpoints return while LiteLLM is warming —
  the existing `"disabled"` status from `main.py:757`, or a distinct
  `"starting"` so the UI can say *back shortly* rather than *turned off*?
- **Secondary:** `litellm-migrator` and `litellm-schema-migrator` run serially
  for 62s each. Can they run concurrently, or does the schema reconciliation
  genuinely depend on the Prisma deploy completing?

### C. Keep nginx serving through the deploy *(medium — this is the one that answers the notification ask)*

Give nginx runtime DNS resolution so it survives upstream restarts, then keep
it running and let it explain itself:

1. Add `resolver 127.0.0.11 valid=10s;` (Docker's embedded DNS) and move
   `proxy_pass` onto variables so names are re-resolved at request time.
2. Drop nginx's hard `service_healthy` dependencies and exclude it from
   recreation unless its own image changed.
3. Add `error_page 502 503 504` pointing at a static maintenance page baked
   into the nginx image, so an upstream that is down produces a real page.

- **Effect:** the hard outage approaches zero. Users get a branded *"we will be
  back in a few minutes"* page for the ~3m of workstream B instead of a
  connection failure. This is the difference between an outage and a
  maintenance window.
- **Open question:** on a genuine cold boot (whole host restart) nginx would
  now start before its upstreams exist. With `resolver` this should serve the
  maintenance page and recover, but it needs proving — including that nginx
  does not cache an NXDOMAIN and wedge.
- **Open question:** the maintenance page must not leak build or host detail,
  and should be readable by a client on the portal, not only firm staff.

### D. Warn people before the window opens *(medium, needs product input)*

Workstream C explains an outage in progress. It does not stop someone starting
a fee-agreement send thirty seconds before the stack goes down.

`frontend/src/components/ReleaseAnnouncement.jsx` is an existing precedent for
a dismissible, per-user, server-driven in-app notice, and is the obvious thing
to model on.

Open questions, all genuinely undecided:

- **Lead time.** 15 minutes? An hour? A day for something this short?
- **Trigger.** Automatic when a deploy workflow is dispatched, or a deliberate
  operator action decoupled from the deploy?
- **Audience.** Firm staff only, or portal clients too? A client mid-signature
  is arguably the worst person to interrupt, and the one we currently warn
  least.
- **Content.** Who writes it, and does it need to name what is changing?
- **Blocking vs. advisory.** Should the app actively discourage starting a
  long action (paperwork send, signing) inside the window, or only inform?

### E. Look again at core migrations *(investigation, not yet a proposal)*

Once B lands, core DB migrations (2m06s measured) become the critical path.
Before proposing anything we should know whether that is Alembic replaying 169
revisions, genuine data work, or container startup overhead. Measure first.

## Expected outcome

| Stage | Ingress outage | User experience |
| --- | --- | --- |
| Today | ~5m46s (worst case >10m) | Connection failure, no explanation |
| After A + B | ~3m | Connection failure, no explanation |
| After A + B + C | ~0 hard outage; ~3m degraded | Maintenance page |
| After D | unchanged | Warned in advance, then maintenance page |

## Deliberately out of scope

**Full blue/green.** The runbook already names *"the IONOS blue/green edge
design"* as the eventual answer, and it remains the right long-term shape:
a second app-tier stack validated behind its own Cloudflare Tunnel, with the
Tunnel flipped once acceptance passes and the old stack kept warm for instant
rollback. Both stacks would share the single PostgreSQL, so this is an
app-tier blue/green, not a full duplicate environment.

It is excluded here only because it needs host capacity we do not currently
have. Workstreams A–D are chosen specifically to be worthwhile without it, and
none of them are wasted if blue/green is built later.

**Rolling updates via Swarm or Kubernetes.** A larger change to the runtime
model than this problem justifies on a single host.

**Expand/contract migration discipline.** Not needed while only one version of
the backend runs at a time. It becomes a hard prerequisite the moment
blue/green or rolling updates arrive, and should be revisited then.

## Decisions needed before implementation

1. Is ~3m of maintenance page (A + B + C) an acceptable resting point, or is
   workstream E in scope for this round?
2. Workstream B: `"starting"` vs `"disabled"` for AI endpoints during warm-up.
3. Workstream D: lead time, trigger, audience, and whether it blocks or only
   informs.
4. Does the maintenance page need to reach portal clients, or staff only?
