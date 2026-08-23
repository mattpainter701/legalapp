# Customer release notes

Plain-language highlights for people using LawHand. For implementation,
security, and migration details, see the [technical changelog](CHANGELOG.md).

<!-- Generated from backend/app/release_notes.json. Do not edit by hand. -->

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
