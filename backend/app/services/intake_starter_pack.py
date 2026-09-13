"""The paperwork every new client receives, as reviewable starting content.

Matter initiation opens with the same three documents for every client:

* a **fee agreement** stating scope, fees, and the terms of representation;
* a **client questionnaire**, whose questions depend on what kind of matter
  this is — a custody case and a felony charge share almost no facts;
* a **client intake form**, the single record of the client's own data. It is
  answered once and then reused: its fields carry the same bindings the
  document automation fills from, so a value the client typed here does not
  get retyped into every later document.

Before this module a firm typed all three by hand into the intake screen for
every matter, so the questionnaire drifted matter to matter and the automation
had no agreed field names to fill from.

Everything here is content, not policy.  Fee terms, trust-account handling,
and contingency arrangements are regulated differently in every jurisdiction,
so the two documents install as **drafts**: a licensed attorney reviews and
approves them for the firm's jurisdiction before a client ever sees one.  This
module never sends anything, never approves a template, and never overwrites a
firm's own edits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_template import DocumentTemplate
from app.services import practice_resolution
from app.services.practice_resolution import (
    DEFAULT_PRACTICE,
    PackQuestion,
    PackUpload,
    Practice,
    resolve_practice,
)
from app.services.template_bindings import MANUAL_BINDING

__all__ = [
    "CORE_QUESTIONS",
    "DEFAULT_PRACTICE",
    "PackField",
    "PackQuestion",
    "PackUpload",
    "Practice",
    "StarterDocument",
    "resolve_practice",
]


@dataclass(frozen=True)
class PackField:
    """One template placeholder and where its value comes from.

    ``binding`` is a path from the closed server catalogue, or ``manual`` for
    a term only a person can decide — a rate schedule or a scope. Terms the
    matter's records already carry (contingency, retainer, venue) bind to
    them instead.
    """

    name: str
    label: str
    binding: str = MANUAL_BINDING
    #: A suggested value for a term a jurisdiction or a convention settles —
    #: never a fee, a rate, or an amount, which only the firm can decide.
    default: str = ""

    def as_dict(self) -> dict[str, Any]:
        field = {"name": self.name, "label": self.label, "binding": self.binding}
        if self.default:
            field["default"] = self.default
        return field


@dataclass(frozen=True)
class StarterDocument:
    """One installable template: markdown body plus its declared fields."""

    key: str
    title: str
    category: str
    description: str
    body: str
    fields: tuple[PackField, ...]
    #: The jurisdiction whose rules the wording was drafted against, or empty
    #: for a jurisdiction-neutral draft an attorney completes.
    jurisdiction: str = ""

    def variable_schema(self) -> dict[str, Any]:
        return {"fields": [entry.as_dict() for entry in self.fields]}

    def summary(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "category": self.category,
            "description": self.description,
            "jurisdiction": self.jurisdiction,
            "field_count": len(self.fields),
        }


def practices() -> tuple[Practice, ...]:
    """Return every practice pack, for firm-facing pickers and documentation."""

    return practice_resolution.practices()


#: Asked on every matter, whatever the practice.  Kept first so a client reads
#: the general questions before the ones specific to their case.
CORE_QUESTIONS: tuple[PackQuestion, ...] = (
    PackQuestion(
        "matter_summary",
        "In your own words, what happened and what do you need help with? Include how it started and where it stands today.",
    ),
    PackQuestion(
        "desired_outcome",
        "What outcome would you consider a good result in this matter?",
    ),
    PackQuestion(
        "other_parties",
        "Who else is involved? List every person, business, or agency on the other side, including their attorney if you know of one.",
    ),
    PackQuestion(
        "key_dates",
        "What dates matter in this case — filings, hearings, notices, accidents, agreements, or deadlines? Enter none if you are not aware of any.",
    ),
    PackQuestion(
        "prior_counsel",
        "Has any other attorney worked on this matter? If so, give the name and say whether the representation has ended. Enter none if not.",
    ),
    PackQuestion(
        "related_proceedings",
        "Is there any other open case, claim, investigation, or bankruptcy involving you or the other parties? Enter none if not.",
    ),
    PackQuestion(
        "contact_preferences",
        "How should we reach you, and are there times, numbers, or addresses we should avoid for privacy or safety reasons?",
    ),
    PackQuestion(
        "urgent_risks",
        "Is anything urgent — a court date, a lockout, a deadline to respond, or a safety concern? Describe it, or enter none.",
    ),
)


def questionnaire(
    matter_type: str | None, practice_area: str | None = None
) -> list[dict[str, Any]]:
    """Return the questions for a matter: shared ones first, then specific."""

    practice = resolve_practice(matter_type, practice_area)
    return [question.as_dict() for question in CORE_QUESTIONS + practice.questions]


def upload_requirements(
    matter_type: str | None, practice_area: str | None = None
) -> list[dict[str, Any]]:
    """Return the documents the client is asked to return with the answers."""

    practice = resolve_practice(matter_type, practice_area)
    return [upload.as_dict() for upload in practice.uploads]


_FEE_AGREEMENT_BODY = """# Agreement for Legal Representation

**{{firm_name}}**
{{firm_address}}
{{firm_phone}} · {{firm_email}}

This agreement is made on {{agreement_date}} between **{{firm_name}}** ("the Firm")
and **{{client_name}}** of {{client_street}}, {{client_city}}, {{client_state}} {{client_zip}}
("the Client"). It states what the Firm will do, what it will cost, and what each
side is responsible for. Please read it in full and ask about anything that is
not clear before signing.

## 1. The matter

The Firm is engaged in the matter identified as **{{matter_name}}** ({{matter_type}}).

{{matter_description}}

{{#if matter_jurisdiction}}This matter is handled under the law of {{matter_jurisdiction}}.{{/if}}
{{attorney_name}} is the attorney responsible for the matter. Other lawyers and
staff of the Firm may work on it under that attorney's supervision.

## 2. Scope of representation

The Firm agrees to provide the following services:

{{scope_of_representation}}

## 3. What is not included

This agreement does not cover the following, which would require a separate
written agreement:

{{excluded_matters}}

Appeals, enforcement of any judgment or order, and any new or related matter are
not included unless this agreement says so.

## 4. Fees

The Client agrees to pay for the Firm's services on a **{{billing_method}}** basis.

{{#if hourly_rate}}**Hourly fees.** Work is billed at {{hourly_rate}} for the
responsible attorney. Time for other lawyers and staff is billed at the rates in
this schedule: {{staff_rate_schedule}}. Time is recorded in increments and includes
work such as calls, correspondence, drafting, research, negotiation, travel, and
court appearances.{{/if}}

{{#if flat_fee_amount}}**Flat fee.** The fee for the services described in section 2
is {{flat_fee_amount}}, payable as follows: {{flat_fee_payment_terms}}. The flat fee
covers only the services described in section 2.{{/if}}

{{#if contingency_percentage}}**Contingency fee.** The Firm's fee is
{{contingency_percentage}} of any recovery, calculated as stated in
{{contingency_terms}}. If there is no recovery, no attorney fee is owed, but the
Client remains responsible for costs and expenses as described in section 6.{{/if}}

The Firm has made no promise about the outcome of this matter, the total amount of
fees, or how long the matter will take. Any estimate given is a good-faith
projection and is not a cap on fees.

## 5. Advance deposit and trust account

{{#if advance_deposit_amount}}The Client will deposit {{advance_deposit_amount}} with
the Firm before work begins. The Firm holds that deposit in its client trust account
and applies it against fees and costs as they are billed. {{deposit_replenishment_terms}}
Any unearned balance is refunded to the Client at the end of the representation.{{/if}}
{{#if trust_account_terms}}{{trust_account_terms}}{{/if}}

## 6. Costs and expenses

Costs and expenses are separate from fees and are the Client's responsibility. They
include filing fees, service of process, court reporters, transcripts, records,
expert and investigator fees, mediation fees, travel, and delivery charges.
{{cost_authorization_terms}}

## 7. Billing statements and payment

The Firm sends statements {{billing_cycle}} showing the work performed, the fees and
costs charged, and any trust balance. Payment is due on receipt.
{{late_payment_terms}} The Client should review each statement promptly and tell the
Firm about any question or disagreement within the time stated on the statement.

## 8. The Client's responsibilities

The Client agrees to be truthful and complete with the Firm, to provide requested
documents and information promptly, to keep the Firm informed of any change of
address, phone number, or email, to attend scheduled meetings and court dates, to
preserve documents, messages, and other records relating to the matter, and to make
the decisions the law reserves to the Client.

## 9. Communication and confidentiality

The Firm will keep the Client reasonably informed and will respond to inquiries
within a reasonable time. Communications between the Client and the Firm about this
matter are confidential and protected by the attorney-client privilege. Sharing them
with anyone outside the representation can waive that protection. The Firm may
communicate by email, phone, text, and its client portal unless the Client asks
otherwise in writing.

## 10. Ending the representation

The Client may end this representation at any time by written notice. The Firm may
withdraw as permitted by the rules of professional conduct, including for
non-payment, after giving reasonable notice and taking steps to protect the Client's
interests. Court approval is required to withdraw in a pending case. If the
representation ends, the Client remains responsible for fees earned and costs
incurred up to that point.

## 11. Files and records

At the end of the representation the Client may request the file. The Firm will
retain the file for {{file_retention_period}} after the matter closes and may then
destroy it without further notice.

## 12. No guarantee

Nothing in this agreement is a guarantee or a prediction of any result. Legal
matters are decided by courts, agencies, and opposing parties, whose decisions the
Firm does not control.

## 13. Disputes about this agreement

{{dispute_resolution_terms}}

## 14. Additional terms

{{jurisdiction_required_terms}}

## 15. Entire agreement

This document is the entire agreement between the Client and the Firm about this
matter. It replaces any earlier discussion or understanding and can only be changed
in a writing signed by both.

---

By signing, the Client confirms having read this agreement, having had the chance to
ask questions about it, and agreeing to its terms. The Client is entitled to a signed
copy.

**Client**

Signature: ______________________________  Date: ____________

Printed name: {{client_name}}

**{{firm_name}}**

Signature: ______________________________  Date: ____________

Printed name: {{attorney_name}}
"""


_INTAKE_FORM_BODY = """# Client Intake Form

**{{firm_name}}** · {{firm_phone}} · {{firm_email}}

Matter: {{matter_name}} ({{matter_type}})
Prepared: {{form_date}}

The Firm uses the information on this form to open the file, check for conflicts,
reach the Client, and prepare documents. Please answer every item that applies and
write "none" where it does not. Tell the Firm right away if any of it changes.

## 1. Client identification

| | |
|---|---|
| Full legal name | {{client_name}} |
| Other names used (maiden, former, business, aliases) | {{client_other_names}} |
| Date of birth | {{client_date_of_birth}} |
| Government ID number and type | {{client_identification}} |
| Marital status | {{client_marital_status}} |
| Employer and occupation | {{client_employment}} |

## 2. Contact information

| | |
|---|---|
| Street address | {{client_street}} |
| City, state, ZIP | {{client_city}}, {{client_state}} {{client_zip}} |
| Mailing address, if different | {{client_mailing_address}} |
| Mobile phone | {{client_phone}} |
| Other phone | {{client_alternate_phone}} |
| Email | {{client_email}} |
| Preferred way to be contacted | {{client_contact_preference}} |
| Safe to leave a message? | {{client_message_permission}} |
| Best times to reach you | {{client_contact_times}} |
| Language and interpreter needs | {{client_language}} |

## 3. If the client is a business or organization

| | |
|---|---|
| Legal entity name | {{entity_name}} |
| Entity type and state of formation | {{entity_type}} |
| Tax identification number | {{entity_tax_id}} |
| Person authorized to instruct the Firm | {{entity_authorized_signer}} |
| That person's title, phone, and email | {{entity_signer_contact}} |

## 4. Emergency and authorized contacts

| | |
|---|---|
| Emergency contact name and relationship | {{emergency_contact}} |
| Emergency contact phone | {{emergency_contact_phone}} |
| People we may discuss the matter with | {{authorized_disclosure_contacts}} |

Naming someone here allows the Firm to share otherwise confidential information
with that person. Leave it blank if you would rather the Firm speak only with you.

## 5. The matter

| | |
|---|---|
| What the matter is about | {{matter_description}} |
| Represented side or role | {{matter_role}} |
| Other party or parties | {{matter_counterparty}} |
| Other party's attorney, if known | {{opposing_counsel}} |
| Court or agency, if a case exists | {{matter_court}} |
| Case or file number | {{matter_case_number}} |
| Judge, if assigned | {{matter_judge}} |
| State or jurisdiction | {{matter_jurisdiction}} |
| Known deadlines or court dates | {{known_deadlines}} |

## 6. Conflict of interest check

The Firm must check whether representing you would conflict with its duties to
someone else. Please list everyone connected to this matter.

| | |
|---|---|
| Spouse or partner | {{conflict_spouse}} |
| Children and other family members involved | {{conflict_family}} |
| Businesses you own or work for that are involved | {{conflict_businesses}} |
| Insurers, lenders, or employers involved | {{conflict_organizations}} |
| Witnesses and other people involved | {{conflict_witnesses}} |
| Anyone else who might be affected by the outcome | {{conflict_other}} |

## 7. Billing and payment

| | |
|---|---|
| Person or entity responsible for payment | {{billing_responsible_party}} |
| Billing address, if different from above | {{billing_address}} |
| Billing contact email | {{billing_email}} |
| Preferred payment method | {{payment_method}} |
| Insurance that may cover fees or the claim | {{fee_insurance}} |

If someone other than the Client pays the fees, that does not make them the
Firm's client and does not give them a right to direct the representation or to
receive confidential information.

## 8. How you reached us

| | |
|---|---|
| Referred by | {{referral_source}} |
| Previous attorney on this matter | {{prior_counsel}} |
| Prior matters with this Firm | {{prior_matters}} |

## 9. Client confirmation

I confirm that the information on this form is true and complete as far as I know,
and that I will tell the Firm if it changes. I understand that the Firm relies on
it to check for conflicts and to prepare documents in this matter.

Signature: ______________________________  Date: ____________

Printed name: {{client_name}}

*Firm use — received {{form_received_date}}, reviewed by {{prepared_by}}.*
"""


FEE_AGREEMENT = StarterDocument(
    key="fee_agreement",
    title="Standard Fee Agreement — Legal Representation",
    category="engagement_letter",
    description=(
        "Standard engagement terms sent at matter initiation. Fee, trust-account, "
        "and contingency terms are jurisdiction-regulated: an attorney must review "
        "and approve this template for the firm's jurisdiction before it is sent."
    ),
    body=_FEE_AGREEMENT_BODY,
    fields=(
        PackField("firm_name", "Firm name", "firm.name"),
        PackField("firm_address", "Firm address", "firm.address"),
        PackField("firm_phone", "Firm phone", "firm.phone"),
        PackField("firm_email", "Firm email", "firm.email"),
        PackField("agreement_date", "Agreement date"),
        PackField("client_name", "Client name", "client.name"),
        PackField("client_street", "Client street", "client.address.street"),
        PackField("client_city", "Client city", "client.address.city"),
        PackField("client_state", "Client state", "client.address.state"),
        PackField("client_zip", "Client ZIP", "client.address.zip"),
        PackField("matter_name", "Matter name", "matter.name"),
        PackField("matter_type", "Matter type", "matter.type"),
        PackField("matter_description", "Matter description", "matter.description"),
        PackField("matter_jurisdiction", "Jurisdiction", "matter.jurisdiction"),
        PackField("attorney_name", "Responsible attorney", "attorney.name"),
        PackField("scope_of_representation", "Services included"),
        PackField("excluded_matters", "Services excluded"),
        PackField("billing_method", "Billing method", "matter.billing_method"),
        PackField("hourly_rate", "Hourly rate", "matter.hourly_rate"),
        PackField("staff_rate_schedule", "Other timekeeper rates"),
        PackField("flat_fee_amount", "Flat fee amount"),
        PackField("flat_fee_payment_terms", "Flat fee payment terms"),
        PackField(
            "contingency_percentage",
            "Contingency percentage",
            "matter.contingency_percentage",
        ),
        PackField("contingency_terms", "Contingency calculation terms"),
        PackField("advance_deposit_amount", "Advance deposit amount"),
        PackField("deposit_replenishment_terms", "Deposit replenishment terms"),
        PackField("trust_account_terms", "Trust account terms"),
        PackField("cost_authorization_terms", "Cost authorization terms"),
        PackField("billing_cycle", "Billing cycle", "matter.billing_cycle"),
        PackField("late_payment_terms", "Late payment terms"),
        PackField("file_retention_period", "File retention period"),
        PackField("dispute_resolution_terms", "Fee dispute resolution terms"),
        PackField("jurisdiction_required_terms", "Jurisdiction-required terms"),
    ),
)


CLIENT_INTAKE_FORM = StarterDocument(
    key="client_intake_form",
    title="Client Intake Form",
    category="other",
    description=(
        "The client's own data, collected once at matter initiation: identity, "
        "contact, entity, conflict-check, and billing details. Its fields carry "
        "the bindings the document automation fills from, so answers here are "
        "reused rather than retyped."
    ),
    body=_INTAKE_FORM_BODY,
    fields=(
        PackField("firm_name", "Firm name", "firm.name"),
        PackField("firm_phone", "Firm phone", "firm.phone"),
        PackField("firm_email", "Firm email", "firm.email"),
        PackField("form_date", "Form prepared date"),
        PackField("client_name", "Client full legal name", "client.name"),
        PackField("client_other_names", "Other names used"),
        PackField("client_date_of_birth", "Date of birth"),
        PackField("client_identification", "Government ID"),
        PackField("client_marital_status", "Marital status"),
        PackField("client_employment", "Employer and occupation"),
        PackField("client_street", "Street address", "client.address.street"),
        PackField("client_city", "City", "client.address.city"),
        PackField("client_state", "State", "client.address.state"),
        PackField("client_zip", "ZIP", "client.address.zip"),
        PackField("client_mailing_address", "Mailing address"),
        PackField("client_phone", "Mobile phone", "client.phone"),
        PackField("client_alternate_phone", "Other phone"),
        PackField("client_email", "Email", "client.email"),
        PackField("client_contact_preference", "Preferred contact method"),
        PackField("client_message_permission", "Message permission"),
        PackField("client_contact_times", "Best times to reach"),
        PackField("client_language", "Language and interpreter needs"),
        PackField("entity_name", "Entity legal name"),
        PackField("entity_type", "Entity type and state"),
        PackField("entity_tax_id", "Entity tax identification number"),
        PackField("entity_authorized_signer", "Authorized signer"),
        PackField("entity_signer_contact", "Authorized signer contact"),
        PackField("emergency_contact", "Emergency contact"),
        PackField("emergency_contact_phone", "Emergency contact phone"),
        PackField("authorized_disclosure_contacts", "Authorized disclosure contacts"),
        PackField("matter_name", "Matter name", "matter.name"),
        PackField("matter_type", "Matter type", "matter.type"),
        PackField("matter_description", "Matter description", "matter.description"),
        PackField("matter_role", "Represented side", "matter.role"),
        PackField("matter_counterparty", "Other party", "matter.counterparty"),
        PackField("opposing_counsel", "Opposing counsel"),
        PackField("matter_court", "Court or agency", "matter.court"),
        PackField("matter_case_number", "Case number", "matter.case_number"),
        PackField("matter_judge", "Judge", "matter.judge"),
        PackField("matter_jurisdiction", "Jurisdiction", "matter.jurisdiction"),
        PackField("known_deadlines", "Known deadlines"),
        PackField("conflict_spouse", "Spouse or partner"),
        PackField("conflict_family", "Family members involved"),
        PackField("conflict_businesses", "Businesses involved"),
        PackField("conflict_organizations", "Insurers, lenders, employers"),
        PackField("conflict_witnesses", "Witnesses"),
        PackField("conflict_other", "Other interested people"),
        PackField("billing_responsible_party", "Responsible for payment"),
        PackField("billing_address", "Billing address"),
        PackField("billing_email", "Billing contact email"),
        PackField("payment_method", "Preferred payment method"),
        PackField("fee_insurance", "Insurance that may apply"),
        PackField("referral_source", "Referred by"),
        PackField("prior_counsel", "Previous attorney"),
        PackField("prior_matters", "Prior matters with the firm"),
        PackField("form_received_date", "Received date"),
        PackField("prepared_by", "Reviewed by", "current_user.prepared_by"),
    ),
)


_HOURLY_ND_BODY = """# Attorney-Client Hourly Fee Agreement

**CLIENT:** {{client_name}} (the "Client")
**FIRM:** {{firm_name}} (the "Firm")
**ATTORNEY:** {{attorney_name}} (the "Attorney")

## Section 1. Purpose

The Client employs the Attorney to represent and advise the Client in the
following matter: {{matter_name}}{{#if matter_counterparty}}, against
{{matter_counterparty}}{{/if}}.

{{scope_of_representation}}

This Agreement covers only that matter. It does not obligate the Attorney to
represent the Client in any other matter, and a separate written agreement is
required for one. If the Client asks the Firm to perform services on another
matter after signing this Agreement, the terms of this Agreement apply to that
work until a new fee agreement is signed.

{{representation_limits}}

The retainer described in Section 2 does not include the costs and expenses
described in Section 3.

## Section 2. Retainer and attorney fees

The Client agrees to pay a retainer of {{retainer_amount}}. The Firm deposits
the retainer in its client trust account and applies it against fees and costs
as they are billed. The Client remains responsible for every invoice, whether or
not the retainer covers it.

Work is billed at {{hourly_rate}} for the Attorney. Time is recorded in
increments of {{billing_increment}} and rounded up to the nearest increment.
Telephone calls are billed at a minimum of {{call_minimum}}. Travel to and from
court appearances and meetings outside the office is billed at the same hourly
rate.

The Firm may assign work to another attorney of the Firm or to legal assistants,
paralegals, and law clerks working under the Firm's supervision. Paralegal and
law clerk time is billed at {{staff_rate_range}}. Other attorneys of the Firm are
billed at their applicable rate, currently {{attorney_rate_range}}. Rates may
increase during the representation.

{{#if trial_fee_amount}}The Firm's trial fee is {{trial_fee_amount}} per day.
Payment for the first day is due {{trial_fee_due}} before the scheduled trial
date, and additional days are billed at the same rate in the normal course of
billing.{{/if}}

Fees and costs are billed {{billing_cycle}}. A balance is due
{{payment_due_days}} from the date of the statement. {{late_charge_terms}} The
Firm may stop work if a statement is not paid in full when due.

The Firm will tell the Client when the trust balance falls below
{{retainer_minimum_balance}} and additional funds must be deposited in the
trust account to cover projected fees and costs, and the Client agrees to
deposit the requested sum promptly. The Firm may stop work if the deposit is
not made.

The Client grants the Firm a lien, to the extent the law allows, against funds
held for the Client in the Firm's trust account and against any money or property
recovered in this matter. The lien is released when the Client's balance is paid
in full. The Client authorizes the Firm to pay itself earned fees and incurred
costs from those funds before releasing the balance to the Client.

If the Firm must bring an action to recover fees or costs owed under this
Agreement, the Firm is entitled to reasonable attorney fees as determined by the
court in that action. Venue for such an action is {{venue}}.

## Section 3. Costs and expenses

In addition to fees, the Client agrees to pay all out-of-pocket costs the Firm
incurs on the Client's behalf. Costs may include filing and trial fees, service
of process, photocopying, records, appraisals, investigation and deposition
costs, expert and witness fees, mediation fees, travel, and any other cost the
Firm considers necessary to the representation. {{cost_authorization_terms}}

The Client is responsible for fees charged by other professionals engaged on the
Client's case, and the Firm is not liable as the Client's agent for those fees.

Even where the Firm asks a court to order another party to pay part or all of the
Client's fees and costs, no other person is responsible for them. Awards of fees
are unpredictable, and the Client remains responsible for any amount owed to the
Firm.

## Section 4. Payment of fees by someone other than the Client

The Client consents to the Firm accepting payment of the Client's fees from
someone other than the Client. A third party who pays is not the Firm's client
and has no right to direct the representation.

The Client's communications with the Firm are confidential under
{{confidentiality_rule}}, and the Firm will not disclose them to a person paying
the Client's fees unless the Client consents in writing. The Client may give that
consent on the Firm's written consent form and may withdraw it at any time.

The Firm may limit a paying third party's access to the Attorney if that access
becomes excessive, and time the Firm spends responding to a third party is billed
to the Client.

## Section 5. Withdrawal by the Attorney

To the extent the law and the rules of professional conduct allow, the Attorney
may withdraw from the representation for good cause on written notice to the
Client's last known address. Good cause includes a material misrepresentation by
the Client, the discovery of facts that would make continued representation
inconsistent with professional standards, and the failure of the Client — or of
anyone who agreed to pay on the Client's behalf — to pay fees or expenses when
due. Withdrawal in a pending case requires court approval.

If the Firm withdraws, it may apply the Client's retainer and other trust funds
to the Client's outstanding balance.

## Section 6. Discharge of the Attorney

The Client may discharge the Attorney at any time, with or without cause, and may
be entitled to a refund. A refund is calculated by applying the hourly rates in
Section 2 to the work completed and deducting fees earned and costs incurred from
the Client's trust balance. The Client remains responsible for fees earned and
costs advanced through the date of discharge. The Client is free to consult
another attorney about this Agreement or about any disagreement over fees.

## Section 7. No guarantee of outcome

The Firm will use its best efforts in representing the Client. The Attorney has
made no promise about the outcome of this matter or the success of any strategy,
and cannot do so. Any view the Attorney expresses about the matter is an opinion
offered to help the Client evaluate the case. Neither the Firm nor the Attorney
can determine in advance how much time the matter will take.

## Section 8. Authority to sign on the Client's behalf

{{signing_authority_terms}}

## Section 9. The Client's responsibilities and enforcement

The Client agrees to cooperate with the Firm, to be truthful with the Firm at all
times, to keep the Firm informed of anything bearing on the matter, to keep
appointments and court dates, to respond promptly to the Firm's requests, to
produce documents and appear for depositions as needed, and to report any change
of address, telephone number, email address, or employment within five days.

If the Client fails to appear at a hearing or trial, the Client authorizes the
Firm to proceed as it judges best in the circumstances.

The Firm's failure to require strict performance of any provision of this
Agreement at any time does not waive that provision or limit the Firm's right to
enforce it later.

{{additional_terms}}

---

**THE FIRM HAS NOT ACCEPTED THIS CASE AND WILL NOT ADVISE THE CLIENT, ACT AS THE
CLIENT'S ATTORNEY, SIGN COURT DOCUMENTS, OR APPEAR ON THE CLIENT'S BEHALF UNTIL
THE CLIENT HAS SIGNED THIS AGREEMENT AND PAID THE RETAINER.**

The Client has read this Agreement, has received a copy of it, and agrees to its
terms. There are no verbal agreements modifying or expanding it. The Client is
free to review this Agreement with another attorney before signing.

Dated: {{agreement_date}}

**Client**

Signature: ______________________________  Date: ____________

Printed name: {{client_name}}

Phone: {{client_phone}}

Email: {{client_email}}

Address: {{client_street}}, {{client_city}}, {{client_state}} {{client_zip}}

**{{firm_name}}**

Signature: ______________________________  Date: ____________

By: {{attorney_name}}

{{firm_address}} · {{firm_phone}} · {{firm_email}}
"""


HOURLY_FEE_AGREEMENT_ND = StarterDocument(
    key="hourly_fee_agreement_nd",
    title="Attorney-Client Hourly Fee Agreement (North Dakota)",
    category="engagement_letter",
    description=(
        "Hourly engagement terms drafted against North Dakota practice: retainer "
        "held in trust, replenishment, the firm's lien, third-party payment under "
        "N.D.R. Prof. Conduct 1.6, withdrawal, discharge and refund, and venue for "
        "a fee action. Rates, the retainer, and any trial fee are the firm's to "
        "set; an attorney reviews and approves before it is sent."
    ),
    jurisdiction="North Dakota",
    body=_HOURLY_ND_BODY,
    fields=(
        PackField("firm_name", "Firm name", "firm.name"),
        PackField("firm_address", "Firm address", "firm.address"),
        PackField("firm_phone", "Firm phone", "firm.phone"),
        PackField("firm_email", "Firm email", "firm.email"),
        PackField("attorney_name", "Responsible attorney", "attorney.name"),
        PackField("agreement_date", "Agreement date"),
        PackField("client_name", "Client name", "client.name"),
        PackField("client_phone", "Client phone", "client.phone"),
        PackField("client_email", "Client email", "client.email"),
        PackField("client_street", "Client street", "client.address.street"),
        PackField("client_city", "Client city", "client.address.city"),
        PackField("client_state", "Client state", "client.address.state"),
        PackField("client_zip", "Client ZIP", "client.address.zip"),
        PackField("matter_name", "Matter", "matter.name"),
        PackField("matter_counterparty", "Opposing party", "matter.counterparty"),
        PackField("scope_of_representation", "Services included"),
        PackField(
            "representation_limits",
            "Stage this engagement covers",
            default=(
                "This Agreement covers pre-trial and settlement representation. If "
                "the matter proceeds to a contested hearing or trial, additional "
                "fees apply as described in Section 2."
            ),
        ),
        PackField("retainer_amount", "Retainer", "matter.retainer_amount"),
        PackField("hourly_rate", "Attorney hourly rate", "matter.hourly_rate"),
        PackField(
            "billing_increment",
            "Billing increment",
            default="0.1 hour (six minutes)",
        ),
        PackField("call_minimum", "Telephone call minimum", default="0.25 hour"),
        PackField(
            "retainer_minimum_balance",
            "Retainer replenishment threshold",
            "matter.retainer_minimum_balance",
        ),
        PackField("staff_rate_range", "Paralegal and law clerk rates"),
        PackField("attorney_rate_range", "Other attorney rates"),
        PackField("trial_fee_amount", "Trial fee per day"),
        PackField("trial_fee_due", "Trial fee due", default="45 days"),
        PackField("billing_cycle", "Billing cycle", "matter.billing_cycle"),
        PackField("payment_due_days", "Payment due", default="ten (10) days"),
        PackField(
            "late_charge_terms",
            "Late charge",
            default=(
                "A late charge of 1.5 percent per month (18 percent per year) may "
                "be applied to a balance more than thirty (30) days old."
            ),
        ),
        PackField(
            "cost_authorization_terms",
            "Cost authorization",
            default=(
                "The Firm will obtain the Client's approval before incurring any "
                "single cost over the amount the Client sets in writing."
            ),
        ),
        PackField("venue", "Venue for a fee action", "matter.venue"),
        PackField(
            "confidentiality_rule",
            "Confidentiality rule",
            default="Rule 1.6 of the North Dakota Rules of Professional Conduct",
        ),
        PackField(
            "signing_authority_terms",
            "Authority to sign on the Client's behalf",
            default=(
                "The Client authorizes the Attorney to sign, on the Client's "
                "behalf and within the scope of this representation, procedural "
                "documents such as pleadings, verifications, stipulations as to "
                "scheduling, dismissals, and orders. The Attorney will not settle "
                "the matter, or sign a settlement agreement or release, without "
                "the Client's authority."
            ),
        ),
        PackField("additional_terms", "Additional terms"),
    ),
)


DOCUMENTS: tuple[StarterDocument, ...] = (
    FEE_AGREEMENT,
    HOURLY_FEE_AGREEMENT_ND,
    CLIENT_INTAKE_FORM,
)


def documents() -> tuple[StarterDocument, ...]:
    """Return the templates that accompany the questionnaire, in send order."""

    return DOCUMENTS


def pack(matter_type: str | None, practice_area: str | None = None) -> dict[str, Any]:
    """Return everything step one of matter initiation needs for a matter."""

    practice = resolve_practice(matter_type, practice_area)
    return {
        "practice": practice.slug,
        "practice_label": practice.label,
        "matter_type": matter_type or "",
        "practice_area": practice_area or "",
        "questions": questionnaire(matter_type, practice_area),
        "upload_requirements": upload_requirements(matter_type, practice_area),
        "documents": [document.summary() for document in DOCUMENTS],
    }


async def _existing(
    db: AsyncSession, tenant_id: UUID, title: str
) -> DocumentTemplate | None:
    """Return the tenant's template with this title, however it was cased."""

    return await db.scalar(
        select(DocumentTemplate).where(
            DocumentTemplate.tenant_id == tenant_id,
            func.lower(DocumentTemplate.title) == title.lower(),
        )
    )


async def install(db: AsyncSession, tenant_id: UUID) -> list[dict[str, Any]]:
    """Add any missing starter template to the tenant's library as a draft.

    Installation is idempotent by title and never touches a template that is
    already there: a firm that has edited or approved its own fee agreement
    keeps it. New rows stay ``draft`` and unapproved, so nothing reaches a
    client until an attorney approves it through the normal review path.
    """

    installed: list[dict[str, Any]] = []
    for document in DOCUMENTS:
        existing = await _existing(db, tenant_id, document.title)
        if existing is not None:
            installed.append(
                {
                    **document.summary(),
                    "template_id": str(existing.id),
                    "created": False,
                }
            )
            continue
        template = DocumentTemplate(
            tenant_id=tenant_id,
            title=document.title,
            body=document.body,
            category=document.category,
            description=document.description,
            visibility="tenant",
            status="draft",
            # The column defaults to active, which would list the draft as
            # generatable while the render gate still refuses it for lacking a
            # published version. A draft is inactive until it is published.
            is_active=False,
            format="markdown",
            kind="client_intake",
            jurisdiction=document.jurisdiction or None,
            variable_schema=document.variable_schema(),
        )
        db.add(template)
        await db.flush()
        installed.append(
            {**document.summary(), "template_id": str(template.id), "created": True}
        )
    await db.commit()
    return installed
