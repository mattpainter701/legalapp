## Unreleased — Client correspondence can no longer fall back to the platform relay

- Gate `connected_mail.send_client_email`'s SMTP fallback behind `CLIENT_MAIL_SMTP_FALLBACK_ENABLED`, off by default. The fallback assumed `EMAIL_*` might be a firm's own mail server, but there is no per-tenant SMTP configuration: on the hosted product `EMAIL_*` is LawHand's relay, so a tenant with no Microsoft or Google grant would have had a client's matter correspondence sent from a LawHand address.
- It was inert only because outbound email was disabled. Turning the relay on for system email would have silently activated a client-mail path that sends under the wrong identity — a regression introduced by enabling something unrelated, which is why this is gated rather than documented.
- With the gate off, a firm with no connected mailbox now receives `UNCONFIGURED` and a message telling them to connect one, instead of a letter going out as LawHand. Turn it on only for a single-firm deployment whose `EMAIL_*` really is that firm's own mail server.
- Tests: `test_connected_mail.py` pins both halves — that the platform relay is never used for client mail by default, and that an operator who opts in still gets the legacy path. The two `test_task_automation.py` cases that reached SMTP through the fixture tenant's missing grant now opt in explicitly, since they are about delivery semantics rather than transport.

## Unreleased — Separate sending identities for security and notification mail
- Teach the production ops scripts about the second identity. `scripts/prod_env_preflight.sh` pinned `EMAIL_FROM` to `support@getlawhand.com` via the same `operator_email` variable that pins `VITE_CONTACT_URL`, so one value served as both the public contact address and the system-mail sender. It now requires `EMAIL_FROM=notifications@`, `EMAIL_FROM_SECURITY=security@`, and that the two differ; `VITE_CONTACT_URL` stays pinned to `support@`. Caught by a real deploy: the preflight rejected the intended `notifications@` and an operator set `support@` to get past it, which is precisely the collapse the split exists to prevent.
- `scripts/production_check.sh` folds `EMAIL_FROM_SECURITY` into the runtime SMTP fingerprint the backend and scheduler must agree on, so the two processes cannot send password resets from different addresses without the check noticing. `scripts/rehearse_fresh_host.sh` uses the new addresses, and `test_production_ops.py` pins both rules.

- Add `EmailCategory` and `EMAIL_FROM_SECURITY`. Account-security mail (password reset in `routers/auth.py`, address verification in `routers/user_aliases.py`) now sends from `security@getlawhand.com` while every other system message sends from `notifications@getlawhand.com`. The split is by purpose and it is the whole point of having two addresses: a user who filters or mutes "task due" and "document ready" mail must not thereby filter their own password reset.
- This is a live concern rather than a future one. The platform relay already carries task reminders, agent digests, renewal alerts, e-signature notices, trial mail, client-portal alerts and conversion-loop mail alongside auth — one identity for all of it meant one filter rule could take out account recovery.
- `send_email` defaults to `EmailCategory.NOTIFICATION`, so a newly added caller cannot silently borrow the security identity; the two auth senders pass `SECURITY` explicitly. An empty `EMAIL_FROM_SECURITY` collapses both categories onto `EMAIL_FROM`, keeping a single-identity deployment valid, and a malformed one is rejected at boot rather than sending with an empty From header.
- Neither address needs a mailbox for sending — domain verification authorizes that — but the runbook now recommends adding both as aliases on the existing `support@` mailbox so replies do not bounce. For `security@` that is worth more than politeness: "I did not request this password reset" is the earliest signal of a credential-stuffing run or an account takeover attempt, and it is also the address people conventionally use to report a vulnerability.

## Unreleased — System email: a boot-time preflight and a bounce suppression list

- Add `EMAIL_REQUIRED`, validated by `validate_platform_email_settings` at startup. System email carries password resets, and its failure mode is silent by construction: with `EMAIL_ENABLED=false` the send is a typed non-success that `POST /api/auth/forgot-password` discards, still answering "If that email exists, a reset link has been sent" — correct for account enumeration, indistinguishable from working delivery for an operator. A deployment that sets `EMAIL_REQUIRED` now refuses to boot unless delivery is enabled and authenticated, rather than serving accounts that can never recover a password. It also rejects a half-supplied credential pair and a placeholder `EMAIL_PASS`. It is opt-in through `.env` (the production template sets it) and the Compose files default it to `false`: the check is fail-closed by design, so defaulting it to `true` in Compose would mean any host whose `.env` predates this variable refuses to boot on the first deploy of this change. dev1 and CI leave it false deliberately, which is also why the check is an explicit flag rather than inferred from a non-loopback URL.
- Add a platform suppression list (`email_suppressions`, migration `182_platform_email_suppression`, `down_revision 181_session_epoch`) and enforce it in `EmailService.send_email` behind the new `EmailDeliveryResult.SUPPRESSED`. Repeatedly mailing an address that has permanently failed is what destroys a sending domain's reputation, and one domain carries password reset for every customer, so a hard bounce is recorded once and every later send is filtered against it. A send whose recipients are all suppressed never opens an SMTP connection; a partially suppressed send still delivers to the rest.
- The table is deliberately not tenant-scoped and carries no RLS policy, matching `stripe_webhook_events`: a bounce is keyed on the recipient mailbox and arrives before any tenant can be resolved, and the same address may belong to users in several tenants. A tenant predicate here would not isolate anything, it would stop suppressions from ever matching.
- Add `POST /api/platform/email/webhook/resend`, which applies the relay's `email.bounced` and `email.complained` events. Resend signs deliveries with Svix, so the endpoint verifies an HMAC-SHA256 over `svix-id.svix-timestamp.body` inside a five-minute window rather than accepting a bare shared secret: the signature covers the payload, and a captured delivery cannot be replayed or edited. It is allowlisted in `test_route_auth_coverage` with that reason. `PLATFORM_EMAIL_WEBHOOK_SECRET` now holds the Svix signing secret and is checked at startup for decodable base64 of at least 24 bytes, because a malformed key would fail every signature check at runtime and reject every bounce silently.
- Deliveries are deduplicated on `(provider, event_id)` in `platform_email_webhook_events`, keyed on the Svix message id rather than Resend's `email_id` — a bounce and a later complaint for the same message share the latter, so it would collapse two distinct events. A retry therefore cannot re-suppress an address an operator has since released.
- An event naming more than one recipient suppresses nobody. Resend reports the message's recipient list without saying which entry failed, and suppressing all of them would lock working mailboxes out of password reset over somebody else's dead address.
- Only `bounce.type == "Permanent"` suppresses; Resend is built on SES and carries that classification through unchanged. A transient or undetermined bounce — full mailbox, greylisting, a temporary DNS failure at the recipient's host — is logged and dropped, because a momentary outage at someone's mail provider must not cost them account recovery. The lookup also fails open: if the suppression table is unreachable the message is sent, since one message to a dead address is cheaper than locking every user out of password reset.
- `release_suppression` lifts a block by setting `released_at` rather than deleting the row, so the history of why an address was ever blocked survives the release.
- Give the webhook its own nginx `location` in both server contexts, capped at 256k on the dedicated 60r/m `webhook` zone, and extend `scripts/test_nginx_webhook_ingress.sh` and `test_nginx_matter_rate_limit.py` to assert it. This is load-bearing rather than tidiness: the relay sends no session cookie, so the per-caller `api` zone key would fall back to the source address and place unauthenticated provider ingest in the wider anonymous bucket at 240r/m, alongside the 55 MiB `/api/` upload allowance.
- `.env.prod.example` now carries the relay configuration rather than the previous `EMAIL_ENABLED=false` block, with the API key and signing secret left empty and marked never-commit.
- The runbook pins Resend's link-tracking options off. Click tracking rewrites every URL to route through the tracking subdomain, which would send the password-reset token through a third-party redirect and record it in the relay's click log; a reset token is a bearer credential, and the rewritten `links.` redirect also reads exactly like the phishing pattern users should not be trained to click.
- Add `docs/platform_email_setup.md`: why system email is a separate lane from matter correspondence (which still sends from the firm's own connected mailbox via `connected_mail.py`), why a transactional relay is preferred over an M365 app registration, SMTP AUTH client submission, or self-hosted Postfix, why Resend is the default over sending straight through SES, and the manual DNS and Microsoft 365 steps. The DNS section uses a custom Return-Path on a bounce subdomain so SPF stays evaluated against that subdomain and the root SPF record is never widened away from M365, while DKIM still aligns on the root and DMARC passes on DKIM alignment.
- Tests: `test_platform_email_suppression.py` covers the preflight (including that a disabled deployment is still allowed to boot), address normalization, partial and total suppression, the fail-open path, and the webhook's auth, transient-bounce, replay and malformed-payload behaviour.
## Unreleased — Firm-wide email to-dos

- Add one opaque firm forwarding address, administrator enable/replace/disable controls, a firm time zone, downloadable LawHand contact, and a Matters tip with the pending review count.
- Accept registered active staff forwards only after independently verifying a full-body RSA-SHA256 DKIM signature with signed From/Subject and an exact signing-domain match. Preserve the existing signed Worker boundary and tenant RLS; unknown or spoofed senders cannot create queue records.
- Preview ordinary `[TASK]` to-dos with an optional comma-separated colleague name and supported relative dates. Suggest matters from original-sender hints and case numbers; require review for every request, revalidate the selected matter and owner, and atomically file the source and create the assigned task. No email or calendar dispatch is added.
- Extend the linear schema with migration `184_firm_email_intake`; retain queued evidence when addresses are disabled or replaced. Add cryptographic, route, real PostgreSQL, RLS rehearsal, and mobile/UI checks, plus user/admin guide chapters and the operator runbook.

## Unreleased — Draft starter templates, signed-but-unfiled paperwork, e-sign mail from the sender's mailbox

- Portal signers can draw their signature. `ClientSignatureDocument` offers "Typing my name" or "Drawing my signature"; the new `portal/SignaturePad` canvas (pointer events, device-pixel scaled, clear) reports a PNG data URL that travels as `PortalSignRequest.drawn_signature_png` alongside the still-required typed legal name. `esign/drawn_signature.decode_drawn_signature` accepts only a real PNG under 200 KB within 40x20–2000x1000 that actually contains ink (opaque strokes on a transparent canvas, or non-white pixels on a white one); anything else is a 422 with a plain reason. The bytes are kept on `signature_signers.drawn_signature_png` (migration `183_drawn_signature`, `down_revision 182_unpublished_tpl_inactive`), and `record_portal_signature` writes `signature_method` and `drawn_signature_sha256` into the signer audit the evidence certificate carries. `render.Stamp` gained `image`; `_draw_stamp` paints it with reportlab `drawImage` (aspect preserved, anchored on the signature line, alpha honoured) in place of the italic name, keeping the line and the evidence caption; initials and dates stay typed. A browser with no 2D canvas says so and keeps the typed path.
- The paperwork drawer has the E-Signature panel's placement review. Each chosen PDF the client signs (fee agreement, intake form, questionnaire, additional form) shows "Review signing positions", which loads the PDF through `getMatterDocumentSigningSource` and opens `GeneratedSigningPlacementReview` for the single intake `signer` role. Placements ride `agreement_positioned_fields` and each `selected_documents[].positioned_fields` on `IntakeStart`; `plan_signing_placements` takes them ahead of any stored on the document and, for these explicit ones (`strict`), answers 422 naming the document when they no longer match the PDF instead of silently falling back. Choosing a different PDF or switching a form to unsigned drops them; the Send step counts placed fields. Previously a form with no AcroForm fields and no detectable printed line reached the client as "cannot be filled in the browser" with only the typed-name fallback.

- The intake starter pack installed its fee agreement and forms with the column default `is_active=true` and no `published_version_no`, so the matter template picker (`MatterTemplatePicker`, which lists only active templates) offered them while `published_template_view` refused every render and smart-fill with 409 "Publish a tested version". `intake_starter_pack.install` now writes `is_active=False`; migration `182_unpublished_tpl_inactive` (`down_revision 181_session_epoch`, data only) deactivates existing rows that are active without a published version and returns a `published` status on such a row to `draft`. The gate's message names the action ("publish a tested version from Templates") and the picker says drafts are published there.
- `matter_intake.mirror_submissions` also mirrors `esign.completion_pending` — every signer signed, no uploaded copy awaiting review, executed copy not yet filed — onto the requirement as `signed_pending_filing`, exposed to the client view. Previously a storage refusal at the last signature (recorded on the request as `completion_error` and retried by `esign-complete-pending` every five minutes) left the client checklist and the firm's paperwork strip on "Needs signature", the `due:<key>` follow-up task open, and nothing else changed until filing succeeded. `ClientIntakeChecklist.formStatus` and `paperwork.requirementState` render it as "Signed — filing", the row is not actionable, and `reconcile` closes the due task with "signed". The client checklist polls every 15 seconds while any form is awaiting review or filing, since that state changes server-side.
- `esign.notifications.notify_signer` / `notify_actionable_signers` now take the session and send through `connected_mail.send_client_email` as the request's `created_by_user_id` — the sender's Google or Microsoft mailbox, then a firm mailbox, then SMTP — rather than platform `email_service` alone, matching intake mail. A deployment with platform SMTP disabled therefore still delivers invitations, reminders and resubmission requests, and the sender is on record. The signer audit gains `<kind>_provider` and, on failure, `<kind>_delivery_detail`.
- New `notify_requester_signed(db, request)` emails the requesting user when the last signer signs, called from `portal_sign` whether the request completed or is only `completion_pending`; the text says whether the copy is filed or being filed. Failures are logged and never block the signature.
- `CaseSetupCard` polls `GET /matters/{id}/intake` only once a packet exists; a matter with no packet answered 404 every thirty seconds.
- Tests: starter templates install inactive; `mirror_submissions` sets and clears `signed_pending_filing` and the client view carries it; notifications go through `send_client_email` as the requester with provider and detail in the audit; the requester notice names signers, document and matter and is skipped without a sender; router delivery fakes take the session; migration head and file; checklist and paperwork-strip labels.

## Unreleased — Firm Profile: rename the firm, edit its identity and branding

- Let a firm rename itself. `PUT /api/firm/branding` accepts `tenant_name`, which writes `Tenant.name` rather than layering another override on it. Nothing could change that name before: sign-up derives it from the email domain of the first account (`domain.split(".")[0].capitalize()` in `auth.py`, for email/password, Microsoft and Google), so a firm signing up from `painterlaw.com` was named "Painterlaw" on its portal invitations, engagement email, invoices, trust statements and Smart Fill templates, permanently. The name is what every unset `firm_*` field falls back to, so it cannot be cleared — a blank `tenant_name` is a 422.
- Add `firm_name_override` and `tenant_name` / `tenant_domain` to `FirmBrandingResponse`. The GET resolves `firm_name` through the tenant-name fallback, so an editor that round-trips the resolved value would freeze the fallback into a stored override the first time an admin opened the form and saved. The raw override distinguishes "unset" from "set to the same string"; `get_firm_branding()` itself is unchanged, so the branding snapshots kept on invoices keep their existing shape.
- Validate branding input. Free text is trimmed, and a blank field is stored as NULL so the fallback returns instead of an empty string rendering onto an invoice; a value wider than its column is a 422 naming the field rather than a 500 from Postgres. `firm_currency` is still accepted by the endpoint (three letters, upper-cased) but has no UI control — LawHand firms are US-based, and the stored value already defaults to USD.
- Promote firm identity to its own **Firm Profile** admin tab, next to Users. It was `FirmBrandingPanel`, the fifth card down inside the Settings tab, which is why firms could not find it. `FirmProfilePanel` splits the fields into Identity (account name, display name, read-only tenant domain, and a preview of the name clients will see), Contact details, and Branding (logo URL with a preview that hides itself if the image fails, PDF footer). The panel omits `firm_currency` from its payload entirely, so saving leaves whatever the API holds untouched rather than clearing it.
- Repoint the Smart Fill "Firm settings" deep links from `/admin?tab=settings#firm-branding` to `/admin?tab=firm`, retiring the anchor whose delayed-loading scroll behaviour was noted as a follow-up in `docs/template-studio-live-ux-audit-2026-09-08.md`.
- Renaming is not retroactive: matter numbers already issued keep their prefix (`ensure_tenant_prefix` returns early once `matter_prefix` is set), and documents already generated keep the branding they were rendered with.

## Unreleased — Chat answers survive leaving the Chat page

- Keep a streamed answer alive across navigation. `ChatPage`'s unmount cleanup aborted every in-flight stream controller, and each authenticated route renders its own `AppShell`, so opening any other menu mid-answer unmounted Chat and cancelled the read. The backend persists an assistant turn only when the SSE read reaches `[STREAM_COMPLETE]`; a cancelled read takes the `finally` path in `_stream_message_under_generation_lock` and commits an interruption marker instead, so the answer was lost rather than merely hidden. The page now detaches from the read without aborting it.
- Add `frontend/src/chatGenerations.js`: a module-scoped registry of in-flight generations, keyed by conversation id to match the server's per-conversation generation lease. It holds the optimistic turn, the accumulating assistant message, the `AbortController` and a terminal status, and publishes every mutation to subscribers. Settled records are kept for the page that will reconcile them, then evicted after 15 minutes or once 12 records accumulate.
- Render the transcript from the registry rather than from the send handler. `handleSend` no longer writes stream tokens into `messages`; it patches the record, and an effect applies whichever generation belongs to the conversation on screen (`upsertGenerationTurn`, which splices via `mergeRefreshedTranscript` on first sight and replaces in place afterwards). Keying on the visible conversation is now what keeps a detached generation's tokens out of the thread the user moved to, in place of the old `isCurrentStream()` token gate. `loadConversation` reattaches a running turn as soon as the saved transcript arrives, so reopening Chat mid-answer shows live tokens instead of a lone question.
- Settle a finished turn in whichever page is mounted, not the one that started it. A turn that ended while a page was still bound to it keeps the previous behaviour (in-place failure text, or a `getConversation` refresh merged over the streamed answer for citation and proposed-action metadata); a turn that ended detached is re-read from the server, which is authoritative once the page let go. `streamControllersRef`, `activeStreamAbortRef`, `generationCount` and `isSending` state are replaced by registry-derived values; `cancelActiveStream` becomes `detachActiveStream`, and only conversation deletion still aborts a read (`abortChatGeneration`).
- Mount the streamed assistant turn before it has any text. `upsertGenerationTurn` delegated placement to `mergeRefreshedTranscript`, which only splices an assistant that already carries content because it exists to reconcile finished turns. A turn that has just started has none, so its placeholder was never mounted: retrieval progress did not render until the first token, and a stream that failed before any token (a 503 from the stream endpoint) had nowhere to report itself, leaving the outage silent in the transcript. It now inserts the placeholder after the matched user turn, and never adds a second answer to a turn the server has already answered. Caught by the `chat remains usable after a deterministic stream failure` browser E2E.
- Tests: `frontend/src/chatGenerations.test.js` covers the registry, and `ChatPage.test.jsx` gains unmount-survival coverage — an answer that keeps streaming after Chat is closed and reattaches on return, and one that finishes with nothing mounted and is waiting on return — plus a `upsertGenerationTurn` case proving a reattached turn does not duplicate the persisted question. Both page tests fail if the unmount abort is reintroduced.

## Unreleased — Per-caller edge rate limits and an isolated chat connection pool

- Key the edge rate limiters on the caller instead of the source address. `nginx.conf` gained three `map` blocks that derive `$lh_limit_key` from the SPA's `access_token` cookie (falling back to `Authorization: Bearer`, then to `$binary_remote_addr`), and the `api`, `matter` and `qbo` zones now use it. `$binary_remote_addr` collapses a whole firm into one bucket — every user at an office shares one public NAT address — so a 50-seat customer had the general 60r/m zone for the entire building, roughly one request a second, while a single SPA navigation fans out several calls. Rates were raised to per-user budgets accordingly (`api` 60→240r/m with `burst` 20→40, `matter` 240→480r/m).
- The caller key is a 128-character slice of the JWT payload segment. `_create_access_token` serializes `sub` and `tenant_id` first and base64url is a streaming encoding, so that prefix contains the whole user id, is identical across the 30-minute reissue (`iat`/`jti`/`exp` are serialized after it, so refreshing cannot mint a fresh bucket), is bounded so a zone entry stays small, and stops before the signature. A claim-order change degrades it to the IP fallback, never to a shared bucket.
- `auth`, `oauth` and `platform` stay keyed on the address: the first two have no authenticated caller yet, and the operator console carries its own bearer token rather than the firm cookie (`RateLimitMiddleware` limits it per token digest). The MCP zones also stay per-IP — hosted MCP clients arrive from shared cloud egress, and their tokens serialize `iss`/`aud` before `sub`, so the app-zone key would put every caller in one bucket rather than separating them. Queued in `TASKS.md`.
- Add a `webhook` zone at the `api` zone's original 60r/m and move the four Zoom Phone and Teams voice webhook locations onto it. They shared `api`, so making that per-caller would otherwise have quietly raised unauthenticated provider ingest to 240r/m. `scripts/test_nginx_webhook_ingress.sh` asserts the new zone.
- Give conversation generation its own connection pool (`database.generation_engine`, sized by `DATABASE_GENERATION_POOL_SIZE` / `_MAX_OVERFLOW` / `_POOL_TIMEOUT_SECONDS`). A turn holds a session-level advisory lock, which cannot move between connections, so it pins one connection for the whole turn — seconds to minutes, against the milliseconds an ordinary query holds one. Those came out of the request pool, so enough concurrent chats left matter, task and document reads queueing for `DATABASE_POOL_TIMEOUT_SECONDS` and then failing with nothing wrong with them.
- `_try_conversation_generation_lease` draws from that pool via `get_generation_engine`, which falls back to the session's own engine so a session bound elsewhere (every test) keeps its lock on the right database. Pool exhaustion is caught as `sqlalchemy.exc.TimeoutError` — not the builtin, which is a different class and would never have matched — and answered with 503 plus `Retry-After`, distinct from the 409 an advisory-lock conflict returns: a different conversation is not busy, the server is at capacity.
- Rebalance both deploy profiles for the second pool and keep the documented ceiling under `max_connections=100`. The request pool shrank (`.env.prod.example` 8+8 → 5+5, `docker-compose.cube-m.yml` 8+8 → 6+6) because the long-held connections that sized it have moved out; Cube M is now 3 × (6+6+3+2) = 51 pooled connections against 48 before. `test_production_ops.py` asserts both halves are pinned, so leaving the generation pool at its code default cannot silently widen the budget.
- Tests: `test_chat_generation_pool_isolation.py` saturates a one-slot generation pool and proves ordinary reads still serve, that exhaustion is a 503 and a lock conflict is still `None`, and that a refused lease strands nothing. `test_nginx_matter_rate_limit.py` is extended to assert which zones are keyed per caller, which stay per address, that every `map` keeps its fallback, and that the key window is wide enough to cover `sub` but too narrow to reach the per-reissue claims.
- Queue the remaining findings from the 50-seat review in `TASKS.md`: unpaginated overdue-task and matter-document endpoints, the matters search that omits `case_number`, 37 RLS policies whose `tenant_id::text` cast defeats the index, the never-revisited `ivfflat lists = 100`, the Redis security/cache split, a pooler, MCP zone keying, and the load-test baseline that would let any of this be measured rather than reasoned from code.

## Unreleased — Session lifetime: an epoch, an absolute bound, and a shorter idle window

- Add `app/services/session_policy.py` and `users.sessions_valid_after` (migration `181_session_epoch`, `down_revision 180_mediation_asset_recipient`). The column is the instant before which every credential a user holds is void; access tokens are placed against it by their `iat`, rotation chains by the origin timestamp each token now carries. `get_current_user` refuses a stale access token with a 401 alongside the `is_active` / tenant / license checks, so revocation crosses workers on the next request rather than waiting out the access token's 30 minutes.
- `POST /api/auth/reset-password` stamps that epoch. Previously it wrote the new hash and nothing else: the rotating refresh chain an attacker already held kept renewing itself for its full lifetime, so the one action a user takes when they believe they are compromised did not sign the attacker out. Session revocation had no mechanism at all — refresh families are only reachable from a token, and nothing recorded a per-user cutoff.
- Add `POST /api/auth/sessions/revoke-all`, reachable from a "Sign out everywhere else" control on the profile page. It stamps the epoch, revokes the caller's own chain, then re-issues the caller so the device asking is not signed out with the rest. `session_epoch_now()` truncates to a whole second precisely so the credential issued by that request outlives the epoch it just wrote.
- Refresh tokens carry `family_issued_at`, passed through unchanged on every rotation, and `/api/auth/refresh` refuses a chain that has reached `SESSION_ABSOLUTE_TIMEOUT_HOURS` (default 30 days) however continuously it has been used — a bound the rotating TTL cannot express, because every rotation renews it. A refused chain is revoked, not merely declined, so a live successor on another device dies with it. A chain minted before this change carries no origin, so it is refused once: the first refresh after deploy sends everyone through a sign-in.
- Replace `REFRESH_TOKEN_EXPIRE_DAYS` (14) with `SESSION_IDLE_TIMEOUT_HOURS` (12) and `SESSION_ABSOLUTE_TIMEOUT_HOURS` (720), validated at startup — an absolute bound below the idle bound silently replaces it, so it is rejected. Fourteen days of sliding idle was long for sessions that reach privileged client matter data, particularly on shared machines.
- Frontend: a 401 that survives a refresh now redirects to `/login?reason=session_expired` and the login page says the session ended, so a routine expiry does not read as the app losing someone's work. Adds `revokeAllSessions` to `api.js`.
- A password reset also revokes that user's Workspace MCP grants, in the same transaction that stamps the epoch, with runtime token cleanup afterwards. Sessions were only half of it: an MCP access token authenticates with its own audience-bound credential, so a connected assistant would otherwise survive the reset and stay reachable by whoever prompted it. `app/services/workspace_mcp_revocation.py` now owns that path and Privacy Mode is refactored onto it, so the two cannot drift. Research MCP is untouched — it reaches only public authority and is a separate product. "Sign out everywhere else" deliberately stays browser-only, and its copy says so.
- Make the disconnect legible instead of silent. Reconnecting is an OAuth flow only the assistant can start, so `GET /api/auth/me` returns `workspace_mcp_reconnect`, naming each assistant a reset disconnected that has not come back. It is computed rather than stored, so reconnecting is what clears it; it is suppressed while Privacy Mode is on, because a reconnect would be refused; and entries age out after 14 days. A dismissible banner names them on sign-in and a card in Profile → Workspace MCP assistants carries the server URL and the three steps. Without this the symptom is an assistant that quietly stops answering.
- Document the boundary in `docs/mcp_security_operations.md` — what the epoch reaches, what it does not (research grants, tenant `lhrk_` API tokens, other users' grants), and where to send someone whose assistant stopped working. The incident-response step that revokes grants now covers only what a reset cannot. `docs/session_lifetime.md` cross-references it.
- Not changed: capabilities, plan and billing tier are already read from the database on every request rather than trusted from the JWT, so a role or entitlement change still takes effect immediately and needs no epoch.


## Unreleased — Signing field placement: readable blocks, undo, per-signer roles

- Size and draw signing blocks like the ones a signer expects. `SIGNING_FIELD_SIZES` in `pdfFieldGeometry.js` sets signature 230x56pt, initials 96x56pt and date 150x44pt, with `SIGNING_FIELD_MIN_SIZES` as the resize floor; `GeneratedSigningPlacementReview` renders a role caption over a ruled line with the cross that marks the signature, tinted per signer. Every field previously landed at the same hard-coded `[72, 100, 192, 124]` rect — a 120x24pt strip, stacked on its predecessor.
- Replace that fixed rect with `createSigningFieldRect()`: a grid of uniform slots sized for the widest block, filled left to right from the foot of the page then upward, so a second signer's signature never lands on top of the first. `PREFERRED_DIMENSIONS.signature` in Template Studio's manual-field builder reuses the same size.
- Add placement history and removal. Undo/redo (buttons plus Ctrl+Z, Ctrl+Shift+Z, Ctrl+Y) covers every add, drag, resize and delete; a selected field can be removed from the toolbar, from a per-field control labelled by signer and type rather than by UUID, or with Delete/Backspace. Corner resize handles appear on the selected field.
- Make the review multi-signer. `signerRoles` now accepts `{ role, name }` rows, and each signer gets a toolbar row with their name, colour, field count and Signature/Initials/Date buttons, plus a list of roles with no fields yet. `SignatureRequestsPanel` passes the signer behind each role, defaults an added signer to a role nobody has taken, and warns when two signers share one — previously every added signer defaulted to `client`, which left one indistinguishable chip and a request the API rejected for having two signers on one role.
- Stop reporting an undelivered invitation as sent. `signatureSendNotice()` reads the per-signer `invitation_delivery_status` the send and resend endpoints already return and names the address and the reason (delivery disabled, incomplete settings, mailbox reauthorization, rejected recipient, mail-server failure) instead of the unconditional green "Signature request sent"; a failed status in the request queue is now amber rather than muted grey.
- Release note 2026.09.12.4.

## Unreleased — One way to choose each piece of client paperwork

- Unify the three standard pieces in the Send client paperwork drawer behind a single `DocumentCard`. The fee agreement, the client intake form and the client questionnaire each asked which document the piece is, but through differently ordered controls with three wordings for the same labels. Every card now reads: the chosen matter document, then the two ways to add one — prepare from a firm template, upload a file — in that order.
- Fix the fee agreement answering that question twice at once. Its file input held the file in local React state and never reached the select above it, so uploading one left the select reading "Do not include a fee agreement" beside an input naming the file. The upload now goes through `uploadMatterDocument` like the other two pieces, so every path writes one `agreement_document_id` and the raw `agreement` file is gone from the drawer's send. A fee agreement uploaded here is therefore saved to the matter when it is chosen, not only when the packet is sent; `POST /matters/{id}/intake` still accepts a raw `agreement` file for `MatterIntakePanel`.
- Replace the native file inputs on the Documents step with a label over a hidden input. The browser's own "No file chosen" sat directly under a dropdown that already named a chosen document, which read as two controls contradicting each other.
- Stop the fee agreement list offering the document already chosen as the intake form or questionnaire; the intake and questionnaire lists already excluded each other and the agreement.

## Unreleased — Hotfix: automation scope, matter-folder naming note, oversized intake answers

- Narrow workflow-automation rule matching. `rule_matches()` no longer compares both sides by resolved practice slug: `_canonical` is replaced by `_label_matches(rule_value, matter_value)`, which matches on normalized equality first and widens to the practice only when the *rule's* value names a whole practice. Comparing by slug in both directions made every label inside a pack equivalent, so a rule scoped to "Adoption" fired on divorces and protective orders, "Chapter 7" on Chapter 13 filings, and "DUI" on every felony and traffic matter — rules that plan tasks and draft documents. Widening is now asymmetric and deliberate: a rule keyed to "Family Law" still fires for a matter typed "Dissolution of Marriage"; the reverse direction (asserted by `test_a_rule_keyed_to_an_alias_matches_a_matters_slug` in 2026.09.11.12) is withdrawn, and those rules fall back to the pre-2026.09.11.12 exact comparison.
- Add `practice_scope_key()` and the curated `_SCOPE_ALIASES` table to `practice_resolution.py`: the aliases that name a practice rather than one kind of work inside it. The general pack is never a scope — it is where unrecognised labels fall, so widening to it would scope a rule to everything else. `test_practice_resolution` asserts every scope alias resolves to the practice it is listed under, so a typo fails rather than silently stopping the widening.
- Report intake answers too long to store. `derive_changes()` skipped any answer exceeding its target column, so the client answered, the field stayed empty, and staff never learned the answer existed. `oversized_answers()` now reports them on the packet (`proposed_changes.oversized`), in the review-task description with the length and the limit, and in the proposed-changes payload as `oversized` / `oversized_count`. An oversized answer raises the review task on its own and keeps it open after the other changes are decided, since nobody can accept it — it needs shortening with the client. Nothing is truncated.
- Cache `_smart_fill_alias_vocabulary()` with `functools.lru_cache(maxsize=1)`. It probes the Smart Fill resolver with a synthetic matter and depends on nothing but the code, but ran on every template approval.
- Release note 2026.09.12.3, covering the matter-number folder naming that shipped in #439 without one, plus the two fixes above.

## Unreleased — Billing surfaces: gated reports, billable timers, editable bills

- Gate firm financials. `require_finance_admin` now guards the realization, WIP, aging and matter-budget report endpoints, and `/reports`, `/invoices` and `/invoices/:id` carry the `financeOnly` route gate. Previously any signed-in user could open Reports or call the API and read every matter's A/R. `hasFinanceAccess` in `App.jsx` now mirrors the backend by honouring the `view_billing`/`manage_billing` capabilities rather than only the admin and accountant roles.
- Fix A/R aging. `written_off` invoices are forgiven debt and no longer counted as receivables (`_NON_RECEIVABLE_STATUSES`), and an invoice that is not yet due moves out of the late columns into a new `current` bucket, with a `total` per row. Realization reports `invoiced_amount` alongside collected, so billing realization (invoiced/worked) and collection rate (collected/invoiced) are shown separately instead of one blended ratio that could exceed 100%.
- Give reports a period. Realization and WIP accept `start`/`end`, surfaced as preset and custom range controls, carried into the CSV filename; an inverted range is a 400. Empty CSV exports now write a header row instead of zero bytes, `format` is validated, matter rows link through to the matter's billing tab, tables carry totals and `aria-sort`, tabs use tablist semantics, and failed loads offer Retry. Adds the first `ReportsPage` test suite.
- Repair a Pydantic self-shadowing bug. A field named `date` annotated `Optional[date]` binds the value before the annotation is evaluated, so `date` already meant `None` and the field resolved to `NoneType`, rejecting every real date with a 422. This silently broke editing a time entry and editing an expense, because both edit forms send the date on every save. `TimeEntryUpdate`, `ExpenseUpdate` and `TimerStartRequest` use a `_date` alias, with regression tests.
- Make timer entries billable. The running-timer banner takes the narrative and stopping requires one, so entries no longer land as "Timer session" needing a second edit pass. `TimerStartRequest` accepts the timekeeper's local `date`, so evening work is not stamped with tomorrow's UTC date. The entry form accepts `1.5`, `1:30`, `90m` and `1h15m`, rounded up to the firm's increment (read from a new timekeeper-readable `GET /billing/time-entry-settings`); the old 0.25 floor rejected the 0.1-hour entries the timer itself produces.
- Show and filter by timekeeper. `TimeEntryResponse` and `GET /matters/{id}/time-entries` return `user_name` (resolved in one query), which the matter billing table had always rendered as a dash. The time list gains a Timekeeper column and a Mine/All toggle, uses the server's totals rather than summing the first page, and hides edit/delete on other people's entries instead of failing with a 403 on click. Admin gains a Billing defaults card for the firm rate and increment.
- Make drafts editable. New routes add, edit and remove invoice line items while an invoice is a draft, including `discount`, `flat_fee` and `adjustment` lines, recomputing totals server-side and preserving the tax rate in force at generation. Removing a line returns its time entry or expense to the unbilled queue. Previously the only remedy for a wrong line was to void, fix the entry, and regenerate under a new number.
- Actually send invoices. `POST /billing/invoices/{id}/send` emails the client the invoice PDF and records it as sent; a delivery failure leaves the invoice a draft rather than claiming a bill was delivered. Write-off is reachable from the detail page once a part payment rules out voiding.
- Apply client funds to a bill. `POST /billing/invoices/{id}/apply-trust` draws a retainer down and posts the matching payment in one transaction, and `GET .../available-trust` lists what the matter holds, flagging a balance under its evergreen minimum. Before this, a "retainer" payment left the retainer untouched and a drawdown recorded no payment.
- Refuse to sync an unreviewed draft to QuickBooks in the router, the service and the button, matching what the bulk sync already did; one click used to promote a draft to sent and make it outstanding A/R in both systems.
- Fix recurring billing numbering. It restarted at 1 on every run, colliding with `uq_invoices_tenant_number` on the second run and leaving the session in a failed transaction for every later matter in that tenant. It now continues the tenant's own sequence via the shared numberer, commits per matter, and recovers on failure.
- Add `docs/billing-reports-time-parity-review-2026-09-12.md`, the source review of Time Tracking, Invoices and Reports against Clio, MyCase, PracticePanther, Smokeball and CosmoLex that these changes remediate.

## Unreleased — Sign and fill inside the document; Dropbox Sign retired

- Retire the Dropbox Sign provider. `app/services/esign/dropbox_sign.py`, the `POST /api/esign/webhooks/{provider}` route, the Dropbox coordinate converters in `esign/placement.py`, and the `DROPBOX_SIGN_API_KEY` / `ESIGN_WEBHOOK_SECRET` / `ESIGN_PROVIDER_BASE_URL` settings are removed. `get_provider()` resolves only `internal`; `POST /api/matters/{id}/signatures` rejects any other `provider` with 422. The `esign_webhook_events` table is left in place (no drop migration). The firm E-Signature panel no longer shows a provider picker.
- Positioned signing fields become first-class for the native provider. `InternalProvider.send` accepts them and `validate_pdf_geometry` drops the Dropbox-only US-Letter restriction (rotation, `/UserUnit`, non-zero origin and CropBox checks stay). The two `matter_intake` 422 branches that refused documents with placements are removed; intake copies the document's placements onto the signature request it creates.
- Add `app/services/esign/plan.py`: a placement plan run at request creation. It uses AcroForm `/Sig` widgets when the PDF has them, otherwise staff-placed `positioned_fields`, otherwise printed signature lines detected from text positions ("Signature", "Signed", "By:", underscore runs; a "Date" label on the same line yields a date field), and as a last resort stacks a signature + date block per signer role in the bottom margin of the last page. Every request therefore has at least one signature placement per role.
- Add `GET /api/portal/client/signatures/{id}/fields` and the staff `GET /api/matters/{id}/signatures/{id}/fields`: a manifest of every fillable AcroForm widget (`acroform:<name>`) and signature/date/initials placement with page, PDF-point rect, kind, options, role and (portal only) `mine`. Signature-type widgets are assigned to signer roles by name match, falling back to `sign_order` round-robin.
- Extend `POST /api/portal/client/signatures/{id}/sign` with `field_values` (validated against the manifest; required fields assigned to the signer must be filled). Add `POST .../upload` for a downloaded-and-signed PDF copy: it is stored as a `client_uploads` matter document, recorded on `submitted_document_id`, the signer is marked signed with `method="uploaded_copy"`, and the request waits for staff. Add `POST /api/matters/{id}/signatures/{id}/accept-submission` (completes the request with the uploaded copy as the executed document) and `.../reject-submission` (clears the submission, reopens the signer, notifies them).
- Completion now renders an executed copy (`app/services/esign/render.py`): AcroForm values from every signer's `field_values` are filled with pypdf, signature/initials/date placements are stamped with a reportlab overlay (adopted typed name in italic, electronic-signature caption, date in the matter's timezone), widgets and `/AcroForm` are removed so the output is flat. It is filed as a `signed` matter document on `signature_requests.executed_document_id` beside the evidence certificate (still `provider_envelope_id`, which `matter_intake.reconcile` relies on).
- Storage outages no longer surface to the client. `complete_request_if_done` catches `MatterFileStoragePolicyError` and failed `StorageResult`s, records `completion_error` / `completion_attempted_at`, emits one system `MatterEvent`, and returns; `portal_sign` responds 200 with `completion_pending: true`. A new scheduler job `esign-complete-pending` retries filing every five minutes and runs the same post-completion steps (`close_signature_followup`, `matter_intake.reconcile`) on success.
- Migration `179_native_signing` (`down_revision 178_intake_optional_agreement`): `signature_requests.executed_document_id`, `submitted_document_id` (both FK `matter_documents` ON DELETE SET NULL), `submitted_at`, `completion_error`, `completion_attempted_at`; `signature_signers.field_values` (JSON) and `method`.
- Client portal: `ClientSignatureDocument` is an in-document form. Pages render continuously with pdf.js; inputs, checkboxes and selects sit on the PDF's own fields; signature and initials fields adopt the typed legal name on click; the Sign button is gated on required fields, every signature field, the typed name and consent. A download → complete → upload path sits beneath the document. `ClientIntakeChecklist` regroups paperwork into "Forms to complete and sign" and "Records to send us" with status pills, and no longer crashes on a packet without a fee agreement.
- Paperwork drawer: Documents lists Fee agreement, Client intake form, Client questionnaire (a PDF supplied like the intake form) and Additional forms, each with a "Client signs this form" toggle; the free-text questionnaire question list is removed from the UI (`include_questionnaire` now defaults to `False`; old packets keep working). Requested client uploads are an explicit, off-by-default "Records to request from the client" section with a one-click suggested list from the matter's practice pack.
- The paperwork-only portal scope (in force until the fee agreement is signed) now admits the field manifest and signed-copy upload routes for the packet's own signature requests, since signing the fee agreement happens through them.
- Tests: `test_esign_plan.py`, `test_esign_render.py`, the extended `test_esign_service.py` / `test_esign_router_delivery.py`, a Postgres end-to-end paperwork cycle in `test_native_signing_cycle_postgres.py`, and updated frontend suites for the portal signing tab, checklist, E-Signature panel and paperwork drawer. `test_esign_dropbox_placement.py` is deleted with the provider.

## Unreleased — Resizable, sortable My Matters list and a faster matter page

- Give `MATTER_LIST_COLUMN_DEFS` (`frontend/src/components/matters/MatterListColumns.jsx`) a default width, a minimum width, a comparator, and a first-click direction per column, so the header, the body cell, the width store, and the sort can never drift apart. `useMatterListColumns` gains `widthOf`/`setWidth`/`resetWidth`/`resized` backed by a `matter-list-widths:<tenant>:<user>` localStorage key, clamped to each column's floor and a shared 640px ceiling; a blocked storage API still leaves the in-memory choice standing.
- Add `frontend/src/components/matters/MatterListTable.jsx`: `MatterListHeaderCell` (sort button with `aria-sort`, plus a `role="separator"` drag handle that also responds to arrow keys, shift-arrows, Enter and double-click) and `useHorizontalScrollEdges`, which watches the scroll container and the table so an edge shadow marks whichever side still has content behind it.
- Rebuild the My Matters list as `MyMattersTable` in `MatterPortfolioPage.jsx`: `table-fixed` over a `<colgroup>` driven by the width store, `border-separate` so the frozen Matter and Actions columns keep their borders, and truncating cells that carry the full value as a `title`. The unsized actions column absorbs slack when the chosen columns are narrower than the page.
- Sort state lives in the `msort`/`mdir` query parameters. `sortMatters` orders risk by severity, status by lifecycle, the next deadline by `next_deadline` rather than the badge text, and sorts rows with no value last in both directions, stably; `nextSortState` cycles natural direction, opposite, then off.
- Run cloud-search providers concurrently under a wall-clock budget (`CLOUD_SEARCH_BUDGET_SECONDS`, with `CLOUD_SEARCH_UI_BUDGET_SECONDS` for the matter page's Cloud Files panel) instead of awaiting them one after another; a source that overruns the budget is cancelled and dropped from that response. Cancelling the request cancels every source.
- Fan out per-hit metadata inside a provider search: Drive descriptions and Gmail message metadata are fetched concurrently (`CLOUD_SEARCH_FANOUT_LIMIT`) on the client that already ran the search, replacing one serial request per hit on a fresh TLS connection.
- Resolve Google and Microsoft tokens once before the fan-out and hand the provider coroutines no session, because an `AsyncSession` cannot serve overlapping coroutines; `search_index` stays the only task holding it. A provider with no stored credential is skipped rather than scheduled.
- Defer `getMatterCloudFiles` on the matter page until the matter itself resolves, and load the paperwork card's 200-document list only when a packet exists or the drawer opens, so a matter whose paperwork state is "Start this case" no longer waits on either.

## Unreleased — Fee-term bindings and approval validation

- Add four closed-catalogue bindings: `matter.contingency_percentage`, `matter.retainer_amount`, `matter.retainer_minimum_balance`, and `matter.venue`. Smart Fill resolves contingency and venue from the matter, and the retainer amounts from the matter's current retainer row — the most recently created active retainer, falling back to the most recent of any status — queried only when a template declares a retainer binding or names a retainer alias. Retainer amounts stringify exactly like the existing `hourly_rate`/`budget_amount` aliases.
- Add `Matter.venue` (`varchar(300)`, nullable), exposed in the matter create/update schemas and the matters router, with migration `177_matter_venue` (`down_revision 175_document_template_sets`; 176 is claimed by a sibling branch).
- Add `_validate_approval_ready`, called from the template publish endpoint and the PDF activation path before `approved_at` is stamped. A template with a required, default-less field that no record can fill is rejected with a 422 listing the offending placeholders; a body referencing `jurisdiction_required_terms` requires a recorded template jurisdiction. Resolvability is checked against the binding catalogue (item and `custom.*` bindings included) and an alias vocabulary derived from the Smart Fill resolver itself against a probe matter, so the check cannot drift from the resolver. PDF source-backed fields (`pdf_source_key`/`pdf_overlay`) are exempt — their mandatory activation preview, not Smart Fill, guarantees a value.
- Re-bind the starter pack: the fee agreement's `contingency_percentage` and the North Dakota hourly agreement's `retainer_amount` and `venue` now bind to the new catalogue entries, and the ND body gains a `retainer_minimum_balance` placeholder in the trust-replenishment clause. `staff_rate_range` and `attorney_rate_range` remain manual, pending a rate-card model.

## Unreleased — One practice resolver behind routing

- Add `app/services/practice_resolution.py`: the intake starter pack's practice alias table, longest-alias-wins matching, and `resolve_practice()` move out of `intake_starter_pack.py` into one shared module (with a `practice_key()` helper returning the canonical slug for a recognized label), so every subsystem reads `Matter.matter_type` / `Matter.practice_area` the same way. `intake_starter_pack.py` re-exports the moved names, so existing importers are unchanged and the pack behavior is identical.
- Route plugin suggestions through the shared resolver: `suggest_plugin_for_matter()` resolves the matter's practice and also matches plugin `matter_types` terms against the resolved slug and label, so a matter like "Dissolution of Marriage" suggests the family plugin even though its text names no plugin keyword. The existing substring haystack match stays in the same sorted scan as a fallback, so tenants that relied on it (e.g. "Breach of Contract" → commercial-legal) do not regress.
- Route workflow-automation rule matching through it: `rule_matches()` compares the rule's `match_matter_type` / `match_practice_area` and the matter's values by their resolved practice slug, so a rule keyed to "family" fires for "Family Law" and "Dissolution of Marriage" alike, case-insensitively in both directions. Values the alias table does not recognize keep the historical normalized-string comparison.
- Add a computed `resolved_practice` (slug + label, null when the labels resolve to nothing) to `MatterResponse`, populated in the matters router, so staff can see how a matter was read and correct one that resolved to the wrong practice. No schema change.

## Unreleased — Matter lifecycle troubleshooting

- Propagate a portal decline to the firm. `portal_decline` now closes the signature follow-up (`close_signature_followup`), writes a `signature` matter event via `esign.decline_event`, and reconciles the intake packet, matching the completed path. Previously a decline committed the status change only: the matter timeline showed nothing and the follow-up task stayed open chasing a request the client had already refused.
- Teach `matter_intake.reconcile` about declined signatures. Each linked `SignatureRequest` (the fee agreement and every `kind: "signature"` requirement) that is `declined` marks its requirement `declined` with the reason, emits a `signature` timeline event, closes the `due:<key>` chase task, and raises one idempotent `declined:<key>` rework task. `event()` gains an `event_type` keyword.
- Keep a client's signature when evidence storage fails. `complete_request_if_done` binds `evidence_sha256`/`completion_artifact_sha256` only after the upload succeeds, and `portal_sign` commits the recorded typed signature before re-raising a storage failure (retaining the row lock through the attempt). A retry resolves the already-signed signer via `_signed_portal_signer` and finishes completion without a second signature. Previously the 503 rolled back the whole transaction and the client's signature was silently discarded.
- Give matter and task reads their own nginx burst bucket (`zone=matter`, `rate=240r/m`, `burst=80`) in `nginx/nginx.conf`. The general `/api/` zone stays at `60r/m burst=20`; a matter view fans out dashboard, signatures, messages, documents, intake and tasks from one IP, so ordinary navigation could exhaust the burst and 429 every other request.
- Add `bound_storage_readiness` to `cloud_folder_audit`, surfaced by `scripts/audit_matter_cloud_folders.py` under `storage_policy`. It explains a bound-but-unusable cloud provider (`needs_reauth` vs `folders_unbound` vs `ok`) without provider I/O, and `18-cloud-provider-support.md` documents the runbook.
- Tests: `test_client_portal_api.py` covers decline propagation and storage-failure signature durability/retry; `test_matter_intake_postgres.py` covers declined reconciliation and idempotency; `test_cloud_folder_audit.py` covers the readiness states; `test_nginx_matter_rate_limit.py` guards the dedicated zone.

## Unreleased — Intake answers apply to the matter after review

- Add `app/services/intake_writeback.py`: a submitted client questionnaire now derives structured write-back proposals from its answers. The answer-key mapping is built from the declared bindings on `CLIENT_INTAKE_FORM` in `intake_starter_pack.py` (question slugs share the form field namespace), plus one curated alias (`other_parties` → `matter.counterparty`); resolvable bindings cover client name/address/phone/email and matter name/type/description/role/counterparty/court/case number/judge/jurisdiction.
- Diff each mapped answer against the live record inside the submit transaction: empty field + answer proposes a `fill`, a differing curated value proposes a `conflict`, equal values are skipped. Proposals persist in a new `proposed_changes` JSONB column on `matter_intakes` (migration `176_intake_writeback`, down_revision `175_document_template_sets`), and a staff Task titled "Review intake updates: <matter name>" is created with a deterministic `uuid5(packet.id, "writeback")` id, `source="intake"`, and a `pending_action` referencing the conflict-check record. Derivation runs only on first questionnaire completion, so resubmitting identical answers cannot duplicate tasks or proposals.
- Add staff endpoints `GET /api/matters/{id}/intake/proposed-changes` and `POST .../proposed-changes/accept` / `.../reject`, each taking a single `change_id` or the whole set. Acceptance re-reads the live value and applies the write only when the record still matches the proposal snapshot, resolves silently when the record already equals the proposal, and refuses with 409 when the field changed in between. Decisions are audited with a `MatterEvent` (`intake_writeback`) plus task events, and the review task completes when no pending changes remain.
- Feed the conflict-of-interest answers (`conflict_spouse`, `conflict_family`, `conflict_businesses`, `conflict_organizations`, `conflict_witnesses`, `conflict_other`, plus other-party answers) into `conflict_check.run_conflict_check` mirroring `routers/plugins.py`, persisting the result as a `ConflictCheckRecord` labeled "Intake questionnaire — <matter>"; failures and hits never block the submit.
- Proposed changes are visible in the staff intake payload only — the client portal response never exposes them.

## Unreleased — Platform-managed shared Twilio sender

- Add `app/services/platform_sms.py`: the operator stores one LawHand-owned Twilio account in `platform_settings` (`platform_sms_provider_v1`), with the Auth Token encrypted by `token_vault` and only a last-4 hint exposed. A partial update keeps the stored token when the field is omitted.
- Add `/api/platform/sms/provider` (GET/PUT/DELETE) and `/api/platform/sms/test` under the existing platform-operator token, so writes need `platform:write` and reads need `platform:read`. Test send validates E.164, resolves the saved credentials, and posts to Twilio `/Messages.json`.
- Add an operator SMS tab in `frontend/src/pages/PlatformPage.jsx` to save the sender and send a test message.
- No migration: configuration uses the existing `platform_settings` table. The separate, firm-owned tenant SMS path (`/api/sms/config`) is unchanged.

## 2026.09.11.12 — Branded client paperwork message and preview

- Replace the single run-on paragraph sent to clients with `render_client_message` in `services/matter_intake.py`, wrapped by the new `email.render_branded_email` in the firm's existing `_BASE_HTML` shell: a greeting by first name, the firm's name and contact details, the document and upload lists, and an **Open Secure Client Portal** button. `deliver` resolves firm branding via `get_firm_branding` and the client's display name and passes them through.
- Drop the false "your general client portal link will follow after the fee agreement is signed" line. The invitation link is the portal; the copy now says signing unlocks the rest of the portal. The fee agreement is listed whenever it is in the packet, not only when `portal_after_signing` is set, so a checkbox can no longer hide a document the client still has to sign.
- Give SMS its own short body (`ClientMessage.sms`) so the branded HTML and checklist cannot arrive as a text message.
- Add `POST /api/matters/{id}/intake/preview` (`IntakePreviewResponse`) rendering the exact subject, HTML, text and SMS for the current draft with a sample token. Read-only and gated by `manage_matters` plus matter access; no packet row is created.
- Add a **Message the client will receive** card to the drawer's Send step with Email/Text tabs, a debounced preview from the new endpoint, and the branded HTML in a sandboxed iframe. Update the onboarding e2e link extraction for the multi-line text body.

## 2026.09.11.11 — Optional fee agreement and matter-type questionnaire

- `PaperworkDrawer` now lists the three common pieces as selectable cards — fee agreement, client questionnaire, client intake form — and allows any subset. `canSend` no longer requires a fee agreement; the Send step warns only when nothing at all is included. The router previously raised 422 when no agreement was passed (`routers/matter_intake.py`); it now hands empty bytes through.
- The questionnaire seeds from `GET /api/intake-starter-pack` on open, so "Start this case" carries the matter type's own questions and requested uploads instead of the generic three-question default, and the upload placeholder shows that practice's records rather than family-law examples. The button reads "Reset to standard questions".
- Optional-agreement backend: `start_packet` builds the signature, its `fee_agreement` requirement, and `config.source_sha256` only when an agreement is supplied, while always creating the `ClientPortalInvite`. A packet with no agreement stores `portal_after_signing=false`, so the client reaches the questionnaire and uploads immediately, and `reconcile` starts the 24-hour follow-up from `sent_at` when there is no signing milestone. `MatterIntake.signature_id` becomes nullable (migration `176_intake_optional_agreement`, down_revision `175_document_template_sets`).
- Harden the portal paperwork gate: `paperwork_only` now requires a `fee_agreement` requirement, and a null signature id no longer leaks the string "None" into `signature_ids`.
- `IntakeSetupFields.intakeOptions` drops questions when `include_questionnaire` is false and hides the questionnaire due date and editor, matching the drawer.

## 2026.09.11.10 — Matter-aware template flow for paperwork forms

- Replace the in-app fill form in the paperwork drawer with the matter-aware template flow that Case Documents already uses. `PaperworkDrawer` opens `MatterTemplatePicker` pinned to the matter, so a fee agreement or additional form is completed in `RenderModal` with Smart Fills from the matter, previewed, and saved to the matter by the template render endpoint. The previous renderer exposed each sample's raw `variable_schema` (detected text placeholders, duplicate and blank field names) as an unusable form — the Nevada Living Will showed "undefined" and dozens of stray fields.
- On save the drawer resolves the new matter document from the render response (`matter_document_id`, `output_filename`, `output_format`), preferring the matter's own record from `GET /matters/{id}/documents` for its `content_type`, and pre-selects it: from the Fee agreement card it becomes `agreement_document_id`; from the Additional forms card it is attached as a checked signing form. The agreement select also lists the chosen document when it rendered as Word, since the intake endpoint accepts any reviewed matter document.
- Remove `FormLibraryDialog`. Shared samples are no longer offered from the drawer; they remain available from the Template Studio home.
- Add a "Choose a file" upload to the drawer's Additional forms card. `uploadFormFile` posts to `POST /matters/{id}/documents/upload` and attaches the returned document as an additional signing form, so a locally filled form no longer has to be saved to the matter's documents first.

## 2026.09.11.9 — A front door to the client portal

- Add a passwordless `frontend/src/pages/ClientPortalLoginPage.jsx` at `/portal/client/login` (with `/portal/client` redirecting to it). It emails a one-time code, verifies it, and opens the portal. Client `User` rows are created without a password, so `POST /portal/client/request-code` and `/verify-code` replace the unused `/activate` and `/login` endpoints and the `activateClientPortalAccount`/`loginClientPortalAccount` wrappers.
- Let a signed-in session outlive the invitation. `PORTAL_SESSION_TTL_HOURS` caps the cookie at 24 hours while the invite still expires at 14 days (`INVITE_TTL_DAYS`). Because the sign-in code proves control of the email, `invite.revoked` remains the authorization control and magic-link sessions still expire with the link.
- Resolve the matter at sign-in: an address with one matter goes straight in, and one spanning several answers 409 with the choice list and a short-lived selection ticket rather than 403. `GET /portal/client/matters` and `POST /portal/client/switch-matter` power the in-portal switcher.
- Store sign-in codes only as a keyed digest, single-use, with a per-address cooldown and an attempt cap, and rate-limit `request-code`/`verify-code` by source IP. `/request-code` answers identically for a known and an unknown address, so it is not an account oracle.
- Return the firm's identity from `/portal/client/matter` and `/portal/client/session` — name, logo, address, phone, email, website, currency — and render it in the portal header with a persistent "Need help?" footer. The branding was already stored and already stamped on invoice PDFs; the portal alone still announced the vendor. Adds `firm_currency` (migration `174_firm_currency`, down_revision `173_rls_tenant_guc_nullif`, default USD) so money follows the firm instead of a hardcoded USD.
- Add `GET /portal/client/invite-info` and `GET /portal/client/documents/upload-policy`: the firm identity for the accept screen and the enforced size and extension limits for the upload panel. Neither consumes the invite.
- Name the firm, the attorney, the expiry date, and the firm's contact details in the invitation email, which previously said only "your legal team" and "will expire", and carry the sender's name on portal messages, which read "Legal team" for every inbound.
- Confirm before sign-out and before declining a signature, and move the decline reason above the button it belongs to. Decline fired immediately on a mis-tap, recorded no reason, and had no undo; sign-out was an unguarded one-way trip to a screen telling the client to find an email.
- Announce sign, decline, and error outcomes through live regions and `ErrorBanner` alerts. The page had none, so the most legally significant action in the product reported its result visually only.
- Fix mediation document upload: the file input carried no `onChange`, so choosing a file re-rendered nothing and the Upload button kept the `disabled` value from the previous render. `PortalCasePage` also gains its first test file, which is how this reached main.
- Give the mediation portal a working invitation-code entry on its access-denied screen (the button navigated to a page that reads the token from the URL and then reported it missing), a sign-out control, and card lists instead of seven-column tables on phones.
- Rewrite the client upload panel for clients. It told them to choose "this client's folder from your computer or USB drive" and referred them to New Matter, a firm-only feature; it now states the enforced limits and pre-flags oversized files before the upload starts.
- Wrap the client tab strip so Signatures and Invoices stay reachable at phone width, and move message send to Ctrl/Cmd+Enter so Enter starts a new line.
- Add `docs/client-portal-ux-review-2026-09-11.md`, the review these changes close out.
- Review follow-ups: a wrong sign-in code no longer re-saves the entry with a fresh `PORTAL_SIGNIN_CODE_TTL_SECONDS` (the attempt counter is bumped on the code's remaining life, so spaced wrong guesses cannot keep a code alive); `GET /portal/client/matters` serves a 120-second per-address cache (`PORTAL_MATTERS_CACHE_TTL_SECONDS`, dropped on switch-matter and logout) instead of walking every active tenant on each page load, and the portal fetches it once per visit; the matter chooser names the firm from `get_firm_branding` like the portal header, falling back to the tenant name; the sign-out prompt and signed-out screen no longer mention a password; message drafts are stored per matter and cleared on sign-out.

## 2026.09.11.8 — 30-day self-serve trials

- Add `app/services/trials.py` and `SIGNUP_TRIAL_DAYS=30`: plan signup now provisions a bounded trial window. `Tenant.expires_at` is the enforced boundary (fail-closed in `require_active_tenant`) and `TenantSettings.custom_config` carries `trial`, `trial_started_at`, and `trial_ends_at` for operator display. Signup previously wrote a 14-day `trial_ends_at` that nothing read, so a self-serve tenant never expired.
- Hold premium AI back for the trial: `PUT /admin/users/{id}/premium` and `PATCH /admin/users/{id}` reject enabling it while the tenant carries the trial marker, so trial spend stays on the standard route.
- Notify the operator on signup (`notify_operator_trial_started`) through `MARKETING_LEAD_EMAIL`. Delivery is best-effort and never fails signup; live delivery requires `EMAIL_ENABLED=true` with SMTP configured.
- Add `trial_ends_at` to the platform tenant update (future = start/extend, past = revoke, null = clear) and expose `on_trial` in the tenant list and detail. No migration.

## 2026.09.11.7 — Sample library routing, catalog seeding, and filled intake forms

- Fix the global sample library never loading. `sample_templates_router` and `document_templates_router` share the `/api/templates` prefix; the tenant router's greedy `GET /{template_id}` was registered first, so `GET /api/templates/library` parsed "library" as a UUID and answered 422 (surfaced as "The sample library could not be loaded"). Register `sample_templates_router` first and add a regression test that reads real route registration order (`effective_route_contexts`), which the previous handler-level tests bypassed.
- Seed the platform-owned sample catalog during deploy. The one-shot `migrator` now runs `alembic upgrade head && python scripts/seed_sample_templates.py` in every Compose profile; the catalog was previously never populated, so even a correctly-routed library rendered empty. The seed is idempotent by slug and Postgres-only, and `seed_sample_templates.py` now imports only the catalog model instead of all of `app.main`.
- Fill firm templates and shared samples inside "Start this case". `FormLibraryDialog` lists tenant `DocumentTemplate`s and global samples, renders their `variable_schema` fields (signature fields are skipped), fills the PDF through `/templates/{id}/render-file` or `/templates/library/{id}/render-file`, uploads it to the matter, and hands the resulting document to `PaperworkDrawer` as the fee agreement or an additional signing form.
- Remove the optional intake packet from `NewMatterModal`. The "Start client intake with this matter" checkbox and its `IntakeSetupFields` panel are gone; the modal now only creates the matter. Intake still starts from the matter page (`CaseSetupCard` / `MatterIntakePanel`), which keeps `IntakeSetupFields`.

## 2026.09.11.6 — Matter page: signing alerts, composer prefill, first-class portal

- Alert in place when the client signs. `CaseSetupCard` reports each polled packet via a new `onPacketChange` callback; the matter page shows a success toast on the signed transition and bumps a `refreshKey` so `SignatureRequestsPanel` reloads itself. Previously the only in-app signal was the card's 30s poll and the panel never refreshed after mount.
- Carry `client_email` and `client_contact_type` on `MatterResponse` (`_matter_to_response`). The Email Client composer and the portal invite form read them, so the To field is prefilled; both previously read `matter.client?.email`, a field the payload never included.
- Add an "Insert requested records" helper to `ComposeEmailModal`, fed by the intake packet's outstanding `upload_*` requirements, so the attorney can name what the client still owes without retyping it.
- Move Client Portal from the Matter settings row into the matter's primary tabs (`PRIMARY_SECTIONS`); it is a daily destination, not a setting.
- Link the client name on the matter to `/clients/{id}` when the contact is typed client/prospect (the CRM record) and to `/contacts/{id}` otherwise. CRM clients are a filtered view over the same contacts table, so the matter's client was already the CRM record; the link makes that visible.
- Name the requested record on the client-side upload control ("Upload Marriage certificate") instead of the generic "Upload completed document".

## 2026.09.11.5 — Onboarding copy and follow-up clarity

- Name the requested client records in the paperwork emails. `requested_upload_labels()` feeds both the initial "Review your paperwork" message and the post-signing portal-ready message, so the client learns what to upload without opening the portal. Previously only `selected_documents` were listed.
- Format the staff follow-up description with `followup_task_description()` in the packet timezone instead of `due.isoformat()`. The raw ISO value (microseconds and `+00:00` offset) was rendered verbatim in the task row.
- Prefer `task.title` over `task.task_type` for `task_due` calendar events, so a deadline reads "Fee agreement signed — follow up with client" rather than "follow_up".
- Add `docs/matter-onboarding-ux-audit-2026-09-11.md` and the `E2E_AUDIT`-gated `frontend/e2e/matter-ux-audit.capture.e2e.js` capture harness.

## 2026.09.11.4 — Consent-scoped SMS for the live case

- Add `app/services/sms_categories.py`: `intake` and `case_updates` as named consent categories, each with its own disclosure version, plus `granted_categories`, `disclosure_version`, and `allows_case_updates`.
- Record `case_updates` only when the firm confirms the client agreed to it (`sms_case_updates_verified` on `IntakeStart`, a second checkbox in both the paperwork drawer and the matter-creation form). Intake-only consent keeps `allowed_categories: ["intake"]` and the existing `intake-notifications-v1` disclosure.
- Text signers about a document waiting for signature via `notify_actionable_signers_sms`, sent under `case_updates` alongside the email that already goes. Signature notifications were email-only even for a client who asked to be texted.
- No migration and no backfill: `send_sms` already gates on `category in consent.allowed_categories`, so every existing consent refuses `case_updates` on its own. Widening an old consent requires the client to agree again. An SMS failure is recorded on the signer's audit and never fails the send it accompanies.

## 2026.09.11.3 — Outbound correspondence, portfolio triage, creation deadlines

- File a sent email as a document on the matter. `file_outbound_email` builds the `.eml` from exactly what was handed to the transport (compose sends via SMTP or a connected mailbox, so there is no provider message to fetch back), files it into Correspondence, and links it to the `CommunicationLog` row. Only a message that actually sent is filed, and a filing failure is logged rather than failing a request for mail that already left.
- Show `matter_number` on the portfolio board card and as a list column, and match it in the search predicate. The number shipped in 2026.09.10.1 and had reached neither surface, so a number quoted on the phone could only be used by typing a URL.
- Drop `risk_level` from the portfolio `needsAction` heuristic. Risk is a standing attribute, so a high-risk matter sat in Needs Action permanently regardless of whether anything was due; the column now means deadlines, threatened status, and staleness, and risk stays a badge.
- Move the portfolio status filter, practice filter, search, and board/list toggle into the URL (`replace: true`), matching how the matter page already keeps its tab. Opening a matter and returning no longer discards the list you built.
- Carry paperwork deadlines through matter creation: `IntakeSetupFields` gains due-date inputs for the fee agreement, questionnaire, and requested uploads, and `intakeOptions` emits `agreement_due_at`, `questionnaire_due_at`, and per-document `due_at`. An undated requirement sends `due_at: null` so the server sees one shape either way.

## 2026.09.11.2 — Matter closing and automatic document filing

- Add `app/services/matter_closing.py` and `GET /matters/{id}/close-readiness`: unbilled billable time and expenses, held trust balance, open tasks, live signature requests, and incomplete client paperwork, split into blocking and acknowledgeable checks.
- Guard `DELETE /matters/{id}` (the soft close) with those checks. Unbilled work and a non-zero trust balance return 409 `matter_close_blocked`; warnings return 409 `matter_close_needs_acknowledgement` until `acknowledge_warnings` is passed. The close records a `matter_closed` timeline event with an optional note and runs intake reconcile so a closed matter stops chasing its client.
- Add `POST /matters/{id}/reopen`, recording `matter_reopened`. Closing is reversible; it was previously unreachable from any screen at all — `closeMatterV2` existed in the API client with no callers.
- Add a `CloseMatterDialog` on the matter page listing what each check found, with the acknowledgement and closing note, plus Close/Reopen controls in the matter topbar.
- Extend system folders beyond Client Uploads to Correspondence, Signed, Intake, and Generated Documents, and add `autofile_folder_id`, which maps a document's existing `document_category` onto the folder a firm would have filed it in. A category the product does not own stays unfiled, and a folder failure never costs the document — it lands unfiled instead.
- File by category at the four sites that create documents automatically: inbound email, signature completion artifacts, captured correspondence, and intake questionnaires. Only client-portal uploads were previously filed anywhere; everything else landed in the explorer root.
- Extract inbound email attachments as their own documents beside the stored `.eml`, filed into the same folder, capped at 20 attachments and 25 MiB each, with sender-supplied filenames stripped of path separators and control characters. A storage failure on one attachment is logged and skipped rather than losing the email.

## 2026.09.11.1 — Case lifecycle on the matter page

- Add per-requirement due dates to the intake packet: `due_at` on `IntakeDocumentSelection`, `IntakeUploadRequirement`, and the new `agreement_due_at` / `questionnaire_due_at`, each rejected when naive so a deadline is never read as UTC. The value is persisted onto its requirement and returned to staff and client alike, alongside the packet's `timezone`.
- Raise one assigned follow-up per dated requirement in `reconcile`, keyed `due:{requirement}` through the existing `ensure_task` uuid5 identity, so a reconcile pass never duplicates it; the task closes when the requirement completes and on packet cancellation.
- Fix two completion paths that rebuilt a requirement from scratch — fee-agreement signature completion and portal questionnaire submission — discarding fields set at send time. Both now preserve the requirement, so a dated item's follow-up can be closed rather than left open forever.
- Add `components/casesetup/`: a `CaseSetupCard` spine on the matter Overview that prompts for paperwork on a new matter and otherwise renders the live requirement strip (state, deadline with overdue tone, delivery failures with the existing verified retry, staff verification, meeting booking), plus a three-step `PaperworkDrawer` replacing the raw intake fieldset.
- Move `SignatureRequestsPanel` from the Client Portal tab to Overview, beside the paperwork it belongs to, and guard its list response so a non-array body cannot take the page down.
- Reduce the matter page to four primary sections — Overview, Documents, Activity, Billing — with Client Portal, Team, Workflow, Correspondence, Chat, and Settings behind one Matter settings sub-nav, replacing two partial sub-navs and an orphaned "Matter assistant" link. Correspondence and Chat previously had no home in the desktop tab bar.
- Remove Sync Cloud from Quick Actions, which the cloud panel already offers. The phone action grid stays: its buttons are the only ones on the page that guarantee a 44px touch target, which Quick Actions' tighter buttons do not.

## 2026.09.10.3 — Global sample form library in Template Studio

- Add a platform-owned `sample_templates` catalog (no `tenant_id`, no row-level security) seeded from a curated set of fillable AcroForm PDFs organized by category and jurisdiction tags.
- Add read-only `GET /api/templates/library`, `GET /api/templates/library/{id}`, `GET /api/templates/library/{id}/source`, and `POST /api/templates/library/{id}/render-file` endpoints available to every authenticated tenant.
- Add `scripts/build_sample_template_library.py` (deduplicate by SHA-256, normalize titles, tag jurisdictions, and reject PDFs with active content) and `backend/scripts/seed_sample_templates.py` (idempotent upsert by slug).
- Keep tenant templates fully isolated: the catalog shares nothing per tenant and exposes no mutation endpoints.

## 2026.09.10.2 — In-deploy maintenance heads-up banner

- Add `GET /api/release-window`, a public endpoint (like `/api/version`) that reads `release-window.json` from the read-only host-status mount beside the host disk status and returns only `active`/`window_id`/`message`; the strict reader fails closed on a missing, malformed, symlinked, or stale marker and caps the message length, so no host or build detail reaches the browser.
- `deploy_prod.sh` writes the marker after preflight and before the databases come up (maximizing lead time), removes it via an EXIT trap on every exit path — folded into the scheduler cutover trap mid-script and re-armed for the post-cutover gates — and treats a failed write as warn-and-continue so an advisory can never block a deploy.
- Add `ReleaseWindowBanner`, a slim dismissible top banner polled every 30 seconds, shown to every signed-in user including portal clients (unlike `ReleaseAnnouncement`), dismissed per user per window id.

## 2026.09.10.1 — Human-readable matter numbers

- Add `matters.matter_number` / `matter_number_seq` and `tenants.matter_prefix` / `matter_sequence_counter` (migration 170), backfilling every existing tenant with a prefix derived from its company or tenant name and numbering its matters in `created_at` order.
- Allocate numbers in `app/services/matter_number.py` via `UPDATE tenants ... RETURNING`, whose row lock serializes concurrent creations for one tenant; every matter-creation path (matters, mediation, intake, plugins, matter imports, external imports) stamps a number.
- Enforce the two properties a quotable identifier needs: `uq_matters_tenant_matter_number` for uniqueness, and a `BEFORE UPDATE` trigger that rejects any change to an assigned number. No create or update schema carries the field, so it cannot be set through the API.
- Add `GET /api/matters/by-number/{matter_number}`, tenant-scoped, accepting the forms people type (lowercase, hyphenated, padded); include `matter_number` in matter detail, list, my-matters, and client-portal responses.
- Resolve `/matters/SMIT0001` in the frontend to the matter's UUID URL before the workspace mounts, and show the number with a copy control in the matter header and the client portal header.
- Return 404 rather than a Postgres cast error for a non-UUID matter id.

## 2026.09.09.1 — Standard client paperwork for every new matter

- Add a server-owned intake starter pack: a standard fee agreement, a client intake form, and matter-type questionnaires for family, criminal, injury, estate, employment, business, real estate, immigration, bankruptcy, litigation, mediation, and general matters.
- Resolve the questionnaire from either free-text label a firm typed — matter type first, then practice area — with specific aliases beating generic ones and an unrecognised matter still receiving questions.
- Install the two documents as unapproved drafts, idempotently by title, so jurisdiction-regulated fee, trust, and contingency terms reach an attorney before a client; a firm's own template of the same name is never touched.
- Declare every placeholder against the server binding catalogue, keeping fee amounts, scope, and exclusions manual so no fee term is inferred from a record.
- Add a North Dakota hourly fee agreement modelled on a firm's own engagement terms — retainer in trust, replenishment, lien, third-party payment under N.D.R. Prof. Conduct 1.6, withdrawal, discharge and refund, and fee-action venue — stored with its jurisdiction and defaulting only terms a convention settles, never a rate or amount.
- Render the pack as fillable AcroForm PDFs and a completed Word sample from the same markdown sources, with tests that fail when a printed form and its template stop agreeing on fields.
- Add `GET /api/intake-starter-pack`, `GET /api/intake-starter-pack/practices`, and `POST /api/intake-starter-pack/documents`, with Template Studio and intake-setup controls that load or install on request rather than automatically.

## 2026.09.08.16 — Matter workspace and selected intake paperwork

- Add matter-local Template Studio attachment and destination-folder persistence, reviewed email attachment digests, and multi-attachment provider delivery.
- Add personal field visibility, tenant presentation policy, primary matter navigation, Folder/Detailed layouts, and idempotent private document copies.
- Add drawn field and whiteout rectangles to Studio, preserving its existing geometry and undo/save contracts.
- Extend intake with an existing attorney-prepared agreement, independently tracked selected forms, requested client uploads, scoped pre-signing access, and a fee-signature portal-delivery/24-hour task milestone.
- Keep current logical filing semantics for Move. No physical cloud move, database migration, deployment, or merge is included.
- Require stored signing evidence before completion, show clients the source PDF, and atomically create the post-signing follow-up. Rehearse real HTTP and browser onboarding with a disposable database.

## 2026.09.08.15 - Stable reviewed Word-to-PDF saves

- Normalize generated DOCX ZIP entry timestamps before conversion so independent preview/save fills have identical bytes and PDF identifiers.
- Preserve exact output-hash comparison and source contents; changed values still invalidate reviewed evidence.
- Exercise independent fills through real LibreOffice in CI, alongside the timestamp and content-change regression tests.

## 2026.09.08.14 - Generated PDF review inside Studio

- Reuse the existing PDF.js renderer for generated PDF and Word-to-PDF previews, with responsive fit, zoom, page selection, loading and failure states.
- Remount the viewer for each returned output so page and render state cannot carry over from an earlier preview. Preserve value-bound publication and save evidence.
- Keep completion controls visible, prioritize required blanks, retain the current input while typing in a filtered review queue, and link to firm/matter source repair without leaving the fill session.

## 2026.09.08.13 - Guided Word template review

- Add searchable review queues, source context, mobile section navigation, cross-page field lookup with ambiguous/missing results, and direct page selection.
- Separate bounded complete Word navigation context (100,000 characters) from the 20,000-character editor preview.
- Distinguish diagnostic preview results and busy states from publication tests; align review counts and guide empty generation back to the library.

## 2026.09.08.12 - Approved automation service rules

- Add named noninteractive service identities with immutable narrow grants; services cannot sign in, hold roles, consume a seat, consent to MCP, or approve legal work.
- Freeze completed durable workflow inputs and verified sources into hash-bound rules that require an active licensed legal-work approver before activation.
- Schedule daily, weekly, or pinned lifecycle-event work with one immutable occurrence receipt, tenant RLS, durable run checkpoints, reviewer assignment, and bounded daily admission.

## 2026.09.08.10 - Durable capability workflows

- Add bounded Chat/MCP workflow plans, encrypted step payloads, missing-input/review continuation, current authority checks and firm/matter evidence views.
- Persist artifact and provider checkpoints around cloud I/O; verify original folder and exact bytes when reconciling an uncertain write. Keep final delivery in existing reviewed task workers.
- Replace durable-job dispatch branches with an explicit handler registry, preserve legacy transaction modes, and account for runtime MCP steps in existing grant budgets.
- Add migration 168 with forced RLS and immutable evidence guards; rehearse actual process termination, one cloud upload, retained artifact identity and one normal attorney approval.

## [2026.09.08.11] - 2026-09-08

- Add bounded firm Workspace MCP activity and active-grant views with named actors, exact revision review evidence, tenant-scoped joins and historical audit links.
- Apply atomic grant/tenant read-byte budgets to direct MCP reads and durable runtime reads without AI debits. Preserve blocked checkpoints until continuation.
- Promote firm-approved assistant setup with separate Workspace/Research URLs, account governance and metered-feature boundaries; add OpenCode setup.
- Rehearse tenant-isolated activity and read-budget pause/resume on the existing killed-worker and human artifact-review fixture.

## 2026.09.08.9 - Signing-aware template completion

- Exclude signatures and signer-linked dates from Smart Fill and pre-signing completion while preserving ordinary required dates.
- Keep signing fields blank in Word output, reject supplied signing values, and clear old signing-date AcroForm values in generated PDFs.
- Cover Word, PDF overlays, AcroForms, mixed PDFs, and fill UI without sending live signature requests.

## 2026.09.08.8 - Firm configuration synthesis

- Prepare bounded ordinary template/rule drafts from tenant metadata and migrated Clio/Tabs3 history, using existing definition hashes and human approval endpoints.
- Preserve immutable proposal evidence, source fingerprints, rejection suppression, and non-destructive amendments to active templates in migration 167.
- Add mapped task/matter CSV ingestion, automatic Tabs3 staging analysis, and onboarding/Workflow settings evidence controls.
- Rehearse native/imported drafts, approval, drift, rollback/retry, and tenant isolation on migrated PostgreSQL; include rehearsal evidence in backend coverage.

## 2026.09.08.7 - Lifecycle workflow preparation

- Add eight lifecycle events to approved workflow rules and the firm rule editor.
- Capture source writes transactionally, including imports and background writes; defer fingerprints until transaction completion and deduplicate source occurrences.
- Scan approaching deadlines and overdue unpaid invoices under tenant context every 15 minutes; preserve changed-source blocks, crash recovery, and immutable evidence without applying workflows.
- Add migration 166 and a migrated PostgreSQL rehearsal covering all eight events, both intake paths, RLS, rollback, worker recovery, and replay.

## 2026.09.08.6 - Document-first Template Studio

- Focus Studio on the document, simplify field properties, and keep unsaved edits from being discarded by Studio navigation.
- Place PDF fields at the pointer with correct viewport geometry; stabilize canvas error callbacks to prevent a redraw loop and scale loss.
- Edit Word paragraph wording into a revised draft, guard source/version, rebase unaffected anchors, reject included-field overlap, and preserve source evidence.
- Show a source reference beside fill values, distinguish draft references from published output, and move output choices below completion.


## 2026.09.08.4 - Exact artifact review and approved attachments

- Add migration165: tenant-scoped review requirements, immutable decisions and delivery receipts, exact content/file hashes, stage ordering, override evidence and revision supersession.
- Add firm staff/attorney or attorney-only policy and authenticated review controls in chat and the work board.
- Resolve optional approved artifact attachments through the shared email proposal handler; verify bytes before a bounded Microsoft, Google or SMTP send and preserve interrupted outcomes.
- Add PostgreSQL migration/RLS rehearsal plus focused review, transport and UI tests.

# 2026.09.06.4 - Durable workflow preparation

## 2026.09.08.5

- Add explicit tenant-scoped firm profile template bindings for name, address, phone, email, and website, reusing existing branding settings and fallbacks without a migration.
- Show configured/missing shared values in the Field Library and distinguish saved profile values from suggestion confidence in document fill review.
- Preserve manual entries, require explicit firm source mappings, and keep saved generated documents independent of later profile changes.

## 2026.09.08.3 - Template variations and matter completion

- Add tenant-scoped template copying with independent source/evidence files and draft-only lifecycle state.
- Add fill completion, missing/review filters, per-value confidence, explicit changed-suggestion acceptance, and matter-switch isolation.
- Document the expanded authoring requirement and remaining editor, DOC import, rescan and matter-native generation work.

## 2026-09-07 - Role navigation profiles

- Added tenant role view allowlists and account-synced personal navigation hiding/order (migration 159).
- Added six role view starting points, role editing, navigation gear, consistent mobile/header visibility, and focused API/UI/browser tests. Visibility is independent of existing authorization.

- Queue matching workflow triggers in the matter-save transaction; atomically commit prepared runs and worker receipts, recover leases, retry transient failures, and preserve ever-per-condition deduplication.
- Revalidate actor/tenant, original fact fingerprint, rule and approved version; block changed context with a manual-review path. Payloads/errors omit source content. No automatic task application or provider calls.
- Extend existing activity APIs and workflow panels with pending/retry/failure states and review of stored previews. Add transaction/recovery/permission and UI approval tests.

# Changelog

## 2026.09.08.2 - Workspace grant admission

- Enforce Redis-atomic tool-call budgets by tenant and OAuth grant, surviving access-token rotation while retaining existing token/tenant request controls.
- Bound read-result UTF-8 JSON size across both MCP representations, return retry/narrow-query guidance, and record content-free result sizes/refusal codes.
- Record broad BYO-harness channel support independently from LawHand inference metering; amend BK24 and preserve explicit capability/promotion residuals.
- No migration, AI route change, or automatic execution authority.


## 2026.09.08.1 - Restore Word previews and focus Studio editing

- Strip the narrowly validated local XYZ opening destination emitted by LibreOffice before PDF normalization; continue rejecting action dictionaries, additional actions, and malformed destinations.
- Move optional Word-copy and applicability controls below the editor, consolidate field counts, and reduce the workspace header.
- Route Add field to usable text mapping when page rendering is pending or unavailable.
- Verified with the installed production converter using synthetic content only; no deployed code or source templates changed during diagnosis.

## 2026.09.07.23 - Background route provider pricing

- Add native DeepSeek V4 peak rates and explicit zero rates for provider-verified OpenCode Zen free endpoints, fixing activation of DeepSeek V4 Flash with a Mimo V2.5 Free fallback.
- Accept explicitly attested, provider-qualified zero-price overrides. Unknown prices, partial zero rates, and negative/nonfinite rates remain rejected.
- Keep positive bookkeeping reservations and request-count limits for free work; release the hold to zero on confirmed free responses. Unknown outcomes retain the conservative reservation.
- No migration, live route activation, chat profile change, or background data-policy change.

## 2026.09.07.22 - Public support path and requirements matrix

- Publish `/support`, rendering the existing versioned support policy (coverage hours, S1-S4 definitions, acknowledgement objectives, escalation) live from `GET /api/public/support-policy` so published response times cannot drift from the security-review packet. Contact is `mailto:support@getlawhand.com`; the email path and the objectives-not-an-SLA boundary render without a successful fetch.
- Publish `/requirements` with platform prerequisites, the Microsoft 365 and Google Workspace matrix, the administrator role each provider needs, the exact scopes requested for tenant-wide and per-user consent, the onboarding sequence, and current limits including the presently broad consent bundle.
- Add `frontend/src/marketing/integration-scopes.json` as the canonical published scope list, asserted against the router and Teams scope constants by `backend/tests/test_public_integration_scopes.py` so narrowing a scope fails a test rather than leaving a stale public claim.
- Activate the built-but-unused tenant support workflow as an admin-only Administration → Support tab: severity picker backed by the published policy, acknowledgement clock and policy version returned on filing, request history, and the backend's unsafe-content rejection surfaced verbatim. Accountant roles do not see the tab.
- Wire both routes through routing, SEO metadata, sitemap, no-JavaScript prerender shells, nginx rewrites and analytics CSP maps, and the marketing footer. The `/support` shell deliberately carries no hours or acknowledgement figures so it cannot become a second stale copy of the policy.


## 2026.09.07.21 - Microsoft Calendar read-time normalization

- Ask Microsoft Graph to return calendar-view times in UTC, normalize timed values to explicit UTC instants, and preserve all-day values as date-only. The browser now groups timed provider events by its local date instead of slicing a UTC timestamp.
- Live synthetic evidence is retained locally at `validation-artifacts/2026-09-07-chat-rag/calendar-roundtrip.json`: Graph returned an offset-less UTC time that reproduced the five-hour display shift. The fixture's 15-minute provider-default reminder is not evidence that LawHand sent or delivered an alert.
- Add mocked Graph regressions for UTC, offset, malformed, all-day, and UTC-cross-midnight inputs. No provider writes, calendar-item mutation, or migration.

## 2026.09.07.20 - Preserve legacy cloud root bindings

- Preserve valid saved OneDrive, SharePoint, and Google root IDs on retry, OAuth reconnect, and matter-folder provisioning; initialize only absent provider roots.
- Flag malformed saved root bindings for administrator repair without rebinding tenant or matter folders. Add regression coverage for legacy and secondary-provider paths. No migration.

## 2026.09.07.19 - Assistant retrieval continuity and source honesty

- Restore only ready, unexpired same-tenant, same-conversation, same-matter attachments for follow-up turns; preserve explicit subsets and enforce the private-route gate for implicit attachments.
- Extract supported cloud PDF/DOCX and native Google exports instead of decoding raw binary bytes. Bound downloads to 10 MiB, preserve provider redirects, and parse off the async event loop.
- Decode retained EML and Outlook MIME headers and bodies, including HTML-only messages, while excluding attached-file payloads. Record a verified Microsoft self-delivery and OneDrive EML retention roundtrip.
- Admit connected-source planning against the exact selected chat route, including Premium; reject malformed/empty keyword plans and blank manual queries. Remove profile jurisdiction defaults from public/general requests.
- Persist public retrieval outage metadata, reconcile source counts/origins, and expose the 4,000-character/100-chunk attachment excerpt limits. No migration or automatic promotion of conversation documents to matter knowledge.
- Record live synthetic TXT/PDF/DOCX and long-file validation, known public-search timeouts, and remaining indexing, relevance, artifact-citation and layout concerns. Add focused backend/frontend regressions and updated user/admin guidance.

## 2026.09.07.17 - Dedicated premium template AI profile

- Add an independently activated, platform-global document-template profile using the stored OpenRouter vault key and `anthropic/claude-opus-5`.
- Keep Standard/Premium chat, tenant BYOK, and Background Automations routing unchanged. Unconfigured template AI fails closed, with no shared-route fallback.
- Meter `template_ai_map` with explicit profile token rates and the existing PAYG markup; preserve gateway request/model correlation, consent, bounded editor context, and daily token limits.
- Validate a revisioned template alias before publishing settings, enforce the existing confidential-data policy, and prevent deletion of its active key. No migration.

## 2026.09.07.16 - Context-aware intake AI suggestions

- Verify the real LLM HTTP boundary and retain provider-reported model identity separately from the requested alias, including fallback responses.
- Add a bounded, redacted current-editor context to premium field proposals, including optional requirements and server-owned binding definitions. Retain existing model routing, billing and source reconciliation.
- Preserve draft text, field edits and exclusions when appending proposals; reject stale responses after same-document edits. Add context contract, privacy, HTTP validation and UI regression coverage. No migration.

## 2026.09.07.15 - Matter cloud-folder lock repair

- Override Matter's nullable joined partner-attorney relationship while selecting a row for cloud-folder repair, and lock only the Matter table. PostgreSQL can now initialize an unbound matter without rejecting `FOR UPDATE` on an outer join.
- Add a PostgreSQL retry-route regression for a matter with no cloud folder and no partner attorney.

## 2026.09.07.14 - Document-first Studio import and clear test results

- Start bounded, tenant-scoped DOCX page preview on file selection, before analysis or persistence; retain explicit Fields/failure fallback.
- Select rendered Word text to create a named field and click a persistent field box to edit it beside the page. Resolve saved Word selections to exact paragraph anchors; retain the text view for ambiguous locations. Keep saved PDF field labels visible.
- Expose test evidence, missing values and exact render errors separately from visual approval; keep cleanup controls from displacing the Word field inspector.

## 2026.09.07.13 - Cloud folder recovery test order

- Make the cloud-folder retry regression deliberately fail the first attempted matter, then verify the next one proceeds. PostgreSQL does not guarantee query row order, so the check now validates savepoint recovery without assuming a particular matter is first.

## 2026.09.07.12 - Cloud folder retry isolation

- Resolve cloud provider tokens before each retry pass, then isolate every matter-folder initialization in its own database savepoint. A lock failure now rolls back only that matter's work and leaves the next matter eligible to proceed.
- Commit each successful matter binding before the next one to release locks promptly. Report partial setup accurately through `matters_failed` and a `partial` status, and show the retry count in Integrations.

## 2026.09.07.11 - Template Studio AI proposal handoff

- Reuse the short-lived, signed local-analysis snapshot when Template Studio requests an explicitly consented Premium AI field proposal. The snapshot remains bound to the exact tenant, user, file name, and source bytes; stale or changed inputs require a new local analysis.
- Avoid a second full PDF/DOCX/OCR analysis before the AI request while preserving the existing source normalization and local proposal reconciliation safeguards.

## 2026.09.07.10 - Connected matter workflow corrections

- Submit ZIP and folder import file/path payloads as multipart data; render Cloud Search fetch_content_results.
- Scope live matter retrieval to provisioned folders and exact tenant/matter document references; never authorize siblings from a linked file. Use a bounded metadata fallback and bypass stale live-matter RAG caches. New SharePoint index item and parent identities include the drive; legacy unqualified rows require normal resync and are excluded from scoped fallback without purging unrelated metadata.
- Preserve scheduled-event instants across browser and provider timezones with DST tests. Add event details, provider links, and delete controls; failed deletes retain the event and already-absent Microsoft events remain idempotent.
- Route Email Client through the existing actor/firm Microsoft and Google mailbox delivery service; retain SMTP only when no cloud-mail grant exists. Restore tenant context after token refresh and record recipient/provider with the delivery outcome.
- Preserve provider item, drive, parent, and backend identity on newly captured .eml records; restore tenant scope after token refresh. Capture/read regressions cover OneDrive, SharePoint, and Google Drive. Disconnected scans now give reconnection instructions.
- Escape text in HTML email and label failed/unconfirmed correspondence. Prevent blind composer retries after an ambiguous provider outcome.

## 2026.09.07.9 - Word derived placeholder drafts

- Convert explicitly confirmed Word source spans into literal placeholders in a new draft while retaining the immutable original upload and provenance chain.
- Preserve field metadata, formatting and surrounding text across body, table, header and footer stories; reject stale, overlapping or ambiguous edits safely.
- Add source mode choice, original evidence download, targeted token-preserving cleanup and edit-then-fill coverage in Template Studio.

## 2026.09.07.8 — Positioned signing fields

- Bind template signing roles to the generated PDF digest and actual page geometry; retain placements on matter documents and signature requests.
- Review final PDF signing positions before dispatch, including Word documents after reflow. Dropbox Sign receives explicit role-bound tabs; unsupported page geometry and internal portal placement fail before sending.
- Migration 163 adds signing placement metadata. Source bytes, tenant boundaries, roles, and page geometry are revalidated before provider dispatch.

## 2026.09.07.7 - PDF source cover regions

- Add value-less, tenant-reviewed PDF cover regions in Template Studio. Authors can add, move, resize, remove, undo and redo white cover rectangles without introducing automation variables.
- Preserve the existing source overlay controls with an explicit “Cover what is underneath” option and flatten covers before generated PDF field content is drawn.
- Validate cover geometry, page bounds and flattened output requirements; original source bytes remain integrity checked and unchanged.

## 2026.09.07.6 - Word source page previews

- Add a tenant-authorized, integrity-checked source preview endpoint using bounded LibreOffice conversion and a 64 MiB / 32-entry per-worker LRU.
- Show Word source PDF pages, thumbnails and zoom in Studio with persistent Document/Fields switching and safe conversion fallback. Source previews do not create generation evidence or change original uploads.
- Coordinate after source-review PR #352 (release .5); document remaining visual-authoring work and provider coordinate constraints.

## 2026.09.07.5

- Add the searchable Field Library using existing built-in and tenant custom definitions, distinct current-template counts, paginated usage and Studio links. Document explicit Word placeholder conventions and the difference between shared data sources and template-local value links.

- Separate repeated generic Word placeholders by source span, support explicit same-value links and leading choice marks, and preserve legal definitions when replacing labelled names.
- Require source-backed review decisions for unresolved candidate blanks and sample values on new Word masters before publication; preserve legacy published mappings.
- Render source-order tables, common numbering, direct run styles and existing field highlights in Studio; exclude UI labels from Unicode selection offsets. Avoid creating missing header/footer parts during inspection and preserve formatting on unchanged replacements.
- Add synthetic backend/browser regressions and document local validation of five customer sources (38 unchanged rendered pages); customer files remain outside Git.

## 2026.09.07.4 — Cloud provider portability remediation

- Add verified user aliases with tenant-scoped uniqueness, one-time email proof, primary-address-first OAuth matching, and correspondence matching for verified aliases on assigned matters.
- Add administrator storage migration controls for connected target roots, server-side discovery evidence, matching-rung review, explicit cutover confirmation, durable status refresh, and pending reindex visibility.
- Preserve the canonical `claritylegal-records` matter-folder root, route captured EML files to `correspondence`, and retain the configured cloud root when onboarding setup is re-entered.
- Document account-tier limits and distinguish bounded cloud routing metadata from full text and embeddings retained for explicitly ingested document workflows. The platform-wide corpus posture decision remains pending.

## 2026.09.07.3

- Lead Firm Memory with one query box and a scope control; move matter, source, file-type and date filters behind a collapsed Refine panel and surface any active refinement as a removable chip. The matter selector becomes an optional narrowing filter rather than the first control a reader meets.
- Collapse the separate source, file-share and cloud-provider selects into one control feeding the same `source_ids` list; selecting a cloud provider still expands to every source behind it.
- Render search-node `<mark>` highlights as React nodes, unescaping only the text between markers, so hits are emphasised and no corpus markup is interpreted. Metadata-fallback snippets highlight query terms client-side.
- Match research questions with `default_operator: OR` and `minimum_should_match` (`2<70%`, tunable through `OpenSearchLimits`) instead of a blanket AND; retry an OpenSearch parse error once with the query escaped as literal text. Explicit `AND`/`OR`/`NOT` and quoted phrases still bind as typed.
- Replace `plainto_tsquery` with `websearch_to_tsquery` on the SaaS metadata fallback and widen a syntax-free question to an OR over its terms, matching the RAG retrieval path's recall.
- State coverage positively when a search is complete and name the administrator action behind `matter_binding_required` and `no_authorized_matter_scope`. No authorization change: ACL, deny-token, path-scope and matter-binding clauses are untouched, and an unbound SMB share is still never searched.

## 2026.09.07.2

- Add portal multi-file/folder transfer with bounded relative paths, preserved subfolders, per-file retry results and deterministic source/content identities for replay. Reject storage failures and prevent same-name physical file overwrites.
- Add explicitly published portal upload links with matter access checks and an append-only matter event audit; source links enter the existing portal correspondence review flow.
- Add document-list refresh controls and document the separate metadata sync and content-indexing boundaries. No migration or production indexing activation.

## 2026.09.06.8

- Preserve matter sections in URLs and return document review to Documents; expose scoped task links and completion/load recovery on the dashboard.
- Add general task corrections, explicit date/note clearing, matter-scoped task creation and current-route filter synchronization.
- Keep estate drafts after failures, expose required-field validation and load retry, make correction controls accessible, and permit clearing optional dates/values.

- Limit Assistant list requests to its workspace; preserve independent list success and ignore late navigation results.
- Add intake load retry and stage failure feedback; protect communications and research navigation from stale responses; serialize portal message polling.
- Preserve both field and region state in Studio undo/redo. Isolate Workspace and Research MCP consent state by request ID.
- Batch tenant user lookup and cache new-user MCP defaults during Microsoft/Google sync. Bound chat attachment reads before rejecting oversized files.
- Record feature coverage, regression evidence and scoped follow-ups in `docs/platform-feature-review-2026-09-06.md`; no migration or deployment change.

## 2026.09.06.7

- Add matter intake packets with independently verified fee agreement and questionnaire completion, encrypted portal invitations, connected Microsoft/Google email and storage, existing Twilio SMS delivery, seven-day document follow-up and scheduling tasks due within 24 hours.
- Add staff receipt/meeting/retry/renewal controls and a client questionnaire checklist. Migration 158 persists tenant-scoped packet, delivery and timing state; demo data purges include packets.

## 2026.09.06.6

- Add reviewed folder/ZIP matter creation and historical EML correspondence imports with persistent per-file results, destination authorization and Microsoft/Google storage routing.

## 2026.09.06.2 — Template Studio launch preparation

- Read immutable published snapshots for generation while authoring/testing later drafts; preserve source-hash and reviewed PDF contracts.
- Bind fields to non-sensitive typed matter/client custom definitions and show source/review provenance without confidence-as-truth labels.
- Add bounded PDF/DOCX/text label-value extraction with explicit corrected acceptance, optimistic source/value/definition evidence, tenant/actor binding, and HMAC-only matter audit metadata.
- Add named single-rule applicability using saved custom facts, friendly boolean/select controls, guided setup, and header contrast fixes.
- No migration or production activation. Structured child/asset repeats, unlabeled extraction and customer acceptance remain outside this increment.

## [Unreleased]

### Mobile casework — 2026.09.06.5
- Add a phone matter section picker, six touch-sized casework shortcuts, visible stage/open-task context and matter-filtered task list/board links.
- Retain interrupted note input and replay an optional request UUID under a matter row lock. Tenant/actor/matter-derived IDs and an event digest prevent duplicate notes/events, changed-payload replay and recreation after deletion; no migration.
- Fit email composition into a short visual viewport and explain unsupported Windows network-file opening on phones.
- Add synthetic Chromium/WebKit casework journeys and real PostgreSQL note concurrency/rollback/tenant regressions. These do not establish physical-phone or customer-provider acceptance.

### Research readiness (2026.09.06.3)
- Verify source jurisdiction before counting scoped public research coverage; preserve corpus metadata through prompts, streaming previews, stored citations and source cards.
- Add an offline, configurable acceptance evaluator with digest-bound attorney review, authority recall, jurisdiction, quotation, abstention, currentness disclosure, latency and cost gates. Synthetic fixtures are never customer acceptance or a parity benchmark.


### Fixed
- **Firm Memory native authorization and OpenSearch serving:** fail closed on generalized search fallback; recover document envelopes and explicit DENY filtering; connect scanner/extraction manifest to OpenSearch with isolated parsing, durable generations, query-time DACL revalidation and version fencing. Default-off pilot; see `docs/firm-memory-launch-readiness.md` for runtime provisioning and remaining acceptance limits.
- **Firm Memory result links no longer fail silently:** a shared
  `/firm-memory?matter=…&file=…` link resolved only when its matter appeared in
  the picker's first page of matters, so links into large firms rendered an
  empty page. The deep link now resolves on its own; the server authorizes it
  either way. A response that searched no source at all is now reported as
  partial rather than as a quiet non-partial zero.

### Documentation
- **COMP-02/03 closure audit:** reconciled the merged switching and conversion
  slices against current `origin/main`, recorded focused validation evidence,
  and documented the canonical import-promotion, SMS compliance/provider,
  signed-agreement promotion, and production-rehearsal blockers. The COMP
  checkboxes remain open until those acceptance gates are demonstrably met.

### Added
- **Word templates can become signature-ready PDFs:** document generation now
  offers either an editable DOCX or a PDF for signature from the same reviewed
  Word template. PDF output is converted with a bounded headless LibreOffice
  process, validated as a passive PDF, previewed inline, and bound to the exact
  matter and field values before it can be saved for the e-signing workflow.
- **Governed Template Studio publication lifecycle:** migration 157 repairs the
  version-156 pre-edit history ambiguity by appending an exact snapshot of every
  live versioned template, then adds explicit tested and published version
  pointers. Content and field-map edits become drafts and invalidate test
  evidence; successful representative previews mark one exact immutable
  version ready to publish; publication makes that tested version available;
  and matter generation refuses a missing or mismatched published version while
  recording its number in the matter event. Studio now has an actionable Test
  tab, format-aware authoring guidance, tested/published badges, and a publish
  control. Its four non-overlapping work queues come from dedicated firm-wide
  server queries instead of mixing global counts with the current 12-row page.
- **Template data bindings, logic, versions, and visual Word authoring:** a
  template field may declare one binding from a closed, server-owned catalogue
  (`matter.case_number`, `client.address.city`, `party.plaintiff.name`, …)
  resolved in `build_variable_suggestions`, replacing name-only Smart Fill
  matching that never fired on customer-authored field names. A declared
  binding is authoritative and never falls back to name matching, including
  when the catalogue no longer knows the path: a blank field naming the source
  it cannot reach beats quietly re-sourcing a clause. Conditional and repeating
  regions (`{{#if}}`, `{{#unless}}`, `{{#each}}`) resolve in markdown bodies and
  Word sources over a closed operator vocabulary with no expression language;
  repeat items are never inlined during expansion, so a customer value cannot
  be reinterpreted as a marker. Word regions resolve after field replacement
  because anchors address paragraphs by ordinal in the original document.
  Migration 156 adds `document_template_versions`, one append-only row per
  published state under FORCE RLS, guarded by a trigger that rejects ordinary
  UPDATE/DELETE while still permitting the `ON DELETE CASCADE` from
  `document_templates` (a rewrite rule would have swallowed that cascade
  silently). `GET /templates/{id}/outline` serves a Word template's paragraphs
  numbered by the same iterator that fills it, so the editor places fields by
  text selection rather than by rasterizing pages; regions marked there are
  stored as ordinal ranges and materialised as markers in the in-memory
  document at render time, leaving retained source bytes untouched. All
  additive: a template with no bindings, logic, regions or versions behaves
  exactly as before.
- **Bounded matter workflow automations (COMP-09 trigger/action slice):**
  approved workflow templates no longer wait for someone to remember them.
  Migration 155 adds `matter_workflow_automation_rules` and append-only
  `matter_workflow_automation_events`, both FORCE RLS with composite
  `(tenant_id, id)` parents. A rule pairs one bounded trigger — a matter is
  opened, or a matter enters a named stage — with optional matter-type and
  practice-area equality conditions and one approved template. It plans the
  same reviewable `planned` run the manual preview endpoint plans and never
  applies it: tasks and matter stages still change only through the existing
  `approve_legal_work` + `manage_matters` apply path. Rules are authored with
  `manage_workflows`, activated with `approve_legal_work` against the exact
  reviewed `definition_sha256`, and an edit that changes what a rule does
  returns it to draft — enforced by a database trigger, not just the API, while
  a rename costs no approval because the name is not part of the definition. Dispatch runs after the matter
  change commits, in its own transaction, and never raises into the caller, so
  a broken automation cannot cost a firm a saved matter; each rule is planned
  in its own savepoint. A dedupe key unique per rule makes one plan per rule,
  matter, and triggering condition, so retries, concurrent requests, and a
  matter re-entering an automated stage do not produce a second run. A rule
  that cannot plan records a `blocked` outcome with a failure code instead of
  going silent, and both the rule and the matter surface that history.
  Automation evidence reuses migration 148's append-only trigger, so the
  verified expired-demo purge remains its only delete path.
- **Firm-wide Firm Memory research over document text:** the unified Firm Memory
  search now routes matter-bound SMB sources through the customer search-node
  relay, so results carry document text, passages and page numbers instead of
  file names and a capped preview. A query with no matter filter expands into
  the matters on each share the actor is already authorized on, decided by the
  same matter policy a typed filter goes through, capped at 100 matters and
  reported when truncated. The relay now accepts a matter set and sends one task
  per agent rather than per matter; agent 0.17.0 binds that set in the signed
  identity ticket, and an older agent is reported as uncovered rather than
  searched with a weaker binding. Coverage names the index that answered
  (`smb_local_fulltext` or the `smb_metadata_fts` fallback), a node that did not
  answer is never reported as an empty corpus, and every response that is not
  complete states in one sentence why.
- **Service-account authorization model made explicit:** LawHand reads a file
  share through one service account and firm logins do not map one-to-one onto
  Windows accounts, so the matter binding — not file permissions — is the
  authorization boundary. An SMB source with no matter binding is now reported
  `matter_binding_required` and is never searched, and every returned path is
  re-checked against the bound folders of the actor's authorized matters before
  it leaves the service, on the full-text path as well as the metadata one.
- **Firm Memory result actions:** matter-bound on-premises results now carry a
  server-issued `lawhand_result` action addressing the existing fail-closed
  matter-file resolver, so a found document can be opened. The opaque
  `document_id` remains a non-reversible HMAC, and `open_on_device` is reported
  unavailable with a reason rather than as a dead control.
- **Matter document folders and tags:** case documents are no longer a single
  flat list. Migration 154 adds a per-matter folder tree (materialized paths,
  depth and cycle limits, case-insensitive sibling names, FORCE RLS) plus a
  firm-wide tag vocabulary and its assignment rows. `matter_documents.folder_id`
  is `ON DELETE RESTRICT`, so a folder can never delete or orphan the documents
  filed in it; the delete endpoint re-files a subtree's documents into the
  parent in the same transaction when the caller opts in. The document list
  gains server-side folder scoping (with or without subfolders), filename and
  description search with escaped LIKE wildcards, conjunctive tag filtering, and
  name/date/size sorting. Uploads into a folder are written to the matching
  folder under the matter in the firm's bound cloud share (OneDrive, SharePoint,
  Google Drive) or under the mirrored local path, while uploads with no folder
  keep the historical category layout untouched. Client portal uploads file
  themselves into a protected `Client Uploads` system folder that still routes
  to the canonical provisioned cloud subfolder. Moving an existing document
  between folders does not relocate the copy already written to the cloud share.
- **On-premises Search Node core:** added a default-off OpenSearch serving
  engine with generation-fenced atomic document envelopes and nested chunks,
  BM25 phrase/Boolean/field search, page provenance, ACL filtering, bounded
  bulk/query operations, atomic alias rebuilds, a local authenticated query
  gateway, durable control-only SQLite state, and exact restore/recovery hooks.
  Document corpus and query execution remain on customer infrastructure.
- **Template Studio durable render orchestration:** dedicated PostgreSQL durable
  job kinds now provide tenant-wide request-hash idempotency, admission and
  storage quotas, fenced leases and heartbeats, cancellation, sanitized retry
  failures, stale-output preservation, and exact current-evidence adoption.
  Tenant-scoped content-addressed storage verifies atomic reads/writes and keeps
  durable staged receipts for crash reconciliation; artifact retention enforces
  expiry, legal hold, preferred-evidence, and metadata gates. A no-shell,
  no-network, bounded subprocess worker is independently health-checked and the
  generic durable worker excludes Studio kinds. Migration 150 adds tenant-safe
  composite ownership, FORCE RLS, lifecycle constraints, and preferred-evidence
  bindings. Production activation remains fail-closed until encrypted CAS
  backup and restore rehearsal join the deployment gate; DOCX fidelity remains
  Phase 5 work.
- **Default-off Firm Memory identity and native authorization gate:** the SaaS
  can map a user to an immutable AD/Entra object and SID set, expand versioned
  effective group SIDs, and mint one-use Ed25519 search tickets bound to tenant,
  authorizer so inaccessible matter names and association counts are never
  returned through a second policy path. The rollout remains disabled until
  identity resolution, signing, and ACL coverage are explicitly healthy.
- **Secure on-premise file opening (default off):** Firm Memory result actions
  can mint 90-second, single-use intents bound to tenant, user, opaque local
  source identity, agent, share, revision, and action. The signed interactive
  Windows opener sends only the opaque handle
  over a local-only named pipe to the session-0 agent. The agent pins the peer
  to the protected installed opener, records its session and hashed Windows
  SID, redeems once, resolves the source in its local ledger, and revalidates
  the assigned root and revision. It impersonates the pipe client for a live
  SMB/NTFS access probe before returning the path over IPC; the interactive
  process probes again and invokes Explorer/Shell. Browser and launch URLs
  never contain UNC paths; outcome audit stores no token or path.
- **Configurable matter data and review-first workflow execution:** tenant
  administrators can define bounded typed matter fields in the matter workflow
  UI, with tenant-safe contact field definitions and values available as an API
  foundation pending coordinated CRM integration. They can also define immutable
  approved matter-template versions containing ordered stages, checklists,
  relative tasks, assignee roles, and required-field rules. Matter teams preview
  the exact template, matter, field, and task snapshot before a separately
  authorized apply creates work in one transaction. Stable idempotency keys,
  stale-preview rejection, append-only database-protected run/step evidence,
  tenant-safe composite foreign keys, FORCE RLS, and compensating cancellation
  of unchanged workflow tasks plus prior-stage restoration make retries and
  rollback reviewable without destructive history deletion. This slice does
  not add arbitrary triggers/actions, outbound email,
  native DOCX/Smart Fill, generalized Studio automation, or a no-code builder.
- **Consent-aware provider-backed SMS:** tenant administrators can keep SMS
  inactive until provider credentials, sender readiness, ownership, consent,
  and quiet-hours evidence are configured. Outbound messages require verified,
  provenance-bearing consent and stable idempotency; provider account and
  account-scoped active destination ownership, locked configuration-generation
  checks, dispatch-admission/credential-rotation serialization, and signed
  tenant-bound inbound/status callbacks fail closed on
  cross-tenant routing; inactive senders cannot ingest new inbound messages.
  Durable unknown-outcome recovery retains one authorized timeline marker and
  sanitized audit record, rebinds matching in-flight task runs, and does not
  claim delivery. Unauthorized SMS tasks are omitted from generic task, report,
  calendar, and Workspace MCP reads. SMS proposals are never copied to assignment
  email or third-party calendars; ordinary approval/update responses do not
  depend on external calendar cleanup, while assignment revocation requires an
  exact-user, verified delete-or-absence result for legacy copies. Provider
  timeline rows cannot be fabricated,
  edited, or deleted through the generic communications API. SMS proposal and
  task-event evidence cannot be hard-deleted. STOP-family replies revoke consent;
  ambiguous inbound routes prove access to every candidate matter before
  returning status or target errors and may be resolved only to exact stored
  candidates. Assistant proposals require live
  matter access before creation and idempotent replay, remain human-approved,
  and recheck consent at approval. Demo purge treats pre-SMS and complete-SMS
  schemas coherently, including retired credential history, and refuses a
  partially installed SMS table family.
- **Template Studio Phase 1 shell:** the existing document-template library is
  now a first-class Template Studio with response-derived continue-setup,
  needs-attention, ready-to-generate, and recent queues; canonical persistent
  workspaces; recoverable new/import routes; and an allowlisted UUID-only
  `lawhand.open_studio` browser event adapter. Test, version, activity, draft,
  proposal, and snapshot routes expose truthful unavailable states until their
  server contracts ship. Existing upload/source review, PDF/image preparation,
  activation, preview, Smart Fill, generation, matter-save, and `/templates`
  behavior remain in place.
- **Collaborative research workspace:** matter teams can keep a shared,
  tenant-isolated research trail for issues, searches, authorities, highlights,
  annotations, exclusions, outlines, and memo assembly. Records preserve their
  `cited`, `verify`, or `model` class with exact source/provenance metadata;
  snapshots and export packages retain that distinction and remain reviewable,
  not a good-law or citation-correctness guarantee.
- **Mediation review and native portal overlay:** the licensed mediation module
  now extends its linked matter in the native My Matters portal while retaining
  a separate invite surface for external parties. Documents and proposals are
  private to their submitter and firm until recipient-specific release;
  proposals require attorney review, counters require same-case released
  parents, and supersession occurs only on release. Released documents,
  proposals, and approved asset rows are immutable, release races serialize on
  the protected record, portal responses mask other recipients and internal
  user identifiers, and new document downloads verify their recorded SHA-256
  digest. Firm and party surfaces now require the live paid add-on entitlement,
  and approval/release actions require the `approve_legal_work` capability.
  The native overlay fails closed for inactive entitlements, ambiguous matter
  links, or mismatched contacts without disrupting the base portal.
- **Review-first citator control plane:** promoted, reviewed public-authority
  snapshots now have separately immutable source facts for authority identity,
  direct/later history, citation context, and amendment/repeal evidence plus
  provisional machine treatment and append-only attorney review/override
  records. Tenant/matter watches require consent, RLS isolation, idempotent
  alert fingerprints, revocation, quiet/failure outcomes, and a durable audit
  trail. Customer MCP read tools expose source/version/as-of evidence and known
  gaps without making a good-law or complete-coverage claim; a licensed or
  attorney-reviewed evaluation benchmark remains a release gate for any
  authoritative-completeness assertion.
- **Versioned public-authority coverage control plane:** reviewed rights and
  provenance fields, immutable corpus release/audit ledgers, harvest evidence,
  claim-safe coverage projection, and exact embedding compatibility now back the
  public research source-health surface. A single version-bound lineage contract
  now requires current reviewed source rights, storage policy, explicit public
  admission, catalog schema, implementation state, and promoted-manifest identity
  across ingestion, search, citation, court/docket, status, coverage, audit, and
  promotion paths. Tenant, firm, private, custom, revoked, and mismatched records
  fail closed and remain out of authority content, identifiers, aggregates,
  telemetry, and customer claims.
- **Bounded Firm Memory search relay:** an explicitly matter-scoped local
  SQLite FTS5 control index can now answer bounded searches through the
  outbound-polled file-share agent, authenticated REST/portal, Chat structured
  sources, and user-bound Workspace MCP. Results carry opaque file identity,
  bounded snippets/page hints, safe same-origin deep links, index state, and
  partial/degraded status. The browser receives no raw `file://` or `smb://`
  link; users can copy the canonical UNC path after authorization is rechecked.
  Query text is short-lived and excluded from application logs, audit rows, and
  evaluator output. This release does not claim 4 TB scale, Tika/OCR/OpenSearch,
  native Windows ACL trimming, or semantic retrieval.

### Fixed
- **Search Node extraction is contained on both platforms and gated by CI:**
  the parser and OCR children now run inside a whole-tree container, so a Tika
  JVM or Poppler process can no longer outlive the parser that spawned it — on
  Windows through a job object that also supplies the memory, process-count,
  and CPU limits the platform previously had none of, and on POSIX through the
  existing session plus rlimits. `RLIMIT_AS` is replaced by `RLIMIT_DATA` when
  a Tika jar is configured, because a JVM reserves more address space than any
  bound worth setting and would otherwise fail to start at all. The
  `search-node` distribution had no CI job of any kind; it now lints and tests
  on both Linux and Windows as a required merge gate — which immediately
  surfaced that `RLIMIT_NPROC` is counted per real UID rather than per
  descendant tree, so on any host whose service account owns other processes
  the parser died with EAGAIN on a fork it was entitled to make. Process
  count is now bounded where it can be scoped to the tree: the job object on
  Windows, and the operator's cgroup `pids.max` on Linux.
- **Search Node preflight says why it rejected a node:** an unhealthy engine
  stops the whole agent, so the failure now names each cause — cluster status,
  a missing active index, drifted disk watermarks, a write block, or a rebuild
  quarantine lease — instead of a bare "not healthy". OpenSearch's stock 85%
  low watermark against the packaged 80% is the common case, and it was
  previously indistinguishable from an outage. The required real-node contract
  job now exercises the health gate against live cluster settings.
- **The Search Node acceptance queries can actually be run:** the operator
  runbook directed operators to run the benchmark queries after an upgrade,
  after a snapshot restore, and during rebuild quarantine recovery, but only
  the fixtures existed and the installers shipped neither them nor a runner.
  Both installers now place a runner and its fixtures in the install tree; it
  loads into a disposable index generation, checks phrase, Boolean/field, ACL
  deny and allow behaviour, cleans up, and never touches the aliases the agent
  serves from.
- **Firm Memory architecture doc no longer overstates what ships:** it still
  claimed the repository ships no Tika, OCR, or OpenSearch three releases after
  those components landed. It now records what exists, that none of it is
  connected to a query path, and that allow-only `acl_tokens` cannot represent
  a Windows explicit DENY ACE.
- **Authority alerts and claims revalidate their evidence lineage:** queued
  citator alerts persist the exact history or citation fact they represent and
  re-resolve its current promoted public-source lineage before a delivery can be
  recorded as sent. Customer coverage claims are invalidated by changes to any
  served source/admission/manifest fact until fresh production audits pass, and
  citation edges require each linked opinion to belong to the same reviewed
  source as its authority. This adds no production harvest, deployment, or
  comprehensive-currentness claim.
- **Windows file-share agent enrollment works with managed TLS inspection:**
  the packaged agent now validates HTTPS through the operating system trust
  store instead of only its embedded CA bundle. Enterprise roots deployed by
  Windows administrators are trusted without weakening hostname or certificate
  verification.
- **Chat authority gaps now identify the actionable retrieval state:** legal
  research responses retain independently cited findings, omit unsupported
  claims, and explain whether the public-authority service was unavailable, the
  fallback was unavailable, or no usable match was found. Duplicate source
  notes and uncited jurisdiction-specific model summaries are no longer shown
  as if they were a completed research result.
- **Zoom Phone setup reports each independent connection stage:** tenant
  administrators now return from Phone OAuth to the Zoom integration panel,
  see actionable recovery for rejected credentials and other safe provider
  failures, and can distinguish saved app credentials, authorized API access,
  verified scopes, and real-time webhook proof. Replacing the OAuth pair
  explicitly reports that the old grant was invalidated and requires a new
  LawHand-initiated authorization. Provider errors are logged only as bounded
  error codes without raw response details.
- **Zoom Phone setup prevents state-less Marketplace authorization:** the
  tenant admin panel now lists the exact account call-history and call-detail
  granular scopes, their Zoom Phone > Call Logs location, and a copy action.
  It explicitly requires starting OAuth from LawHand's Connect Zoom Phone
  action instead of the private Marketplace listing's Add action or generated
  OAuth link, which cannot supply LawHand's tenant-bound `state`. The panel also
  requires one matching Development or Production client pair. The operator and
  in-product guides document the same fail-closed recovery path.
- **IONOS release validation now recognizes the launched Research gateway:**
  the stage gate requires an enabled Research MCP to return an unauthenticated
  `401` with a Bearer challenge, still requires disabled deployments to return
  `404`, preserves hostname isolation, and continues to probe the private
  Skynet research upstream without exposing its credential.

### Added
- **Phase 1 deep-research authority activation:** the reviewed Federal Rules,
  Constitution Annotated, and bounded Tax Court Reports families now run as
  independently switchable scheduler jobs. Document-level sync gates exclude
  known unreadable extraction while preserving audit previews, supplemented
  base volumes remain searchable, and explicit Ohio/federal questions apply
  matching jurisdiction filters across case law and other authorities. New
  chunks remain embedding-null for the separate embedding pipeline.
- **Review-first Brief Check:** firms can run bounded DOCX/PDF brief checks that
  normalize citations, compare quotations and pin cites against accessible source
  text, preserve missing/ambiguous/currentness unknowns, compare opposing briefs,
  record attorney decisions, and export linked review and table-of-authorities
  drafts. Candidate authority retrieval is explicitly bounded and never labels an
  authority good law from an absent negative record.
- **Operating trust workflows and evidence:** the versioned public contract now
  drives measurable non-SLA objectives, exact support hours and S1-S4
  escalation, sanitized append-only incidents, a named subprocessor registry,
  DPA/BAA applicability boundaries, and a content-addressed security-review
  packet. Tenant-scoped immutable receipts reconcile agreement-backed
  onboarding, BK28 migration manifests, and complete export category counts.
  Offboarding snapshots legal holds and inventory, requires two distinct
  operator approvals, and records provider and backup disposition without the
  evidence endpoint performing deletion. The penetration-test cadence remains
  explicitly planned-not-attained, and every certification flag remains false.
- **File-share agents can build a private lexical-search control index:** an
  explicitly enabled, agent-local SQLite FTS5 sidecar indexes bounded text from
  supported files without embeddings or a new document-text relay to LawHand.
  Durable retries, assigned-share path validation, fail-closed local ACLs, and
  operator-only query and relevance-evaluation tools support a measured pilot;
  the documented Tika, OCR, OpenSearch, and native-ACL scale architecture
  remains a separate 50–200 GB proof-of-concept gate rather than a 4 TB claim.
- **Bounded COMP-03 lead conversion loop:** firms can publish tenant-scoped,
  conditional intake forms with attributed, honeypot-protected, idempotent
  public submissions; book only published consultation slots; record explicit
  conflict triage, channel consent, appointment/reminder state, and funnel
  events; and send authored email follow-up only when consent and provider
  delivery succeed. Public leads remain blocked from conversion until a clear
  conflict decision is recorded. SMS stays fail-closed until the ECO-23–29
  provider and opt-out/reconciliation gates are complete.
- **Client portal switching bundle:** clients can activate durable, revocable
  portal accounts, pay sent invoices through hosted Stripe Checkout, and use
  authenticated, idempotent Dropbox Sign webhook reconciliation. Portal
  payments, signatures, conflicts, roles, imports, receipts, and closeout
  remain tenant-scoped and auditable.
- **Platform tenant inventory distinguishes lifecycle from billing:** operator
  tenant summaries now include an explicit `demo` or `platform` type and the
  existing tenant expiry. The console labels and filters that type, renders a
  demo expiration (including expired state), and keeps disposable workspaces
  behind their dedicated termination controls rather than generic tenant
  controls.
- **Background automation can use OpenCode Zen free capacity:** operators can
  assign Zen free models to the global Background route while Standard and
  Premium still require confidential-data approval; Background matter context
  remains disabled.
- **Marketing now shows the platform as an end-to-end legal workflow:** the
  public product tour follows one illustrative matter from caller intake and
  saved conflict review through matter setup, document and AI-assisted review,
  client action, signature, billing, and follow-through. Role switchers show
  attorney, paralegal, intake, billing, and client handoffs; the expanded
  capability catalog adds conflict review, matter communications, client
  portal, and signature routing with the existing rollout-state boundaries.
  Server-rendered search copy, structured data, and focused interaction tests
  stay aligned with the visible React experience.
- **Competitive claims now have an evidence and maturity gate:** the public
  capability catalog records implemented, controlled-pilot, planned, and
  partner-dependent states with claim ownership and review dates. Pricing,
  demo, SEO, and no-JavaScript copy consistently present Research MCP as a
  controlled pilot; the dated Clio and Thomson Reuters register preserves
  LawHand's unified matter operating-system positioning while prohibiting
  unsupported AI-superiority, Westlaw-replacement, coverage, good-law, SLA,
  certification, and service-level claims. Focused backend and frontend tests
  keep those boundaries from silently drifting.
- **Tenant agreement evidence and bounded retention controls:** platform
  operators publish immutable counsel-owned document identities; tenant admins
  accept the exact version/hash with authority, signer, request, and optional
  e-sign evidence. Onboarding enforcement is dark-launched behind an explicit
  flag. Admin and Platform dashboards expose metadata-only data inventories,
  policy versions, legal holds, and audit actions. The existing scheduler now
  enforces tenant-configurable expiry only for non-matter chat attachments,
  committing database deletion before path-confined local byte cleanup.
- **Research API keys now have a complete tenant-admin lifecycle:** firm admins
  can record purpose and staff custody, set an expiration, scope allowed tools,
  enforce monthly call and dollar budgets at the snapshotted $0.45 successful-
  call price, and edit controls without exposing the raw secret again. The
  tenant and operator portals show active, expired, and revoked keys with
  creator, last use, billable and failed calls, estimated charges, and remaining
  budget. Standard `Authorization: Bearer lhrk_...` clients are supported while
  `X-MCP-API-Key` remains backward compatible; failed calls remain observable
  but are neither quota-consuming nor billable.
- **Skynet development and disaster-recovery controls:** an isolated
  `dev1.getlawhand.com` Compose project and pinned-SHA runner workflow keep test
  data, writers, email, signup, and MCP surfaces separate from IONOS production.
  Daily network-isolated restore rehearsals create durable alerts, while a new
  operator-only infrastructure page shows primary, dev, DR fencing, release,
  and research gateway health without exposing credentials or raw errors.
- **Document automation now scales with an operator-visible safety boundary:**
  tenant-scoped template search, status/category filters, pagination, readiness
  summaries, and an independently loaded generation view replace the unbounded
  library. Local OCR uses separate bounded model sessions, durable work drains
  unrelated tenants concurrently while preserving per-tenant order, and PDF
  saves stage outside row locks before exact-contract revalidation, idempotent
  consumption, compensating cleanup, or durable reconciliation quarantine.
- **Workspace MCP now covers review-first matter work end to end:** approved
  desktop clients can search and inspect clients, intakes, matters, and tasks;
  load client, party, team, document, event, note, and communication context;
  read bounded uploaded-document and raw-template text; and render approved DOCX
  or Markdown templates into the existing immutable artifact and tenant-cloud
  workflow. Fresh and template-rendered documents return authenticated open and
  download routes plus a LawHand task deep link and always enter staged staff →
  attorney Review. OAuth discovery now requests the complete current scope set,
  including `intakes:read`; existing narrow grants remain unchanged and require
  explicit reconnection rather than silent scope expansion.
- **Matter caption parties now drive explicit document fields:** the Parties
  workspace defines plaintiff and defendant separately from the client
  relationship and represented side, supports a primary contact per role, and
  exposes the canonical singular and plural caption fields to document Smart
  Fill with source provenance and review-required legacy fallback behavior.
- **Admin integrations are consolidated into a single role-aware workspace:**
  Microsoft and Google cloud accounts, Cloud Search, SMB file shares, Teams,
  Zoom, QuickBooks, and MCP now share one catalog with focused subsections,
  explicit data-permission and setup disclosures, and direct operating-guide
  links. Legacy admin-tab URLs canonicalize to the matching nested section, and
  Windows analytics imports use explicit component extensions so Vite resolves
  the intended file despite the adjacent case-colliding analytics helper.
- **QuickBooks connection and invoice tax handling are production-ready:**
  successful Intuit authorization returns to the LawHand QuickBooks admin tab,
  A/R settings serialize database UUIDs correctly, QBO routes use an isolated
  edge-rate bucket, partial catalogue failures remain usable, and invoice
  sales-tax choices explicitly set each synced QBO line taxable or non-taxable.
- **E-sign delivery completion:** internal signature requests now email the
  actionable signer, expose delivery and first-view status, support manual
  resend, notify the next signer in sequential workflows, and execute configured
  expiration-relative reminders from the tenant-scoped scheduler.
- **Standalone conflict review now preserves clearance evidence:** the Conflict
  Search workspace accepts people/aliases, organizations, and email addresses,
  saves the visibility-aware result snapshot, warns without disclosing a
  restricted matter, requires review notes and explicit attorney
  acknowledgement, locks closed decisions, and exports the saved record as PDF.
- **Client portal invoice PDFs are branded and audit-traceable:** staff and
  portal invoice exports use tenant firm identity, while the portal authorizes
  only client-visible invoices on its matter, streams the PDF with private
  no-store caching, and retains a metadata/hash audit rather than PDF bytes.
- **Document templates now have a reviewed Prepare Form workflow:** uploaded
  PDFs and supported images preserve their page design while staff review
  detected fields, add or adjust fields manually, and create a reusable
  template. Bounded local OCR, optional configured AI assistance, validation,
  and clear recovery paths make scans and unfamiliar forms safer to prepare.
- **Reviewed matter email can create a traceable task from an explicit subject
  tag:** a new message beginning with `[TASK]` or `[DEADLINE]` previews its task
  title and a bounded, deterministic due date in the Correspondence queue. The
  reviewer can file the `.eml`, correspondence record, task, and task history
  atomically, after which existing calendar projection is requested without
  making provider availability a condition of durable capture.
- **Cloud-bound matter storage is explicit and fail-closed:** Auto now resolves
  an active Microsoft 365 tenant to OneDrive (or Google Drive when Google is the
  connected provider), while an administrator-selected provider remains
  exclusive. Provider outages return HTTP 503 without a durable local spill.
  Client portal originals route to the newly provisioned `client_uploads`
  folder, and the user/admin/backend guides share a lifecycle diagram and
  operational contract for task routing and customer-owned document content.
- **E-sign delivery completion:** internal signature requests now email the
  actionable signer, expose delivery and first-view status, support manual
  resend, notify the next signer in sequential workflows, and execute configured
  expiration-relative reminders from the tenant-scoped scheduler.
- **File share agents now have operator-grade local diagnostics:** Windows
  services write bounded rotating logs under the protected ProgramData agent
  directory, Linux continues to use journald, and the File Shares console
  exposes copy-ready commands for reading the local log without uploading
  tenant file data or credentials.
- **The File Shares agent list refreshes while administrators are pairing and
  troubleshooting:** registered agents and heartbeats refresh automatically,
  with an explicit refresh control and a separate history view for revoked
  registered devices.
- **Disposable demos can be resumed without passwords** (migration
  `131_demo_resume_profile`): a visitor can choose **Resume demo** and enter the
  same normalized email plus the current demo access code to reopen an active,
  unexpired workspace. Resume preserves the original expiry and AI quota,
  issues fresh hardened cookies, and uses a SHA-256 selector under a dedicated
  SELECT-only RLS policy instead of granting the public endpoint a tenant
  bypass.
- **Platform can select a matter-aware Standard profile for new demos:** an
  active profile whose Standard route passed confidential-data validation and
  allows matter context can be marked **Use for demos**. New disposable tenants
  receive that exact profile automatically; Premium remains unavailable and
  private-detail protection remains enforced.

### Fixed
- **Research MCP hosted-client consent uses the authenticated portal origin:**
  OAuth discovery, dynamic registration, authorization start, token exchange,
  revocation, and JWKS remain isolated to `research.getlawhand.com`, while the
  signed-in consent and grant-management APIs are now explicitly limited to
  `getlawhand.com`. Claude, ChatGPT, and similar public PKCE clients can finish
  authorization without a manually issued Research API key.
- **Windows agent releases now fail closed on platform trust and stop cleanly
  for overtop upgrades:** tagged builds use Microsoft Public Trust Artifact
  Signing for the EXE before it is embedded and for the final MSI, verify both
  Authenticode signatures before publication, and refuse to release when the
  OIDC signing configuration is absent. The Windows service now latches early
  stop requests, cancels its async workers, closes local resources, and uses a
  bounded last-resort exit so a blocked SMB call cannot outlive the MSI service
  stop window or start an overlapping agent process.
- **Production accepts the previously deployed shared OpenCode credential while
  canonical provider names are migrated:** `OPENCODE_ZEN_API_KEY` remains the
  preferred Zen credential, but the verified legacy `DEEPSEEK_API_KEY` is a
  final compatibility fallback for both preflight and the LiteLLM container.
- **Production deploy checks now distinguish disposable file-share pairing
  reservations from registered agents:** pre/post data protection still fails
  on any registered agent loss, while the intentional cleanup of expired,
  never-registered `pending` reservations no longer produces a false data-loss
  alarm.
- **File-share operations now report the real tenant state:** both production
  Compose paths hard-pin SMB retrieval on so a stale host `.env` value cannot
  silently disable it, while the Status tab always authenticates
  and returns tenant-scoped agent, share, credential, heartbeat, scan, and
  index counts even when retrieval is disabled. The Activity tab now combines
  agent lifecycle and heartbeat, update, share scan/connection-test,
  credential verification/delivery, and audited full-content access events so
  administrators can diagnose a quiet or failing installation from one place.
- **File-share connection tests and indexing now agree:** the API accepts the
  empty timestamp sentinel emitted by installed v0.15.0 agents while v0.15.1
  sends proper null values and reports bounded validation details. Admins can
  edit a share's UNC path or assigned agent, and a move retires stale indexed
  metadata before the new location is scanned into matter context.
- **Abandoned file-share pairing attempts no longer look like installed
  agents** (migration `132_smb_agent_lifecycle_indexes`): expired
  never-registered reservations are removed by a tenant/RLS-
  scoped retention job, revoking an unused reservation deletes it immediately,
  and operational counts exclude pending and revoked placeholders while real
  revoked devices retain their audit history. Indexed API-key and
  tenant/status/expiry lookups keep heartbeat authentication and cleanup fast.
- **Windows agent installation no longer couples MSI success to network
  registration:** v0.15.1 installs and upgrades the service first, verifies the
  published checksum, and performs pairing as an explicit second step. A bad or
  expired code now produces a concise registration error instead of MSI 1603,
  and the one-time code is no longer copied into Windows Installer events.
- **Matter-context approval is bound to the route that Platform validated:** a
  Standard or Premium profile toggle no longer authorizes an unrelated tenant
  alias, explicit model, or customer BYOK destination. Chat now fails closed on
  those independently unapproved routes before loading a matter or attachment.
- **Standard privacy messaging reflects the effective routing policy:** when an
  approved Standard profile allows matter context, Chat explains that private
  details are protected instead of incorrectly claiming Standard always
  excludes matters. Public/general Standard routes remain visibly locked down.

### Fixed
- **The dedicated MCP hostnames are no longer advertised to search engines:**
  `$x_robots_tag` is keyed on the request path, so `/` — the marketing home
  page on the product hostname — was published as indexable on every hostname
  nginx answers, including `mcp.getlawhand.com` and `research.getlawhand.com`,
  whose root is an alias for a protocol endpoint that replies with a JSON
  error. A host-keyed `$robots_tag` map now forces `noindex, nofollow,
  noarchive` on those hostnames and falls back to the path-keyed value
  everywhere else. The unauthenticated research response also carries a
  `documentation` pointer to `/product/mcp`, so a person who reaches the
  endpoint in a browser has somewhere to go without the hostname serving HTML.

### Added
- **QuickBooks connection and invoice tax handling are production-ready:**
  successful Intuit authorization returns to the LawHand QuickBooks admin tab,
  A/R settings serialize database UUIDs correctly, QBO routes use an isolated
  edge-rate bucket, partial catalogue failures remain usable, and invoice
  sales-tax choices explicitly set each synced QBO line taxable or non-taxable.
- **Matter correspondence and email are documented for the people who use
  them:** the in-product user guide now covers the matter Correspondence tab —
  mailbox capture rules and **Scan now**, per-matter forwarding addresses and
  their rotation and disabling, and the **Emails awaiting review** queue where
  a person selects **File to matter** or **Reject** before anything becomes
  correspondence. The platform marketing page lists the same capability.
- **Workspace MCP is documented and marketed as its own connection type:** the
  user guide explains the connected-assistant list in Profile and how to revoke
  a connection; the administrative guide covers the per-user **Connected
  assistants** control, the default applied to new accounts, and how the
  consent-based surface differs from keyed product access; and the public MCP
  page contrasts the two connection types instead of describing only scoped
  product keys.

### Fixed
- **Workspace MCP now follows tenant-administered user access:** retired the
  legacy deployment-time pilot tenant allowlist that could reject an otherwise
  eligible user before the Admin → Users permission, Privacy Mode, license,
  OAuth consent, RBAC, and tenant-isolation checks were evaluated. Production
  diagnostics now report the native tenant/user access model directly.

### Added
- **QuickBooks connection and invoice tax handling are production-ready:**
  successful Intuit authorization returns to the LawHand QuickBooks admin tab,
  A/R settings serialize database UUIDs correctly, QBO routes use an isolated
  edge-rate bucket, partial catalogue failures remain usable, and invoice
  sales-tax choices explicitly set each synced QBO line taxable or non-taxable.
- **Release-gated LawHand Research MCP connection contract:** the public
  research catalog is now strictly authority retrieval/status/export and new
  header credentials use the `lhrk_` prefix while existing hashed `clmcp_`
  credentials remain revocable compatibility keys. Hosted ChatGPT and Claude
  use a separate `research:read` OAuth 2.1 grant; API clients can continue to
  use `X-MCP-API-Key`. Both paths share the same tenant entitlement, PAYG,
  quota, and Stripe-meter boundary, and neither can discover or invoke
  Workspace MCP tools. LiteLLM remains internal and is intentionally absent
  from retrieval-only calls; any future model-backed research operation must
  pass opaque correlation metadata and reconcile model spend.
- **Tenant AI routing profiles with per-tier matter-context policy** (migration
  `125_llm_routing_profiles`): Platform operators can create and clone reusable
  Standard/Premium route profiles, independently allow confidential matter
  context for either tier, activate versioned LiteLLM aliases, select the
  inherited default, and assign an active profile to a tenant. Chat enforces
  the selected policy before loading linked matters or attachments in both
  synchronous and streaming paths, while every route target and fallback still
  passes the confidential-data eligibility gate. Platform profile and tenant
  banners show the effective Standard/Premium policy.
- **The client portal now tells the client what is waiting on them:** the
  overview opens on counters for unread messages, documents awaiting signature,
  shared documents and balance due, each linking to its tab, plus the next key
  date called out with plain-language timing. Firm key dates are parsed from
  the formats staff actually type and presented in chronological order, with
  undated notes kept rather than dropped. The tabs themselves carry badges, so
  the client can see what needs attention without opening each one.
- **Clients can sign out of the portal** (`POST /api/portal/client/logout`),
  which blacklists the session's JTI and clears the cookie — a shared or
  borrowed device no longer keeps a live matter session for the rest of the
  seven-day token life. `GET /api/portal/client/session` reports which identity
  the portal is signed in as, shown in the portal header.
- **Portal activity is visible to the firm** (migration
  `123_client_portal_activity`): `client_portal_invites.last_seen_at` records
  each portal session's most recent use, and the matter's Client Portal tab
  shows every invitation's real state — awaiting first sign-in, active, expired
  or revoked — alongside when it was last used, so an invite that never reached
  its recipient is obvious. Issuing an invite can now revoke the matter's other
  live invitations in the same step, and the one-time link has a copy button.
- **The firm is notified when a client writes in:** a portal message emails the
  matter's assigned users with a bounded preview and a link to the matter,
  instead of waiting to be noticed in the communications log.
- **Messages track read state** (`messages_seen_at` on the invite): firm
  messages the client has not seen are marked unread and counted, the portal
  marks them read when the thread is opened, and the thread refreshes itself
  while the client is reading it.
- **Secure per-matter inbound email** (migration `124_inbound_email`): each
  matter can create one opaque, rotatable address at
  `intake.getlawhand.com`. A Cloudflare Email Worker signs the exact RFC 822
  bytes before the backend performs a select-only alias lookup; tenant RLS is
  established immediately afterward. Messages enter a tenant-scoped
  quarantine and require an explicit **File to matter** or **Reject** decision
  before they become correspondence. The Correspondence panel also exposes the
  party addresses used by automatic capture rules.
- **File share agent is now built and shipped:** `agent/packaging/` adds a
  PyInstaller spec plus Windows (`build.ps1` → `lawhand-agent.exe` and a WiX v5
  MSI) and Linux (`build.sh` → binary + systemd install tarball) builds, and
  `.github/workflows/agent-release.yml` builds both on every change to `agent/`
  and attaches them to an `agent-v*` tag release. The MSI registers an
  auto-starting service and can pair during install
  (`msiexec /i … PAIRING_CODE=… SAAS_URL=…`); `lawhand-agent service
  install|start|stop|restart|status|remove` manages the Windows service or the
  systemd unit from one command.
- **Per-tenant credential vault for file shares** (migration
  `121_smb_share_credentials`): `smb_credentials` stores NTLM/Kerberos/guest
  identities encrypted with the `TOKEN_ENCRYPTION_KEYS` keyring, RLS-scoped to
  the tenant, and `smb_shares.credential_id` binds one to each share — so a
  single agent can serve shares needing different identities. Admin APIs never
  return a secret (`has_password` only) and a credential may be pinned to one
  agent; the plaintext is delivered exclusively to that agent over its
  API-key-authenticated share endpoint and held in memory.
- **Authentication panel in Administration → File Shares:** adding a share now
  exposes the credential (reuse a stored one, create one inline, or use the
  agent's own identity), file types, exclusion globs, folder depth and scan
  schedule, plus per-share **Test connection** and **Scan now** actions that
  round-trip through the agent and report the identity used and the real error
  text. A Credentials tab manages the vault, and the Agents tab shows the
  Windows/Linux install commands for the generated pairing code.
- **Agent scan reporting:** the agent posts scan outcomes to
  `/api/v1/smb/agents/{id}/shares/{share_id}/scan-status`, so the console shows
  last scan time, file count and failure reason instead of an empty cell.

### Fixed
- **Chat retrieval and citation provenance now match what the user sees:** the
  Standard route injects only its sanitized public-authority RAG context into a
  dedicated public prompt while continuing to exclude matter, attachment,
  memory, profile, and history data. The citation renderer combines structured
  annotation offsets with remaining raw source markers instead of dropping the
  latter, and the review-tag legend is sticky and responsive rather than hidden
  on mobile or scrolled out of view. Premium's validated-response boundary is
  preserved while live source previews remain visible, and usage telemetry now
  records provider TTFT, provider duration, validation/release delay, retrieved
  versus cited sources, hyperlink coverage, and source-utilization percentage.
- **Creating a matter forwarding address no longer fails on matters without a
  partner attorney:** `Matter.partner_attorney` is eagerly loaded with a left
  join, and the alias endpoint's blanket `FOR UPDATE` made PostgreSQL try to
  lock the nullable side of that join. The endpoint now emits `FOR UPDATE OF
  matters`, preserving per-matter serialization without locking the joined
  user table; a PostgreSQL-dialect query regression test covers the exact shape.
- **Mobile task rows no longer collapse the task name underneath its due date
  and controls:** below the small-screen breakpoint, the completion control and
  task content occupy a two-column grid while due dates, state badges, and row
  actions wrap on a separate line. Desktop rows retain their compact horizontal
  layout, and the responsive class contract is covered by the Tasks page test.
- **Any commit inside a client-portal request silently emptied the portal:** the
  tenant GUC that RLS filters on is transaction-local, and `get_db` only
  registers the per-transaction rebind for a tenant the middleware resolved from
  the firm `access_token` cookie — which it never reads for the portal's own
  cookie. A commit mid-request therefore dropped the tenant, and every later
  query fell through to RLS's fail-closed path. `bind_tenant_context` now pins
  the portal session to its tenant across commits, and a runtime-role test
  covers it (the suite's superuser bypasses RLS, so nothing else could catch it).
- **Portal uploads accepted any file type and any size of message:** client
  uploads are now checked against an allowlist of document, image, mail and
  media extensions, rejected when empty, and stripped to a basename that also
  handles Windows paths; message bodies are capped at 10,000 characters and
  subjects at 200, and blank bodies are refused rather than stored.
- **Portal invoices hid what was actually owed:** each invoice now carries the
  amount paid, the outstanding balance, and whether (and by how long) it is
  overdue, with matter-level totals — previously the client saw only the invoice
  total, with no way to tell a part-paid invoice from an unpaid one. An invoice
  marked paid settles its balance even when the firm recorded no payment row.
- **Matter names were interpolated unescaped into portal invitation emails,**
  so a name containing HTML broke (or could inject into) the message body. Both
  the client and mediation invite templates now escape the name and the link.
- **File share pairing was impossible:** `smb_agents.pairing_code` is
  `varchar(20)` while `secrets.token_urlsafe(16)` produces 22 characters, so
  every pairing-code request failed at insert. Codes are now four groups of
  four characters from an alphabet without look-alikes (19 characters, ~78
  bits) — short enough for the column and for an installer command line.
- **SMB API responses failed to serialize:** `ShareInfo`, `AgentInfo` and the
  other SMB response models declare `id` fields as `str` but are validated from
  ORM rows carrying `uuid.UUID`, which pydantic rejected — creating or listing
  a share returned a 500. They now share a base model that coerces UUIDs.
- **Agent could not use the shares the API returned:** it expected `server`/
  `share` keys the API never sent, registered with an `agent_name` field name it
  did not use, and sent heartbeat keys the schema drops (leaving agent version
  and hostname empty in the console). Share payloads are now normalized from the
  UNC path, and registration/heartbeat match the API contract.
- **MCP platform tool arguments validated before use:** `list_matters`,
  `list_matter_documents`, and `create_document` read their arguments from a raw
  dict and passed them straight to the database and to `int()`. A client that
  sent a matter name where a UUID belongs — what an external model does when it
  skips `list_matters` — raised `DBAPIError`, and a non-integer `limit` raised
  `ValueError`; both surfaced as HTTP 500. Because `_call_platform_tool_metered`
  caught only `HTTPException`, neither reached `record_mcp_usage`, so the failed
  call left no trace in usage or audit records. Arguments now validate against
  Pydantic models before any query runs, the declared `inputSchema` carries the
  same UUID formats and bounds so the protocol path's jsonschema agrees with it,
  and every failure is metered with its real exception class. Failure metering
  runs on its own session and commits there: the database errors it exists to
  record leave the request transaction unusable, and that transaction is rolled
  back as the error propagates, so a usage row written on it would either raise
  or vanish. `content` is bounded by encoded bytes rather than characters,
  because bytes are what the 256 KiB transport cap measures. Note two contract
  changes for clients: a `limit` outside its range is rejected rather than
  clamped, and an undeclared argument is rejected rather than ignored — the
  advertised schemas now set `"additionalProperties": false` to say so.
- **CSV export quotes tab and carriage-return formula leads:** `_csv_safe`
  guarded `=`, `+`, `-`, and `@` but not `\t` or `\r`, which Excel and
  LibreOffice also treat as formula leads once they strip them during cell
  parsing.
- **Demo requests can no longer lose a lead silently:** an embedded newline in
  `name`, `firm_name`, `phone`, or `team_size` survived into the notification's
  subject header. Python refuses to serialize a header containing an embedded
  one, `send_email`'s broad handler turned that into a delivery failure, and the
  request row was stored while nobody was notified. The single-line fields now
  reject line breaks at the schema edge.
- **Client and matter search escape LIKE wildcards:** a `%` or `_` in a search
  term acted as a wildcard, so a client number containing `_` matched far more
  than the user asked for.
- **Stripe webhook stops returning parser internals:** the unauthenticated
  endpoint answered a malformed payload with the exception's text. It now
  returns a fixed string and logs the reason server-side. Same-second event
  pairs, which Stripe's one-second `created` resolution leaves unordered, are
  now noted in the log rather than resolved invisibly.
- **Legacy `.doc` refused identically by both text extractors:**
  `extract_text_from_path` routed `.doc` into python-docx, which fails on the
  container and surfaced as an opaque indexing error instead of the actionable
  "convert to DOCX, PDF, or TXT" message `extract_text` already returned.
- **`GET /api/workspace-mcp/oauth/authorize` rate-limited in the application:**
  `AUTH_LIMITS` is enforced for POST only, so the endpoint — which persists a
  Redis key per unauthenticated call — had edge limiting but nothing covering
  traffic that reaches the app without passing through the proxy.

- **Microsoft Teams Phone (voice) call capture:** inbound Teams Phone calls are
  imported into `communication_logs` so the intake dashboard treats them exactly
  like Zoom Phone calls. Graph exposes call records only through the
  `CallRecords.Read.All` *application* permission — there is no delegated
  equivalent — so `app/services/teams_voice.py` runs an app-only
  client-credentials grant against the customer's Entra directory instead of
  reusing the delegated Teams token. `teams_voice_settings` (migration
  `121_teams_voice_capture`) holds the directory GUID, the per-tenant
  notification `clientState`, and the Graph subscription state, under RLS and
  excluded from demo cloning.
- **Two converging capture feeds:** `communications/callRecords` change
  notifications carry latency, and an hourly sweep of the Teams PSTN usage
  report is the backstop for anything the notification path drops (Microsoft
  publishes that report with a lag, which is why it is not the primary feed).
  Both write through a `teams_voice:call:<id>` partial unique index, so a call
  seen twice is stored once. After intake staff have worked a call,
  reconciliation refreshes only provider-owned metadata and never overwrites the
  curated caller identity or narrative.
- **Notification routing UI:** `PUT /api/integrations/teams/notification-settings`
  existed with no interface able to reach it. The Teams admin panel is now
  tabbed (Channels / Notifications / Voice) and the routing editor is driven by
  a new `GET /event-types`, served from the dispatcher's own catalogue so the UI
  and the code that fires notifications cannot drift apart.

### Fixed
- **Teams Graph reads were silently truncated and silently failing:**
  `list_joined_teams` / `list_channels` read a single Graph page and discarded
  `@odata.nextLink`, so a firm with more teams than fit one page lost the rest;
  both also returned `[]` on any error, so a 403 from expired consent rendered
  in the admin panel as "you have no teams". Collection reads now page to
  completion under a cycle-safe cap and raise `TeamsIntegrationError` with text
  naming the remedy.
- **`DELETE /api/integrations/teams/links/{id}` 500ed on a malformed id:** the
  raw path string reached the UUID column and Postgres raised. It is now
  validated (422) and reports 404 for a link that does not exist, instead of
  204 for a delete that removed nothing.
- **Unroutable notification events could be saved:** a typo'd `event_type` was
  stored happily and then never fired, which is indistinguishable to an admin
  from a broken integration. Routes are validated against the catalogue, and
  duplicate `(event, channel, matter)` rows in one payload are collapsed rather
  than reaching the unique index and failing the whole save.
- **Teams link and routing responses carry the matter name,** so the admin UI
  no longer renders a truncated UUID as a matter's identity.
- **`teams_notify.notify` resolved a credential per channel,** reopening a
  session and re-decrypting the token for every target of one event; it now
  resolves once for the whole fan-out.
- **Stripe webhook idempotency and ordering:** added `stripe_webhook_events`
  (migration `119_stripe_webhook_events`) and a shared claim guard. Stripe
  retries until it sees a 2xx and does not guarantee delivery order, so a
  retried `customer.subscription.deleted` arriving after a resubscription
  previously wrote `billing_tier="payg"` and nulled `stripe_subscription_id` —
  downgrading a paying firm into a state `_handle_payment_succeeded` could not
  repair, because its recovery branch is gated on the id that had just been
  cleared. Events are now claimed before dispatch and refused when duplicated or
  older than the last event applied to the same Stripe object.
- **Stripe webhook retries no longer suppressed:** `/api/billing/webhooks/stripe`
  caught every handler exception and returned 200, telling Stripe the event
  succeeded and cancelling all retries; a transient database error during
  `checkout.session.completed` meant the customer paid and the application never
  recorded it. Handler failures now surface as 5xx, which is safe now that the
  claim row rolls back with the failed transaction.
- **Single Stripe dispatch table:** both webhook routes now share
  `_SUBSCRIPTION_HANDLERS`, so whichever endpoint is configured in the Stripe
  dashboard interprets subscription lifecycle events identically.
- **Stripe reconciliation is no longer silent:** an unresolvable
  `stripe_customer_id` and a plan missing `metadata.tier` are logged at error
  level instead of being discarded or defaulted to the flat tier.
- **O(tenants) Stripe fallback removed:** resolving an invoice's tenant iterated
  every tenant with two queries each. Every Stripe object the application
  creates already carries `tenant_id` in metadata, so the fallback now emits an
  actionable error instead.
- **Postgres and Redis sized to their limits:** postgres ran on a 128M
  `shared_buffers` default inside an 8G container; redis had no `maxmemory` and
  would be OOM-killed rather than reporting pressure. Redis uses `noeviction`
  deliberately — every key here carries a TTL, so any LRU policy would evict
  refresh-replay tombstones and the revoked-`jti` denylist and re-enable replay.
- **Uploads rejected before buffering:** `documents`, `matter_documents`, and
  `client_portal` now check declared `Content-Length` before `await file.read()`
  materializes a body they are about to refuse.
- **Untrusted document text is structurally delimited:** MCP
  `get_matter_document_text` wraps extracted text in
  `<untrusted_document_text>` tags and neutralizes counterfeit closing tags, so
  authored content cannot end the wrapper and appear to speak as the product.
  The warning field previously sat as a JSON sibling of the text it described.
- **Office non-NAA sign-in message:** the add-in told users to "sign in to
  LawHand first", which `COOKIE_SAMESITE=lax` makes unreachable from the add-in
  iframe — the cookie is never sent, so the instruction could not succeed. It
  now names the unsupported Office versions instead.

### Added
- **QuickBooks connection and invoice tax handling are production-ready:**
  successful Intuit authorization returns to the LawHand QuickBooks admin tab,
  A/R settings serialize database UUIDs correctly, QBO routes use an isolated
  edge-rate bucket, partial catalogue failures remain usable, and invoice
  sales-tax choices explicitly set each synced QBO line taxable or non-taxable.
- **Billing status visible to the firm:** `/auth/me` and `/api/billing/status`
  now return `subscription_status` and `billing_status`. The browser previously
  received only `billing_tier` and could not learn a tenant was `past_due` even
  in principle. An `AppShell` banner surfaces the state to every user and routes
  finance roles to the Stripe portal; the billing page flags a tier that
  disagrees with the subscription on file and suppresses the upsell there.
- **Request timeout with a distinct message:** the axios instance had no
  `timeout`, so a slow query showed an open-ended spinner until nginx returned
  504 at 30s. Requests now abort at 25s and report a timeout rather than a
  generic failure. A `TableSkeleton` replaces the spinner on the list surfaces
  that go slow first.
- **Privacy Mode consequences stated:** the toggle now says it blocks connected
  assistants, and the grants panel it sits directly above shows a blocked state
  for Privacy Mode, inactive licence, or inactive account — conditions that
  previously surfaced only as a 403 inside a third-party client. The MCP consent
  screen also states what approving means for firm data leaving the boundary.

### Removed
- **Legacy `word-addin/` prototype:** hardcoded `http://localhost:8000`, stored a
  bearer token in `localStorage`, and received it through a URL query string —
  the pattern the main application deliberately migrated away from. `office-addin/`
  is the supported implementation. SBOM inventory regenerated and CI dependency
  patterns updated in the same change, per `docs/office-document-assistant-plan.md`.
  See `docs/archive/word-addin-removed.md`.

### Added
- **QuickBooks connection and invoice tax handling are production-ready:**
  successful Intuit authorization returns to the LawHand QuickBooks admin tab,
  A/R settings serialize database UUIDs correctly, QBO routes use an isolated
  edge-rate bucket, partial catalogue failures remain usable, and invoice
  sales-tax choices explicitly set each synced QBO line taxable or non-taxable.
- **Dedicated Clients & CRM workspace:** replaced the hidden generic contact
  entry with first-class client list and profile workspaces covering lifecycle,
  identity, addresses, two phone numbers, DOB, emergency contacts,
  communication preferences and timestamped SMS consent, linked matters,
  activity, tasks, billing defaults, internal notes, and finance-controlled
  QuickBooks/Stripe customer mappings. A tenant-scoped `/api/clients` surface
  adds CRUD, summaries, matter linkage, bounded admin-only CSV import/export
  with spreadsheet-formula hardening, and admin QuickBooks customer sync that
  excludes DOB, emergency details, and internal notes. Migration
  `111_client_crm_management` adds validated fields, indexes, tenant-unique
  client numbers, and reasserts forced row-level security on contact PII.
- **In-app version and release updates:** `/api/version` now returns structured,
  customer-facing release metadata alongside the deployed commit and build
  time. All users can review version details and release notes from Profile,
  admins get the same information in Admin Settings, and each user sees an
  accessible, device-local, one-time summary when signing in during a recent
  release window. A packaged JSON catalog is now the source for the UI and the
  generated plain-language `RELEASE_NOTES.md`; CI validates their schema and
  synchronization on every commit, while PR policy keeps customer notes and
  this implementation-level changelog paired.
- **Profile- and matter-aware AI context:** general chats and plugin skills now
  receive a verified, user-managed professional profile, while matter-linked
  work adds a bounded matter snapshot containing the summary, posture, key
  dates, risk, financial, activity, and AI-memory fields. Privacy-scoped caches,
  mutation invalidation, the tenant matter-context toggle, editable profile and
  matter context surfaces, and a dismissible three-field setup prompt make the
  feature safe and approachable for nontechnical users.
- **Conversational matter-document revisions:** added a protected, mobile-first
  DOCX revision workspace launched from matter documents. Standard or licensed
  Premium model routes may propose at most eight exact text replacements; a
  deterministic engine capability-checks and applies them to a new private
  derivative while preserving the source, lineage, hashes, and review evidence.
  Approval is bound to the re-read output SHA-256, newer candidates supersede
  stale reviews, and assistant derivatives cannot enter the legacy portal or
  signature-request paths because content approval is not destination approval.
  The current internal signature flow exposes only an
  explicitly non-executable replacement preview; it does not void, create,
  notify, or send anything.
- **Law-firm Work Board:** expanded `/tasks` with a responsive Board/List
  workspace using To Do, In Progress, Waiting, Review, and Done stages; My/Firm
  scopes; risk counters; filters; per-column pagination; accessible drag/drop
  and Move controls; waiting/reviewer/closure workflows; privacy-minimized cards;
  an on-demand detail drawer; and an append-only internal timeline. A canonical
  transition service, tenant-scoped reviewer checks, optimistic concurrency,
  conflict refresh, row-level security, task indexes, and migration
  `100_task_work_board` keep intake, mediation, email-agent, and direct task
  changes consistent without removing the existing deadline list. Tenant admins
  can disable the board independently, and content-free structured telemetry
  supports controlled rollout and bottleneck monitoring.
- **Document automation and e-sign enhancement research:** added
  `docs/research/2026-07-08-document-automation-esign-enhancements.md`,
  comparing Gavel, Clio Draft, Docassemble, Dropbox Sign, DocuSign, PandaDoc,
  and Documenso against the current Clarity template/e-sign implementation.
  The recommended path is an office-user Template Studio, deterministic
  smart-fill with provenance, engagement-letter/fee-agreement generation, and
  Dropbox Sign provider wiring around the existing native e-sign interface.
- **Document Automation workspace foundation:** reframed the Templates page as
  a tabbed Document Automation workspace while keeping `/templates` stable,
  added matter-aware Generate/Smart Fill flow hooks, template lifecycle metadata
  fields, and `POST /api/templates/{id}/smart-fill-preview` for deterministic
  suggestions from matter, linked client contact, attorney, and current-user
  context. Existing template CRUD/render callers remain backward-compatible.
- **Office-ready native e-sign metadata:** signature requests now support
  multiple signers, signer roles, sequential signing, expiration, reminder
  metadata, decline/void reasons, and portal decline handling. Existing
  internal portal typed signatures still create the executed certificate matter
  document on completion.

### Fixed
- **Production AI provider rotation and availability:** production preflight now
  rejects missing or placeholder primary-provider credentials, Skynet releases
  recreate and health-gate the LiteLLM gateway so rotations take effect, and
  Premium requests can fall back to the Standard route when Premium capacity is
  unavailable. The fallback preserves request availability while allowing a
  lower quality tier, and is documented for operator/customer disclosure.
- **First-customer Call Intake launch path:** the `intake-only` plan now exposes
  Call Intake and Tasks as first-class workspaces, uses a focused navigation and
  administration surface, avoids blocked Chat/Documents shell requests, and
  gives tenant admins a direct Zoom Phone setup prompt. Mobile navigation now
  presents Call Intake and Tasks without horizontal overflow.
- **Plan-aware self-service signup:** `/signup?plan=intake-only` now provisions
  the selected public plan through `/api/auth/signup/plan`, preserves firm
  profile fields, displays the selected product, enforces the backend's
  12-character password rule, and lands on the server-provided default route.
  Generic OAuth shortcuts are hidden on plan signup until plan-aware OAuth
  provisioning is supported.
- **Zoom Phone 2026 API compatibility:** added current call-element completion
  webhooks and `/phone/call_element/{id}` detail retrieval while retaining v2
  call-history compatibility. Intake now requests only the two scopes it uses,
  and non-admin status responses no longer expose OAuth app metadata.
- **Google/Microsoft integration scope visibility:** `/api/admin/permissions`
  now returns each provider's required OAuth scopes explicitly, and the
  Integrations panel shows required, granted, and missing scope counts with an
  alias-safe required-scope list plus any extra granted scopes.
- **Chat LiteLLM hypervisor redeploy:** redeployed the hypervisor stack from
  `fa580e5` with `docker compose -f docker-compose.hypervisor.yml up -d
  --build`, including the LiteLLM and LiteLLM Postgres services. Production
  health, LiteLLM/backend/frontend container health, OAuth redirect smoke
  checks, cloudflared, and pre/post data guard all passed.
- **Recent merge-stack integration:** reviewed the newly fetched SBOM,
  probate/template, RBAC/admin-users, Teams, Zoom, and add-on workflow branch
  stack and integrated only the low-risk pieces. The old Zoom recordings
  `call_intake` router remains unmerged because current `main` already has the
  newer Zoom Phone call-history/webhook intake flow; the large add-on UI patch
  remains a follow-up instead of being merged over newer matter/contact/plugin
  work.
- **Admin Users RBAC response contract:** `/api/admin/users` now returns manual
  RBAC role assignment IDs and names alongside license, PAYG budget, and default
  billing-rate fields, so the Users tab can render the actual assigned roles
  after save/reload instead of falling back to the legacy `user.role` string.
  Last-admin deactivation now checks `admin_settings` capability holders, and
  legacy `accountant` role support remains intact for finance-access flows.
- **Teams reauthorization and notification safety:** generic Microsoft
  reauthorization now preserves Teams scopes for tenants that already granted
  them, Google integration status coerces missing user-sync totals to `0`, and
  Teams notification dispatch no longer mutates the caller's field dictionary
  while building Adaptive Cards.
- **Intake production-session follow-ups:** refreshing the browser after login
  no longer treats the user as logged out just because the legacy localStorage
  bearer token is absent; app boot now verifies the httpOnly cookie session via
  `/auth/me` and lets the normal refresh-cookie path run before clearing auth
  state. Call Capture draft tabs now include an X close/delete affordance with
  a discard confirmation for non-empty drafts, so users can clean up extra
  local/server draft cards without submitting them or accidentally losing
  typed call notes. The intake dashboard also starts a throttled Zoom Phone
  sync on load for connected tenants, so recent Zoom calls are pulled in
  without requiring the manual Sync button every time. Intake-created staff
  tasks now include the
  caller name in the title (`Caller - Call back caller`), include the creator
  in the task description, and task notification emails/calendar events include
  creator/customer context plus a functional `/tasks/{id}` link. The frontend
  now serves `/tasks/:taskId`, loads the linked task if filters omit it, and
  scrolls/highlights it.
- **Admin → Users role-assign badge never reflected the actual assignment:**
  the per-user role badge in `RoleAssignCell` always displayed the legacy
  single `user.role` string, regardless of which custom roles were actually
  assigned via the checkbox dropdown. `PUT /api/admin/roles/assign/{user_id}`
  was working correctly the whole time (confirmed via nginx logs: three
  successful `200` responses) and every tenant has at least one user holding
  `manage_roles` (confirmed via direct DB query) — the backend was never
  broken. The badge now derives its label from the user's actual assigned
  role names (`role_ids` from the backend), falling back to the legacy role
  string only when no manual role assignment exists, so a successful
  assignment is now visibly reflected instead of looking like a no-op.
- **Raw nginx error page leaking into the call draft receipt trail:** when a
  proxy/gateway rejected a request before it reached the app (nginx 429, or a
  502/503/504), `normalizeApiError()` treated the raw HTML error page body as
  the error message text. `retryReceipt()` in `useCallDrafts` then stored that
  full HTML page as a receipt's `error` field with no length limit, and
  `ReceiptTrail` rendered it verbatim and unbounded — dumping the entire nginx
  error page into the small receipt card in the intake dashboard's Call
  Capture section, corrupting the layout. Root cause traced from a 2026-07-06
  429 burst against `/api/intake/drafts/*` (2,593 hits in nginx logs,
  concentrated in the same few seconds — a request storm, not ordinary
  concurrent usage) that predates and appears to have prompted the same-day
  "stop draft autosave flood" fix; the corrupted receipt persisted in
  localStorage afterward since nothing ever re-sanitized it. Fixed at three
  layers: `normalizeApiError()` now recognizes HTML bodies and falls back to a
  short status-based message (with a friendly 429/502/503/504 message) instead
  of the raw page; `useCallDrafts` sanitizes any receipt error (including
  already-persisted ones, on next load) that still looks like an HTML
  document; `ReceiptTrail` truncates and line-clamps error text as a last line
  of defense. This also fixes any already-corrupted receipt already sitting in
  a user's browser without requiring them to clear storage.

### Added
- **QuickBooks connection and invoice tax handling are production-ready:**
  successful Intuit authorization returns to the LawHand QuickBooks admin tab,
  A/R settings serialize database UUIDs correctly, QBO routes use an isolated
  edge-rate bucket, partial catalogue failures remain usable, and invoice
  sales-tax choices explicitly set each synced QBO line taxable or non-taxable.
- **SBOM and AI-BOM inventory tracking:** added a standard-library inventory
  generator, `make sbom-inventory`, generated JSON/Markdown inventory outputs,
  and an AI/SBOM/DLP risk roadmap. This is a tracking artifact for current
  manifests, Docker bases, compose images, and LiteLLM routes, not a replacement
  for formal CycloneDX/SPDX release SBOMs.
- **Probate, estate planning, and template automation plans:** imported the
  probate/estate workflow, cross-module template index, and competitive
  document automation planning docs, and tracked the work as backlog items
  `BK12`-`BK17` instead of duplicating Sprint 15 task IDs.
- **Favicon:** created `frontend/public/favicon.svg` (navy/gold "CL" mark
  matching the PWA icons) — `index.html` referenced it but the file never
  existed, so browser tabs showed a blank icon. Also added
  `apple-touch-icon` and `theme-color` meta tags to `index.html`.

### Changed
- **Brand palette harmonization:** InvoicesPage, InvoiceDetailPage,
  TimeTrackingPage, and ProfilePage used generic Tailwind-gray/blue/green/red
  inline hex colors that clashed with the app's warm cream/ink/sage design
  system. All ~155 hex values remapped onto the brand palette (lines →
  `#E1D9C9`/`#CFC4AE`, muted text → `#6A7587`, links/success → sage
  `#426146`/`#5A7A5C`, errors → brand rose, warnings → brand amber). QBO
  brand green left untouched.

### Fixed (mobile/tablet audit)
- **Mobile viewport height:** AppShell used `h-screen` (100vh), which on
  mobile browsers includes the URL bar — the bottom tab bar could sit
  partially off-screen. Now uses `100dvh` with `h-screen` fallback.
- **Safe-area support:** added `viewport-fit=cover` to the viewport meta and
  `env(safe-area-inset-bottom)` padding on the mobile bottom nav so it clears
  the iPhone home indicator in installed (standalone PWA) mode.
- **iOS input auto-zoom:** app inputs are 13–14px, which makes iOS Safari
  zoom the page on focus. Added an iOS-only CSS rule (`-webkit-touch-callout`
  supports guard, ≤767px) forcing 16px font-size on inputs/selects/textareas.
- **Pages overflowing the shell:** CalendarPage and CommunicationsPage used
  `h-screen` while rendered inside AppShell (which already reserves header +
  mobile-nav height), making content taller than the viewport. Changed to
  `h-full`, matching ChatPage.
- **Tables clipping on narrow screens:** 16 tables across 12 files either had
  no horizontal-scroll wrapper or sat in `overflow-hidden` containers that
  clipped columns on mobile. Containers switched to `overflow-x-auto` (still
  clips rounded corners) or gained scroll wrappers with sensible `min-width`:
  RolesTab, BillingPage, MatterPartiesTab, MatterDocumentsTab,
  InvoiceDetailPage (×2), ProfilePage, TrustAccountDetail,
  TrustAccountReconcile, TrustAccountingPage, DomesticDetailPage (×3),
  PortalCasePage (×3).

### Fixed
- **Google OAuth `at_hash` verification:** fixed Google login callbacks that
  failed with `No access_token provided to compare against at_hash claim` when
  Google included an `at_hash` in the ID token. The callback now passes the
  provider access token into ID-token verification and focused tests cover a
  signed Google ID token with a matching `at_hash`.
- **Mobile OAuth callback duplicates:** fixed Google/Microsoft login failures
  where mobile browsers could request the same provider callback URL more than
  once, causing the second request to surface `Invalid or expired OAuth state`
  as a raw API error page. Successful provider callbacks now keep a
  60-second replay record bound to the exact OAuth `state` + authorization
  code, and duplicate callbacks mint a fresh frontend exchange code without
  reusing the provider authorization code. Google token-exchange failures now
  log the provider status/body for diagnosis.
- **Intake draft autosave flood:** fixed a production issue where the call
  draft hook could rehydrate repeatedly, sync untouched cards, and continue
  autosave attempts after nginx returned 429s, consuming the shared API rate
  limit and making unrelated pages appear broken. Draft hydration is now
  stable, blank cards do not sync to the backend, 429 autosave failures do not
  auto-retry or stack toasts, duplicate in-flight saves are blocked, and the
  capture form flushes only when focus leaves the form rather than during
  normal field-to-field movement. Also fixed the intake rotation admin panel's
  invalid hook dependency list and added migration
  `079_error_logs_system_policy` so tenantless/system error-log rows can pass
  the nullable `error_logs.tenant_id` RLS policy.

### Added
- **QuickBooks connection and invoice tax handling are production-ready:**
  successful Intuit authorization returns to the LawHand QuickBooks admin tab,
  A/R settings serialize database UUIDs correctly, QBO routes use an isolated
  edge-rate bucket, partial catalogue failures remain usable, and invoice
  sales-tax choices explicitly set each synced QBO line taxable or non-taxable.
- **Intake call drafts and action receipts:** added
  `intake_call_drafts` (`078_intake_call_drafts`) with hardened tenant RLS,
  current-user draft list/upsert/delete endpoints at `/api/intake/drafts`,
  server-authored draft timestamps, and frontend two-tier draft persistence
  through `useCallDrafts()` (localStorage immediacy plus backend durability).
  The intake dashboard now has a call-card strip above Call Capture, `Alt+1..9`
  card switching, `Alt+Shift+N` new draft, shared `ToastProvider`,
  `AsyncButton`, and per-draft `ReceiptTrail` retry support for failed draft
  saves, lead assignment, and call submission.
- **API error observability and route-client contract checks:** added
  request-id middleware (`X-Request-ID`), migration
  `077_error_logs_nullable_tenant` for durable tenantless/system error logs,
  focused error-observability/startup tests, and
  `backend/scripts/route_client_contract.py` plus a pytest wrapper to verify
  frontend API call sites still match backend route methods/paths.
- **API/frontend/backend error-readiness map**
  (`docs/api-front-backend-map-eval-2026-07-05.md`): mapped 511 backend routes
  and 339 frontend API call sites, reviewed exception/error-log/access-log
  paths, and documented the next stability priorities: request/error IDs in
  safe 500 responses, tenantless/system error logging, production DB
  fail-closed startup, shared frontend API error normalization, removal or
  dev-gating of bearer-token localStorage fallback, and route/client contract
  smokes.
- **Tenant-context RLS hardening:** migration
  `076_harden_strict_tenant_rls` recreates the strict legacy
  contacts/tasks/communication_logs/leads tenant policies with
  `current_setting(..., true)` + `NULLIF`, and focused regressions now cover
  post-commit tenant context re-binding, Stripe webhook tenant resolution,
  recurring billing tenant loops, cloud-sync provider rebinds, and chat
  attachment UUID serialization.
- **Backend 500 root-cause review** (`docs/backend-500-review-2026-07-05.md`):
  full-codebase audit of the recurring "Internal server error" class. Root
  cause: RLS tenant context is a transaction-local GUC dropped by every
  `db.commit()`; ~122 vulnerable commit sites enumerated across 30+ routers
  and 3 services, verified against production `error_logs`/`pg_policies`.
  Also documents three confirmed silent failures (Stripe payment webhook
  reconciliation, recurring-invoice generation, multi-provider cloud sync)
  and the recommended systemic fix (auto re-bind on transaction begin in
  `get_db`, hardening migration for 4 strict legacy RLS policies, regression
  test). Review only — no code changes.
- **Time tracking, invoicing & QBO overhaul:** live billing timers with one
  running timer per user (`POST/GET/DELETE /api/billing/time-entries/timer`,
  start/stop endpoints; elapsed time rounds UP to the tenant's billing
  increment — default 6 minutes, minimum one increment), tenant billing
  settings (`GET/PUT /api/billing/settings`: default hourly rate + timer
  rounding), and a Start Timer / Stop & Log / Discard timer bar with live
  elapsed display on the Time Tracking page (plus a Matter column). Invoices
  now generate as **drafts** with sequential per-tenant numbers
  (`INV-YYYY-NNNN` instead of random hex) and billing-period dates derived
  from the billed entries; status transitions are validated
  (draft→sent→paid/partially_paid, void/written_off rules), `sent_at` is
  stamped on send, payments are blocked on draft/void invoices, and voiding
  an invoice releases its time entries and expenses back to the unbilled
  pool for re-invoicing. Invoice APIs return `amount_paid`, `balance_due`,
  `is_overdue` (computed from due date — no cron needed), and `matter_name`;
  the Invoices page gained status/overdue filters, matter + balance columns,
  and overdue badges; the invoice detail page gained Send Invoice / Mark
  Paid / Void actions. Time-entry list gained `date_from`/`date_to`/
  `user_id`/`billable_only` filters and pagination with SQL-side totals.
  QBO sync fixes: the automatic invoice/payment sync on status change was
  silently broken (it read nonexistent plaintext-token attributes off the
  integration row — it now resolves a fresh decrypted token via the token
  vault, honoring refresh); QBO `CustomerRef` now uses the customer **Id**
  (find-or-create) instead of a name-only reference that QBO rejects;
  TimeActivity sync is idempotent via a new `qbo_timeactivity_id` column
  (full syncs no longer duplicate time in QBO); invoice updates fetch the
  current `SyncToken`; payment sync includes the required `CustomerRef`
  and is skipped when already synced; `sync/all` now also pushes pending
  payments and reports `payments_synced`; expense invoice lines resolve
  their category-specific QBO item mapping. Migration
  `075_billing_timer_and_qbo_dedupe`.
- **Production deploy data guard:** added `scripts/prod_data_guard.sh` to take
  a custom-format Postgres dump and exact public-table/per-tenant row-count
  snapshot before production deploys, then fail the post-deploy check if any
  existing count decreases. The legacy deploy script now runs the guard before
  build/restart and verifies counts after health checks, and no longer uses
  `--remove-orphans` during the main app restart.
- **Call Intake tasks: assigner notes, closure reasons, customer-history
  documentation:** task assignment emails can now carry a personal message
  from the assigner (`assignment_note` on `POST /api/tasks` and on reassign
  via `PATCH /api/tasks/{id}` — rendered as a highlighted "Message from
  assigner" row in the alert email and appended to the task description).
  Closing a task now records accountability: `closed_reason` +
  `closed_by_user_id` are set on completion/cancellation, cancelling
  **requires** a reason (422 without one), and reopening clears the closure
  record. Every lifecycle event on a contact-linked task — assigned,
  reassigned, customer contacted (Log Contact), completed, cancelled — is
  now documented in the customer's communication history as a
  `CommunicationLog` row (`external_ref = task:{id}:{event}`), including the
  intake-dashboard lead follow-up / general-call task assignments and the
  partner qualify handoff. The Tasks page gained an Assign To picker +
  "Message to Assignee" field on create, Reassign and Close (with required
  reason) actions per row, a Cancelled status filter, and closed-reason
  display on closed rows. Migration `074_task_closure_tracking`.
- **Call Intake standalone: tasks module + follow-up receipts:** the `intake-only`
  plan now bundles a new `tasks` module (nav item, `/tasks` route, and
  `/api/tasks` module-guard mapping) so receptionists and assignees on the
  standalone product can view and work lead follow-up tasks. Tasks gained
  in-app read receipts (`viewed_at`, set only when the assignee views their
  task — automatically on the Tasks page or via `POST /api/tasks/{id}/view`)
  and customer-contact tracking (`POST /api/tasks/{id}/contacted` with
  method + note → `customer_contacted_at`/`customer_contact_method`, first
  contact promotes a pending task to in-progress). The Tasks page shows
  Seen/Unread and Contacted badges plus a Log Contact modal, the intake
  dashboard call panel shows lead status, assignee, task status, seen, and
  contacted state per call, and the intake calls CSV export gained
  `task_viewed_at`, `customer_contacted_at`, and `customer_contact_method`
  columns for partner-commission and follow-up reconciliation. Migration
  `073_task_read_receipts`. Legacy `enabled_modules` tenants keep the Tasks
  page (matters implies tasks); the Open Matter intake action is hidden on
  plans without the matters module.
- **Cloud document storage metadata:** added durable matter-document storage
  metadata for explicit provider/backend, provider object ID, drive ID, parent
  ID, and storage errors, plus structured `StorageResult` upload plumbing for
  OneDrive, SharePoint, Google Drive, and local fallback.
- **Integration reliability core:** added shared provider HTTP clients for
  Microsoft Graph and Google APIs with default timeouts, bounded transient
  retry, `Retry-After`/429 handling, and typed provider exceptions. Gmail and
  Microsoft mail readers now use the shared layer, with focused provider-client
  and mail-reader coverage.
- **Integration observability spine:** added `integration_sync_runs` plus
  token-health/scope-audit columns on tenant and user OAuth credential tables.
  Microsoft/Google OAuth callbacks now persist missing scopes, and admin
  integration health cards show token health, refresh errors, reconnect state,
  and recent cloud/user/correspondence sync runs.
- **CourtListener embedding scheduler:** added `mcp_server.embedding_scheduler`
  and a profile-gated `embedding-scheduler` compose service. The scheduler
  periodically counts `opinion_chunks` with missing embeddings, uses a Postgres
  advisory lock to prevent overlapping Jetson dispatches, and launches the
  existing dispatcher only when queued chunks meet the configured threshold.
- **CourtListener MCP + Jetson embedding stack:** added a standalone `mcp-server/` package and `docker-compose.courtlistener-mcp.yml` for a separate `courtlistener-db` pgvector service, REST MCP server, loader, low-volume sync placeholder, and Jetson embedding dispatcher. The MCP schema owns `courts`, `dockets`, `opinion_clusters`, `opinions`, `opinion_citations`, `opinion_chunks`, `ingest_runs`, and `embedding_jobs`, with `opinion_chunks.embedding vector(1024)` for local `mixedbread-ai/mxbai-embed-large-v1` embeddings. The loader can stage the latest CourtListener S3 quarterly snapshot, load core CSVs, and create chunks; the Jetson worker now targets `opinion_chunks` with mxbai-1024 and batch size 32. Backend `/api/mcp` now proxies to `MCP_SERVER_URL` when configured while preserving the existing local fallback/API-key endpoints. Runbook: `docs/courtlistener_mcp_jetson.md`.
  - **Release corpus filter:** added `--load-mvp` for the first app-backed corpus: ND/MT/MN/SD state authority, SCOTUS, U.S. Tax Court, BIA, and regional bankruptcy/BAP courts. The default keeps published/precedential clusters and avoids broad federal/IP loading for the small-town firm MVP.
  - **S3 staging hardening:** bulk snapshot staging now validates S3 `Content-Length` and downloads through `.part` files before renaming, so interrupted archives are not treated as complete on retry. Production staging completed on the hypervisor Docker bulk volume.
  - **Production smoke import:** loaded 1,000 MVP dockets, 130 clusters, 20 real opinions, and 237 chunks from the staged S3 corpus; live MCP `search_caselaw` returns regional chunks. Loader fixes from the smoke include CourtListener CSV dialect support for backslash-escaped multiline fields, Harvard XML/html fallback text, table-specific smoke limits, and `lbzip2`-backed `.bz2` streaming.
  - **MVP corpus expansion:** increased the live CourtListener DB to 50,000 regional/specialty dockets, 2,103 published clusters, 500 opinions, and 5,024 chunks. Search coverage now includes ND/MT/MN/SD state authority plus SCOTUS and U.S. Tax Court samples; Jetson 3 is embedding the newly created chunks through the reverse-tunnel worker.
  - **Vector/hybrid MCP search:** completed the expanded-corpus embedding pass on Jetson 3, added a Jetson-hosted query embedding service, configured `MCP_QUERY_EMBEDDING_URL`/model/timeout for `courtlistener-mcp`, created the HNSW pgvector index, and switched `search_caselaw` to hybrid pgvector + PostgreSQL FTS ranking with FTS fallback when query embeddings are unavailable.
  - **MCP product gateway:** added tenant-scoped CourtListener MCP product keys separate from the legacy tenant MCP key. New `mcp_product_keys` and `mcp_usage_events` tables support hashed `clmcp_` keys, per-key allowed tools, monthly call limits, revocation, usage logging, and RLS. External calls use `X-MCP-API-Key` and are checked before proxying; internal LegalApp chat remains keyless but logs `internal_chat` MCP usage under the tenant. The MCP route now also accepts JSON-RPC `tools/call` messages at `/api/mcp/messages` and exposes an SSE discovery endpoint at `/api/mcp/sse`.
  - **Expanded CourtListener MCP tools:** added `get_full_opinion`, `find_similar_cases`, `validate_citation`, `normalize_citation`, `get_authority_treatment`, `get_court_coverage`, `search_dockets`, `export_research_bundle`, `sync_status`, and `corpus_status`. `get_case_details` and `get_full_opinion` now advertise an exact-one identifier contract for `opinion_id` vs. `cluster_id`, and tenant product-key scope validation/UI docs include the full tool catalog.
  - **Tenant MCP key management UI:** replaced the old single-key MCP page with tenant-admin product-key management at `/mcp`, including create, one-time key display, 30-day usage totals, per-key usage, scoped tools, revoke, and REST/messages/SSE endpoint display.
  - **MCP product gateway hardening:** product-key tool scopes are validated against supported tools, monthly quota checks are serialized per key with a transaction advisory lock, and internal chat MCP usage logging uses an isolated DB session so retrieval cannot commit the active chat transaction.
  - **Operations handoff:** added `docs/courtlistener_mcp_operations.md` as the start-here runbook for managing the live MCP stack, staged S3 cache, bounded loader passes, Jetson embedding dispatch, verification SQL, recovery commands, and known CourtListener/Citation pitfalls.
  - **CourtListener compose hardening:** the CourtListener DB compose stack now requires `COURTLISTENER_DB_PASSWORD` and binds the database port to `127.0.0.1` by default; LAN exposure for Jetson workers must be explicit via env.
  - **Env hygiene:** Jetson launcher/dispatcher now support `JETSON_HOSTS`, indexed `JETSON_0_HOST`...`JETSON_9_HOST`, and per-index `JETSON_0_USER`... variables; concrete hypervisor, Jetson, and legacy-source connection details were removed from project memory in favor of env-variable names.
  - **Jetson embedding smoke:** brought up Jetson 3 on the wired/testlab address with SSD-backed worker files under `/data/legalapp-embeddings`, added reverse SSH tunnel mode for segmented networks where the Jetson cannot initiate DB traffic to the hypervisor, and embedded all 237 staged smoke chunks with `mixedbread-ai/mxbai-embed-large-v1` (`embedding_version=1`, `vector_dims=1024`).
- **RBAC core (Phase 1):** tenant-scoped role registry where each role carries a capability checklist (catalog of 10 capabilities). New `roles` + `user_roles` tables (migration 068), a capability resolver (`rbac_service`), a `require_capability` dependency, and a `manage_roles`-gated admin API (`/api/admin/roles` CRUD + `/assign/{user}`) with a last-admin guard and cross-tenant target protection. `require_admin`/`require_finance_admin` now consult capabilities (legacy `user.role` fallback preserved). Login JWT carries a `caps` claim; new firms are seeded the four system roles (Administrator/Accountant/User/Client) with the founder assigned Administrator across all four signup paths. Admin → Roles tab for role management + per-user assignment. The legacy `user.role` column and tenant-plan module guard are intentionally retained/untouched. Design + plan: `docs/superpowers/specs/2026-06-23-rbac-and-m365-group-sync-design.md`, `docs/superpowers/plans/2026-06-23-rbac-core-phase1.md`.
  - **RLS (migration 069):** `roles`/`user_roles` now carry the same two-policy pair as every other tenant table — `tenant_isolation` + `rls_bypass` (FORCE ROW LEVEL SECURITY); `user_roles` gained a denormalized `tenant_id`. `provision_tenant_rbac` binds the tenant RLS context so registration role inserts pass WITH CHECK. Enforcement verified directly against Postgres with a non-superuser role (tenant-scoped insert succeeds, cross-tenant insert/read rejected, context-less insert rejected, bypass path works) since the pytest harness runs RLS-off. Minor app-wide hardening still open: the shared policy cast `current_setting('app.current_tenant_id', true)::uuid` errors on an empty-string GUC (would matter only if a future caller mixes `clear_tenant_context()` with RBAC writes on one connection); `NULLIF(..., '')` would harden all tenant tables.
- **M365 group sync (Phase 2):** not started — separate plan to be written after Phase 1 merges.
- **Gateway privacy defaults:** LiteLLM message logging remains disabled and the base config no longer enables success/failure callbacks. LegalApp gateway usage, MCP usage, and error logs now suppress raw prompt/query text by default behind `GATEWAY_RAW_TEXT_RETENTION_ENABLED=false`; chat sends only metadata fields (`tenant_id`, `user_id`, `conversation_id`, `operation_type`, `matter_id`, `plugin`, `skill`, `premium`) to LiteLLM. Retention defaults are documented as 30 days for gateway logs, 7 days for debug logs, and 365 days for spend logs.
- **Gateway operator audit logs:** added `operator_audit_logs` plus metadata-only audit entries for Platform AI route saves, provider key disable/delete actions, and synthetic model tests. A shared tenant debug-mode audit payload helper is ready for the 1203 debug-mode UI without logging prompts, responses, keys, or raw customer content.

### Changed
- **Intake call capture:** the existing Call Capture form is now backed by the
  active draft while preserving the dashboard layout. Selecting history
  matches, recent callers, phone context, notes, task routing, and staff
  assignment updates the active draft and flushes to backend on blur/card
  switch.
- **Production deploy:** shipped `25a9238` for the systemic API/RLS/error
  observability hardening to the hypervisor after local backend/frontend
  validation, staged secret scan, production env guard, and predeploy
  dump/count snapshot. Rebuilt and recreated backend/frontend, ran migrations
  through `077_error_logs_nullable_tenant`, verified local and public health,
  Microsoft/Google OAuth 307 redirects, active cloudflared, request-id headers,
  closed docs/dev routes, and passed the postdeploy production data guard.
- **Assistant chat source/reference UX:** chat turns now retain source
  retrieval context after streaming and refresh. User prompts and assistant
  answers both show a compact References strip with matter, upload,
  firm/cloud, and CourtListener counts, and the final answer ledger is now
  labeled for mixed sources and references instead of only authorities.
- **Production deploy:** shipped `d2e7851` for the Matter create API 500 fix
  to the hypervisor after a predeploy dump/count snapshot; rebuilt
  backend/frontend, recreated the app containers, verified
  health/OAuth/cloudflared/closed docs-dev routes, and passed the postdeploy
  production data guard.
- **Production deploy:** shipped `acbbe64` for the Call Intake
  create-lead/staff-task 500 fix to the hypervisor after a predeploy dump/count
  snapshot; rebuilt backend/frontend, recreated the app containers, verified
  health/OAuth/cloudflared/closed docs-dev routes, and passed the postdeploy
  production data guard.
- **Production deploy:** pulled and deployed merge `4e70405` for the billing
  timer/invoicing/QBO overhaul after rotating the placeholder `SECRET_KEY`,
  setting production `DEV_MODE=false`, taking a predeploy DB dump/count
  snapshot, and verifying the postdeploy data guard, health checks, OAuth
  redirects, closed dev/docs routes, and Alembic revision
  `075_billing_timer_and_qbo_dedupe`.
- **Git/deploy hygiene:** cleaned merged local and remote branches after the
  production data guard landed, removed stale clean worktrees, preserved
  unmerged in-flight integration work, and re-verified the production data
  guard snapshot plus public health before resuming feature work from `main`.

### Fixed
- **Matter cloud-folder sync scope:** the per-matter cloud folder sync endpoint
  now refreshes only that matter's mapped primary, subfolder, and context
  folders instead of launching a tenant-wide cloud metadata scan.
- **Google Drive folder provisioning race:** Google folder creation now
  re-lists and reuses the existing folder when a concurrent create returns 409,
  matching the OneDrive/SharePoint duplicate-recovery behavior.
- **Opaque production API failures:** safe error responses now include
  `request_id` and captured `error_id`, request IDs persist in `error_logs`,
  tenantless/system errors no longer skip logging, and production startup
  fails closed when the initial DB connectivity probe fails. Frontend API
  handling now normalizes Axios and streaming-fetch failures, preserves
  request/error IDs from response bodies/headers, parses validation details,
  and removes the production localStorage bearer-token fallback unless
  explicitly enabled for dev.
- **Assistant chat reference ledger wrapping:** long unbroken citations,
  source excerpts, and message/reference text now wrap inside the chat card
  instead of overflowing across the page.
- **Systemic post-commit RLS 500s:** request DB sessions now attach a
  per-session SQLAlchemy `after_begin` listener that re-binds both tenant GUCs
  on every new transaction, so any DB work after `db.commit()` remains scoped
  instead of returning empty RLS reads or `Could not refresh instance` 500s.
  Auth register/signup explicitly re-enable the transaction-local RLS bypass
  after their first commit. Stripe payment webhooks resolve and bind tenant
  context before invoice/payment reconciliation, recurring billing runs per
  active tenant with tenant context, cloud sync re-binds around each provider,
  and chat attachment responses serialize UUID IDs as strings.
- **Matter create API 500:** `POST /api/matters` now explicitly binds tenant
  context before writing and re-binds it after commit before refreshing/reloading
  the created matter, preventing production RLS from hiding the just-created
  row during response construction.
- **Call Intake create lead + staff task 500:** stopped refreshing the
  `CommunicationLog` after commit in `POST /api/intake/dashboard/calls` and
  re-bound tenant context before task notifications so production RLS no
  longer hides the just-created call row during the combined lead/staff-task
  workflow.
- **Zoom Phone intake call-feed sync:** fixed a production 500 where tenants
  with connected Zoom Phone grants could fetch call history but inserts into
  `communication_logs` failed RLS because legacy policies still read
  `app.tenant_id` while newer request setup only set `app.current_tenant_id`.
  The shared tenant-context helper now keeps both GUC names synchronized and
  clears them to a fail-closed sentinel UUID.
- **Backend/API security hardening (prod-readiness pass):** frontend auth now
  lives entirely in httpOnly cookies — the SPA no longer reads, writes, or
  falls back to a bearer token in `localStorage`, closing an XSS session-theft
  vector (`App.jsx`, `api.js`, `LoginPage.jsx`, `SignupPage.jsx`,
  `AuthCallback.jsx`). `get_settings()` now fails closed at startup if
  `SECRET_KEY` or a configured `PLATFORM_SECRET_KEY` is short or matches a
  known unfilled-template placeholder (e.g. the committed
  `change-this-to-a-long-random-secret-key...` / `generate-with-openssl...`
  values), preventing every JWT in the system from being forgeable by
  accident. The `/dev/*` router (email-only login, 365-day tokens for every
  user) is now excluded from the app entirely unless `DEV_MODE=true`, instead
  of 404ing per-request — it no longer exists in routing or the OpenAPI schema
  in prod. Interactive API docs (`/docs`, `/redoc`, `/openapi.json`) are
  likewise only served when `DEV_MODE=true`. Detecting a superuser/BYPASSRLS
  database role (RLS silently disabled) is now a fatal startup error outside
  `DEV_MODE` instead of a log line — the app refuses to serve traffic with
  tenant isolation off. `/health` no longer echoes raw DB exception text to
  unauthenticated callers. The rate-limit middleware's JWT claim extraction
  now also reads the httpOnly access-token cookie (previously
  `Authorization`-header only), so cookie-authenticated requests are no longer
  invisible to per-user/per-tenant limits. Document upload
  (`POST /api/documents/upload`) now rejects file types outside an explicit
  allowlist (PDF/DOC/DOCX/TXT) instead of silently UTF-8-decoding arbitrary
  binaries into the RAG pipeline.
- **Backend/API security hardening, follow-up pass:** added
  `backend/tests/test_route_auth_coverage.py`, a static/structural regression
  test closing the "fail-open tenant middleware" finding without rearchitecting
  request handling — it walks every registered route and asserts each one
  calls a recognized auth function (or a same-codebase helper that resolves to
  one, e.g. `require_teams_enabled`), against an explicit reviewed allowlist
  for the ~40 genuinely public/differently-authenticated routes (OAuth
  callbacks validated by CSRF state, webhooks validated by provider signature,
  portal magic-links, MCP discovery, `DEV_MODE`-gated docs/dev routes).
  Companion script `backend/scripts/audit_route_auth.py` regenerates the
  candidate list after adding routes. `QBOSyncService._safe_qbo_string` now
  also escapes backslashes and strips control characters, not just quotes.
  Redis unreachability at startup is now fatal when `DEV_MODE=false` (mirrors
  the RLS-bypass fatal-startup pattern) — the in-memory revocation/rate-limit
  fallback is not reliable across multiple uvicorn workers and must not run
  silently in production. `RegisterRequest`/`PlanSignupRequest`/
  `ResetPasswordRequest` reject a small local common-password blocklist and
  structural patterns (all-same-char, sequential digits) that a length-only
  policy misses. Removed the deprecated `X-XSS-Protection` nginx header.
  `error_tracker.capture_error` now runs `message`/`stack_trace` through the
  existing `app.services.pii_detection.scrub_pii` before persisting, so an
  exception that echoes a client's email/SSN/phone/card number (e.g. a
  uniqueness-constraint violation) doesn't land verbatim in `ErrorLog`.
- **Cloud-backed document delete:** matter document delete now removes
  provider-backed files by durable Google Drive, OneDrive, or SharePoint IDs
  before deleting the DB row, tolerates provider 404s, and keeps failing closed
  for legacy URL-only rows that cannot be safely routed.
- **OAuth token refresh race:** tenant and per-user token refresh now re-checks
  freshness under a row lock, uses one provider refresh path for Microsoft,
  Google, and Zoom, retries transient token-endpoint failures, persists rotated
  refresh tokens, and records health/last-refresh error state.
- **Integration failure visibility:** token refresh failures now record
  `last_refresh_error`/`last_refresh_at`, `invalid_grant` marks credentials
  revoked/inactive, and integration scheduler per-tenant failures write
  admin-visible `ErrorLog` entries instead of living only in worker logs.
  Cloud-search status now returns a real error state when its status query fails.
- **Client portal security and UX:** client portal sessions now use a dedicated
  `client_portal_token` cookie instead of overwriting firm-app auth, portal JWTs
  carry the accepted invite ID, and portal requests fail closed if the invite is
  revoked, expired, missing, or from a legacy token without invite scope. Portal
  signature requests now list/sign only pending signers matching the portal
  contact or invite email. Client and firm portal screens now surface message,
  document, signature, invoice, invite-load, revoke, upload, and email-delivery
  failures instead of silently showing empty states.
- **CourtListener scheduler runtime posture:** stopped the live
  `embedding-scheduler` sidecar after validation and documented that, during
  MVP/test-hardware operation, the scheduler should remain off unless a bounded
  import intentionally creates unembedded chunks. The runbook and project
  memory now warn not to attempt a full CourtListener corpus sync on current
  storage.
- **Chat streaming progress metadata:** chat streaming now emits typed
  `[PROGRESS]` SSE events with live counts for matter context, uploads,
  firm/cloud/private retrieval, and CourtListener MCP authority. The frontend
  stream parser consumes those events separately from text tokens, and the
  assistant working state now renders dynamic source counters plus safe
  query-focus wording before and during answer streaming.
- **Chat transcript active state:** simplified the user query card header to
  `You · time`, moved query copy into a small hover control, and replaced the
  duplicate streaming typing card with a single assistant working state that
  shows source search, authority check, and drafting status until tokens arrive.
- **Chat start page source copy:** updated the empty chat state to describe
  source-material behavior accurately: sources are used when available,
  citations appear where retrieved materials are used, and outputs are prepared
  for attorney verification rather than represented as attorney-approved.
- **Chat context language and document upload:** replaced model-visible
  `FIRM CONTEXT` prompt wording with source-material language, added guardrails
  that rewrite old/custom `[FIRM CONTEXT: ...]` tags to user-safe cited-context
  provenance tags, and renders those tags as colored badges. General document
  upload now refreshes before commit while tenant RLS context is active, and
  background document processing binds tenant context in its worker session.
- **Chat source attribution UX:** chat now keeps the submitted question visible
  if the post-stream conversation refresh returns stale data, strips raw
  CourtListener HTML from source citations/excerpts, carries MCP case URLs into
  chat source records, and renders authority citations as external links with
  color-coded provenance badges for cited authority, cloud context, matter
  context, and firm context. Inline provenance tags now also recognize model
  reasoning, well known fact, cited by context, and firm context aliases. MCP
  search SQL now escapes JSON-path braces correctly while returning source URL
  and citation metadata.
- **Chat/MCP matter and cloud context hardening:** conversation creation now
  rejects invalid or cross-tenant `matter_id` values instead of persisting an
  unvalidated FK. Chat message handling computes one validated effective matter
  from the message body or linked conversation, uses it for matter context,
  RAG retrieval, LiteLLM metadata, context tags, and usage records, and scopes
  RAG cache keys by skill plus matter/cloud retrieval scope. Streaming chat now
  persists cloud source citations and cloud source IDs like non-streaming chat.
  Cloud metadata index fallback is folder-scoped for matter searches, matter
  context loading supports an explicit tenant filter, cloud-backed matter
  document deletion now fails closed instead of orphaning provider files, and
  MCP product-key tool listings stay aligned with the remote CourtListener tool
  catalog when the remote manifest is temporarily unavailable.
- **CourtListener MCP deployment guard:** documented that main-stack-only
  `--remove-orphans` deploys remove the separate CourtListener sidecar services
  from the shared `legalapp` project/network, and recorded the restart command
  plus required `COURTLISTENER_DB_PASSWORD` env source of truth.
- **Alembic migration chain:** corrected the MCP product gateway migration to
  depend on revision `069` instead of the filename-like `069_rbac_rls`, which
  prevented production backend startup from resolving the migration graph.
- **Platform AI route reload button:** migrated the Platform -> AI Routing
  reload path off LiteLLM's rejected legacy `/config/update` `model_list`
  payload. The backend now reads `/model/info`, preserves matching file-backed
  aliases, upserts DB-backed route-builder fallbacks through `/model/new` or
  `/model/{id}/update`, and sends only `router_settings` to `/config/update`.
  Production reload smoke returned `reloaded=true` with four models and two
  fallbacks registered; saved `clarity-premium` was aligned to the live
  OpenCode Go `deepseek-v4-pro` file-backed alias.
- **Platform AI route source drift:** aligned the saved Platform -> AI Routing
  `clarity-standard` route with the deployed latency fix. The route-builder row
  still had OpenRouter Llama as primary and Gemma as fallback, even though the
  file-backed LiteLLM config and backend fallback path had been changed. The
  platform API now reports OpenRouter `google/gemma-4-31b-it:free` as primary
  with OpenCode Zen `nemotron-3-ultra-free` and `deepseek-v4-flash-free`
  fallbacks, and the backend fallback list accepts both platform-generated
  `clarity-standard-fb-*` aliases and file-backed named aliases.
- **Chat MCP latency and LLM route resilience:** measured the production chat
  path and found MCP retrieval was fast (~245ms) while the standard LLM route
  was the bottleneck. `clarity-standard` now uses the faster OpenRouter Gemma
  model as primary, with backend-owned pre-token fallback to Nemotron then
  DeepSeek when the free provider 429s. Removed dead Qwen/insufficient-balance
  aliases and cleared the stale LiteLLM DB-backed `clarity-standard-fb-0`
  fallback. Production MCP-enabled chat smoke returned 200 with first SSE data
  at 1.105s, complete at 14.145s, and no stream error.
- **Production chat creation/isolation/rate limiting:** fixed the deployed
  conversation-create 500 by keeping chat response construction RLS-safe after
  commit, removed same-tenant admin override access from conversation
  detail/update/upload/delete/message routes, and made stale/unauthorized chat
  IDs drop out of the frontend instead of looping on GET/DELETE. nginx now
  keys API rate limits on the cloudflared `X-Forwarded-For` client instead of
  the shared Docker peer, tenant daily metering counts only LLM/tool-heavy
  conversation/plugin paths, and error-log writes bind tenant RLS context before
  inserting. Production smoke verified conversation create/load/delete, deleted
  404, same-tenant other-user 404, public `/health`, nginx real-client-IP logs,
  and no recurring error-log RLS failures.
- **OAuth login 401 (RLS):** `get_current_user` now calls `set_tenant_context()` before querying the `users` table. With `clarity_app` (no BYPASSRLS), the `tenant_isolation_users` RLS policy previously filtered the user query against `NULL`, returning no rows and raising 401 "User not found" on every authenticated request including the `/api/auth/me` call immediately after OAuth exchange. Fix reads `tenant_id` from `request.state` (TenantMiddleware routes) or the JWT payload (auth-bypassed routes). Production nginx also now trusts Docker bridge CIDRs (`172.16.0.0/12`, `10.0.0.0/8`) for `CF-Connecting-IP` so per-user rate limiting works correctly through the cloudflared tunnel.
- **MCP endpoint auth/RLS hardening:** `POST /api/mcp/tools/call` now authenticates and binds tenant context before proxying to CourtListener MCP, API-key auth sets tenant RLS context before looking up the tenant admin user, fallback `get_chunk` uses an explicit UUID cast for tenant filtering, and admin MCP configuration now reports the live proxied CourtListener tool list instead of the stale fallback list. Production smoke verified unauthenticated tool calls return 401, authenticated tool calls return 200, `/api/mcp` exposes `clarity-courtlistener` with 7 tools, admin `/api/mcp/api-key` exposes the same tool list, and chat persists CourtListener context tags.
- **Chat CourtListener vector relevance:** chat now maps CourtListener MCP `similarity` scores into source relevance instead of treating small RRF rank values as user-facing percentages, while preserving the retrieval mode. Production chat smoke stored CourtListener context tags and vector similarity scores for the returned sources.
- **CourtListener citation-map trim safety:** citation-map imports now keep only edges where both citing and cited opinion IDs are present in the local trimmed corpus, preventing FK failures during bounded MVP loads.
- **Chat CourtListener MCP pipeline:** chat public legal research now uses the configured CourtListener MCP server instead of the stale `public_chunks`/BGE path, maps MCP hits into the existing chat source-citation contract, and stores `context_used`/`context_relevance_scores` entries as `courtlistener:<chunk_id>`. Fixed RLS-sensitive post-commit refreshes in conversation/message/attachment responses, split RAG cache keys by public/private authority plus a full-query hash so the chat toggle cannot reuse stale empty public-context results, and refreshed streamed chat messages after completion so the UI shows persisted source citations immediately. Production smoke verified `/api/mcp`, `POST /api/conversations`, `POST /api/conversations/{id}/messages`, CourtListener source storage, and the `include_public=false` negative path.
- **Production LegalApp recovery + MCP wiring:** restored the `legalapp` hypervisor Compose project from `/home/varta/legalapp/docker-compose.hypervisor.yml`, removed accidental `work-*` containers without deleting volumes, restarted the CourtListener MCP side stack under the `legalapp` project, configured production `MCP_SERVER_URL=http://courtlistener-mcp:8021` after creating an `.env.backup.*`, and verified public `/health`, OAuth redirects, `/api/mcp`, and a live `search_caselaw` tool call.
- **Self-inflicted 429s on the admin tab (and empty call feed):** the intake dashboard call feed polled every 15s (240 req/hr) while the per-user hourly cap was only 200, so leaving the dashboard open exhausted the budget mid-hour and 429'd every other request — including `/api/admin/tenant` — until the clock hour rolled over. Raised the per-user cap to 600/hr, exempted the polled `/api/intake/dashboard/recent-callers` read from the per-user counter (nginx still IP-limits it), and slowed the poll to 30s.
- **Intake dashboard mobile + action feedback:** the call-logging/lead result banner is now sticky and auto-scrolls into view, so tapping "Create Lead"/"Log Call" at the bottom of a long mobile page shows the "Lead created…" confirmation instead of appearing to do nothing. Tightened mobile padding/heading sizes and removed the dev-only "MVP Boundary" panel to declutter the screen.
- **Call feed time visibility:** each call-feed row now shows the call time prominently plus a relative "12m ago / 3h ago / 2d ago" recency line (feed remains ordered newest-first), so reception sees when each call came in without clicking into it.
- **Intake history matches show who answered:** call-log results in the History Matches panel now surface `answered_by` (the staff member who answered, from the call's `callee_name`) and the `result` (answered/missed) alongside phone and timestamp — matching what the live call feed already shows — so reception can see who took the call, not just the caller-history name match.
- **Zoom Phone intake call history:** sync now requests inbound Zoom Phone history only, the importer skips non-inbound call-history rows, nested caller/callee payloads are scanned for the actual caller phone number, and the Zoom Phone queue filters out previously imported outbound legs.
- **Zoom Phone token expiry copy:** replaced the confusing one-hour access-token expiry footer with admin-facing copy explaining that Clarity refreshes Zoom access automatically during sync/test and only needs reauthorization if access is revoked, scopes change, or the refresh grant expires unused.
- **Platform AI routing save/reload:** enabled LiteLLM DB-backed model storage in compose (`STORE_MODEL_IN_DB=True`) so operator route changes can hot-reload through `/config/update` instead of returning a 500.
- **Platform AI routing load:** stopped auto-fetching provider model lists for saved routes on page load; the AI Routing page now uses the cached catalog by default and only probes provider `/models` endpoints when an operator explicitly clicks a model refresh button.
- **Chat legal footer:** the "Prepared for ... Attorney review recommended" footer is now conditional for legal analysis/drafting/advice-like responses instead of being required on every chat message.
- **Admin integrations clarity:** moved optional Zoom meeting setup into its own Admin tab so missing Zoom OAuth credentials no longer make regular Microsoft/Google integrations look unhealthy.
- **Zoom Phone OAuth visibility:** Admin → Zoom now always renders the Zoom Phone intake connection card instead of hiding it behind meeting-only Zoom configuration, so admins can see the phone OAuth state and required setup separately.
- **Zoom integration admin polish:** redesigned Admin → Zoom around first-class Phone intake and Meetings cards with customer-facing connect actions, friendly scope checklist labels, and a separated operator setup section for OAuth app credentials and redirect URIs.
- **Zoom operator setup visibility:** hid global Zoom OAuth app credentials and redirect URI readiness from tenant Admin → Zoom and moved redacted Zoom setup readiness into the platform operator console.
- **Admin users active toggle:** restored the friendlier Active switch in the Users table for enabling/disabling user accounts while preserving the OAuth-grantor safety confirmation.
- **Admin licensing and add-on controls:** license toggles now allow unlicensing integration grantors without failing the request, premium AI access is managed separately per licensed user, and add-on purchase/trial/disable actions refresh the current module list and show confirmation feedback.
- **Intake dashboard search coverage:** history search now finds log-only callers, split first/last names, partial name fragments, and partial phone digits so receptionist-only calls such as "Jan Patterson" surface before being promoted to leads.
- **Partner-to-attorney intake workflow:** intake follow-up tasks now let a partner qualify a caller, assign the qualified intake to an attorney, complete the partner follow-up, carry receptionist plus partner notes into the attorney’s urgent intake task, and let the attorney open a linked matter in `waiting_fee_agreement` status from that task.
- **Intake dashboard call logging feedback:** prevented successful call logging from immediately triggering an empty dashboard search, which caused a misleading `422` and made the first call log appear to do nothing. Assigned intake leads now also create/update an urgent partner follow-up task and send the standard task assignment alert.
- **Task assignment alerts:** expanded task-assignment notifications into ticket-style alerts with assignee, creator, created/alert time, due time, customer/matter context, source, and reason/description fields.
- **Intake dashboard recent callers:** added a preloaded recent-caller panel with 10/20/50 limits so reception can quickly pick up repeat callbacks without searching from scratch.
- **Intake dashboard callback details:** recent callers now expand into call details including logged-by user, routed partner, lead status, follow-up task status, due/completed time, reason, and notes. History-search `401` failures now show a session-expired message instead of a generic search error.
- **Intake dashboard auto-assignment preflight:** create-lead flow now checks rotation availability before calling `assign-next`, disables auto-assignment when no practice/general rule exists, and keeps successful call logging from being marked failed by a non-critical search refresh.
- **Teams matter linking:** Teams admin now loads canonical matters from `/api/matters`, supports creating a standard Teams channel for the selected matter/team, and requests `Channel.Create` on Teams reconnect for channel creation.
- **Trust account matter picker:** new trust account creation now displays canonical matter names from `/api/matters` instead of falling back to matter UUIDs.
- **Subscription billing placement:** moved Clarity/Stripe subscription billing into the Admin portal as a Subscription tab, removed it from the workspace accounting sidebar, and redirects legacy `/billing` visits to `/admin?tab=billing` for admins.
- **Chat/assistant not falling back to general reasoning or indicating confidence tags (v2):** Second pass on `SYSTEM_PROMPT_TEMPLATE` in `app/services/llm.py`. Added negative examples (WRONG vs RIGHT), explicit "do NOT explain your reasoning process" rule, "greet in 1-2 words then answer" simplification, and a direct "if user types 2+2, reply 4" non-legal-query example. The free-tier models were reading the old prompt as rules to explain rather than follow.
- **Chat latency — parallel pre-work + faster failover:** Parallelized five independent async operations (matter context, attachment context, memory context, LLM route, RAG cache check) with `asyncio.gather` in both `/messages` and `/messages/stream` endpoints — saves ~150-300ms per request. Reduced LiteLLM `request_timeout` 60→25s, `num_retries` 1→0, `cooldown_time` 30→15s, and added per-model `timeout` values (15s free, 20-30s paid) for faster failover to fallback models.

### Tests
- **Integration observability:** added focused tests for scope normalization,
  missing-scope persistence, and token-health derivation. Verification:
  `py -m compileall backend/app/models backend/app/services backend/app/routers backend/app/schemas`,
  `py -m pytest backend/tests/test_integration_observability.py backend/tests/test_token_vault_revoke.py -q`
  with a throwaway `TOKEN_ENCRYPTION_KEY`, and `npm run build` in `frontend/`.
  DB-backed integration readiness tests still cannot run locally because the
  test Postgres connection is refused.
- **Intake call drafts:** added focused backend coverage for draft CRUD,
  idempotent upsert, current-user/tenant scoping, cross-tenant draft-id
  collision handling, and server-authored `updated_at`. Verification passed
  for backend compile, route-client contract (`394` frontend API call sites),
  and frontend production build. Local DB-backed intake tests remain blocked
  by unavailable Postgres (`ConnectionRefusedError`, `WinError 1225`) after 6
  non-DB tests passed.
- **Matter create RLS regression:** added `backend\tests\test_matters.py` to
  cover `POST /api/matters`, primary assignment/event creation, and the
  post-commit tenant-context requirement before refreshing the created matter.
  Verification: `backend\tests\test_matters.py`,
  `backend\tests\test_module_guard.py`, backend compile, and frontend
  production build.
- **Call Intake create lead + staff task regression:** added coverage for the
  exact `create_lead` + `specific_staff` dashboard action, including a guard
  against post-commit communication-log refreshes and a tenant-context check
  before notifications. Verification: targeted regression, full
  `backend\tests\test_intake_dashboard.py`, backend compile, and frontend
  production build.
- **Zoom Phone intake RLS regression:** expanded the tenant-isolation test to
  assert both current and legacy tenant-context GUCs are set/cleared together,
  and made it honor `TEST_DATABASE_URL` so the non-superuser RLS probe runs
  against the local test Postgres port. Verification:
  `py -m pytest backend\tests\test_tenant_isolation.py backend\tests\test_intake_dashboard.py -q`
  and `py -m compileall -q backend\app`.
- **Client portal remediation:** added unit regressions for invite-bound portal
  JWTs, dedicated portal cookie naming, revoked invite rejection, legacy token
  rejection, and contact/email-bound portal signer matching. Verification:
  `py -m pytest backend/tests/test_client_portal_security.py -q`, targeted
  backend compile, and `npm run build`.

### Added
- **QuickBooks connection and invoice tax handling are production-ready:**
  successful Intuit authorization returns to the LawHand QuickBooks admin tab,
  A/R settings serialize database UUIDs correctly, QBO routes use an isolated
  edge-rate bucket, partial catalogue failures remain usable, and invoice
  sales-tax choices explicitly set each synced QBO line taxable or non-taxable.
- **Call Inbox dashboard redesign:** reworked the intake dashboard into a two-pane "Call Inbox" — a left-hand unified call feed that auto-refreshes every 15s (visibility-aware polling) and a right-hand work panel (caller facts → auto-searched history → pre-filled capture/route form). New calls (manual or webhook-imported) surface within ~15s with an in-page toast + WebAudio chime; mute toggle persisted per tenant. The `recent-callers` feed now exposes `source`, `answered_by`, `result`, `duration_seconds`, and recording/transcript URLs, accepts `limit=5`, and batches its enrichment queries (was N+1 per row). Source-agnostic framing: integration controls (Sync, source filter) appear only when the tenant has a connected call source, so a manual-only tenant sees a clean inbox. New `frontend/src/hooks/useCallFeedPolling.js`, `useCallAlerts.js`, and `components/intake/` (CallFeed, CallFeedItem, CallFacts, NewCallToasts, RecordsTabs).
- **Zoom Phone post-call webhooks:** added tenant-specific Zoom Phone webhook URLs with Zoom CRC/signature validation, encrypted tenant webhook secret-token storage, and post-call `phone.callee_call_history_completed` / `phone.caller_call_history_completed` ingestion. Completed inbound webhook records now fetch Zoom call-history detail before idempotent `CommunicationLog` upsert; manual Sync Zoom remains the backfill path. Migration `067` adds webhook secret storage to tenant-owned Zoom apps.
- **Tenant-owned Zoom Phone OAuth apps:** tenant admins can now save encrypted Zoom OAuth client credentials from a firm-owned Zoom app, use that app for the Phone authorization callback, and refresh Zoom Phone tokens without global Clarity Zoom OAuth credentials. The Admin -> Zoom Phone card shows the callback URL, required Phone scopes, masked saved client ID, save/clear actions, and keeps platform/global credentials as fallback only.
- **Zoom Phone admin OAuth grant:** added a customer-facing Admin → Zoom flow for Zoom Phone intake. Tenant admins can connect Zoom Phone through Zoom OAuth, storing encrypted `zoom_phone` tenant credentials separately from the existing Zoom meetings integration. The Zoom tab now shows Zoom Phone status, missing scopes, disconnect, and a connection test that probes Call History without importing. The call-history importer now prefers the tenant OAuth token and keeps the S2S/env path only as an operator fallback. Expected callback: `/api/integrations/zoom-phone/callback`.
- **Zoom Phone intake call history:** added a Zoom Phone Server-to-Server call-history importer that stores each call idempotently as a `CommunicationLog` (`zoom_phone:call:{id}`), preserving caller ID, normalized phone, direction/result, duration, recording/transcript links, and summary/transcript details. The intake dashboard now has a Zoom Phone Calls queue with admin sync, transcript/recording links, and click-to-prefill into the existing lead/task capture flow; saving from a Zoom row updates the imported call record instead of creating an unrelated duplicate. New `ZOOM_PHONE_*` env template keys document the account-credentials setup.
- **Zoom Phone admin OAuth backlog:** corrected the near-term follow-up to use a customer admin OAuth grant in the Clarity portal, matching Microsoft/Google integrations, with encrypted per-tenant `zoom_phone` tokens, connection testing, setup status, and S2S/env kept only as a temporary operator fallback.
- **Zoom Marketplace P2 backlog:** documented the deferred Zoom-side app plan, including the required heavy research pass for Marketplace app types, Zoom Phone APIs/webhooks/scopes, review/security requirements, tenant install flow, and whether an in-Zoom receptionist surface is worth building.
- **Platform free legal model eligibility:** model catalog rows now include legal eligibility, tier, badges, latency eligibility, and exclusion reasons; the AI Routing UI defaults to Recommended models and adds Free Legal, All Free, and Excluded tabs.
- **Matter-linked chat workflow:** general chats can now be linked, changed, or unlinked from matters in Chat, and Matter Detail opens matter-scoped conversations through `/chat?conv=...`.
- **Accountant finance role and restricted licensing modes:** added an `accountant` role for billing/licensing/subscription/reporting access, premium-AI assignment on users, backend enforcement for unlicensed users, and intake-only module resolution so call-intake tenants land on the intake dashboard plus add-on modules only.
- **General intake task routing:** receptionist call capture now supports partner rotation by default, no-task logging, or a general staff task assigned to any active tenant user with preset/custom task labels; recent callers and CSV exports show these staff task assignments.
- **Standalone caller-intake packaging:** intake-only tenants can use the dashboard as a self-contained licensed product, and the intake dashboard now exports tenant-scoped call records to CSV with optional date range filters for finance/Tabs3 partner-association reconciliation without promoting every caller into CRM/matters.
- **Sellable plan/tier framework (Call Intake solo):** new plan registry (`app/services/plans.py`) drives module visibility from a named plan, replacing the hardcoded intake-only branch and laying groundwork for additional public tiers. Tenants can be provisioned intake-only two ways — an operator plan selector on the platform tenant editor (`PUT /api/platform/tenants/{id}` `plan`, `GET /api/platform/plans`) and a public self-serve signup (`POST /api/auth/signup/plan`) that creates an intake-only tenant + admin user on a 14-day trial. Plans expose an `upsell_target` for in-product upgrade prompts.
- **Fail-closed API module enforcement:** new `ModuleGuardMiddleware` rejects API calls to modules outside a tenant's plan (keyed off a signed `plan` JWT claim), so an intake-only tenant is walled at the API, not just the UI. Tokens without the claim default to full platform (backward compatible).
- **Partner assignment log + export:** new append-only `partner_assignment_log` table (migration `064`, RLS-scoped) records every partner/staff assignment (rotation, prior-attorney, specific-staff) with name snapshots. Exposed via `GET /api/intake/dashboard/partner-log` and `/partner-log/export` (CSV), plus a Partner Log panel on the intake dashboard.
- **In-product upsell:** intake-only tenants see locked nav teasers for other modules that open an "Upgrade to the full platform" modal; requests are captured to `plan_upgrade_requests` (migration `065`) via `POST /api/plan/upgrade-request` for sales follow-up.
- **Clause-level legal chunking:** New `app/utils/legal_chunker.py` replaces fixed 500-token chunking with structure-aware splitting that respects legal document anatomy (sections, articles, numbered clauses). Each chunk carries `section_path` (e.g. "Article I > Section 1.01 > (a)") and `clause_type` (definition/obligation/remedy/governing_law/recital/general) metadata for clause-type-aware retrieval. Migration `060_chunk_metadata_fts` adds the columns + a GIN-indexed `tsvector` column for PostgreSQL full-text search.
- **Hybrid retrieval (dense + FTS + RRF fusion):** `app/services/rag.py` now runs pgvector cosine similarity and PostgreSQL FTS in parallel, fusing results via Reciprocal Rank Fusion (0.6 dense / 0.4 FTS weight). FTS matches on exact identifiers (section numbers, defined terms, dates) that dense embeddings miss. Context headers now include `section_path`, `clause_type`, and keyword-match indicators.
- **Complexity-based LLM routing:** `classify_query_complexity()` in `app/services/llm_routing.py` pattern-matches user queries as simple (definitions, math, small talk) or complex (drafting, analysis, multi-hop). `_auto_tier()` in `chat.py` auto-upgrades complex queries to premium and auto-downgrades simple queries to standard — saves cost on lookups, improves quality on hard questions.
- **Free model speed vetting + auto-cooldown:** `record_model_latency()` tracks per-model time-to-first-token in a ring buffer. Models exceeding 15s latency or with >50% slow samples enter a 5-minute cooldown. Wired into `llm.py` `complete()` and `stream_complete()` for both success and error paths.

### Changed
- **Directory user sync licensing default:** new active Microsoft 365/Google directory users are now imported as standard licensed users by default, while existing users keep their current license flag so admins can exclude service accounts manually.
- **LiteLLM gateway timeout tuning:** `request_timeout` 60→25s, `num_retries` 1→0, `cooldown_time` 30→15s, `allowed_fails` 2→1, per-model `timeout` values (15s free, 20-30s paid). Slow free models fail over faster instead of holding connections.
- **Chat endpoint pre-work parallelized:** Both `/messages` and `/messages/stream` now run matter context, attachment context, memory context, LLM route resolution, and RAG cache check via `asyncio.gather` instead of sequentially. Pooled IOLTA bank accounts with three-way reconciliation and saved snapshots.
- **Production RLS runtime cutover readiness:** compose now supports `APP_DATABASE_URL` as a runtime override (backend + scheduler) so the app can connect as least-privilege `clarity_app` while migrations keep owner/DDL access, and hardening docs were updated with the production cutover checklist.
  - Migration `054_trust_ledger`: `trust_bank_accounts` (pooled, RLS), `trust_accounts.bank_account_id` FK, `trust_reconciliations` (persisted snapshots, RLS).
  - `routers/trust_accounting.py`: pooled bank-account CRUD (`/api/trust/bank-accounts`), pooled three-way reconcile (`bank == book == Σ client ledgers`) that persists a snapshot, reconciliation history, and per-client ledger statement (`/accounts/{id}/statement`) with CSV export. The existing per-account reconcile now also persists a snapshot. Trust models registered in `models/__init__.py`.
  - Fixed a pre-existing latent serialization bug (UUID→str) in trust response schemas that would have 500'd the 1314 create/transaction flows in production; added a shared coercion mixin.
  - **Firm branding + branded PDF statements:** migration `055_firm_branding` adds branding columns to `tenant_settings`; `GET/PUT /api/firm/branding` (`routers/firm.py`, PUT admin-only, firm name/address fall back to the tenant record); `services/trust_statement_pdf.py` renders a firm-branded ledger statement (logo embedded best-effort, skipped on fetch failure); `?format=pdf` on the statement endpoint returns it. Verified by `tests/test_firm_branding.py` + the full trust suite (16/16 passing on deploy).
  - **Branding UI:** `FirmBrandingPanel` in the Admin Settings tab to define all branding fields, and a "Download PDF" button on the trust account ledger view; `api.js` gains `getFirmBranding`/`updateFirmBranding`/`downloadTrustStatementPdf`.
- **Task 1314 — Trust Accounting Frontend:** Full UI for the existing trust accounting backend (9 endpoints in `trust_accounting.py`).
  - `frontend/src/api.js`: 9 wrapper functions (`createTrustAccount`, `listTrustAccounts`, `getTrustAccount`, `updateTrustAccount`, `closeTrustAccount`, `createTrustTransaction`, `listTrustTransactions`, `reconcileTrustAccount`, `getTrustReconciliation`).
  - `TrustAccountingPage` (`/trust`): portfolio view with total-balance summary, active/all filter, accounts table, and a "New Trust Account" modal with matter selector.
  - `TrustAccountDetail` (`/trust/:id`): balance ledger header (current/minimum balance, auto-replenish), transaction history table with deposits/disbursements/net summary, "Post Transaction" modal, inline edit form, and close-account action.
  - `TrustAccountReconcile`: reconciliation tab — bank balance / outstanding deposits & disbursements form, three-way reconciliation result with reconciled vs. out-of-balance banner, and last-reconciliation display on load.
  - Sidebar nav entry ("Trust Accounting", Landmark icon) and routes added to `App.jsx`.
  - `MatterDetailPage`: new "Trust Balance" card next to the Budget card, summing balances across the matter's trust accounts with a quick-link to the detail/reconciliation view (shows "No trust account" when none exist).
- **Task 1308b — Accounting Reports (Phase 1):** Three core billing reports with CSV export.
  - Backend (`routers/reports.py`): tenant-scoped `GET /api/reports/billing/realization` (per-matter billable hours/amount vs collected, realization %), `/billing/wip` (uninvoiced billable time + value), and `/billing/aging` (outstanding invoice balances bucketed 0–30 / 31–60 / 61–90 / 90+ days overdue). Each endpoint supports `?format=csv` for a downloadable CSV.
  - Frontend (`ReportsPage`): tab bar (Overview / Realization / WIP / A/R Aging) with sortable tables and per-report Download CSV buttons; new `api.js` report fetchers + CSV blob helpers.

### Changed
- **Task 1305 — Court-Rules Deadline Engine: dropped.** LawToolBox commercial-API path abandoned (no customer-demand pull); research artifacts retained under `docs/research/1305-*.md`. Revisit only on explicit litigation-firm demand.

### Fixed
- **Outlook mail sync used an invalid Graph path:** `cloud_sync.sync_outlook_mail` requested `/users/me/messages`, which Microsoft Graph rejects with `400 TargetIdShouldNotBeMeOrWhitespace` (literal `me` is not a valid `/users/{id}` segment) — so every Outlook mail sync failed. Now uses the `/me/messages` delegated shortcut, matching the working OneDrive call in the same module.
- **Google Directory sync surfaced a cryptic 400:** a personal (non-Workspace) Google account returns `400 Invalid Input` for the `my_customer` directory. `user_sync` now translates this into an actionable message (connect a Workspace admin account, or disable directory sync) instead of a raw status dump, mirroring the existing 403 handling.
- **Cloud workspace integrations:** Matter documents now expose live OneDrive/Google Drive links for cloud-backed files, matter cloud folders can be force-provisioned and synced from the matter Documents tab, cloud file lists are scoped to the provisioned matter folder, cloud content fetches preserve the requesting user's token context, email inbox scans dedupe provider messages, disabled/suspended directory users are skipped, and Google Directory sync no longer sends the invalid `isSuspended=false` query.

## [0.14.0] — 2026-06-06

### Sprint 12 — LiteLLM Gateway & AI Operations Control Plane

### Added
- **QuickBooks connection and invoice tax handling are production-ready:**
  successful Intuit authorization returns to the LawHand QuickBooks admin tab,
  A/R settings serialize database UUIDs correctly, QBO routes use an isolated
  edge-rate bucket, partial catalogue failures remain usable, and invoice
  sales-tax choices explicitly set each synced QBO line taxable or non-taxable.
- **Task 1206 — Provider Route Builder:** Full UI-driven AI routing console in the Platform admin. Operators can now manage provider API keys, fetch live model lists, and configure standard/premium routes with fallback chains — all without touching config files.
  - `llm_provider_keys` table (migration 045): Fernet-encrypted key vault with provider association and masked key hints
  - `GET/POST/DELETE /api/platform/llm/provider-keys`: key vault CRUD
  - `POST /api/platform/llm/provider-keys/sync-env`: imports `DEEPSEEK_API_KEY` and `OPENROUTER_API_KEY` from environment into the vault
  - `POST /api/platform/llm/provider-keys/{id}/fetch-models`: proxies provider `/models` endpoint via stored key
  - `GET/PUT /api/platform/llm/routes`: reads/writes route config and hot-reloads LiteLLM via `POST /config/update`
  - `POST /api/platform/llm/routes/test`: validates a route with a synthetic prompt, returns latency + first response tokens
  - Provider presets: OpenCode Zen, OpenCode Go, OpenRouter, DeepSeek, Anthropic
  - **AI Routing tab** in PlatformPage: KeyVaultPanel (key list, add form, sync-env button) + RouteCard (provider/key/model selection, fallback chain builder, route test)
  - **Live Model Catalog v2:** Derived capability tags (vision, tool_use, reasoning, research, rag, legal, large_context, structured_output) from provider model metadata; legal-specific heuristics flag models mentioning law/litigation/contract/compliance in descriptions; capability filter pills, colored badges, pricing/modality display per row; compact ApplyRouteDropdown replaces six inline routing buttons; show-all toggle removes 60-model cap

### Fixed
- **OAuth/integration stability:** Normalized Google userinfo scope aliases in integration health checks, made admin/user integration OAuth redirects use the configured API base URL, moved post-connect directory sync to a background task, restored missing manifest icons, and improved Google Directory 403 messaging.
- **Calendar/task sync:** Task calendar pushes now prefer the assigned/creator user's connected Google/Microsoft calendar token, remove old events on reassignment, and keep completed/uncompleted task changes reflected in external calendars.
- **Backend bug sweep:** Fixed missing billing admin import, plugin router logger crash, new-matter cloud provisioning imports, and made the backend test database URL configurable with `TEST_DATABASE_URL`.
- **Matter/admin/SMB stability sweep:** Fixed tenant settings UUID response
  validation, hardened matter list/detail serialization for legacy null fields,
  prevented cloud-folder share failures from aborting matter workflows, corrected
  doubled SMB API paths, normalized SMB admin response handling, and restored
  LiteLLM container healthchecks by including `curl`.
- **Matter create fallback:** Blank matter types now persist as `general` across
  matter create paths, with a database default to prevent `matter_type` NOT NULL
  crashes when the UI leaves the optional field empty.
- **Cloud folder provisioning repair:** Cloud integration retry now merges root
  and matter folder records across providers, backfills missing provider folders
  for existing matters, saves plugin-created matter folder IDs, avoids blocking
  matter creation when provider folder setup fails, and fixes cloud metadata
  sync upserts missing `tenant_id`.
- **Chat-system follow-up:** Cached resolved LLM routes with invalidation on
  tenant/platform LLM settings writes, fixed the missing `asyncio` import for
  parallel RAG, removed stray blank context separators, centralized nginx API
  streaming proxy directives, and added no-context regression coverage.
- **Task 1206 follow-up — AI Routing Console hardening:** Route saves now validate
  provider/key pairings, prune blank fallback rows, return 400s for malformed key
  IDs, register LiteLLM fallback mappings alongside model aliases, and handle
  LiteLLM-native Anthropic model prefixes/testing correctly. The Platform AI
  Routing tab now shows alias readiness, primary/fallback ordering, model-fetch
  state, validation feedback, and safer key deletion behavior.
- `admin.py`: add `from_attributes=True` to `TenantSettingsResponse.model_validate()` calls
- `platform.py`: guard `PLATFORM_SECRET_KEY` length < 32, fix `pg_total_relation_size(relid)` column reference
- `.env.hypervisor`: clear leftover placeholder instruction from `PLATFORM_SECRET_KEY`
- `.env.prod.example`: add `openssl rand -hex 32` generation comment for `PLATFORM_SECRET_KEY`

## [0.13.9] — 2026-06-06

### Fixed
- **BK13:** Chat refused to answer general legal questions without context — system prompt lacked explicit instruction to answer from general knowledge when FIRM CONTEXT is empty. Added rule: answer directly, tag all claims [model knowledge], never gate on context availability.
- **BK14:** Premium model 404 — all three route legs broken: (1) primary `openai/deepseek-chat` at `opencode.ai/go/v1` returns HTML 404 (wrong path); correct endpoint is `opencode.ai/zen/go/v1` with model `deepseek-v4-pro`. (2) standard `opencode.ai/go/v1` similarly broken; fixed to `opencode.ai/zen/v1` with `deepseek-v4-flash-free`. (3) `llama-4-maverick:free` removed from OpenRouter; replaced with `gemma-4-31b-it:free` (confirmed working) as premium OpenRouter fallback.

## [0.13.8] — 2026-06-06

### Fixed
- **BK10:** LiteLLM 401 on chat — `LITELLM_API_KEY` was missing from `.env.hypervisor` template. LiteLLM container defaulted to master key `sk-local-litellm` (from docker-compose default) while backend sent `"not-needed"` as auth. Fixed: added full LiteLLM section to `.env.hypervisor`; changed fallback in `LLMService` and `EmbeddingService` from `"not-needed"` to `"sk-local-litellm"` to match docker-compose default.
- **BK11:** OAuth 429 on back-to-back SSO logins — nginx `auth` zone (10r/m, burst=5) was applied to all `/api/auth/` paths. A complete OAuth flow uses 3+ requests, so 2 logins = 6 requests → burst exhausted. Fixed: added dedicated `oauth` zone (30r/m, burst=15) applied to `/api/auth/(google|microsoft)/` paths before the catch-all `auth` block.
- **BK12:** LiteLLM slow/failed responses — three root causes: (1) `clarity-standard` primary route pointed to `zen.opencode.ai` which is unreachable; switched to `DEEPSEEK_BASE_URL` (working OpenCode endpoint). (2) `deepseek/deepseek-r1:free` removed from OpenRouter → 404 on fallback; replaced with `qwen/qwen3-235b-a22b:free` and `meta-llama/llama-4-maverick:free`. (3) `clarity-embeddings` model registered against `OPENAI_API_KEY` which is unset → LiteLLM rejected model → 400 on every embed call; removed `clarity-embeddings` from config. `EmbeddingService` now only routes through LiteLLM when `LITELLM_EMBEDDING_MODEL` is explicitly set; otherwise falls back to direct provider (or disables embeddings gracefully). Reduced `request_timeout` 120→60s, `num_retries` 2→1, `cooldown_time` 60→30s.

## [0.13.7] — 2026-06-05

### Fixed
- **BK06:** TimeEntryResponse UUID validation crash (`billing_extended.py`) — `model_validate()` on ORM objects without `from_attributes=True` caused 500 on UUID→str coercion. Fixed all 10 calls (TimeEntry, Expense, InvoiceLineItem, Payment).
- **BK07:** Time Tracking now auto-selects matter from context — MatterDetailPage passes `?matter_id=` query param to TimeTrackingPage.
- **BK08:** Time Tracking matters list now loads independently and sorts by `updated_at` desc (recent activity).
- **BK09:** Hypervisor chat broken — `docker-compose.hypervisor.yml` was missing `litellm` + `litellm-postgres` services. Added both services, healthcheck dependency, and volume.
- **BK01:** Google Workspace scope audit mismatch (`drive.readonly` → `drive` in `admin.py:1141`). Added error logging to `refresh_google_token()`.
- **BK03:** Microsoft 365 scope audit mismatch (`Files.Read.All` → `Files.ReadWrite.All` in `admin.py:1130`).

### Audited (Non-Code)
- **BK04:** Mediation module audit complete — 97% production-ready. Backend has 24 firm + 12 portal endpoints (all real code). Frontend has 4 pages built. Gaps: missing alembic migration for 7 mediation tables, no sidebar nav link, ProposalStatusUpdate schema unused, no portal document delete.
- **BK05:** Trust & Estates module audit complete. Estate backend + frontend fully built. Trust Accounting backend fully built (9 endpoints, 2 models, migration 017) but has **zero frontend** — no pages, no API functions, no routes.

## [0.13.6] — 2026-06-05

### Fixed
- **Calendar page:** Single "Sync Calendar" button auto-detects which provider (Microsoft/Google) the user has configured, instead of showing both buttons unconditionally.
- **Estate creation:** Map human-readable estate types (Probate, Trust Administration, etc.) to snake_case backend values, fixing 422 validation errors on estate creation.
- **Time tracking:** Hide `hourly_rate` field from non-admin users in time entry form. Time entries now auto-use the user's `default_billing_rate` set by admin.
- **Admin users tab:** New inline-editable "Rate" column for setting each user's `default_billing_rate`.
- **Reports schema:** `budget_currency` made Optional with "USD" default to prevent potential schema validation 500s.

### Added
- **QuickBooks connection and invoice tax handling are production-ready:**
  successful Intuit authorization returns to the LawHand QuickBooks admin tab,
  A/R settings serialize database UUIDs correctly, QBO routes use an isolated
  edge-rate bucket, partial catalogue failures remain usable, and invoice
  sales-tax choices explicitly set each synced QBO line taxable or non-taxable.
- `GET /api/auth/calendar-providers` endpoint — returns which calendar providers the current user has configured tokens for.
- `default_billing_rate` field added to `UserPatchRequest` so admin can set rates via `PATCH /admin/users/{user_id}`.

### Changed
- `TimeEntryCreate.hourly_rate` field is now Optional, defaults to user's `default_billing_rate`.
- Time entry update endpoint recalculates amount correctly using Decimal precision.

## [0.13.5] — 2026-06-05

### AppShell Layout — Restore Consistent UI Across All Pages

### Added
- **QuickBooks connection and invoice tax handling are production-ready:**
  successful Intuit authorization returns to the LawHand QuickBooks admin tab,
  A/R settings serialize database UUIDs correctly, QBO routes use an isolated
  edge-rate bucket, partial catalogue failures remain usable, and invoice
  sales-tax choices explicitly set each synced QBO line taxable or non-taxable.
- **`AppShell.jsx`:** Shared layout component wrapping all authenticated pages with sidebar (always visible on desktop, hamburger overlay on mobile) + top header bar with prominent Admin button (Shield icon) for admin users.
- **`AppShellContext`:** React context for shared conversations/documents state across sidebar and ChatPage.
- **AdminPage collapsible tabs:** Toggle button to collapse/expand the admin tab bar; dropdown picker when collapsed for mobile-friendly tab switching.

### Changed
- **`App.jsx`:** Created `ShellRoute` wrapper that composes `ProtectedRoute` + `AppShell` for all 25+ authenticated routes. ChatPage, MatterPortfolio, Admin, Calendar, Communications, TimeTracking, Invoices, Reports, Templates, Billing, Contacts, Tasks, Intake, Plugins, Plugin, Profile, RenewalTracker, EstatePortfolio, EstateDetail, MediationPortfolio, MediationDetail, MCP, OnboardingWizard — all now share the AppShell layout.
- **`Sidebar.jsx`:** Logout icon changed from Settings (gear) to sign-out arrow for clarity. Already had mobile overlay support from task 1110.
- **`ChatHeader.jsx`:** Removed admin button from "More" dropdown (moved to AppShell header).
- **`ChatPage.jsx`:** Refactored to use shared `AppShellContext` for conversations/documents; sidebar rendering removed (handled by AppShell).
- **Multiple pages:** Removed redundant `min-h-screen bg-brand-bg` outer wrappers from MatterPortfolioPage, BillingPage, IntakePage, ReportsPage, ContactsPage, AdminPage.

### Fixed
- Regression where sidebar was only visible in ChatPage — now present across all authenticated pages.
- Admin button was hidden in ChatHeader dropdown menu — now prominently in AppShell top-right for all pages.
- AdminPage now has collapsible tab navigation for better mobile UX.

## [0.13.4] — 2026-06-05

### Cloud Drive Integration Fix — Google Drive + OneDrive Folder Creation

### Fixed
- **`integrations.py`:** Google admin OAuth scope changed from `drive.readonly` → `drive` — all write operations (folder creation, sharing) were returning HTTP 403 silently.
- **`integrations.py`:** Microsoft admin OAuth scope changed from `Files.Read.All` → `Files.ReadWrite.All` — OneDrive folder creation and sharing require write permissions.
- **`integrations.py`:** Added `_ensure_cloud_root()` call after admin re-auth so re-authorizing automatically backfills `claritylegal-records` root folder for tenants that completed onboarding with broken scopes.

### Added
- **QuickBooks connection and invoice tax handling are production-ready:**
  successful Intuit authorization returns to the LawHand QuickBooks admin tab,
  A/R settings serialize database UUIDs correctly, QBO routes use an isolated
  edge-rate bucket, partial catalogue failures remain usable, and invoice
  sales-tax choices explicitly set each synced QBO line taxable or non-taxable.
- **`integrations.py`:** `POST /api/integrations/cloud-init/retry` endpoint — admin-only, re-creates the `claritylegal-records` root folder and backfills all matters with `cloud_folder = null`, returning `{root, matters_initialized, matters_failed}`.
- **`cloud_init.py`:** `initialize_matter_folders()` now stores `url` for OneDrive (via `_get_onedrive_web_url`) and Google Drive (direct `drive.google.com/drive/folders/{id}` URL) so matter detail pages can link directly to folders.
- **`api.js`:** Added `retryCloudInit()` call for the new retry endpoint.
- **`IntegrationsPanel.jsx`:** "Retry cloud setup" button in overall status row — triggers backfill and shows count of matters initialized. Updated scope labels to reflect write scopes.
- **`MatterDetailPage.jsx`:** Cloud Storage row in Case Details card — shows "OneDrive" and/or "Google Drive" pill buttons linking to the matter's cloud folder when `matter.cloud_folder` is populated.

---

## [0.13.3] — 2026-06-05

### Task 1111 — Operator Console: Error Diagnostics & API Traffic Logs

### Fixed
- **`PlatformPage.jsx`:** Fixed `LIMIT is not defined` ReferenceError by capturing `limit` from API response and replacing all hardcoded `LIMIT` variable references.
- **`PlatformPage.jsx`:** Masked user emails in tenant detail view — now shows `full_name` (or "User XXXX…") and user ID prefix instead of exposing email addresses.

### Added
- **QuickBooks connection and invoice tax handling are production-ready:**
  successful Intuit authorization returns to the LawHand QuickBooks admin tab,
  A/R settings serialize database UUIDs correctly, QBO routes use an isolated
  edge-rate bucket, partial catalogue failures remain usable, and invoice
  sales-tax choices explicitly set each synced QBO line taxable or non-taxable.
- **Platform error log endpoints** in `platform.py`: `GET /api/platform/logs` (cross-tenant errors, paginated, filterable by tenant/severity/type/days/unresolved), `GET /api/platform/logs/summary` (by_severity, by_type, by_tenant top 20, daily trend), `GET /api/platform/logs/tenant/{id}`, `GET /api/platform/logs/tenant/{id}/summary`. All endpoints anonymize user_id.
- **`ApiAccessLog` model** (`api_access_log.py`) + migration 038: metadata-only request logging (tenant_id, endpoint, method, status_code, latency_ms, ip_address, user_agent_short).
- **`ApiAccessLogMiddleware`** (`middleware/access_log.py`): Logs every request after TenantMiddleware resolves tenant_id. Skips /health, /docs, /api/platform, /static.
- **Platform access log endpoints**: `GET /api/platform/access-logs` (paginated, filterable by tenant/endpoint/status/hours), `GET /api/platform/access-logs/summary` (total_requests, by_status, avg_latency, by_endpoint top 20, by_tenant top 20).
- **Operator Console Logs tab**: 3 sub-tabs — System Errors (summary cards + filterable/paginated table), Tenant Logs (per-tenant drill-down with selector), API Traffic (access log with summary statistics). Added FileText/Globe/AlertTriangle icons.

### Changed
- **`models/__init__.py`:** Registered `ApiAccessLog` model.
- **`main.py`:** Registered `ApiAccessLogMiddleware` in middleware stack (after TenantMiddleware, before RateLimitMiddleware).
- **`frontend/src/api.js`:** Added 6 platform log/access-log API functions.

---

## [0.13.2] — 2026-06-05

### Task 1109 — Calendar Sync Multi-User Fix

### Fixed
- **`token_vault.py`:** `get_fresh_user_token()` now logs a warning (user_id, provider, reason) on every silent `None` return instead of failing invisibly.
- **`calendar_sync.py`:** Replaced bare `RuntimeError` with `ValueError` carrying a user-readable message ("No Microsoft calendar token. Please reconnect your calendar in Settings.") for both missing-token and HTTP-failure cases.
- **`email_agent.py`:** Sync endpoint catches `ValueError` from calendar service and returns `HTTP 401` with the readable detail instead of crashing as a 500.
- **`CalendarPage.jsx` + `api.js`:** Added "Sync to Calendar" button with spinner; success/error banner displayed after each attempt so users know exactly what failed.

---

### Task 1110 — Mobile Responsive UI Overhaul

### Added
- **QuickBooks connection and invoice tax handling are production-ready:**
  successful Intuit authorization returns to the LawHand QuickBooks admin tab,
  A/R settings serialize database UUIDs correctly, QBO routes use an isolated
  edge-rate bucket, partial catalogue failures remain usable, and invoice
  sales-tax choices explicitly set each synced QBO line taxable or non-taxable.
- **Sidebar mobile overlay:** Hamburger button in `ChatHeader` (hidden on md+) opens sidebar as a slide-in overlay with backdrop on mobile. Sidebar uses `position: fixed md:relative` so it doesn't push content on desktop. State managed via `sidebarOpen` in `ChatPage`.
- **iOS safe-area bottom padding:** `ChatInput` uses `env(safe-area-inset-bottom)` so the input bar clears the home indicator on iPhone.

### Changed
- **`Sidebar.jsx`:** Accepts `isOpen`/`onClose` props. All nav clicks close the sidebar on mobile. Desktop layout unchanged (always visible, in-flow).
- **`ChatHeader.jsx`:** Hamburger button (md:hidden), model selector hidden on mobile (sm:hidden), public case law toggle hidden on small screens (md:hidden), gap reduced on small viewports.
- **`ChatInput.jsx`:** Horizontal padding responsive `px-4 md:px-8`.
- **`MatterDetailPage.jsx`:** Topbar and content padding responsive. Tab bar `overflow-x-auto` with `flex-shrink-0` on each tab. Edit form grids `grid-cols-1 sm:grid-cols-2`. Billing stats `grid-cols-1 sm:grid-cols-3`. Team add form `flex-col sm:flex-row`.
- **`AdminPage.jsx`:** Topbar `px-4 md:px-8`, content `px-4 md:px-8 py-8 md:py-12`, tab nav `overflow-x-auto` with `whitespace-nowrap` and smaller gap on mobile.
- **`MatterPortfolioPage.jsx`:** Topbar and content padding responsive `px-4 md:px-8`.
- **`index.css`:** Sidebar slide transition classes made unconditional (not wrapped in media query) so Tailwind `md:translate-x-0` override works correctly.

---

## [0.13.1] — 2026-06-04

### Sprint 10 Post-Review Bug Fixes

### Fixed
- **Agent sync broken (CRITICAL):** `share_id` sent in JSON body, but router expects query param — sync operations silently failed. Moved `share_id` to `params` in agent's `api_client.py`.
- **`_scan_share` wrong type annotation + duplicate args (CRITICAL):** Removed unused `scanner: SaaSClient` param, fixed call sites to pass correct args.
- **Content fetch tasks never succeed (CRITICAL):** `task_worker.py` called `read_content(session=None)` without SMB session. Moved `register_session` call to always execute before reading.
- **`tomli_w` inline import in `config.py` (CRITICAL):** Moved to top-level import with `ImportError` fallback; `save_config` falls back to JSON if TOML unavailable.
- **Pairing code registration — no tenant isolation:** `register_agent` query selects by pairing code across all tenants. Added optional `tenant_id` filter param.
- **Share CRUD — no tenant RLS validation:** `update_share`, `delete_share`, `list_shares` queried without tenant filter. Added `tenant_id` conditions to all DML.
- **Content fetch task — no tenant ownership check:** `get_content_status` endpoint polled any `task_id` without tenant validation. Added file ownership check.
- **Frontend field name mismatches:** `SmbAdminPage` used `agent.name`/`agent.version` (should be `agent_name`/`agent_version`) and `MatterSmbSharesTab` used non-existent `s.name`/`s.share_name`/`s.server_host` (should be `display_name`/`share_path`).
- **Sync file count cap race condition:** Added `db.flush()` before count query to see pending inserts.

### Changed
- **SmbShare model:** Added `ForeignKey("tenants.id", ondelete="CASCADE")` on `tenant_id` and `Index("ix_smb_shares_tenant_id", "tenant_id")`.
- **SMB auth rate limiting:** Added Redis-based rate limiter (30 req/60s) on `X-Agent-API-Key` endpoint.
- **RAG integration:** `rag.py` now uses `SmbService` directly instead of duplicating through `smb_search.py` module.
- **Content fetch polling:** Added `poll_content_result()` with exponential backoff (1s → 8s) to `SmbService`, replacing fixed 2s polling in `smb_search.py`.

### Added
- **QuickBooks connection and invoice tax handling are production-ready:**
  successful Intuit authorization returns to the LawHand QuickBooks admin tab,
  A/R settings serialize database UUIDs correctly, QBO routes use an isolated
  edge-rate bucket, partial catalogue failures remain usable, and invoice
  sales-tax choices explicitly set each synced QBO line taxable or non-taxable.
- **Per-share file extension filtering:** `SmbScanner.scan_share()` accepts `file_extensions` parameter, propagated from share config through `_scan_share()`.
- **`build_smb_context()`** on `SmbService` (static method) — consolidates context formatting from `smb_search.py`.
- **JSON config fallback** in agent `config.py` — `load()` supports both TOML and JSON formats.

### Design — Legal MCP Database & CourtListener Ingest Pipeline

- **Architecture Design Doc**: `docs/legal_rag.md` — full schema, ingest pipeline, embedding migration, MCP tools, metering, deployment architecture. Implementation tabled; only a minimal 2-tool MCP REST endpoint exists in `backend/app/routers/mcp.py`.

---

## [0.12.0] — 2026-06-04

### Sprint 10 — SMB File Share Relay Agent

### Added
- **QuickBooks connection and invoice tax handling are production-ready:**
  successful Intuit authorization returns to the LawHand QuickBooks admin tab,
  A/R settings serialize database UUIDs correctly, QBO routes use an isolated
  edge-rate bucket, partial catalogue failures remain usable, and invoice
  sales-tax choices explicitly set each synced QBO line taxable or non-taxable.
- **SMB Agent Models**: `SmbAgent`, `SmbShare`, `SmbFileIndex`, `SmbAccessLog`, `MatterSmbShare` SQLAlchemy models with pgvector-style tsvector/GIN full-text search, RLS policies, and migration 036
- `smb_folders` JSONB column on `matters` table (parallel to `cloud_folder`)
- **Migration 036**: Five new tables (`smb_agents`, `smb_shares`, `smb_file_index`, `smb_access_log`, `matter_smb_shares`) with RLS, GIN index on search_vector, tsvector auto-update trigger, and `smb_folders` column on matters
- **SMB API Router** (`/api/v1/smb`): 19 endpoints — agent registration, pairing, heartbeat, file sync, content fetch task queue, user search, admin stats, matter binding
- **SMB Auth Middleware**: API key authentication for agent endpoints (SHA-256 hashed keys, separate from JWT)
- **SmbService**: Pairing code generation, agent registration, heartbeat, file sync (upsert with ON CONFLICT), content fetch task dispatch via Redis, full-text search, share CRUD, matter binding CRUD
- **SmbSearchService**: tsvector full-text search with `plainto_tsquery`, matter-scoped search via `matter_smb_shares` join, content fetch orchestration, `build_smb_context()` for LLM context injection
- **RetrievalPlanner**: Added `smb_enabled` parameter to planner, `smb` source in PROVIDER_SOURCES, updated prompt to include on-prem file share as search source
- **RAG Integration**: `hybrid_rag_query()` now checks for active SMB agents and runs tsvector search alongside pgvector and cloud search, merges results into unified context
- **Admin Endpoints**: `GET /admin/smb/status` (agent/share/file counts, last activity) and `GET /admin/smb/activity` (access log)
- **Config**: `SMB_ENABLED`, `SMB_PAIRING_CODE_TTL_MIN`, `SMB_MAX_FILE_INDEX_PER_SHARE`, `SMB_SNIPPET_MAX_CHARS`, `SMB_TASK_POLL_INTERVAL`, `SMB_CONTENT_FETCH_TIMEOUT`
- **Relay Agent Package** (`agent/clarity_agent/`): pip-installable agent with SMB scanner (3-tier change detection), file reader (PDF/DOCX/text extraction), SaaS API client, task worker, heartbeat, local SQLite ledger, and CLI (`clarity-agent register/start/scan/status`)
- **Scheduler Integration**: `smb-heartbeat` agent added to AGENT_REGISTRY — cron job every 15 min pauses agents with no heartbeat for 15+ minutes
- **Frontend: SmbAdminPage** — 4-panel admin page (Status, Agents, Shares, Activity) with pairing code generation, agent pause/resume/revoke, share management, and access log viewer
- **Frontend: MatterSmbSharesTab** — "File Shares" tab on matter detail page for binding SMB shares/folders to matters with add/remove/auto-scan
- **Frontend: API functions** — 9 SMB admin functions + 4 matter binding functions added to api.js

### Changed
- Bug fixes in `services/smb.py` — proper UUID conversion via `_uuid()` helper, correct RLS context in pairing code generation and agent registration, cap-aware sync count, null-safe Redis access
- Bug fixes in `routers/smb.py` — correct FastAPI Body defaults, null-safe Redis via `request.app.state.redis`
- `RetrievalPlanner` — added `smb_enabled` parameter, `smb` source in PROVIDER_SOURCES, updated prompt
- `hybrid_rag_query()` — now checks for active SMB agents, runs tsvector search in parallel with pgvector/cloud, accepts `matter_id` for matter-scoped SMB search
- `AdminPage.jsx` — added "File Shares" tab with SmbAdminPage component
- `MatterDetailPage.jsx` — added "File Shares" tab with MatterSmbSharesTab component

## [0.11.0] — 2026-06-04

### Sprint 9 — Plugin Platform & Matter Workflow Framework

### Added
- **QuickBooks connection and invoice tax handling are production-ready:**
  successful Intuit authorization returns to the LawHand QuickBooks admin tab,
  A/R settings serialize database UUIDs correctly, QBO routes use an isolated
  edge-rate bucket, partial catalogue failures remain usable, and invoice
  sales-tax choices explicitly set each synced QBO line taxable or non-taxable.
- Canonical plugin catalog manifest with display metadata, skill IDs, workflow routes, matter type mappings, required/optional integrations (`backend/app/services/plugins/manifest.py`)
- `TenantPluginEntitlement` model: tenant-level plugin purchase/trial/locked state, decoupled from practice profile
- `TenantPluginSetup` model: structured per-plugin configuration with typed schemas (jurisdictions, escalation rules, approval thresholds, templates, source folders, calendars, house style)
- Migration 034: `tenant_plugin_entitlements` table + `matters.primary_plugin` + `matters.plugin_workflow_state`
- Migration 035: `tenant_plugin_setups` table with `setup_data` JSONB + `needs_setup` tracking
- Plugin setup health endpoint (`GET /plugins/{plugin}/setup`) and upsert (`PUT /plugins/{plugin}/setup`)
- Plugin entitlement endpoint (`PUT /plugins/{plugin}/entitlement`) for admin-controlled purchase/trial state
- `GET /api/plugins` now returns canonical catalog with tenant entitlement, profile, and setup status merged per plugin
- PluginPage: setup health badges, capability checks (integrations, credentials), configuration tab with structured fields
- PluginsPage: category grouping, entitlement badges (Included/Trial/Purchase/Setup Required), matter workflow detail cards

### Changed
- Plugin manifest is now the single source of truth — frontend `PLUGIN_META` removed entirely; all plugin metadata derived from backend catalog API
- PluginsPage redesigned with state tabs (Purchased / Trials / Available / Setup Required / Locked) with per-tab counts
- Sidebar consolidated: plugin-specific workflow links replaced with single unified "Matters" link
- `POST /plugins/{plugin}/cold-start` now initializes structured `TenantPluginSetup` row alongside `PracticeProfile`
- `PluginExecutor` enriched with cloud search context via `RetrievalPlanner` + `CloudSearchService` + `build_cloud_context`
- Matters V2 router gained `primary_plugin` and `plugin_workflow_state` in create/update/list/detail
- `MatterContextService` enriched with plugin workflow state for conversation context
- `NewMatterModal` suggests plugins based on practice area, displays plugin assignment field
- `MatterDetailPage` shows assigned plugin + workflow state badge

### Fixed
- Plugin cold-start interview: fixed 422 from mismatched `{message, step}` → `{input_text, context}` request format
- Plugin cold-start interview: backend now returns `step`, `profile_complete`, `profile` alongside LLM result
- Plugin cold-start interview: frontend now reads `res.memo` (SkillResponse field) instead of `res.message`
- Cloud search: search_index and status DB queries wrapped in try/except to return degraded results instead of 500
- Cloud metadata sync: backend returns `total` + `duration_seconds` for frontend result panel
- Microsoft integration: `offline_access` scope now persisted when MS omits it from token response but refresh_token is present
- Google Workspace: added `openid email profile` to admin consent scopes; `last_sync_error` surfaced in audit UI
- Estate portfolio: migration 030 DDL manually applied on hypervisor (was stamped but never ran — missing columns + 7 sub-tables)

## [0.10.0] — 2026-06-03

### Sprint 8 — Tenant Onboarding & Integration Hub

### Added
- **QuickBooks connection and invoice tax handling are production-ready:**
  successful Intuit authorization returns to the LawHand QuickBooks admin tab,
  A/R settings serialize database UUIDs correctly, QBO routes use an isolated
  edge-rate bucket, partial catalogue failures remain usable, and invoice
  sales-tax choices explicitly set each synced QBO line taxable or non-taxable.

#### PR #38 — Mediation Platform Module
- `MediationCase` model expanded: case_name, party_a/b, dispute_type, mediation_stage, mediator, attorney, claim_value, scheduled_session, confidentiality_signed
- New models: `MediationParty`, `MediationInvite`, `MediationAsset`, `MediationDocument`, `MediationProposal` with per-table RLS
- Firm router `/api/plugins/mediation/*`: case CRUD + stats, session log, parties + portal invites, asset schedule with attorney approve → send-to-opposing workflow, document vault upload/download, settlement proposals
- Portal router `/api/portal/mediation/*`: invite acceptance (magic link + JWT cookie), case view, asset submission/decision, document upload/download, proposal exchange
- Portal token helpers (`portal_token.py`), shared response builders (`mediation_service.py`), invite email (`email.py`)
- Migration 031 with 5 new tables + expanded mediation_cases
- Backend tests: 7/7 pass (CRUD, sessions, invites, approval workflow, visibility scoping, invite acceptance, proposal chains, tenant isolation)
- Frontend: `MediationPortfolioPage` with create modal, `MediationDetailPage` with 6 tabs (Overview, Parties, Assets, Documents, Proposals, Sessions), `MediationSubTable` generic CRUD component
- Portal frontend: `PortalAcceptPage` (magic link acceptance), `PortalCasePage` (4 tabs: My Assets, Shared With Me, Documents, Proposals)
- Sidebar "Mediation" nav entry, `App.jsx` portal routes outside ProtectedRoute

#### Task 801 — Admin Onboarding Wizard
- 5-step guided wizard after first admin login: Welcome → Connect Integrations → Sync Users → Review → Complete
- `GET/POST /api/admin/onboarding/status|complete|skip|step/{step}` endpoints
- Post-connect hooks in integration callbacks: auto-store granted_by_user_id + service_account_email, auto-trigger user sync
- `OnboardingWizard.jsx` with step indicator, skip option, progress persistence
- AuthCallback redirects new admins to /onboarding if not completed
- Migration 027: +onboarding_completed, onboarding_step, cloud_root_folder, service_account_email, license_active, granted_by_user_id, customer LLM fields

#### Task 802 — License/Seat Management
- `GET /api/admin/licensing` — per-user license status, seat counts, PAYG usage
- `PUT /api/admin/users/{id}/license` — toggle per-user license_active
- `PUT /api/admin/licensing/seats` — flat seat count with over-limit warning
- `LicensingPanel.jsx` — seat slider, usage progress bar, per-user toggle switches

#### Task 803 — Service Account Safety
- Integration callbacks store granted_by_user_id + service_account_email
- `GET /api/admin/integrations/health` — grantor info, deactivation warnings, expiry alerts
- Deactivate user now checks for service account grants; requires ?force=true

#### Task 804 — Cloud Folder Init & Matter Auto-Folders
- `cloud_init.py` — creates "claritylegal-records" root folder in OneDrive/Google Drive
- Auto-creates per-matter subfolders: emails/, documents/, pleadings/, correspondence/, billing/
- Hooked into matter creation (non-fatal) and onboarding completion

#### Task 805 — Customer LLM Configuration
- `POST/DELETE /api/admin/customer-llm/configure` — encrypted API key storage
- AdminPage Settings: Customer LLM section with toggle, provider, key, endpoint

#### Task 806 — Permission Audit → Integrations Hub
- `GET /api/admin/permissions` — granted vs required scope comparison per provider, +synced user count, +last-sync freshness (user_count, last_sync_at, last_sync_total, last_sync_status)
- `IntegrationsPanel.jsx` (renamed from PermissionsAudit): provider cards with scope checkmarks, synced user count display, last-sync timestamp, "Sync now" button
- Admin "Integrations" tab (renamed from "Permissions")
- Migration 030: `last_user_sync_*` columns on `tenant_credentials` for sync-run bookkeeping
- Daily directory user sync: new `user-sync` scheduler job (2:00 AM ET), manually triggerable via `/scheduler/agents/user-sync/run`
- `UserSyncService` persists last-sync state per credential and creates synced users on the free tier (`license_active=False`)

### Changed
- `Tenant` model: +onboarding_completed, onboarding_step, cloud_root_folder (JSON), service_account_email
- `TenantCredential` model: +granted_by_user_id (FK users.id), +last_user_sync_* columns (migration 030)
- `User` model: +license_active (bool, default true)
- `TenantSettings` model: +use_customer_llm, customer_llm_provider, customer_llm_config (JSON)
- AdminPage: +Licensing tab, +Integrations tab (was "Permissions")

### Fixed
- Cloud search status/metadata endpoints: error handling for missing/broken `cloud_metadata_index` table (returns degraded status instead of 500)
- Cloud metadata sync endpoint: added `total` and `duration_seconds` fields so the frontend "Sync Metadata Now" result panel renders correctly
- Microsoft integration: `offline_access` scope not persisted when MS omits it from token response despite granting it (refresh_token presence now forces scope inclusion)
- Google Workspace integration: added `openid email profile` to admin consent scopes so `id_token` is returned (needed for service account email extraction and proper scope audit)
- Google Workspace sync: `last_sync_error` now surfaced in permissions audit response and displayed in the Integrations panel

## [0.9.0] — 2026-06-03

### Added — Prompt Management System & Missing Skill Prompts

- `PromptOverride` model + migration 021: per-tenant prompt customization with RLS
- `PromptResolver` service: cache-aware resolution (tenant override → code default → generic fallback)
- Redis prompt caching with invalidation on override save/reset
- Admin prompt CRUD routes: list tree, get detail, upsert, reset, test-run prompts
- Admin console UI: "Prompts" tab with skill tree, code editor, variable reference, test panel
- 11 new prompt templates for previously missing skills (portfolio-status, legal-hold, renewal-tracker, reg-gap-analysis, diligence-review, closing-checklist, hire-review, marketing-claims, CND-triage, impact-assessment, vendor-ai-review, policy-diff, NPRM-comment)
- `ALL_DEFAULT_PROMPTS` dict wiring all 44 skill entries across 9 plugins
- Fixed: missing `run_conflict_check` import in plugins.py

## [0.8.0] — 2026-06-03

### Sprint 7 — Calendar, Communications & Matter Operations

### Added
- **QuickBooks connection and invoice tax handling are production-ready:**
  successful Intuit authorization returns to the LawHand QuickBooks admin tab,
  A/R settings serialize database UUIDs correctly, QBO routes use an isolated
  edge-rate bucket, partial catalogue failures remain usable, and invoice
  sales-tax choices explicitly set each synced QBO line taxable or non-taxable.

#### Task 801 — Deadline Calendar
- `GET /api/calendar/events` endpoint aggregating task due_dates, matter key_dates, and renewal dates with `?start=&end=` range filter
- CalendarPage.jsx — month/week calendar view with color-coded events by type; click to navigate to matter/task detail

#### Task 802 — Communications Router
- Full CRUD router for `communication_logs` at `/api/communications` with filters by matter_id, contact_id, channel, date range
- CommunicationsPage.jsx — log list with filters and quick-log form (channel, subject, body, matter link)

#### Task 803 — Lead-to-Matter Conversion
- `POST /api/intake/leads/{id}/convert` — creates a Matter from a qualified Lead; sets `client_contact_id` from lead's contact; marks lead `status = matter_opened`; returns `{matter_id, matter_name, lead_id, status}`
- `LeadConvertRequest` schema (matter_name, matter_type, role, jurisdiction, counterparty)
- IntakePage: "Convert to Matter" button on engaged leads; modal with all required Matter fields; navigates to new matter on success
- `convertLead(id, data)` API helper in `frontend/src/api.js`

#### Task 804 — Matter Budget Tracking
- Migration 024: added `budget_amount` (Numeric 12,2) and `budget_currency` (String 3, default "USD") to `matters` table
- `GET /api/reports/matters/{id}/budget` — sums billable time entries (hours + amount) vs budget; returns utilization percentage
- `MatterBudgetReport` Pydantic schema (matter_id, matter_name, budget_amount, budget_currency, total_hours, total_billed, utilization_pct)
- `MatterResponse` and `MatterUpdate` schemas now include `budget_amount` and `budget_currency` fields
- MatterDetailPage.jsx: budget utilization badge in header (progress bar with color thresholds: green ≤70%, amber ≤90%, red >90%); budget amount and currency fields in edit form
- `getMatterBudget(matterId)` API helper in `frontend/src/api.js`

#### Task 805 — Document Templates
- `DocumentTemplate` model: title, body (Text with `{{variable}}` placeholders), category (engagement_letter/retainer/NDA/motion/other), is_active
- Migration 025: `document_templates` table with RLS (ENABLE + FORCE ROW LEVEL SECURITY, tenant_isolation policy)
- `GET /api/templates` — list active templates sorted by created_at desc
- `POST /api/templates` — create template with category validation (422 on invalid category)
- `GET/PATCH/DELETE /api/templates/{id}` — detail, update (validates category), delete
- `POST /api/templates/{id}/render` — `{{variable}}` regex substitution; optional `matter_id` creates a `MatterDocument` with `document_category="generated"`; verifies matter belongs to tenant (404 if not found)
- `render_template(template_body, variables)` — pure function re.sub replacer; unused variables preserved as-is (`{{name}}`)
- TemplatesPage.jsx — template library grid with category color badges, active/inactive toggle; create/edit modal (title, body textarea, category select); generate modal with auto-detected variable fields, preview render, option to save to a matter
- Sidebar nav link to `/templates` (FileSignature icon)
- Route `/templates` in App.jsx behind ProtectedRoute
- API helpers: `getTemplates`, `createTemplate`, `getTemplate`, `updateTemplate`, `deleteTemplate`, `renderTemplate`

### Changed
- Main.py: registered 7 new routers (contacts, tasks, communications, intake, matter_parties, matter_documents, reports, calendar, document_templates)
- Sidebar.jsx: added Reports, Calendar, Communications, Templates nav items
- App.jsx: added /reports, /calendar, /communications, /templates routes

### Fixed
- Recurring fix: router imports in main.py kept in sync after each task (formatter strips unused, added manually)
- RLS policies use correct `app.current_tenant_id` setting name with `, true` fallback
- Path traversal protection in matter document upload (os.path.basename)
- conflict_status uses "conflict-found" not "flagged" (standardized enum)
- Task reminders deduplicated via reminder_sent_at column (23h cooldown)

### Tests
- All endpoints verified with tenant isolation checks via spec/quality review cycle
- Frontend build succeeds for all 5 tasks

---

## [0.7.0] — 2026-06-03

### Sprint 6 — Matters, Document Management & Firm Reporting

### Added
- **QuickBooks connection and invoice tax handling are production-ready:**
  successful Intuit authorization returns to the LawHand QuickBooks admin tab,
  A/R settings serialize database UUIDs correctly, QBO routes use an isolated
  edge-rate bucket, partial catalogue failures remain usable, and invoice
  sales-tax choices explicitly set each synced QBO line taxable or non-taxable.

#### MatterParty — Multi-Party Matter Support (701)
- `MatterParty` model — M:N link between matters and contacts with role (client/opposing_party/counsel/witness/expert/other), is_primary flag, notes
- Migration 021: `matter_parties` table with RLS tenant isolation
- `GET/POST /api/matters/{id}/parties` — list and add parties to a matter
- `PATCH/DELETE /api/matters/{id}/parties/{party_id}` — update role/notes, remove party
- Frontend: Parties tab in MatterDetailPage with role badges, add/remove form, contact dropdown

#### MatterDocument — Case File Attachments (702)
- `MatterDocument` model — file attachments linked to matters (separate from RAG document store)
- Migration 022: `matter_documents` table with RLS tenant isolation
- `POST /api/matters/{id}/documents/upload` — multipart file upload (50MB limit) with path traversal protection
- `GET/PATCH/DELETE /api/matters/{id}/documents/{doc_id}` — list, update metadata, delete
- `GET /api/matters/{id}/documents/{doc_id}/download` — FileResponse download
- Frontend: MatterDocumentsTab component with upload form, category badges (pleading/contract/evidence/correspondence/other), inline edit, download

#### Conflict Check Service (703)
- `backend/app/services/conflict_check.py` — shared conflict check service extracted from contacts router
- Auto-runs on matter create: sets `conflicts_status` ("not-run"/"clear"/"conflict-found") automatically
- `POST /api/plugins/litigation/matters/{id}/conflict-check` — manual re-run endpoint
- Frontend: conflicts_status badge + Re-run Check button in MatterDetailPage with match list display

#### Task Email Reminders (704)
- `send_task_reminder()` method in email service with HTML + plaintext body
- `_check_task_reminders` hourly APScheduler job — queries tasks due within 24h, sends per-assignee reminders
- Migration 023: `reminder_sent_at` column on tasks prevents duplicate hourly sends (23h cooldown)
- `POST /api/tasks/{task_id}/remind` — manual reminder trigger (202 Accepted)
- Frontend: Bell icon remind button per task row with inline "Sent!" confirmation

#### Firm Reporting (705)
- `GET /api/reports/matters` — matter counts by status, matter_type, risk_level
- `GET /api/reports/intake` — lead counts by status + conversion rate (matter_opened / total)
- `GET /api/reports/overdue-tasks` — overdue tasks with matter context
- `GET /api/reports/bundle` — all three reports in one request
- Frontend: `/reports` route, Sidebar nav link, ReportsPage with 3 summary cards

### Changed
- `contacts.py` conflict_check endpoint now delegates to shared `conflict_check` service (behavior unchanged)
- MatterDetailPage extended with Parties tab, Documents tab, conflict status badge

### Fixed
- Missing `matter_parties_router`, `matter_documents_router`, `reports_router` imports in `main.py`
- RLS policy in migration 021 corrected to use `app.current_tenant_id` (matching the app's `set_tenant_context`)
- Path traversal vulnerability in document upload fixed with `os.path.basename(filename)`
- `conflicts_status` value standardized to "conflict-found" (was "flagged" in initial implementation)

### Tests
- Integration: all new endpoints verified with tenant isolation checks via spec/quality review cycle

---

## [0.6.0] — 2026-06-03

### Added — CRM, Contacts, Tasks & Client Communication

#### Contact/Client Data Model
- `Contact` model — person or organization with entity_type, contact_type (client/opposing_party/witness/expert/vendor/referral/other), email, phone, address (JSON), tags, soft-delete
- `Lead` model — intake pipeline with status lifecycle (new→contacted→qualified→conflict_checked→engaged→matter_opened|declined), source, conflict_check_status, estimated_value
- Migration 018: `contacts` table with RLS; nullable `client_contact_id` FK added to `matters`
- `GET /api/contacts` — list with search (`q=`), contact_type/entity_type filters
- `POST /api/contacts` — create person or organization
- `GET/PATCH /api/contacts/{id}` — detail + inline edit
- `DELETE /api/contacts/{id}` — soft-delete (sets is_active=False)
- `GET /api/contacts/{id}/matters` — linked matters via client_contact_id
- `GET /api/contacts/{id}/communications` — communication history for contact
- `POST /api/contacts/conflict-check` — fuzzy name/email match against contacts + matter counterparty strings; returns clear/matches with matter linkage
- QBO sync: uses `Contact.display_name` when matter has `client_contact_id` set (fallback to `counterparty` string)

#### Task & Deadline Management
- `Task` model — task_type (deadline/hearing/filing/deposition/call/follow_up/review/general), status (pending/in_progress/completed/cancelled), priority (low/medium/high/urgent), due_date, matter_id, contact_id, assigned_to, source (manual/email_agent/calendar_sync)
- Migration 019: `tasks` table with RLS + performance indexes
- `GET /api/tasks` — list with filters: matter_id, contact_id, assigned_to, status, priority, task_type, due_before/after
- `POST /api/tasks` — create task
- `PATCH /api/tasks/{id}` — update; auto-sets `completed_at` on status→completed
- `GET /api/tasks/overdue` — tasks past due date, not completed/cancelled
- `GET /api/tasks/upcoming?days=7` — tasks due in next N days

#### Communication Log
- `CommunicationLog` model — direction (inbound/outbound), channel (email/call/letter/meeting/portal/sms/other), subject, summary, matter_id, contact_id, occurred_at, external_ref
- Migration 020: `communication_logs` + `leads` tables with RLS
- `GET /api/communications` — list with filters: matter_id, contact_id, channel, direction, occurred_after
- `POST /api/communications` — log entry
- `PATCH /api/communications/{id}` — update

#### Intake Pipeline
- `GET /api/intake` — list leads (filter by status, assigned_to, practice_area)
- `POST /api/intake` — create lead with inline Contact creation if needed
- `PATCH /api/intake/{id}` — update status/notes
- `POST /api/intake/{id}/convert` — convert to Matter (creates Matter with client_contact_id, marks lead as matter_opened)

#### Email Agent Integration
- Auto-create `CommunicationLog` (inbound/email/received) for each classified email
- Auto-create `Task` (type=deadline, source=email_agent) when classification returns `deadline_mentioned`
- Date parsing via `python-dateutil` with fuzzy parsing

#### Frontend
- `ContactsPage` (`/contacts`) — list/search contacts with type/entity filters, quick-create modal
- `ContactDetailPage` (`/contacts/:id`) — tabs: Profile | Matters | Communications | Tasks; inline edit
- `ContactPicker` component — search-as-you-type autocomplete for linking contacts in forms
- `TasksPage` (`/tasks`) — grouped sections: Overdue / Due Today / Upcoming / No Due Date / Completed; create modal with ContactPicker; filter by status/priority/type
- `IntakePage` (`/intake`) — pipeline view with stage counters; advance/convert actions; convert-to-matter modal
- Sidebar: added Contacts, Tasks, Intake nav links

### Changed
- `backend/app/models/plugin.py` — added nullable `client_contact_id` FK to `Matter`
- `backend/app/services/qbo_sync.py` — prefer Contact name over counterparty string when available
- `backend/app/services/email_agent.py` — auto-log communications and tasks on email classification
- `backend/requirements.txt` — added `python-dateutil==2.9.0`
- `frontend/src/api.js` — added 20 new API functions for contacts, tasks, communications, intake

## [0.5.2] — 2026-06-02

### Fixed — Security & Bug Fixes

#### Critical Bug Fixes
- `app/services/qbo_sync.py` — SQL injection in QBO query strings: escape single quotes in display_name, item_name, and customer_name via `_safe_qbo_string()` helper
- `app/routers/billing_extended.py` — Added `set_tenant_context()` to all 4 list endpoints (time entries, expenses, invoices, payments) for RLS correctness
- `app/routers/billing_extended.py` — `delete_time_entry` now hard-deletes unbilled entries (was incorrectly soft-deleting with `status=written_off` while returning 204)
- `app/routers/qbo.py` — QBO OAuth fallback state dicts now evict expired entries on each write to prevent unbounded memory growth
- `app/services/cache.py` — Fixed `invalidate_user_cache` key-pattern to match actual key format (`{type}:{tenant_id}|{user_id}|{suffix}`)
- `app/services/pii_detection.py` — Tightened `driver_license` regex (requires 9+ digits after letters) and `bank_account` regex (lookahead/behind to reduce false positives on phone numbers)

#### Sprint 2 Audit Fixes
- `app/routers/billing_extended.py` — Added missing `import asyncio` and `async_session_maker` (QBO sync fire-and-forget was broken at runtime)
- `app/services/rag.py` — Fixed SQL injection in pgvector queries: embedding vectors now passed as bind parameters instead of f-string interpolation
- `app/routers/billing_extended.py` — Added `logger.warning()` to silent `except Exception: pass` blocks in QBO sync tasks
- `app/routers/admin.py` — Added missing error schema imports (`ErrorLogResponse`, `SystemErrorLogsResponse`, `ErrorResolveRequest`, etc.)
- `app/routers/chat.py` — Wrapped `_trigger_auto_memory_generation` in try/except to prevent memory failures from breaking chat responses

## [0.5.1] — 2026-06-02

### Added — Trust Accounting + PDF Export

#### Trust Accounting CRUD
- `TrustAccount` CRUD endpoints (`POST/GET/PATCH /api/trust/accounts`, `POST /api/trust/accounts/{id}/close`)
- `TrustTransaction` endpoints (`POST/GET /api/trust/transactions`) with balance tracking
- Three-way IOLTA reconciliation (`POST /api/trust/accounts/{id}/reconcile`)
  - Bank balance vs trust liability vs unallocated funds
  - Auto-marks transactions as reconciled when balanced
  - Outstanding deposits/disbursements tracking
  - Reconciliation status endpoint (`GET /api/trust/accounts/{id}/reconciliation`)
- `TrustAccountCreate/Update/Response`, `TrustTransactionCreate/Response` Pydantic schemas
- `ReconciliationRequest/Response` with reconciling items detail
- `backend/app/routers/trust_accounting.py` — 8 endpoints
- `backend/app/schemas/trust_accounting.py` — 11 schemas

#### PDF Invoice Export
- `InvoicePDFService` — professional legal invoice PDF generation via ReportLab
- Clean letterhead layout: firm name, invoice details grid, line items table with totals, payments section, balance due
- `POST /api/billing/invoices/{id}/export` format=pdf returns `application/pdf`

### Changed
- `app/routers/__init__.py` — added trust_accounting_router
- `app/services/__init__.py` — added generate_invoice_pdf
- `app/main.py` — wired trust_accounting_router
- `requirements.txt` — added reportlab==4.2.5

## [0.5.0] — 2026-06-01

### Added — Billing & QBO Integration Foundation

#### Core Billing Models
- `TimeEntry` — billable time with matter link, UTBMS task/activity codes, status lifecycle (draft→billed→written_off)
- `Expense` — disbursements with category tracking (filing fees, court reporter, expert witness, etc.)
- `Invoice` — auto-numbered (INV-YYYY-XXXXXX), Stripe payment link, QBO sync status, LEDES export tracking
- `InvoiceLineItem` — polymorphic source tracking (time_entry/expense/flat_fee/adjustment/discount)
- `Payment` — multi-method (stripe/check/wire/trust_account/cash/other) with QBO sync
- 23 Pydantic v2 schemas in `schemas/billing.py`
- Migration 015: billing tables with RLS policies

#### QBO Integration
- `QBOIntegration` model — per-tenant QBO OAuth2 tokens (Fernet AES-256-GCM encryption, same pattern as TenantCredential)
- Full OAuth2 flow: `GET /api/integrations/qbo/connect` → callback → token exchange + encrypted storage
- Token refresh with refresh_token grant, sandbox/production toggle
- State-based CSRF protection with Redis fallback
- `QBOSyncService` — Matter→QBO Customer, TimeEntry→TimeActivity, Invoice→Invoice, Payment→Payment sync
- Migration 016: qbo_integrations table with RLS

#### Time Tracking & Billing CRUD
- TimeEntry CRUD: create, list (by matter/status/unbilled), detail, edit, soft-delete
- Expense CRUD: create, list (by matter/category/unbilled), detail, edit, delete
- Invoice generation: gather unbilled time+expenses → compute line items → auto-number → link sources
- Invoice CRUD: list, detail (with line items + payments), status transitions
- Payment recording with auto invoice status update (paid/partially_paid)
- Stripe Payment Link generation on invoice

#### Legal Billing Compliance
- LEDES 1998B pipe-delimited export (24-field format, full UTBMS task/activity code maps)
- Litigation (L100-L220), Counseling (C100-C800), Project (P100-P500), Bankruptcy (B100-B190) codes
- CSV invoice export

#### Trust Accounting Foundations
- `TrustAccount` model — per-matter IOLTA accounts with auto-replenish support
- `TrustTransaction` model — deposit/disbursement/transfer/replenishment/fee/adjustment types
- Migration 017: trust_accounts + trust_transactions tables with RLS

### Changed
- `app/config.py` — added QBO_CLIENT_ID, QBO_CLIENT_SECRET, QBO_REDIRECT_URI, QBO_ENVIRONMENT, QBO_WEBHOOK_VERIFIER
- `app/models/__init__.py` — registered 8 new models
- `app/schemas/__init__.py` — registered 28 new schemas
- `app/routers/__init__.py` — registered qbo_router, billing_extended_router
- `app/services/__init__.py` — registered QBOSyncService, export_ledes_1998b
- `app/main.py` — wired qbo_router, billing_extended_router

## [0.4.0] — 2026-06-02

### Added - Enhanced User Model & Context Management

#### User Preferences & Expertise Tracking
- `User.practice_areas` — JSON array of legal specializations (commercial, litigation, privacy, employment, product, IP, AI governance, regulatory, trust & estate, mediation)
- `User.expertise_level` — Proficiency classification: "junior", "mid", "senior" (drives cache TTLs and response complexity)
- `User.default_skill` — Preferred plugin/skill for routing (stored on user profile)
- `User.privacy_mode` — Strict PII handling flag (affects context injection and scrubbing)
- `User.memory_summary` — Auto-generated summary of user interactions and preferences
- `User.last_memory_update` — Timestamp for memory freshness tracking
- Migration 010: Add columns to `users` table with sensible defaults; index on practice_areas

#### Per-User Memory & Interaction Context
- `UserMemory` model with type-based storage:
  - `memory_type`: "preference" (user-set), "expertise" (observed), "matter_context" (case-specific), "interaction_pattern" (learned behavior)
  - `key` / `value` — Flexible key-value store (e.g., `preferred_rag_source_type`, `client_X_context`)
  - `confidence` — Relevance score 0–1 (how certain we are about this memory)
  - Timestamps and tenant/user isolation
- Migration 011: Create `user_memory` table with RLS
- `MemoryService` — CRUD ops + auto-summarization via LLM
- Auto-memory trigger: every 10 messages → `summarize_conversation()` → extract key facts/decisions → store as interaction_pattern
- Update `User.memory_summary` after each summary

#### PII Detection & Scrubbing
- 8 PII pattern types: SSN, credit card, phone, email, IP address, passport, driver's license, bank account
- Input scanning: detect PII in user messages before RAG query
- Output scrubbing: mask PII in assistant responses while preserving intent (e.g., "[MASKED_SSN]" instead of actual SSN)
- `PII Detection Service` (`services/pii_detection.py`):
  - `detect_pii(text: str)` — Returns list of {type, location, confidence}
  - `scrub_pii(text: str)` — Replaces with placeholders
  - `assess_pii_risk(text: str)` — Returns "low" | "medium" | "high"
- Guardrails integration: `apply_guardrails()` now returns `(cleaned_text, needs_retry, pii_findings)`
- Conversation flagging: Message.pii_flags stores detected PII metadata for audit
- User opt-in: privacy_mode=true enables stricter scrubbing

#### Explicit Context Usage Tracking
- Extended `Message` model:
  - `context_used` — JSON array of source IDs (document chunks, precedents, regulations) used in response
  - `context_relevance_scores` — Dict mapping source_id → relevance score (0–1)
  - `skill_applied` — Which plugin/skill was active for this message
  - `pii_flags` — Array of detected PII with type and confidence
- Chat response footer: **"### Sources & Context"** section shows:
  - Relevance scores for top 3 sources
  - Source type (Case law, Regulation, Firm material)
  - Hit rate summary (used X of Y retrieved)
- Migration 012: Add columns to `messages` table

#### Skill-Based Chat Routing
- Extended `MessageCreate` schema:
  - Optional `skill` field: route to specific plugin (e.g., "commercial-legal", "litigation-matter-intake")
  - Optional `matter_id` field: inject case context into conversation
- Chat endpoint enhancements:
  - If skill provided: prepend skill context to RAG prompt
  - If matter provided: load matter details, scrub PII if privacy_mode=true, inject into conversation history
  - Track applied skill in Message model + UsageRecord
- Skill-aware response templates (already in plugin system, now injected into RAG)

#### Tenant Settings & Feature Flags
- `TenantSettings` model (one per tenant, unique constraint):
  - Cache controls: `cache_enabled`, `cache_ttl_multiplier` (0.5–2.0)
  - User defaults: `default_expertise_level`, `default_practice_areas` (array), `default_privacy_mode`
  - Feature flags: `enable_auto_memory`, `enable_pii_detection`, `enable_skill_routing`, `enable_matter_context`
  - Rate limiting: `max_requests_per_minute`, `max_daily_tokens`
  - Custom config: JSON blob for tenant-specific overrides
  - Notes: Admin annotations
- Migration 014: Create `tenant_settings` table with RLS + indexes
- System defaults applied at tenant signup; admins override per-tenant
- New admin endpoints:
  - `GET /admin/settings` — Retrieve tenant settings
  - `PUT /admin/settings` — Update (admin only)

#### Expertise-Aware Caching
- `ExpertiseCacheManager` service — Three-tier caching by expertise level:
  - **Junior** (paralegal): RAG 1h, LLM 30m, matter 2h (40% hit target)
  - **Mid** (associate): RAG 30m, LLM 15m, matter 1h (25% hit target)
  - **Senior** (partner): RAG 15m, LLM 5m, matter 30m (10% hit target)
- Skill-based TTL multipliers:
  - Commercial 1.5x (higher complexity, longer cache OK)
  - Employment 1.3x
  - Litigation 0.7x (time-sensitive, shorter cache)
  - Renewal 2.0x (static data)
- Methods:
  - `get_cached_rag_results()`, `set_cached_rag_results()`
  - `get_cached_llm_response()`, `set_cached_llm_response()`
  - `get_cached_matter_context()`, `set_cached_matter_context()`
  - `invalidate_user_cache()` — Clear on privilege change
  - `get_cache_config()` — Retrieve active config for user
- Extended `UsageRecord` with cache tracking:
  - `cache_hit_rag` — Boolean, did RAG query hit cache?
  - `cache_hit_llm` — Boolean, did LLM response hit cache?
  - `cache_hit_matter` — Boolean, did matter context hit cache?
- Cache analytics endpoint: `GET /admin/cache-analytics`

#### Enhanced Admin Console
- New admin endpoints:
  - `GET /admin/tenant/detailed` — Full tenant profile with analytics:
    - User counts (total, active)
    - Message volume, total cost
    - Cache hit rate, avg response time
  - `GET /admin/users/{user_id}` — User detail with:
    - Practice areas, expertise, privacy mode, memory summary
    - Last activity, created/updated timestamps
  - `GET /admin/cache-analytics` — Cache performance metrics:
    - Total requests, cache hits, hit rate (%)
    - Per-tier hit rates (RAG, LLM, matter)
    - Estimated cost savings
- New schemas in `schemas/admin.py`:
  - `UserDetailResponse` — Full user profile
  - `TenantSettingsResponse`, `TenantSettingsUpdate`
  - `TenantDetailResponse` — Analytics-rich tenant view
  - `CacheAnalytics` — Performance metrics

#### Error Logging & Support Management
- `ErrorLog` model — Global error tracking:
  - Per-user and system-level logging (user_id nullable for system errors)
  - Error classification: api_error, rag_query_error, llm_error, cache_error, database_error, authentication_error, validation_error, timeout_error, rate_limit_error, permission_error
  - Severity levels: critical, error, warning, info
  - Request context: endpoint, method, status_code, IP address, user agent
  - Error details: message, stack trace, request ID
  - Conversation context: conversation_id, query_text for debugging
  - Resolution tracking: is_resolved, resolved_at, resolution_notes
  - Composite indexes for efficient 72-hour rolling per-user queries and system-level recent errors
- Migration 015: Create `error_logs` table with RLS
- Admin endpoints (pending implementation):
  - `GET /admin/errors/user/{user_id}?days=3` — Per-user 72-hour rolling error logs
  - `GET /admin/errors/system?days=3` — System-level errors
  - `GET /admin/errors/summary` — Error metrics and top issues

### Changed
- Chat endpoint: integrated cache manager, matter context loading with PII scrubbing, PII detection in user input
- Guardrails: extended to include PII detection alongside prohibited phrase checking
- Message model: now tracks context usage, skill applied, and PII flags for full audit trail
- Admin dashboard: enhanced tenant view with detailed analytics and user drill-down
- User model: expertise-driven system behavior (cache TTLs, response length, confidence thresholds)
- Auth schemas: use validated emails and password length constraints

#### Auth Hardening
- Existing tenant domains now require admin invitation/account pre-provisioning instead of automatic self-registration joins
- OAuth login callbacks now use short-lived frontend exchange codes instead of bearer JWTs in redirect URLs
- Integration OAuth connects now require authenticated initiating users and bind callback state to user, tenant, intent, and role
- Google OAuth login now rejects unverified Google email claims
- Backend-side auth rate limits now cover login, registration, forgot-password, and reset-password endpoints
- OAuth token storage now fails closed when `TOKEN_ENCRYPTION_KEY` is missing or invalid
- OAuth token expiry writes now use timezone-aware datetimes matching the database schema
- Per-user OAuth token lookup now includes explicit tenant filtering in addition to RLS
- Tenant RLS context is now set with a bound `set_config` parameter and UUID validation

### Migration Summary
- 010: Enhance user model (practice_areas, expertise_level, default_skill, privacy_mode, memory_summary, last_memory_update)
- 011: Create user_memory table
- 012: Extend message context tracking (skill_applied, context_used, context_relevance_scores, pii_flags)
- 013: Add cache tracking to usage_records (cache_hit_rag, cache_hit_llm, cache_hit_matter)
- 014: Create tenant_settings table (per-tenant feature flags and cache config)
- 015: Create error_logs table (per-user and system error tracking)

### Tests
- Lint: all new files pass ruff validation
- Auth: targeted ruff, Python compile, schema probe, frontend build, and regression grep checks for hardened auth modules
- Models: SQLAlchemy validation for RLS policies
- Schemas: Pydantic model_config set to "from_attributes=True" for ORM binding

## [0.3.0] — 2026-06-01

### Added
- **QuickBooks connection and invoice tax handling are production-ready:**
  successful Intuit authorization returns to the LawHand QuickBooks admin tab,
  A/R settings serialize database UUIDs correctly, QBO routes use an isolated
  edge-rate bucket, partial catalogue failures remain usable, and invoice
  sales-tax choices explicitly set each synced QBO line taxable or non-taxable.
- CourtListener public RAG pipeline
  - `scripts/ingest_courtlistener.py` now extracts/chunks only and inserts `public_chunks` rows pending Jetson embeddings
  - `scripts/jetson_embed_worker.py` remains the BGE-small embedding writer for `public_chunks.embedding`
  - `scripts/create_public_chunks_index.sql` builds the IVFFlat index after embedding
  - `scripts/courtlistener_jetson_pipeline.md` documents the single-Jetson same-network workflow
  - RAG now searches `public_chunks` with optional BGE query embeddings alongside tenant document chunks
- Phase 1: OAuth token persistence — encrypted token vault with Fernet (AES-256-GCM)
  - `TenantCredential` and `UserOAuthToken` SQLAlchemy models with RLS
  - `TokenVault` service with auto-refresh for MS Graph + Google APIs
  - `GET /api/integrations/microsoft/connect|callback` — admin/user OAuth flows
  - `GET /api/integrations/google/connect|callback` — admin/user OAuth flows
  - `GET /api/integrations/status` — admin-only integration health
  - `POST /api/integrations/{provider}/disconnect` — revoke tokens
- Phase 2: Email agentic pipeline + Calendar sync
  - `MicrosoftMailService` — per-user/per-tenant inbox read via Graph API
  - `GoogleMailService` — Gmail API inbox read with label-aware filtering
  - `EmailAgent` — LLM classification (legal_query/court_filing/client_comm/etc) + draft response generation
  - `CalendarSyncService` — read/write M365 + Google Calendar; bidirectional deadline sync
  - `POST /api/email/scan` — scan + classify + draft responses
  - `POST /api/email/calendar` — list events + optional deadline sync
- Phase 3: Document sync for RAG
  - `DocumentSyncService` — sync from OneDrive, SharePoint, Google Drive
  - `GET /api/sync/documents/stats` — cross-drive document counts
  - `POST /api/sync/documents/list` — list legal documents by provider
  - `POST /api/sync/documents/sync-and-ingest` — background download + RAG pipeline ingestion
- Phase 4: Gemini + Azure OpenAI LLM providers
  - `LLMService._complete_gemini()` — Google Gemini 2.0 Flash via REST API
  - `LLMService._complete_azure()` — Azure OpenAI (GPT-4o) via SDK
  - Provider routing via `provider=` param on `LLMService.complete()`
- Phase 5: Admin user sync dashboard
  - `UserSyncService` — M365 Graph `/users` + Google Directory API sync
  - `POST /api/sync/users/microsoft` — sync M365 users to Clarity
  - `POST /api/sync/users/google` — sync Google Workspace users
  - `POST /api/sync/users/all` — sync both providers
- Config: `TOKEN_ENCRYPTION_KEY`, `AZURE_OPENAI_*`, `GEMINI_API_KEY`, `GOOGLE_SERVICE_ACCOUNT_*`
- Migration 009: `tenant_credentials` + `user_oauth_tokens` tables with RLS
- New deps: `cryptography`, `google-auth-oauthlib`, `google-api-python-client`, `google-genai`

### Changed
- CourtListener sync tooling now targets `public_chunks` instead of tenant-scoped sentinel rows in `chunks`
- Jetson launcher defaults to one `JETSON_HOST`, with optional multi-host `JETSON_HOSTS`
- Auth OAuth flows: added `offline_access` scope to MS and Google login
- LLMService: added optional `provider` parameter for Gemini/Azure routing

### Tests
- Lint: all new files pass ruff (28 pre-existing issues in other files remain)

## [0.2.0] — 2026-05-31

### Added
- **QuickBooks connection and invoice tax handling are production-ready:**
  successful Intuit authorization returns to the LawHand QuickBooks admin tab,
  A/R settings serialize database UUIDs correctly, QBO routes use an isolated
  edge-rate bucket, partial catalogue failures remain usable, and invoice
  sales-tax choices explicitly set each synced QBO line taxable or non-taxable.
- Email/password registration (`POST /auth/register`) with company details form
- Email/password login (`POST /auth/login`)
- Password reset flow (`POST /auth/forgot-password`, `POST /auth/reset-password`)
- SignupPage, ForgotPasswordPage, ResetPasswordPage (React)
- `password_hash` column to User model (005 migration)
- `company_name`, `staff_size`, `address`, `phone` columns to Tenant model (005 migration)
- JWT `iat` (issued-at) and `jti` (JWT ID) claims
- Token blacklist on logout via Redis (fallback to in-process dict)
- Healthchecks for postgres, redis, backend, frontend in docker-compose
- Production frontend Dockerfile (multi-stage Vite build + serve)
- `/health`, `/docs`, `/openapi.json`, `/redoc` proxying through nginx

### Changed
- Registration reuses existing domain tenant; first user gets admin
- Login queries scoped by created_at desc + limit(1)
- Logout now blacklists JWT tokens
- Backend Dockerfile: added wget for healthcheck
- Frontend Dockerfile: multi-stage build serving via `serve` instead of `vite dev`

### Fixed
- Sidebar: `documents.map` and `conversations.length` crashes (Array.isArray guards)
- Registration: missing `db.commit()` after user creation
- Login: `is_active` check added
- `passlib[bcrypt]` → `bcrypt>=4.0,<5.0` in requirements.txt (incompatibility)
- Reset tokens hidden when `DEV_MODE=false`
- Fallback dict TTL garbage collection
- CORS: added internal HTTPS origin for the deployment host

### Security
- `SECRET_KEY` regenerated
- `DEV_MODE=false` on hypervisor
- Credentials removed from `.env`
- `PRIMARY_LLM` reverted to `deepseek-chat`

## [0.1.0] — Initial

### Added
- **QuickBooks connection and invoice tax handling are production-ready:**
  successful Intuit authorization returns to the LawHand QuickBooks admin tab,
  A/R settings serialize database UUIDs correctly, QBO routes use an isolated
  edge-rate bucket, partial catalogue failures remain usable, and invoice
  sales-tax choices explicitly set each synced QBO line taxable or non-taxable.
- Multi-tenant architecture with domain-based tenant isolation
- Row-Level Security (RLS) on all tables
- OAuth authentication (Microsoft, Google)
- Chat with DeepSeek + Claude Opus (RAG via pgvector)
- Document upload with vector embedding
- Plugin system: Litigation Matters, Commercial Renewals
- Admin dashboard (tenant users, usage stats)
