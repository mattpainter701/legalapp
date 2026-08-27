# Customer release notes

Plain-language highlights for people using LawHand. For implementation,
security, and migration details, see the [technical changelog](CHANGELOG.md).

<!-- Generated from backend/app/release_notes.json. Do not edit by hand. -->

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
