# Client intake starter pack

Matter initiation opens with the same three pieces of paperwork for every
client. This document says what they are, where the content lives, how a matter
type selects the right questions, and what a firm must review before anything
reaches a client.

## What the client receives

| Piece | What it is | Where it lives |
|---|---|---|
| Fee agreement | Standard terms of legal representation: scope, exclusions, fees, trust deposit, costs, billing, client responsibilities, termination, file retention. | A `document_templates` row, category `engagement_letter`, installed as a **draft**. |
| Client questionnaire | The case-specific questions. Eight shared questions plus a set chosen by the matter's practice. | Resolved per matter and sent as the intake packet's questions. |
| Client intake form | The client's own data — identity, contact, entity, conflict-check, billing, referral. Answered once and reused. | A `document_templates` row, category `other`, installed as a **draft**. |

All three are defined in `backend/app/services/intake_starter_pack.py`. Nothing
in that module sends, approves, or files anything.

## Attorney review is required before use

Fee terms, client trust accounts, and contingency arrangements are regulated
differently in every jurisdiction. The two documents install with
`status = "draft"` and `approved_at = NULL`, so they cannot be selected as a fee
agreement for an intake packet until an attorney reviews them for the firm's
jurisdiction and approves them through the normal template path. Installing
again never touches a template of the same name that is already there: a firm
that edited or approved its own agreement keeps it.

The jurisdiction-neutral agreement leaves explicit places for jurisdiction-
specific language — `{{trust_account_terms}}`, `{{dispute_resolution_terms}}`,
`{{jurisdiction_required_terms}}` — rather than guessing at a rule. The North
Dakota variant answers them in its own wording and records the jurisdiction it
was drafted for on the template row, so a firm can see what law the text was
written against.

A field may carry a `default` only where a jurisdiction or a settled convention
decides it — the billing increment, the confidentiality rule, the days a
statement is due. Rates, retainers, trial fees, and venue never carry one: those
are the firm's to set, and a suggested number would be read as advice.

## How a matter type picks the questions

`resolve_practice()` reads two free-text labels the firm typed: the matter type
first, then the practice area. In the live library many matters are typed
`general` and carry the real signal in the practice area ("Family Law"), so
either label can decide. A specific alias wins over a generic one — "breach of
contract" resolves to litigation, not to the "contract" in business — and an
unrecognised matter falls back to the general pack rather than to no questions
at all.

| Practice | Recognised labels include |
|---|---|
| `family` | family law, divorce, custody, support, paternity, adoption, protective order |
| `criminal` | criminal defense, DUI, DWI, felony, misdemeanor, expungement, juvenile |
| `injury` | personal injury, car accident, premises, malpractice, wrongful death |
| `estate` | estate planning, probate, will, trust, guardianship, elder law |
| `employment` | employment, termination, discrimination, wage and hour, severance |
| `business` | business, commercial, contract, corporate, entity, M&A, NDA, SaaS |
| `real_estate` | real estate, landlord, tenant, eviction, lease, closing, title, HOA |
| `immigration` | immigration, visa, green card, naturalization, asylum, removal |
| `bankruptcy` | bankruptcy, chapter 7, chapter 13, foreclosure, garnishment |
| `litigation` | litigation, dispute, lawsuit, demand, subpoena, appeal, breach of contract |
| `mediation` | mediation, arbitration, settlement conference, collaborative |
| `general` | anything else |

Each practice also names the documents the client is asked to send back, which
become the intake packet's upload requirements.

## Field bindings and document automation

Every placeholder in both documents is declared in the template's
`variable_schema`, and each declares where its value comes from: a path from the
server-owned binding catalogue (`client.name`, `matter.case_number`,
`matter.hourly_rate`, …) or `manual`. Terms the matter's own records already
carry fill from them — the contingency percentage (`matter.contingency_percentage`),
the retainer and its replenishment threshold (`matter.retainer_amount`,
`matter.retainer_minimum_balance`, resolved from the matter's current retainer
record), and venue (`matter.venue`). Fee amounts the firm must decide — flat
fees, deposits, rate ranges — and scope and exclusions stay `manual`: a fee
term is decided by a person, never inferred from a record.

That is what makes the intake form worth collecting: its fields carry the same
bindings the rest of the document automation fills from, so a value the client
supplied once is reused rather than retyped into every later document.

## Endpoints

| Endpoint | Capability | Purpose |
|---|---|---|
| `GET /api/intake-starter-pack?matter_type=&practice_area=` | `manage_matters` | The questions, requested uploads, and document list for a matter type. |
| `GET /api/intake-starter-pack?matter_id=` | `manage_matters` | The same, reading the labels off an existing matter the caller may access. |
| `GET /api/intake-starter-pack/practices` | `manage_matters` | Every practice and the labels it recognises. |
| `POST /api/intake-starter-pack/documents` | `manage_documents` | Install any missing starter template as a draft. |

The MCP surface does not create templates: its write-like tools only produce
reviewable proposals. Installing the starter templates goes through the endpoint
above or Template Studio.

## Fillable PDF versions

`backend/scripts/generate_intake_starter_pdfs.py` renders the same markdown
sources as fillable AcroForm PDFs — the fee agreement, the intake form, and one
questionnaire per practice:

    python backend/scripts/generate_intake_starter_pdfs.py --out build/intake-pack

Every form field is named after the template's own variable or the question's
own key, so a returned PDF maps back onto the same bindings, and Template
Studio's existing AcroForm discovery finds the fields when the PDF is uploaded
as a source-backed template. Regenerate after changing any template body or
question; `backend/tests/unit/test_intake_starter_pdfs.py` fails if the printed
form and the template stop agreeing.

Two differences from the rendered markdown are deliberate. The fee agreement's
conditional fee sections (`{{#if hourly_rate}}` and the rest) all print, since a
paper form has no renderer to choose between them — strike the arrangements that
do not apply. And signature lines stay hand-signed: the portal signature flow is
separate.

## A completed sample for review

A partner reviewing a template wants to read a finished agreement, not a form of
placeholders:

    python backend/scripts/render_starter_sample.py --document hourly_fee_agreement_nd

It fills the template through the product's own renderer and writes a `.docx`.
The sample values are fictional and exist to show the wording; they are not a
recommendation about any firm's rates or terms. `--values` takes a JSON file to
override them.

## Where staff see it

* **Template Studio home** — "Standard client paperwork" adds the fee agreement
  and the intake form as drafts for review.
* **Start this case** — the drawer lists the fee agreement, the client
  questionnaire, and the client intake form as the three common pieces and
  seeds the questionnaire and requested uploads from the matter's practice when
  it opens. "Reset to standard questions" restores them after edits.
* **The matter intake panel** — "Use the standard questions for this matter
  type" fills the questionnaire and requested uploads. It replaces what is in
  those boxes, so it is a deliberate action, never automatic; staff edit the
  questions before sending.

Any subset may be sent. A fee agreement is optional: when one is included,
signing it opens the portal and starts the 24-hour follow-up clock; when none is
included, the portal opens on the first message and the follow-up still runs.
