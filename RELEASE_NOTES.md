# Customer release notes

Plain-language highlights for people using LawHand. For implementation,
security, and migration details, see the [technical changelog](CHANGELOG.md).

<!-- Generated from backend/app/release_notes.json. Do not edit by hand. -->

## 2026.08.31.2 — Configurable matter workflows with reviewable runs

Released August 31, 2026.

Firms can define bounded matter data and apply approved stage and checklist templates through previewed, auditable workflow runs.

- **Model firm-specific matter data.** Administrators can define typed matter fields with stable keys, required and sensitive handling, bounded options, and safe retirement controls in the matter workflow surface.
- **Preview before applying a workflow.** Approved matter templates show the exact initial stage, required data, assignee gaps, checklist tasks, and snapshot hash before any matter or task changes occur.
- **Apply once with durable evidence.** Approved runs create deterministic relative tasks in one transaction, reject stale previews, deduplicate retries, and retain immutable run and step evidence.
- **Compensate without erasing history.** Rollback cancels only unchanged workflow-created tasks, restores the prior matter stage, reports blockers for manual review, and keeps authoring separate from legal approval.

## 2026.08.31.1 — Template Studio workspaces and resumable setup

Released August 31, 2026.

Document templates now have a first-class Studio home, persistent workspaces, and clearer paths from source preparation to generation.

- **Resume template setup.** Studio home groups loaded templates into continue setup, needs attention, ready to generate, and recent views using the existing library status.
- **Open a persistent workspace.** Every template links to a stable workspace for reviewing its source, fields, readiness, preview, and generation actions.
- **Keep source preparation intact.** New and imported templates continue through the reviewed Word, PDF, and image preparation flow before opening their workspace.
- **See current limits clearly.** Reserved test, version, activity, draft, proposal, and snapshot routes say when server-backed records are not yet available instead of showing fake controls.

## 2026.08.30.9 — Shared research workspaces with reviewable evidence

Released August 30, 2026.

Matter teams can now preserve a shared research trail without turning machine notes into source evidence.

- **Keep the team trail together.** Create matter-scoped workspaces for saved issues, searches, authorities, highlights, annotations, exclusions, outlines, and memo notes with explicit member roles.
- **Carry evidence labels forward.** Every saved item and frozen export keeps its cited, verify, or model label alongside exact source links, source version, pinpoints, and stored treatment/currentness state.
- **Freeze a review package.** Immutable, hash-identified snapshots create an exportable record of the workspace as reviewed. Bluebook formatting and citation correctness still require attorney verification.
- **Revoke safely.** Workspace access is tenant-and-matter scoped, role checked, auditable, and revocable; archived workspaces and records are retained rather than silently erased.

## 2026.08.30.8 — Review-first citator evidence controls

Released August 30, 2026.

Research now separates linked source facts from provisional treatment interpretation and makes citator limits visible before attorney review.

- **Inspect the evidence state.** Citator results show the promoted source, version, as-of date, history or citation evidence, and currentness limitations separately from any machine interpretation.
- **Keep treatment reviewable.** Machine labels may abstain and remain provisional until an attorney accepts, rejects, requests more evidence, or records an override in the append-only review trail.
- **Watch changes safely.** Saved authority watches are consented, tenant-and-matter scoped, deduplicated, revocable, and ready for quiet-hour and failure-aware delivery. This release sends no alerts.
- **No unsupported status claim.** LawHand does not call an authority good law from a missing negative record. Complete citator coverage still requires a permitted licensed or attorney-reviewed benchmark.

## 2026.08.30.7 — Private mediation review and party-specific sharing

Released August 30, 2026.

Licensed mediation work now appears inside the client's existing matter portal while documents and proposals stay private until attorney review and deliberate release.

- **Keep each submission private.** A party's document or proposal is visible to that party and the firm until the legal team explicitly releases it to selected recipients.
- **Review before delivery.** Attorneys can approve, return, or reject proposals before choosing who receives an approved version; released content is preserved as immutable evidence.
- **Negotiate with traceable counters.** Counteroffers can reference only an active proposal released to that party, and the prior offer is superseded only when the reviewed counter is actually released.
- **Use mediation in My Matters.** Eligible clients see a read-only Mediation tab in their native matter portal, with scoped assets, proposals, and integrity-checked document downloads when the add-on is active.

## 2026.08.30.6 — Clearer legal research gaps

Released August 30, 2026.

Research now separates linked source facts from provisional treatment interpretation and makes citator limits visible before attorney review.

- **Keep verified findings.** When a response mixes cited and unsupported material, cited findings remain visible while unsupported claims are omitted.
- **Explain missing authority.** Research gaps now say whether the authority service or fallback was unavailable, or whether no usable match was found.
- **Open the cited source.** CourtListener and official public-source URLs remain attached to the cited source so reviewers can open the retrieved authority.
- **Avoid uncited legal summaries.** Jurisdiction-specific questions without authority no longer receive a general-knowledge answer presented as completed research.

## 2026.08.30.5 — Versioned public-authority coverage

Released August 30, 2026.

Public legal research now exposes reviewed source scope, corpus release evidence, currentness limits, and honest retrieval fallbacks.

- **See reviewed source scope.** Rights decisions, authority tiers, content types, jurisdictions, temporal scope, cadence, caveats, and provenance are carried with source health.
- **Track corpus releases honestly.** Promoted versions, rollback metadata, harvest checkpoints, quarantine states, and sampled audits prevent unsupported complete/current claims.
- **Preserve retrieval boundaries.** Exact embedding compatibility is required; outages or mismatches fall back to keyword/source search while private firm documents remain outside public telemetry.

## 2026.08.30.4 — Controlled client and matter import promotion

Released August 30, 2026.

Administrators can approve an unchanged import report before promoting the conservative client and matter subset, with durable links and rollback review markers.

- **Approve the exact reconciliation.** Promotion requires an explicit confirmation and report hash, so changed or stale staging cannot be promoted silently.
- **Reconcile existing records.** Client identifiers and email addresses are matched within the tenant before a new client is created; every promoted row receives an external provenance link.
- **Keep rollback reviewable.** Operators can mark a promoted run for non-destructive rollback review while audit receipts preserve what happened.

## 2026.08.30.3 — Search a firm's local case-file memory

Released August 30, 2026.

A bounded local file-search control surface connects an approved file-share agent to the portal, Chat, and Workspace MCP while keeping the firm's query and documents on the firm's network.

- **Search inside supported local files.** Matter-scoped searches can return ranked snippets and page hints from the agent's local SQLite control index without embeddings.
- **Use the same search from three surfaces.** The portal, Chat assistant, and user-authorized Workspace MCP now share one bounded search contract with visible partial and index status.
- **Open results safely.** Results use an authenticated portal link that rechecks access and offers Copy UNC instead of exposing a raw browser file link.
- **Measure the pilot honestly.** Correlation IDs, counts, latency, and indexed or pending status support local validation; this remains a small control-index PoC, not a 4 TB production search promise.

## 2026.08.30.2 — Deeper federal and Ohio research coverage

Released August 30, 2026.

Research can ingest reviewed federal rules, constitutional analysis, and Tax Court reports while keeping jurisdiction and known extraction limits explicit.

- **Search more reviewed federal authority.** Federal rules, Constitution Annotated, and bounded Tax Court Reports now have scheduled, failure-isolated ingestion paths.
- **Keep unsafe text out.** A known unreadable appellate-rules extraction stays auditable but cannot enter searchable text until its parser is replaced.
- **Scope Ohio and federal research correctly.** Explicit Ohio and federal questions now apply matching filters to case law and other public authorities.

## 2026.08.30.1 — Review-first Brief Check

Released August 30, 2026.

Firms can review brief citations, quotations, pin cites, opposing-brief differences, and bounded authority candidates with linked evidence before exporting work product.

- **Inspect evidence before relying on a brief.** DOCX/PDF checks preserve source identity, location, confidence, ambiguity, retrieval evidence, and explicit unknown or unavailable states.
- **Keep attorney decisions auditable.** Reviewers can accept, reject, or follow up on findings; decisions are tenant-scoped and recorded in an immutable audit trail.
- **Export linked review work product.** Download a review report and table-of-authorities draft while bounded recall and currentness limitations remain visible.

## 2026.08.29.1 — Operating trust workflows and evidence

Released August 29, 2026.

Customers and operators can use one versioned contract for support, incidents, lifecycle receipts, offboarding proof, providers, and security review without unsupported SLA or certification claims.

- **Use defined support and status workflows.** Published business hours, S1-S4 severity objectives, audited escalation, and sanitized append-only incident updates make the operating process reviewable without creating an SLA.
- **Carry acceptance evidence through the lifecycle.** Onboarding, BK28 migration, and export receipts reconcile scope and counts; offboarding blocks on holds and needs two operators before proof.
- **Export an honest security packet.** The content-addressed packet names providers and boundaries while marking penetration testing and certifications as not attained.

## 2026.08.28.6 — Attributed intake and safer lead follow-through

Released August 28, 2026.

Firms can capture attributed public inquiries, triage conflicts, book consultations, and follow up with explicit consent while keeping the conversion trail auditable.

- **Capture inquiries safely.** Conditional forms accept only validated answers, resist simple spam, preserve source attribution, and deduplicate retries.
- **Review before conversion.** Public leads require an explicit conflict decision before they can become matters; appointment booking and reminder state remain durable.
- **Respect communication choices.** Authored email follow-up checks current consent and reports provider failure truthfully; SMS remains unavailable until its compliance controls are ready.

## 2026.08.28.5 — Zoom Phone setup now shows exactly what needs attention

Released August 28, 2026.

Tenant administrators can follow Zoom Phone setup stage by stage and recover from authorization failures without leaving the integration panel.

- **See each setup stage.** The Zoom panel separately tracks saved app credentials, account authorization, Phone API permissions, and verified real-time call delivery.
- **Recover from the actual authorization problem.** Zoom authorization returns to the integration panel with specific guidance for rejected credentials, expired requests, missing permissions, and account mismatches.
- **Know when reconnecting is required.** Replacing an OAuth client pair now clearly reports that the previous grant was disconnected and directs the administrator to authorize the current app.

## 2026.08.28.4 — Client portal payments and durable sign-in

Released August 28, 2026.

Clients can activate a durable portal account, pay an invoice through hosted Stripe Checkout, and complete certified Dropbox Sign requests when the firm configures the provider.

- **Pay invoices securely.** The portal opens a hosted Stripe Checkout session for the current invoice balance; payment status updates only after verified provider reconciliation.
- **Return with durable sign-in.** Clients can activate a password-backed account and sign into an explicitly selected matter while invitation revocation remains authoritative.
- **Use certified e-signature providers.** Dropbox Sign dispatch and signed or declined webhook events are authenticated, tenant-bound, and idempotently reconciled.

## 2026.08.28.3 — Zoom Phone setup now shows the exact authorization path

Released August 28, 2026.

Tenant administrators can configure Zoom Phone with the exact required permissions and start authorization from the correct LawHand control.

- **Choose only the required Phone access.** The Zoom setup panel names both account call-history permissions, shows their exact identifiers, and links directly to Zoom's Call Logs reference.
- **Start the secure tenant connection correctly.** LawHand now warns administrators to use Connect Zoom Phone instead of Marketplace Add or generated authorization links, preserving the tenant-bound authorization check.

## 2026.08.28.2 — Demo workspaces are clearly identified for operators

Released August 28, 2026.

Platform operators can now distinguish disposable demo workspaces from regular platform tenants in the tenant inventory.

- **See the tenant type.** The platform tenant list labels each workspace as Demo or Platform without using the billing tier as the visible grouping.
- **See demo expiry at a glance.** Demo rows show their expiration and clearly flag workspaces that have already expired.
- **Filter the inventory.** Operators can focus the tenant list on demo workspaces or regular platform tenants while the dedicated demo controls remain protected.
- **Use Zen free background capacity.** Platform operators can assign OpenCode Zen free models to the Background Automations route while Standard and Premium keep their confidential-data safeguards.

## 2026.08.28.1 — The platform tour now follows a matter end to end

Released August 28, 2026.

The LawHand platform tour now shows how legal work moves from intake and conflict review through preparation, client action, signature, billing, and follow-through.

- **Follow the matter.** Step through a visual workflow from the first call to an opened matter, attorney review, client delivery, and accounting.
- **See every role's handoff.** Compare what attorneys, paralegals, intake staff, billing staff, and clients need from the same matter record.
- **Evaluate the complete platform.** Review expanded capability, practice-area, integration, and control sections with clear rollout labels and concrete workflow details.

## 2026.08.27.12 — Product claims now show rollout and research boundaries

Released August 27, 2026.

LawHand product and pricing pages now distinguish shipped behavior, controlled pilots, planned work, and provider-dependent connections.

- **See the rollout state.** Capability cards identify implemented behavior, controlled pilots, and connections that require a separate provider account or approval.
- **Evaluate Research MCP as a pilot.** Research MCP pricing and product pages now state the controlled-pilot gate and the configured public-authority coverage boundary.
- **Keep research claims precise.** LawHand explains that source links support attorney review without claiming Westlaw replacement, comprehensive coverage, or a good-law determination.

## 2026.08.27.11 — Agreement evidence and safer retention controls

Released August 27, 2026.

Firms can review current agreement acceptance evidence and preview safe expiry cleanup with legal-hold protection.

- **Know what was accepted.** Tenant admins can review counsel-owned agreement versions and record signer, authority, and immutable document evidence.
- **Retention with guardrails.** Review tenant data-store inventory, configure retention, preview cleanup, and protect held or matter data from deletion.

## 2026.08.27.10 — Research API keys put staff access and spend under firm control

Released August 27, 2026.

Firm administrators can provision LawHand Research keys for staff, bound their lifetime and budget, and see usage and charges from the MCP portal.

- **Issue keys with clear custody.** Name each key, record its purpose, assign it to a LawHand staff profile, and choose exactly which Research tools it may use.
- **Bound time and spend.** Set an expiration, monthly dollar budget, call cap, and burst limit; the gateway stops successful calls before they exceed either hard monthly boundary.
- **See the complete key ledger.** The portal shows active, expired, and revoked keys with creator, custodian, last use, successful and failed calls, current-month charges, and remaining budget.
- **Connect standard API clients.** LawHand Research keys work as standard Bearer credentials while the existing custom header remains supported for compatible clients.

## 2026.08.27.9 — Document automation is faster, safer, and easier to manage

Released August 27, 2026.

Firms can navigate larger template libraries, see readiness at a glance, and keep reviewed document generation moving with stronger recovery controls.

- **Find the right template faster.** Search titles and descriptions, filter by status or category, and move through a paged library without loading every template at once.
- **See what needs attention.** Library health cards distinguish ready templates, drafts, and binary templates whose original source must be restored before use.
- **Keep concurrent work moving.** Independent scans and tenant background work can progress within bounded capacity instead of waiting behind one unrelated long-running job.
- **Save the reviewed PDF—not a surprise.** Final PDF saves recheck the exact reviewed output and clean up or quarantine staged files when storage and database results cannot be proven consistent.

## 2026.08.27.8 — Connected assistants can prepare complete matter work for review

Released August 27, 2026.

Claude, Codex, ChatGPT, and other approved Workspace MCP clients can now gather client-to-task context, read matter documents and templates, and place prepared documents into LawHand's staged review workflow.

- **Gather the working context.** Approved assistants can search clients, intakes, matters, and tasks, then load parties, team, events, notes, communications, and history within the user's permissions.
- **Reason over documents and templates.** The assistant can read bounded text from uploaded matter documents and approved templates, including the template's fillable-field contract.
- **Send prepared documents through human review.** Fresh or template-rendered Word documents go to tenant cloud with a LawHand Review task for staff and attorney approval. Assistants cannot approve, file, send, or deliver.
- **Reconnect older one-tool connections.** Connections that still show only Find matter keep their original consent. Remove and reconnect once to review expanded scopes; LawHand never enlarges grants silently.

## 2026.08.27.7 — Research connections now complete authorization

Released August 27, 2026.

Claude, ChatGPT, and other hosted clients can now complete the LawHand Research authorization screen without supplying a separate API key.

- **Approve the connection in LawHand.** After adding the Research MCP URL, sign in and approve research-only access through the normal LawHand authorization page.
- **No pasted key for hosted clients.** Hosted clients register securely and use OAuth; Research API keys remain an option only for clients that support custom headers.

## 2026.08.27.6 — Plaintiff and defendant fields are clear and reusable

Released August 27, 2026.

Matter parties now distinguish caption roles from the client relationship, and document templates can Smart Fill reviewed singular or multi-party plaintiff and defendant names.

- **Identify the actual caption role.** The Parties tab now defines plaintiff, defendant, petitioner, and respondent alongside client, counsel, witness, and expert roles.
- **Choose the primary named party.** For matters with multiple plaintiffs or defendants, staff can mark the primary contact used by a singular template field.
- **Reuse parties safely in documents.** Templates can use explicit singular and plural plaintiff or defendant fields, with every Smart Fill value still reviewed before preview or save.

## 2026.08.27.5 — Integration administration is organized in one clear workspace

Released August 27, 2026.

Administrators can now review every connected service from one Integrations workspace with clearer purpose, permissions, setup requirements, and operating guides.

- **Find every connection in Integrations.** Cloud accounts, search, file shares, Teams, Zoom, QuickBooks, and MCP now live under one organized administrative workspace.
- **Understand access before setup.** Each integration explains what it does, the data and permissions it uses, and the prerequisites an administrator should confirm.
- **Move from overview to the right controls.** Focused sections keep configuration close at hand, while expandable notes link directly to the relevant administrative guide.

## 2026.08.27.4 — QuickBooks invoice sync is smoother and respects tax choices

Released August 27, 2026.

QuickBooks connections now return directly to LawHand, account setup is more reliable, and each invoice's sales-tax choice carries into QuickBooks.

- **Return directly to QuickBooks settings.** After approving the connection in Intuit, administrators land back in LawHand with a clear connected confirmation.
- **Configure accounts without losing other mappings.** Accounts-receivable settings save correctly, and a temporary catalogue error no longer hides every available QuickBooks option.
- **Keep non-taxable legal services non-taxable.** An invoice with a zero sales-tax rate sends non-taxable lines to QuickBooks; a positive rate sends taxable lines.

## 2026.08.27.3 — Signature requests are easier to deliver and follow through

Released August 27, 2026.

Signature requests now provide clearer delivery tracking, safer resend controls, and automatic follow-through for sequential signing.

- **Send the actionable request.** Internal signature requests email the signer who can act next and retain delivery status for staff.
- **Track and resend safely.** Staff can see delivery and first-view status and manually resend when follow-up is needed.
- **Keep sequential signing moving.** Completing one signer notifies the next, while configured reminders run before expiration.

## 2026.08.27.2 — Conflict reviews and portal invoice PDFs are traceable

Released August 27, 2026.

Staff can save and close a conflict review, and clients can download a firm-branded invoice PDF from their matter portal.

- **Conflict searches keep their evidence.** The new Conflict Search workspace saves the terms and results the reviewer saw, records notes and a decision, and locks the record after closing.
- **Restricted matters stay restricted.** A reviewer is warned when a potential match exists on a matter they cannot access without exposing that matter's identity.
- **Clients can download branded invoices.** A client-visible invoice is rendered with firm branding and streamed as a PDF; LawHand records download metadata and a hash without retaining another PDF copy.

## 2026.08.27.1 — Prepare document templates with a guided review workspace

Released August 27, 2026.

Turn PDFs and supported images into reusable document templates while reviewing every detected field before creation.

- **Review the original form in place.** Prepare Form keeps the uploaded page design visible while you inspect, add, move, rename, and configure fields.
- **Recover fields from scans and images.** Bounded local OCR and optional configured assistance can suggest fields without making automatic detection a requirement.
- **Finish safely when detection is imperfect.** Validation, confidence cues, and manual placement let you correct unfamiliar forms before creating the reusable template.

## 2026.08.26.4 — Customer cloud storage is explicit and fail-closed

Released August 26, 2026.

Microsoft 365 tenants now default matter files to OneDrive, and portal uploads keep their original copy in a dedicated customer-cloud folder.

- **Microsoft 365 defaults to OneDrive.** When Cloud Document Storage is Auto, an active Microsoft 365 connection is the authoritative destination unless an administrator selects SharePoint or Google Drive.
- **Portal originals have a stable home.** Client uploads go to the matter's client_uploads folder. A reviewed or revised output is saved as a new matter document instead of silently moving the original.
- **Cloud outages fail honestly.** A cloud-bound upload reports a retryable storage error when the customer provider is unavailable; it does not report success after saving a durable local copy.

## 2026.08.26.3 — Tagged matter email can create traceable tasks

Released August 26, 2026.

A reviewed matter email whose subject begins with [TASK] or [DEADLINE] can now be filed and turned into a linked task in one step.

- **Use an explicit subject tag.** Start a new subject with [TASK] or [DEADLINE]. The tag must be the first token, so replies and forwarded subjects do not trigger.
- **Review the task before filing.** The Correspondence queue previews the task title and any safely parsed due date before the reviewer chooses File + create task.
- **Keep the source email attached.** The filed email, correspondence record, task, and task history remain linked so the source and resulting work can be audited from the matter.

## 2026.08.26.2 — Demo workspaces reopen with approved matter-aware AI

Released August 26, 2026.

Return to an active demo without a password, and demonstrate matter-aware Standard AI when Platform has approved the route.

- **Reopen an active demo.** Choose Resume demo and enter the same email plus the current demo access code; the original expiry and AI quota stay unchanged.
- **Show matters with approved Standard AI.** Standard can use matter and attachment context when its assigned Platform routing profile explicitly allows confidential context.
- **Control the demo route from Platform.** Platform can choose the approved Standard profile assigned to new demos while Premium remains unavailable for disposable tenants.

## 2026.08.26.1 — Guides now cover matter email and connected assistants

Released August 26, 2026.

The in-product guides explain how matter email reaches a file and how to review or revoke an external assistant connected to your workspace.

- **Matter email is documented end to end.** The user guide covers the Correspondence tab: capture rules and Scan now, forwarding addresses for a matter, and the queue where you file or reject a message.
- **Connected assistants are yours to review.** The guide explains the Workspace MCP list in your profile — what each connection holds, when it was last used, and how revoking one takes effect immediately.
- **Administrators can see who may connect.** The administrative guide covers the per-user Workspace MCP control, the default for new accounts, and how consent-based access differs from a scoped product key.

## 2026.08.25.1 — Workspace MCP connections respect your firm’s access settings

Released August 25, 2026.

Claude, ChatGPT, Codex, and other compatible assistants can connect when your firm administrator enables Workspace MCP for your account and Privacy Mode is off.

- **Firm access settings take effect directly.** Admin → Users now separates firm permission, effective availability, and active OAuth connections, with a drawer to review or revoke each client.
- **Your privacy choice remains independent.** Privacy Mode continues to pause external assistants, and turning it off allows a new explicit OAuth connection when firm access is enabled.
- **Security boundaries remain enforced.** Active account, license, consent scope, role capability, token revocation, and tenant isolation checks still apply to every request.

## 2026.08.24.5 — Research answers show and link the sources they use

Released August 24, 2026.

Standard research now gives public authority to the assistant, mixed citations remain clickable, and the review-tag guide stays visible while an answer is prepared.

- **Standard answers can use retrieved public authority.** Standard answers can now use retrieved public cases, statutes, and rules with review tags. Matter and firm information remain excluded from Standard.
- **Every retained source marker stays clickable.** Answers that mix structured citation annotations with ordinary source markers now link both kinds to the authority or the in-answer source ledger.
- **Review tags and research progress stay in view.** The tag legend remains visible on phones and desktops, and retrieved-source previews and elapsed research phases remain visible while Premium prepares a validated answer.

## 2026.08.24.4 — Matter forwarding addresses can be created

Released August 24, 2026.

Creating a secure forwarding address from a matter now completes normally instead of failing when the matter has no partner attorney.

- **Create the address from Correspondence.** The Create address action now locks only the matter being updated, so the unique forwarding address appears immediately and is ready to use.

## 2026.08.24.3 — Task details stay readable on phones

Released August 24, 2026.

Mobile task rows now give the task name, due date, status, and actions their own space instead of squeezing them into one overlapping line.

- **Task names keep their space.** The task name and notes use the full mobile row width, so longer names remain readable beside the completion checkbox.
- **Dates and actions wrap cleanly.** Due dates, priority and unread badges, and task actions move onto a separate wrapping row instead of colliding or spilling off the screen.

## 2026.08.24.2 — Forward email straight to the right matter

Released August 24, 2026.

Give each matter its own secure forwarding address, see exactly which contact addresses are tracked, and review incoming mail before it becomes official correspondence.

- **Use one unique forwarding address per matter.** Create an opaque address from the Correspondence tab and forward or BCC a message there without putting a client name or matter number in the address.
- **Review mail before filing it.** Forwarded messages wait in a review queue until a firm user files or rejects them, so an email cannot silently become part of the matter record.
- **See what automatic matching tracks.** Correspondence rules now list the client and matter-party email addresses they use instead of leaving the matching behavior implicit.
- **Rotate or disable an address immediately.** Replace a forwarding address when it has been shared too broadly, or turn it off when the matter no longer needs inbound mail.

## 2026.08.24.1 — A client portal that shows clients what needs their attention

Released August 24, 2026.

The client portal now opens on what is waiting for the client, tracks what they have read, lets them sign out, and shows the firm whether an invitation is actually being used.

- **Clients see what needs them, first.** The portal opens on unread messages, documents awaiting signature, shared documents, and the balance due, with the next key date called out and how soon it falls.
- **New messages are marked as new.** Messages from the legal team show as unread and are counted on the tab until the client opens the thread, which refreshes on its own while they are reading it.
- **The legal team hears about a client message.** When a client writes in through the portal, the people assigned to the matter are emailed a short preview and a link, rather than waiting for someone to notice.
- **Clients can sign out.** A sign-out button ends the portal session immediately, so a borrowed or shared device does not keep access to the matter.
- **Invoices show what is actually owed.** Each invoice shows the amount paid, the balance remaining, and how far past due it is, with a running total for the matter.
- **See whether a portal invitation was ever opened.** The matter's Client Portal tab shows each invitation as awaiting first sign-in, active, expired, or revoked, and when it was last used.

## 2026.08.24 — Network file shares, connected with their own credentials

Released August 24, 2026.

Point LawHand at a file share that stays on your network: install the agent from a packaged installer, store the share's credentials securely, and test the connection before anyone searches it.

- **Install the file share agent instead of building it.** Windows installs from an MSI that registers a background service and can pair during setup; Linux ships a binary with a service installer.
- **Store share credentials securely, per firm.** Save the username and password, Kerberos, or guest identity each share needs. Secrets are encrypted, never shown again, and only reach your own agent.
- **Different shares can use different accounts.** One agent can serve shares that need separate identities, and a credential can be restricted so it only ever reaches one agent.
- **Test a share before trusting it.** Test connection asks the agent to mount the share and reports which identity it used, or the exact reason it failed, instead of leaving an empty index.
- **See when a share last indexed and why it stopped.** Each share now shows its last scan time, file count, and failure reason, and can be rescanned on demand rather than waiting for its schedule.

## 2026.08.23.1 — Teams calls land in your intake feed

Released August 23, 2026.

Inbound Microsoft Teams Phone calls now appear in the intake dashboard beside Zoom Phone calls, and the Teams admin panel gains notification routing and a guided voice setup.

- **Teams Phone calls reach intake automatically.** Inbound calls to your firm's Teams numbers appear in the intake dashboard beside Zoom Phone calls, with the same follow-up tasks and export.
- **Choose where each Teams notification lands.** The Teams admin panel now routes each kind of notification to a team and channel you pick, with a matter's own linked channel still taking precedence.
- **Linked channels name the matter.** The list of linked channels now shows the matter name instead of an internal identifier, so you can tell at a glance what each channel is bound to.
- **Teams errors say what to do.** When Microsoft refuses a request, the panel explains what happened and how to fix it instead of showing an empty list of teams.

## 2026.08.23 — Billing problems you can actually see

Released August 23, 2026.

Your firm is now told when a subscription payment fails, slow pages say so instead of spinning, and Privacy Mode explains what it turns off.

- **Know about a payment problem before it costs you access.** A banner now appears for everyone in the firm when a subscription payment fails or is suspended, with a direct route to update the payment method.
- **No more being offered a plan you already pay for.** If the plan on file disagrees with the subscription your firm actually holds, the billing page says so and stops prompting you to buy again.
- **Slow is now distinguishable from stuck.** Requests that take too long end with a clear message and a retry instead of an open-ended spinner, and long lists show their shape while loading.
- **Privacy Mode says what it affects.** The Privacy Mode switch now states that it also blocks connected assistants, and the connected assistants list shows when access is blocked and why.

## 2026.08.21 — A dedicated home for client relationships

Released August 21, 2026.

Manage client details, communication preferences, matters, billing choices, and accounting connections from one secure workspace.

- **Keep the complete client profile together.** Store contact details, addresses, emergency contacts, preferences, consent, internal notes, and relationship status in Clients & CRM.
- **See client work in context.** Open a client to review linked matters, activity, tasks, and billing preferences without searching across separate screens.
- **Move client data safely.** Tenant admins can import or export bounded CSV files and synchronize customer records with connected QuickBooks Online accounts.

## 2026.08.20 — A clearer view of what changed

Released August 20, 2026.

See the version currently running and catch up on the latest LawHand improvements without leaving your workspace.

- **Find updates in LawHand.** Open Profile or Admin Settings to see the running version and the latest release notes.
- **Revise documents without losing the original.** Each matter-document revision keeps the source document and its review history.
- **See where work stands.** Move tasks through To Do, In Progress, Waiting, Review, and Done with clearer ownership and history.
- **Keep AI work available during provider limits.** Premium requests can continue through the Standard route when premium capacity is temporarily unavailable.
