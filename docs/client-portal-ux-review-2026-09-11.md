# Client portal UX review — 2026-09-11

A read of the customer-facing portal as the customer, not as the firm. Scope is
everything a non-staff user can reach: the two invite-acceptance pages, the
client portal, and the mediation portal used by parties and opposing parties.

Reviewed at `claude/awesome-hypatia-gtdju5`. Source read, not a live walk — every
finding below cites the file and line that produces it.

## Surfaces

| Route | Page | Who lands here |
| --- | --- | --- |
| `/portal/client/accept` | `ClientPortalAcceptPage.jsx` | Firm client, from an invite email |
| `/portal/client/matter` | `ClientPortalMatterPage.jsx` | Firm client — the portal proper |
| `/portal/accept` | `PortalAcceptPage.jsx` | Mediation party, from an invite email |
| `/portal/case` | `PortalCasePage.jsx` | Mediation party / opposing party |

## Verdict

The client portal proper is good. The tab layout is sensible, the overview tiles
answer "what do I owe and what do I owe you" on first paint, the e-signature flow
is careful and legally literate, and the empty states are written in plain
English. Someone thought hard about it.

What misses the mark is almost entirely *around* it: getting in, getting back in,
knowing who you are dealing with, and knowing where to turn when something goes
wrong. The portal behaves like a place you visit once from an email, not a place
you have an ongoing relationship with — and a legal matter runs for months.

The mediation portal is a tier below in every respect and has one outright
functional break.

## Findings

Severity is from the customer's point of view, not the codebase's.

### Blocking

**1. There is no front door. The portal cannot be logged into.**

No route exists for a client to sign in. `/login` is the firm's staff login.
The only way into the client portal is a one-time link from an email:

- The portal session cookie lives **7 days** (`portal_token.py:19`).
- The invite token lives **14 days** (`client_portal.py:161`).

So on day 8 the client is signed out and must find the original email. On day 15
that email's link is dead too, and they have no route back in at all short of
phoning the firm and asking for a new invite. A matter lasts months.

The backend already solves this. `POST /portal/client/activate` converts an
invite into a durable password account (`client_portal.py:608`) and
`POST /portal/client/login` authenticates it (`client_portal.py:692`). The
frontend wrappers are written and exported — `activateClientPortalAccount` and
`loginClientPortalAccount` at `frontend/src/api.js:1271-1275` — and **nothing in
the application calls either one**. The capability is built and unreachable.

The notification emails make this worse, not better: `portal_client_alerts.py:50`
mails clients a bare link to `/portal/client/matter`. Every "your legal team
shared a document with you" email after day 7 lands the client on a dead end.

**2. "Sign out" is a trap door.**

`ClientPortalMatterPage.jsx:247` puts a Sign out button in the header on every
screen, with no confirmation. It resolves to the same screen as an expired
session (line 185): *"Open the link from your invitation email again."*

Given finding 1, a client who tidies up after themselves on a shared family
laptop — exactly what a careful older user does — has locked themselves out. The
button offers no warning that it is one-way.

**3. Mediation portal: document upload does not work.**

`PortalCasePage.jsx:341` disables the Upload button on
`!fileRef.current?.files?.[0]`. The file input at line 339 has **no `onChange`
handler**, so choosing a file triggers no re-render and `disabled` keeps the
value it had at last render — `true`. The chosen filename is never displayed
either, so there is no feedback at all.

The user picks a file, the button stays greyed out, nothing happens. It only
unsticks if some unrelated state changes (typing in the description field
afterwards). Following the natural order — describe, choose, upload — the feature
is dead. There is no test file for `PortalCasePage.jsx`, which is how this
survived.

**4. Mediation portal: the error screen is a closed loop.**

`PortalCasePage.jsx:170-172` shows "Access Denied" with a button labelled **"Enter
Invite Token"** that navigates to `/portal/accept`. That page has no token entry
field — it only reads `?token=` from the URL (`PortalAcceptPage.jsx:9`). Arriving
without one, it immediately renders *"No invitation token provided."*

The one escape hatch offered leads straight to another error page.

**5. The client portal's own failure screen offers nothing.**

`ClientPortalAcceptPage.jsx:61-70` renders the error state — expired link, revoked
invite, anything — as an icon, a sentence, and no action. No button, no firm
contact, no way forward. The mediation equivalent at least offers "Go to Login"
(`PortalAcceptPage.jsx:69`).

The dead end is at the worst possible moment: the customer's very first
interaction with the product.

### High

**6. The portal is branded as the vendor, not as the law firm.**

`ClientPortalMatterPage.jsx:227` hardcodes "LawHand — Client Portal" with a
generic shield icon. The client hired a law firm; they have never heard of
LawHand.

The data to fix it already exists. Tenant branding carries `firm_name`,
`firm_logo_url`, `firm_address`, `firm_phone`, `firm_email` and `firm_website`
and is already stamped onto invoice PDFs (`client_portal.py:2027-2038`). The
portal just does not read it. This is the difference between "my lawyer's client
area" and "some software my lawyer uses".

**7. There is no way to ask for help.**

Searching the client portal page for help, support, phone, or question returns
nothing. The only contact route is a `mailto:` on attorney rows in the Overview
(`ClientPortalMatterPage.jsx:616`) and the Messages tab.

For the tech-averse user this is the biggest gap in the product. When something
confuses them there is no phone number on screen, no "having trouble?", nothing.
`firm_phone` is sitting in branding, unused.

**8. The invitation email reads like phishing.**

`email.py:822-861`. The email:

- never names the law firm — it says "Your legal team" throughout;
- never names the attorney the client has actually been speaking to;
- is sent from "LawHand", a brand the client does not recognise;
- says the link "will expire" without saying when;
- asks the recipient to click a long tokenised URL.

Every instinct a cautious 70-year-old has been trained to have says do not click
this. And they are being asked to click it to reach privileged legal material.
The one identifying detail present is the matter name.

**9. The signature decline flow invites accidents.**

In `ClientPortalMatterPage.jsx:1123-1132`, the Decline button comes **before** the
reason field in the DOM and on screen. The reason input is below it, its label is
`sr-only`, and its only visible identification is a placeholder. There is no
confirmation dialog.

So: a client taps Decline meaning "not yet", it fires immediately with an empty
reason, the firm is told the client refused to sign and is given no reason why.
There is no undo.

The comparison is damning — the mediation portal *does* raise a confirmation for
the far milder act of submitting an asset for review (`PortalCasePage.jsx:130`).
The most consequential button in the product is the one without a guard.

**10. Sessions expire silently mid-task.**

`GET /portal/client/session` returns `expires_at` (`client_portal.py:780`) and the
page fetches it into state (`ClientPortalMatterPage.jsx:153`) — then uses only
`session.email`. The expiry is never shown and never warned about.

A client part-way through a long message, or through a multi-file upload, is
dropped onto the signed-out screen with no notice and no draft preserved.

### Medium

**11. Staff-facing copy is leaking into the client's upload panel.**

`PortalDocumentTransfer.jsx:101-118` tells the client:

- *"Choose this client's folder from your computer or USB drive"* — the client is
  not managing "this client";
- *"Your legal team can import multiple clients through New Matter"* — New Matter
  is a firm-only feature the client cannot reach, and this sentence is addressed
  to staff;
- *"Select at most 10,000 files per batch"*;
- *"ZIPs are stored as bundles; ask your legal team to unpack them. Email
  originals are preserved for review."*

This reads as a bulk data-migration tool rebadged for clients. The actual client
task is "send my lawyer a photo of a letter I got". A young user will find it
confusing; an older user will close the tab.

**12. Upload rules are invisible until the upload fails.**

The server enforces a 50 MB cap (`config.py:425`) and an extension allowlist
(`client_portal.py:177`). The client UI states neither, and the file inputs carry
no `accept` attribute (`PortalDocumentTransfer.jsx:111-112`), so the phone picker
offers everything including files that will be refused.

A client on a phone uploading a long video over cellular finds out it was too big
only after the upload completes and fails. Relatedly, the "Choose folder" input
uses `webkitdirectory`, which does nothing on iOS Safari — a control that is inert
for a large share of the audience.

**13. Mobile: the tab strip hides the tabs that matter.**

`ClientPortalMatterPage.jsx:259` is `flex ... overflow-x-auto` with five tabs, six
when mediation is present. At phone width roughly three and a half fit. Signatures
and Invoices — *sign this* and *pay this*, the two things the firm most wants done
— fall off the right edge with no scroll affordance, no fade, no chevron.

The overview tiles do link through, which partly saves it, but the primary
navigation is hiding its most important items on the most common device.

**14. Mobile: the mediation portal is not responsive.**

`PortalCasePage.jsx` uses `px-8` on both the header (line 182) and the content
container (line 190) with no responsive variant, and renders seven-column tables
inside `overflow-x-auto` (lines 256, 293, 347) — so phone users scroll a wide table sideways
to reach the Actions column where Approve and Dispute live.

Worse, the Documents toolbar (lines 334-341) is a non-wrapping flex row holding a
heading, a `w-48` text input and two buttons. That does not fit 311px of usable
width; it squashes or overflows.

And the reassurance *"Uploads stay private until released by your attorney"* is
marked `hidden lg:inline` (line 337) — hidden on precisely the devices whose users
are most anxious about what they are handing over.

**15. Accessibility: nothing is announced.**

The client portal page contains no `aria-live` region. Sign and decline outcomes
render as plain `<p>` (`ClientPortalMatterPage.jsx:1054`), and `ErrorBanner`
(line 340) has no `role="alert"`.

A screen-reader user signs a fee agreement and hears nothing back. Whether the
most legally significant action in the product succeeded is communicated visually
only. The tab semantics elsewhere on the page are done properly, which makes the
omission look like an oversight rather than a policy.

### Low

**16. Two components ignore the design system.** `ClientIntakeChecklist.jsx` and
`PortalDocumentTransfer.jsx` are unstyled markup — bare `<button>Refresh
checklist</button>` (line 29), `border rounded-lg px-4 py-2` — dropped into an
otherwise carefully styled page. The "Refresh document list" button
(`ClientPortalMatterPage.jsx:886`) floats unstyled between two polished cards. On
the intake checklist, which is the *first* thing a new client sees in
paperwork-only mode, this reads as a half-finished product.

**17. Currency is hardcoded to USD.** `fmtMoney` (line 49) forces USD while dates
next to it use the browser locale (line 62). Mixed signals for any non-US firm.

**18. Messages are from "Legal team".** Line 801 attributes every inbound message
to the firm generically. Clients have a relationship with a person; the sender's
name is a small thing that makes the portal feel human.

**19. Enter sends the message.** Line 768 sends on Enter, hint text below the box.
Right for the young user, wrong for the older one who presses Enter to start a new
paragraph and has just sent their lawyer half a thought.

**20. The mediation portal has no sign-out at all** — the asymmetric opposite of
finding 2. A party on a shared machine cannot end their session.

**21. `PortalCasePage.jsx` has no test file**, the only portal surface without
one. Finding 3 is the direct consequence.

## What I would fix first

In order, weighted by how many customers hit it and how badly:

1. **Build the front door** (findings 1, 2, 5). Add `/portal/client/login`, wire
   the two API functions that already exist, offer account activation on the
   accept page, and give every dead-end screen a way forward. This is the single
   highest-value change and most of the work is already done server-side.
2. **Put the firm on the page** (6, 7, 8). Read the branding that invoices already
   use: firm name and logo in the header, firm phone and email in a persistent
   "Need help?" footer, both in the invite email alongside the attorney's name.
3. **Guard the decline** (9) and **fix the mediation upload** (3, 4, 21).
4. **Make the phone experience whole** (13, 14): responsive padding, card layouts
   instead of wide tables, a visible scroll cue on the tab strip.
5. **Rewrite the upload panel for the person actually using it** (11, 12), and
   announce outcomes to assistive tech (15).

## What is genuinely good

Worth keeping intact through any remediation:

- The overview tiles are the right four numbers, and they are tappable shortcuts.
- The e-signature flow is thorough and honest: review the document, confirm you
  reviewed every page, type your name, consent explicitly, with the evidence
  certificate and hash linkage explained in plain language
  (`ClientPortalMatterPage.jsx:1048`).
- Message polling pauses when the tab is hidden (line 717) — considerate of
  battery and data.
- Paperwork-only mode correctly narrows the portal to the two tabs that matter
  (line 260) instead of showing a new client five mostly-empty sections.
- The mediation privacy model is communicated well. "Private · attorney review"
  versus "Released to you" (`PortalCasePage.jsx:35`) tells a party exactly what
  the other side can see, which is the thing they are most anxious about.
- Empty states are written for humans: *"You're all caught up."*
