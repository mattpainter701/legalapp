# Product & UX review — MCP power user

Reviewed on branch `claude/product-ux-code-review-tf7ake`, following the
workflow: research a strategy in an external assistant (ChatGPT Desktop, Claude
Desktop) → draft a case document there → push it into the matter over MCP.

This persona is the most sophisticated user of the product and the least served
by its UI. They live in someone else's client. What they experience of LawHand is
a tool list, a consent screen, and whatever comes back from a tool call.

**The capability design here is the strongest work in the codebase.** Ten
capabilities (`app/services/automation_capabilities.py:158-292`) — seven READ,
three PROPOSE, every proposal gated by `ApprovalPolicy.LAWHAND_REVIEW`. No raw
write exists. Tool descriptions are written for a model to read correctly
("Use this first when the user names a matter, to obtain its matter_id").
Untrusted content is labelled as untrusted at the contract level. The consent
screen (`WorkspaceMcpAuthorizePage.jsx`) states the safety boundary plainly.
Auth re-validates the grant, the user, the license, and RBAC *inside* the
RLS-scoped transaction on every call (`workspace_mcp_protocol.py:256-305`).

The findings below are about the last mile: what happens to the work when it
lands.

---

## P0 — The push degrades the artifact

### 1. All document formatting is destroyed on the way in

`backend/app/services/cloud_artifact_materialization.py:105-120`

```python
def render_revision_docx(*, title: str, content: str) -> bytes:
    document = Document()
    document.add_heading(clean_title, level=1)
    for paragraph in str(content or "").split("\n"):
        clean = "".join(ch for ch in paragraph if ch in "\t\n\r" or ord(ch) >= 32)
        document.add_paragraph(clean)
```

Every line becomes a bare `add_paragraph`. Nothing else survives.

ChatGPT and Claude both emit Markdown — always. So a motion drafted in an
external assistant arrives in the matter as:

- `## ARGUMENT` — a body paragraph containing two literal hash marks
- `**Plaintiff's position**` — literal asterisks, no bold
- `1.` / `2.` / `3.` — plain text, no Word numbering, so renumbering is manual
- Blank lines between paragraphs → `add_paragraph("")` → a **real empty
  paragraph** for every one, double-spacing the entire document
- No indentation, block quotes, tables, or page breaks

The result is a DOCX that a paralegal has to reformat by hand before it can be
filed — which is precisely the labor the workflow was supposed to remove. The
power user's careful drafting is the thing being discarded.

Parse the Markdown: headings → Word heading styles, emphasis → runs, ordered and
unordered lists → Word list styles, skip empty lines rather than materializing
them. `python-docx` is already a dependency and the styles already exist in the
default template.

Everything downstream of this function is solid — versioned cloud revisions,
SHA-256 verification of the exact bound bytes, staff-then-attorney review. That
machinery is faithfully preserving a badly rendered document.

### 2. The 50,000-character wall arrives at the worst moment

`backend/app/schemas/chat_action.py:170`

```python
body: str = Field(min_length=1, max_length=50_000)
```

Roughly 8,000 words — about 25 pages. A summary judgment brief with a statement
of facts, a lengthy trust instrument, or a discovery response set clears that.

The failure lands *after* the user has done all the work, as a Pydantic
validation error surfaced through the MCP error channel. There is no way to
learn the limit in advance: it appears in no tool description, and
`ProposeMatterDocumentArgs` does not document it.

Two fixes, both cheap. State the limit in the `propose_matter_document`
description so the model can plan around it. Then make the error actionable —
"body is 61,240 characters; the limit is 50,000" — rather than a generic schema
rejection.

Longer term, a chunked or multi-part push would remove the ceiling, but naming
the number honestly fixes most of the pain.

### 3. Research citations cap at ten, silently and twice

`chat_action.py:172` sets `source_ids: max_length=10`, and the handler then
truncates again with `args.source_ids[:10]` in two places
(`chat_tools/handlers.py:908,946`).

A research-heavy brief cites far more than ten authorities. The cap is not in
the tool description, and the belt-and-braces `[:10]` means that if the schema
bound were ever raised, sources would still be silently dropped in the handler.

For a product whose README commits to a rigorous citation contract, silently
discarding an attorney's eleventh through fortieth authorities is the wrong
default. Raise the bound, and if a cap must remain, return which sources were
dropped rather than truncating quietly.

---

## P1 — Getting connected, and knowing what you have

### 4. There is no in-product way to connect an assistant

The revocation half is done well: `WorkspaceMcpGrantsPanel` is mounted on
`/profile` (`ProfilePage.jsx:198`) and in `IntegrationsPanel`, so any user can
see and revoke their own grants without an admin.

But the panel is 83 lines of list-and-revoke. It shows **no server URL, no
client-id, no setup instructions** — and neither does anything else a normal
user can reach. `MCPPage` has no connection URL either, and it is behind
`/admin?tab=mcp`, which is `financeOnly` plus `adminOnly` on the `/mcp` route
(`App.jsx:378-386`). A power-user attorney who is not an admin or accountant
**cannot open it at all.**

So the actual onboarding path is: someone emails you a URL. The product that
carefully designed the consent screen has no page that gets you to it.

Add a "Connect an assistant" section to the grants panel with the workspace MCP
URL, a copy button, and the two or three lines of client config.

### 5. Grants show status but never expiry

`WorkspaceMcpGrantsPanel.jsx:75` renders client name, organization, and an
active/inactive pill. There is no issued-at, no last-used, no expiry.

A power user with three assistants connected across two laptops cannot tell
which grant is which, which one is still working, or when any of them lapses.
The first they learn of an expiry is a tool call failing inside ChatGPT, with an
error surfaced by someone else's client.

Show issued, last used, and expires. The revocation UI is otherwise the right
shape.

### 6. Three hard 403s with no forewarning

`workspace_mcp_protocol.py:290-305` rejects a call outright when the user is
inactive, when `license_active` is false, and when `privacy_mode` is on:

```python
if user.privacy_mode:
    raise HTTPException(403, "Workspace MCP is unavailable while Privacy Mode is enabled")
```

Privacy Mode is a per-user toggle the power user may well have set themselves,
months ago, for unrelated reasons. Nothing in the profile UI says it disables
MCP. So the failure presents as "my assistant stopped working" with a 403 that
surfaces inside a third-party client.

Note it next to the Privacy Mode toggle, and show a blocked-state banner in the
grants panel when any of the three conditions is active.

---

## P2 — Framing

### 7. Half the advertised surface is off by design

The workflow described as "platform & research MCP" is really two products. The
workspace MCP (the ten capabilities above) is live. The research/product MCP is
gated behind `MCP_PRODUCT_ENABLED`, which defaults to `False`
(`app/config.py:208`) and which the README calls a launch invariant — *"Do not
market, issue, or accept customer MCP keys yet."*

That is a deliberate and well-enforced decision, not a defect. It is worth
stating plainly here because it bounds the persona: **the "research strategies"
half of this workflow is not available in the shipping product.** A power user
today can read matter context and push proposals; they cannot use LawHand's
research corpus from an external client.

If that gate lifts before the formatting fix in #1, the research output will
arrive in matters as unformatted text too.

---

## Suggested order

1. **#1 Markdown rendering** — the workflow's entire value proposition is
   currently destroyed at the last step. Highest impact by a wide margin.
2. **#2 / #3 limits** — put both numbers in the tool descriptions today; make
   the errors actionable next.
3. **#4 connection instructions** — the consent flow is finished and unreachable.
4. **#6 blocked-state forewarning** and **#5 grant metadata** — both small.
