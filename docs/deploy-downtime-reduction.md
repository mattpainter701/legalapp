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

### 2. The public ingress is gated behind LiteLLM, against the application's own design

**Both `backend` and `scheduler`** declare
`depends_on: litellm: condition: service_healthy`, and nginx in turn waits on
`backend`. So the public site waits on the AI gateway.

The application is already written on the opposite assumption. It treats a
LiteLLM outage as a degraded state, not a fatal one:

- `LITELLM_ENABLED` defaults to `False` (`backend/app/config.py:220`) and
  `backend/app/main.py:249` treats that as a supported mode, logging that AI
  features are disabled and continuing to serve. dev1 runs this way
  permanently.
- `app/services/ai_request_broker.py` bounds every call with a timeout and
  catches `httpx.TimeoutException`, `TransportError` and `HTTPStatusError`.
- `main.py:759-763` exposes three states — `disabled`, `ok` and **`degraded`**
  ("gateway ping failed") — and the endpoint deliberately returns HTTP 200,
  documented as being **"so load balancers do not flip on gateway hiccups"**.

That last line states the intent outright: LiteLLM going away should not take
the site with it. The Compose dependency contradicts it. Relaxing the gate is
therefore not a loosening of safety — it makes the topology agree with what the
code already does.

Worth recording: `docker-compose.prod.yml:27-29` **already removed** the
`backend → litellm` dependency in the layered non-IONOS files, and runtime
readiness never included LiteLLM — `/health/readiness` (`main.py:610-743`)
probes disk, database, redis, scheduler and queue only, and
`app/services/readiness_wait.py` polls that endpoint. Only
`docker-compose.hypervisor.yml` — the standalone file IONOS actually runs —
still gates both `backend` and `scheduler` on `litellm: service_healthy`, and
only the lifespan probe at `main.py:266-272` treats gateway reachability as
log-and-continue. The topology the plan proposes is the one the rest of the
system already assumes.

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

Relax the `litellm` dependency from `service_healthy` to `service_started` on
**both `backend` and `scheduler`**, and let AI features report themselves as
degraded until the gateway is live.

- **Effect:** removes 2m40s (measured) to 8m (documented worst case) from the
  critical path. Backend would instead be gated by core migrations.
- **Estimated ingress outage after A+B: ~3m.**
- **Resolved:** an earlier draft asked whether AI endpoints need a new
  `"starting"` status. They do not — `main.py:763` already defines
  **`degraded`** for exactly this ("gateway ping failed"). The UI work is to
  surface the state that already exists, not to invent one.
- **Secondary:** `litellm-migrator` and `litellm-schema-migrator` run serially
  for 62s each. Can they run concurrently, or does the schema reconciliation
  genuinely depend on the Prisma deploy completing?

**B is not only a Compose change.** The `service_healthy` gate is currently the
only thing keeping a latent defect unreachable. During warm-up an AI request
gets connection-refused, which surfaces as:

`httpx.TransportError` → `AIRequestUnknown` → `quota_ledger.mark_unknown(reservation)`

and that exception is documented *"The provider may have accepted work; do not
retry automatically."* Remove the gate as-is and every release leaves quota
reservations parked in **unknown** with retries blocked, for requests that
provably never reached a provider.

`ai_request_broker.py:610` conflates two different situations, because
`ConnectError` and `TimeoutException` are both `TransportError`:

| Situation | Truth | Correct handling |
| --- | --- | --- |
| Connection refused (gateway not listening) | Request definitively never left | Release the reservation; allow retry |
| Timeout after connect | Genuinely ambiguous | `mark_unknown`; block retry |

So **B = the Compose change *plus* distinguishing "never sent" from "unknown"
in the broker.** This is a defect B would expose rather than create; it exists
today and is simply unreachable behind the gate.

Blast-radius note from validation: parked `unknown` reservations are not
permanent. The scheduler's `background-ai-reconcile` job (15-minute interval)
queries the LiteLLM spend ledger and settles or releases them automatically
once the gateway is back (`scheduler.py:697-712`). The broker fix remains
correct — a request that provably never left should release its reservation
immediately rather than wait up to 15 minutes and a ledger round-trip — but
the failure mode is *temporary quota held*, not *quota lost*.

### B2. Separate LiteLLM's release cadence *(medium — highest leverage per unit of work)*

LiteLLM is simultaneously **the slowest component to start** (2m40s measured,
plus two 62s migration jobs, documented worst case over seven minutes) and
**the component that changes least often** — it moves when model configuration
or the reviewed registry changes, not when matter-workspace code ships.

Rebuilding and restarting it on every application release pays its full startup
cost for no benefit on the great majority of deploys.

It is already well separated structurally: its own service, its own PostgreSQL
(`litellm-postgres`), its own migration jobs and its own healthcheck. What is
coupled is lifecycle, not architecture. Giving it a deploy path of its own
would take it off the critical path for most releases entirely, and composes
with B rather than replacing it.

#### How CI would know when to deploy the gateway

B2 is only safe if this decision is reliable. Two mechanisms, and the obvious
one is the wrong one:

- **Path filters** (`litellm/**`, `litellm_config.yaml`) — **rejected as the
  primary mechanism.** The service builds with `context: .`, so its true input
  set is whatever the Dockerfile copies, not a directory. A hand-maintained
  filter silently drifts from that and fails open: a missed path means the
  gateway silently does not get the change it needed.
- **Content-addressed image comparison** — build the image, compare its ID with
  the one the running container was created from, and recreate only on a real
  difference. This cannot drift, because the image *is* the input set.
  `scripts/deploy_prod.sh:224` already reads per-service image IDs, so the
  primitive exists.

**The build input set is now confirmed** (the investigation item this rested
on, resolved by reading `litellm/Dockerfile` against the compose build
declaration):

- Base image is pinned by digest (`docker.litellm.ai/...:main-latest@sha256:…`).
- The Dockerfile COPYs exactly four files:
  - `litellm_config.yaml` — **at the repo root, outside `litellm/`**
  - `litellm/reconcile_schema.sh`
  - `litellm/litellm_schema_repair_v1_93_0.sql`
  - `litellm/runtime_entrypoint.sh`
- No `ARG` declarations; `APP_COMMIT` affects only the image **tag**, never
  its content (verified: no commit/version build args anywhere in the
  Dockerfiles).

Two consequences:

1. A `litellm/**` path filter would miss `litellm_config.yaml` — the fail-open
   risk is concrete, not hypothetical. The true input set spans a repo-root
   file.
2. Docker image IDs are **not reproducible** (layer/config metadata carries
   build timestamps), so "build, then compare the image ID with the running
   container's" always differs and degenerates to always-redeploy. Image-ID
   comparison at `deploy_prod.sh:224` is sound for *rollback tagging* (both
   sides already exist) but not for *change detection* (one side is freshly
   built).

**Recommended mechanism: content-hash tagging.** Compute a hash over exactly
the confirmed input set — the four copied files, the Dockerfile, the pinned
base digest, and (for completeness) the frontend-style build args, of which
there are none — and tag `legalapp-litellm:src-<hash>`. The deploy script
skips the gateway's build/recreate when that tag already exists on the host.
Identical inputs produce an identical tag, the skip is self-evident, and
nothing can drift because the hashed set *is* the Dockerfile's COPY list; a
pre-commit check can assert the hash script's file list still matches the
Dockerfile's COPY directives. Keep the commit tag as a secondary label for
traceability if wanted, but never as the change-detection key.

#### Model aliases are data, not image — which resolves the skew question

The service sets `STORE_MODEL_IN_DB: "True"`. The model registry is
authoritative in LiteLLM's own database, with `litellm_config.yaml` acting as
bootstrap. Most routing and alias changes are therefore **data changes that
need no redeploy at all**, which strengthens the case for B2: the image
genuinely changes rarely.

It also reframes the version-skew risk. Skew is a *data-ordering* problem, not
an image-versioning one: does the alias exist in the gateway before the release
that references it? Proposed rule, for discussion:

1. Alias additions land in the gateway ahead of the application release that
   uses them.
2. The application treats an unknown alias as `degraded` rather than an error,
   so the ordering is forgiving rather than a hard coupling.

- **Open question:** the spend ledger lives in LiteLLM's database. What is the
  continuity requirement across the gateway's own deploys, and does a
  gateway-only deploy need its own data guard?

#### What LiteLLM actually is, for scoping purposes

It is worth being accurate, because "it is just the chat assistant" would
under-scope this. LiteLLM is the AI **gateway** for eleven LLM-calling
surfaces — chat, RAG and the retrieval planner, embeddings, assistant document
revision, Template Studio AI, the Office add-in assistant, the email agent,
call-intake preparation, Firm Memory auto-summarization, the plugin executor,
and prompt-admin test/preview.

One correction from validation: an earlier draft listed MCP. MCP has **no
direct LiteLLM call** — its product price is an upstream-API call fee, not
model inference — so it is a surface the gateway's *availability* matters to
only indirectly. If B2 ships, the MCP documentation note in the PR handoff
should be reworded accordingly.

It also carries responsibilities that are not inference at all:

- **model routing and aliasing** across the standard/premium/background tiers
  (`app/services/llm_routing.py`);
- the **spend ledger**, which `app/services/billing.py:62` calls *"the
  canonical reconciliation source"*;
- **quota enforcement** (`background_ai_quota.py`) and **gateway privacy**
  (`gateway_privacy.py`).

So it holds billing-relevant state, not only prompts. Two mitigating facts
keep it off the critical path regardless:

1. Billing reconciliation runs from the scheduler as a background job, not on
   any request path: `scheduler.py:697-712` registers
   `background-ai-reconcile` on a **15-minute interval**, which queries the
   LiteLLM spend ledger and settles or releases ambiguous reservations
   (`background_ai_reconciliation.py:235`). This also bounds the blast radius
   of the workstream B warm-up defect: reservations parked as `unknown` are
   swept automatically once the gateway is back, so the broker fix is about
   correctness and latency, not about preventing permanent quota loss.
2. Embeddings fall back to a direct provider (OpenAI, then OpenRouter) when
   LiteLLM is **disabled** (`embeddings.py:40-65`) — verified. Note the
   limitation, which an earlier draft overstated: when LiteLLM is *enabled but
   unreachable at request time*, there is no runtime fallback; `embed_text`
   catches the failure and returns `None` (`embeddings.py:153-155`). That is
   still graceful degradation, not an outage, but it belongs in workstream B's
   UI scope: an embeddings outage is silent today.

No non-AI workflow depends on it. Matters, documents, the client portal,
signing, tasks, intake and invoicing all function without it; the Jane Doe
onboarding journey touches it at zero points.


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
- **Defect found during validation, fix inside C:** `office-addin` declares
  **no healthcheck** (`docker-compose.hypervisor.yml:358-370`), so nginx's
  `condition: service_healthy` on it has no health status to key on. Whatever
  Compose does with that today, it is not doing it on purpose. C should either
  add an office-addin healthcheck or drop that edge when nginx's hard
  dependencies go.
- **Open question:** the maintenance page must not leak build or host detail,
  and should be readable by a client on the portal, not only firm staff.

### D. Warn people before the window opens *(medium, needs product input)*

Workstream C explains an outage in progress. It does not stop someone starting
a fee-agreement send thirty seconds before the stack goes down.

`frontend/src/components/ReleaseAnnouncement.jsx` is an existing precedent for
a dismissible, per-user, server-driven in-app notice, and is the obvious thing
to model on. Validation added one relevant fact: it **skips the client role
entirely** (`ReleaseAnnouncement.jsx:39`) — the current precedent is
staff-only. That is a decision made by default today, and workstream D should
revisit it deliberately rather than inherit it.

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
Before proposing anything we should know whether that is Alembic replaying 173
revision files (`backend/migrations/versions/`, head revision `169`), genuine
data work, or container startup overhead. Measure first.

## Expected outcome

| Stage | Ingress outage | User experience |
| --- | --- | --- |
| Today | ~5m46s (worst case >10m) | Connection failure, no explanation |
| After A + B | ~3m | Connection failure, no explanation |
| After A + B + C | ~0 hard outage; ~3m degraded | Maintenance page |
| After D | unchanged | Warned in advance, then maintenance page |
| After B2 | ~3m becomes the exception, not the rule | Most releases never restart the gateway |

B2 is orthogonal to the others: it does not shorten a release that genuinely
changes LiteLLM, it removes the gateway from the critical path of every release
that does not.

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

## Validation record

Every code reference above was re-verified against the tree on
2026-09-10 (branch `claude/jane-doe-acceptance-pass-mjzhj7`, head
`8c925da3`), including line numbers against `origin/main`, on which they
still hold. Twelve of the plan's load-bearing claims checked out exactly.
The corrections from validation are already folded into the text above;
they are collected here so a reader can see what changed and why:

1. **Embeddings fallback is disabled-only, not outage-time**
   (`embeddings.py:40-65` vs `:153-155`). Corrected in the LiteLLM scoping
   section; adds silent-embeddings-failure to B's UI scope.
2. **MCP is not a direct LLM surface.** The eleven-surface list swapped MCP
   for prompt-admin test/preview. The PR's MCP handoff note needs rewording
   when B2 ships.
3. **Alembic count:** 173 revision files under `backend/migrations/versions/`,
   head revision `169` — not "169 revisions".
4. **`office-addin` has no healthcheck**, so nginx's `service_healthy` edge to
   it is accidental, not deliberate. Folded into C as a fix item.
5. **No commit/version build args are baked into any image** — favorable for
   change detection, but Docker image IDs are not reproducible, so image-ID
   comparison cannot drive skip decisions. This is what elevates content-hash
   tagging from "better" to "the mechanism". Folded into B2.
6. **The 15-minute `background-ai-reconcile` sweep exists** and bounds the
   warm-up quota defect to temporary quota hold, not loss. Folded into B.
7. **`docker-compose.prod.yml` already removed `backend → litellm`** and
   runtime readiness never included LiteLLM — only the hypervisor file gates
   on it. Folded into root cause 2.
8. **`ReleaseAnnouncement` skips the client role** (`:39`). Folded into D.

## Decisions needed before implementation

1. Is ~3m of maintenance page (A + B + C) an acceptable resting point, or is
   workstream E in scope for this round?
2. Workstream B2: is separating LiteLLM's release cadence in scope for this
   round, and how do we handle model-alias skew and spend-ledger continuity?
3. Workstream D: lead time, trigger, audience, and whether it blocks or only
   informs.
4. Does the maintenance page need to reach portal clients, or staff only?

**Recommended resolutions** (added after validation; owner to confirm):

1. **Accept A + B + C as this round's target; E stays measurement-only.**
   Once B lands, one timed run tells us what the 2m06s migrator is made of —
   that measurement is an hour of work, not a workstream. Optimization
   proposals wait for the data.
2. **B2 is in scope, with content-hash tagging as the mechanism** (see that
   section). Alias skew is handled as a data-ordering rule, and the spend
   ledger needs no new guard beyond what exists: gateway deploys never touch
   `litellm-postgres` (it is excluded from recreation once A lands, and B2
   recreates the proxy only), the existing pre-deploy data guard already
   dumps and counts the LiteLLM database, and the 15-minute reconciliation
   sweep self-heals ledger gaps. Rollback for a gateway-only deploy reuses
   the existing rollback-manifest pattern keyed by gateway input hash
   instead of app commit.
3. **D, v1: trigger on the stage dispatch itself.** A stage run is already a
   deliberate, confirmation-gated operator action
   (`deploy-ionos-candidate.yml` requires typing `STAGE-IONOS-CANDIDATE`),
   so the workflow start *is* the notice event — no new trigger machinery.
   The release-gate job passes minutes before the ionos job mutates
   anything; posting the banner at gate pass gives natural lead time
   (operator dispatch → gate → banner → deploy). Advisory, not blocking, for
   v1: discourage starting long actions inside the window, never hard-block
   clients mid-signature. Lead time is then whatever the operator chooses;
   recommend documenting "dispatch when the window is acceptable" rather
   than building timers.
4. **The maintenance page is edge-served, so it reaches portal clients
   automatically** — nginx answers every route including the portal. That is
   the point of C. The deliberate choice left for D is whether the *advance
   in-app banner* also goes to clients, given `ReleaseAnnouncement`
   currently excludes them.

## Remaining steps

Decided: **both B and B2 are in scope**, with B2's change detection specified
as content-hash tagging over the confirmed input set. Nothing below is
implemented. Investigation items 1–3 from the previous draft are resolved
above (build input set confirmed; tagging scheme chosen; migration
measurement defined). Still open before code:

### Needs a decision

1. **Where the gateway-deploy decision lives** — recommendation: inside the
   existing deploy script and workflow. The decision is a tag-existence
   check on the IONOS host inside `deploy_prod.sh`, where the images and the
   rollback manifest already live; a separate workflow would re-implement
   host state checks that the script already has. The workflow gains no new
   surface — it still dispatches one `stage` operation.
2. **Rollback for a gateway-only deploy** — reuse the existing rollback
   manifest, keyed by gateway input hash alongside the app-release entries.
   Confirm the retention/pruning story so a rollback tag is not pruned
   before it can be used.
3. **Workstream D** — per the recommendations above: trigger on dispatch,
   advisory-only v1, audience decision (staff-only vs. include clients) is
   the one genuinely product-facing call left.

### Sequencing

A and B are independent of B2 and can land first; B2 changes the deployment
model and should not gate the downtime fix. Suggested order:

**A** (scope `--force-recreate`) → **B** (readiness + the broker fix) →
**C** (nginx resolver and maintenance page) → **B2** (gateway release cadence)
→ **D** (advance notice) → **E** (core migrations, if the measurement
warrants it).

A and B are each small and land in the same two files (compose + deploy
script, plus the broker for B); they can ship as one PR or two, but B's
broker fix must not ship without B's compose change, and vice versa — the
defect is only unreachable behind the gate today.

C is the one that turns an outage into a maintenance window, so it should not
slip behind B2 despite being listed after it.
