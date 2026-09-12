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

from dataclasses import dataclass, field as dataclass_field
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_template import DocumentTemplate
from app.services.template_bindings import MANUAL_BINDING


@dataclass(frozen=True)
class PackQuestion:
    """One questionnaire prompt.

    ``key`` follows the intake schema's key pattern so the resolved pack can be
    submitted to ``POST /api/matters/{id}/intake`` without renaming anything.
    """

    key: str
    label: str
    required: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "required": self.required}


@dataclass(frozen=True)
class PackUpload:
    """One document the client is asked to send back with the questionnaire."""

    key: str
    label: str
    required: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": f"upload_{self.key}",
            "label": self.label,
            "required": self.required,
        }


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


@dataclass(frozen=True)
class Practice:
    """A matter-type family and the questions it needs answered."""

    slug: str
    label: str
    aliases: tuple[str, ...]
    questions: tuple[PackQuestion, ...]
    uploads: tuple[PackUpload, ...] = dataclass_field(default_factory=tuple)


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


_PRACTICES: tuple[Practice, ...] = (
    Practice(
        "family",
        "Family and domestic relations",
        (
            "family",
            "family law",
            "domestic",
            "domestic relations",
            "divorce",
            "dissolution",
            "custody",
            "visitation",
            "support",
            "child support",
            "paternity",
            "adoption",
            "protective order",
            "modification",
        ),
        (
            PackQuestion(
                "household",
                "Who currently lives in your household, and what is each person's relationship to you?",
            ),
            PackQuestion(
                "marriage_facts",
                "If you are or were married, give the date and place of the marriage and the date of separation. Enter not applicable if you were never married.",
            ),
            PackQuestion(
                "children",
                "List each child involved: name, date of birth, and who they live with now.",
            ),
            PackQuestion(
                "custody_arrangement",
                "What parenting or custody arrangement is in place today, and what arrangement are you asking for?",
            ),
            PackQuestion(
                "support_status",
                "Is any child support, spousal support, or alimony being paid or received now? Give amounts and how often.",
            ),
            PackQuestion(
                "income_sources",
                "What are your sources of income, and what do you understand the other party's income to be?",
            ),
            PackQuestion(
                "assets_debts",
                "List the significant property and debts — homes, vehicles, accounts, retirement, businesses, loans — and whose name each is in.",
            ),
            PackQuestion(
                "existing_orders",
                "Are there existing court orders, agreements, or protective orders between you and the other party? Enter none if not.",
            ),
            PackQuestion(
                "safety_concerns",
                "Are there concerns about domestic violence, substance use, or the safety of a child? Enter none if not.",
                required=False,
            ),
        ),
        (
            PackUpload(
                "family_orders", "Any existing court orders, decrees, or agreements"
            ),
            PackUpload(
                "family_income",
                "Your two most recent pay stubs and last year's tax return",
            ),
            PackUpload(
                "family_vitals",
                "Marriage certificate and birth certificates for any children",
            ),
        ),
    ),
    Practice(
        "criminal",
        "Criminal defense",
        (
            "criminal",
            "criminal defense",
            "defense",
            "dui",
            "dwi",
            "felony",
            "misdemeanor",
            "expungement",
            "traffic",
            "juvenile",
            "probation",
        ),
        (
            PackQuestion(
                "charges",
                "What are you charged with or being investigated for, and what agency arrested or contacted you?",
            ),
            PackQuestion(
                "arrest_details",
                "When and where did the arrest, citation, or contact happen? Describe what occurred.",
            ),
            PackQuestion(
                "court_information",
                "What court is the case in, what is the case or citation number, and when is your next court date?",
            ),
            PackQuestion(
                "custody_status",
                "Are you in custody, released on bond, or on any conditions of release? Describe the conditions.",
            ),
            PackQuestion(
                "statements_made",
                "Did you speak with police or any investigator, sign anything, or consent to a search? Describe what happened.",
            ),
            PackQuestion(
                "witnesses_evidence",
                "Who witnessed the events, and is there video, messages, or other evidence you know of?",
            ),
            PackQuestion(
                "criminal_history",
                "Have you been arrested, charged, or convicted before, including in other states? Enter none if not.",
            ),
            PackQuestion(
                "collateral_impact",
                "Does this case affect your job, licence, immigration status, housing, or school? Explain how.",
                required=False,
            ),
        ),
        (
            PackUpload(
                "criminal_charging", "Citation, complaint, or charging document"
            ),
            PackUpload("criminal_bond", "Bond paperwork and conditions of release"),
            PackUpload(
                "criminal_notices",
                "Any court notices, subpoenas, or letters you have received",
            ),
        ),
    ),
    Practice(
        "injury",
        "Personal injury",
        (
            "injury",
            "personal injury",
            "accident",
            "car accident",
            "auto accident",
            "premises",
            "slip and fall",
            "malpractice",
            "wrongful death",
            "product liability",
            "workers compensation",
        ),
        (
            PackQuestion(
                "incident_details",
                "Describe how the injury happened: date, time, place, and what you believe caused it.",
            ),
            PackQuestion(
                "injuries",
                "What injuries did you suffer, and what symptoms do you still have?",
            ),
            PackQuestion(
                "treatment",
                "List every provider who has treated you — hospitals, doctors, therapists — and the dates of treatment.",
            ),
            PackQuestion(
                "insurance_contacts",
                "Which insurance companies have contacted you, and did you give a recorded statement or sign anything?",
            ),
            PackQuestion(
                "coverage",
                "What insurance do you carry — health, auto, disability — and what do you know about the other side's coverage?",
            ),
            PackQuestion(
                "lost_income",
                "Have you missed work or lost income? Give your employer, your pay, and the time missed.",
            ),
            PackQuestion(
                "prior_injuries",
                "Had you injured the same body part before this incident? Enter none if not.",
            ),
            PackQuestion(
                "scene_evidence",
                "Are there photos, video, a police or incident report, or witnesses? Describe what exists and who has it.",
            ),
        ),
        (
            PackUpload("injury_report", "Police, incident, or accident report"),
            PackUpload(
                "injury_medical",
                "Medical records, bills, and discharge instructions you have",
            ),
            PackUpload(
                "injury_insurance",
                "Insurance declarations page and any letters from an insurer",
            ),
            PackUpload("injury_photos", "Photos of the scene, vehicles, or injuries"),
        ),
    ),
    Practice(
        "estate",
        "Estate planning and probate",
        (
            "estate",
            "estate planning",
            "probate",
            "will",
            "trust",
            "guardianship",
            "conservatorship",
            "power of attorney",
            "elder law",
            "succession",
        ),
        (
            PackQuestion(
                "estate_goal",
                "Are you planning your own estate, administering someone else's, or contesting a plan? Describe the situation.",
            ),
            PackQuestion(
                "family_structure",
                "Who are your spouse, children, and other people you want to provide for or exclude?",
            ),
            PackQuestion(
                "existing_documents",
                "Do you already have a will, trust, power of attorney, or healthcare directive? Give the dates if you know them.",
            ),
            PackQuestion(
                "estate_assets",
                "List your significant assets — real property, accounts, retirement, life insurance, business interests — and roughly what each is worth.",
            ),
            PackQuestion(
                "beneficiary_designations",
                "Which accounts or policies name a beneficiary directly, and who is named?",
            ),
            PackQuestion(
                "fiduciaries",
                "Who do you want to serve as executor, trustee, agent, or guardian, and have you asked them?",
            ),
            PackQuestion(
                "decedent_details",
                "If someone has died, give their name, date of death, and where they lived. Enter not applicable if no one has died.",
            ),
            PackQuestion(
                "estate_concerns",
                "Is there any dispute, a beneficiary with special needs, or property in another state? Enter none if not.",
                required=False,
            ),
        ),
        (
            PackUpload("estate_documents", "Existing will, trust, or directives"),
            PackUpload("estate_deeds", "Deeds, titles, and recent account statements"),
            PackUpload(
                "estate_death_certificate", "Death certificate, if someone has died"
            ),
        ),
    ),
    Practice(
        "employment",
        "Employment and labor",
        (
            "employment",
            "labor",
            "termination",
            "wrongful termination",
            "discrimination",
            "harassment",
            "retaliation",
            "wage-hour",
            "wage and hour",
            "classification",
            "severance",
            "non-compete",
            "investigation",
        ),
        (
            PackQuestion(
                "employment_facts",
                "Who is or was your employer, what was your job, and what dates did you work there?",
            ),
            PackQuestion(
                "pay_terms",
                "How were you paid — salary, hourly, commission — and what were your hours and benefits?",
            ),
            PackQuestion(
                "what_happened",
                "Describe what the employer did, including who was involved and when each event occurred.",
            ),
            PackQuestion(
                "complaints_made",
                "Did you report the problem to anyone — a manager, HR, or an agency? Describe what you reported and the response.",
            ),
            PackQuestion(
                "employment_documents",
                "What did you sign — offer letter, handbook acknowledgement, arbitration, non-compete, or severance? Enter none if not.",
            ),
            PackQuestion(
                "agency_filings",
                "Have you filed with the EEOC, a state agency, or for unemployment benefits? Give the dates and any case numbers.",
            ),
            PackQuestion(
                "current_status",
                "Are you still employed there? If not, how did the employment end and what were you told?",
            ),
            PackQuestion(
                "employment_witnesses",
                "Which co-workers or others saw or knew about what happened?",
                required=False,
            ),
        ),
        (
            PackUpload(
                "employment_contract",
                "Offer letter, contract, handbook, or arbitration agreement",
            ),
            PackUpload(
                "employment_pay", "Recent pay stubs, W-2s, and any severance paperwork"
            ),
            PackUpload(
                "employment_correspondence",
                "Emails, texts, warnings, or reviews related to what happened",
            ),
        ),
    ),
    Practice(
        "business",
        "Business and transactional",
        (
            "business",
            "commercial",
            "contract",
            "corporate",
            "entity",
            "formation",
            "m&a",
            "merger",
            "acquisition",
            "vendor",
            "nda",
            "saas",
            "procurement",
            "governance",
            "partnership",
        ),
        (
            PackQuestion(
                "entity_details",
                "What is the legal name of the business, what type of entity is it, and in what state was it formed?",
            ),
            PackQuestion(
                "ownership",
                "Who owns the business and in what percentages, and who is authorized to sign on its behalf?",
            ),
            PackQuestion(
                "transaction_goal",
                "What are you trying to accomplish — form an entity, sign or review a deal, resolve a dispute? Describe it.",
            ),
            PackQuestion(
                "counterparties",
                "Who is on the other side of the transaction or dispute, and who represents them?",
            ),
            PackQuestion(
                "existing_agreements",
                "What agreements are already in place — operating agreement, bylaws, leases, vendor or customer contracts? Enter none if not.",
            ),
            PackQuestion(
                "deal_terms",
                "What terms have been discussed or agreed so far, including price, timing, and any conditions?",
            ),
            PackQuestion(
                "business_deadlines",
                "Are there closing dates, renewal or termination dates, or filing deadlines we must meet? Enter none if not.",
            ),
            PackQuestion(
                "regulatory_exposure",
                "Does the business operate in a regulated area, hold licences, or handle personal data? Describe it.",
                required=False,
            ),
        ),
        (
            PackUpload(
                "business_formation",
                "Formation documents, operating agreement, or bylaws",
            ),
            PackUpload("business_agreements", "The contracts or drafts at issue"),
            PackUpload("business_correspondence", "Correspondence with the other side"),
        ),
    ),
    Practice(
        "real_estate",
        "Real estate and property",
        (
            "real estate",
            "property",
            "landlord",
            "tenant",
            "eviction",
            "lease",
            "closing",
            "title",
            "boundary",
            "hoa",
            "zoning",
            "construction",
            "foreclosure defense",
        ),
        (
            PackQuestion(
                "property_address",
                "What is the full address of the property, and what kind of property is it?",
            ),
            PackQuestion(
                "property_interest",
                "What is your interest in the property — owner, buyer, seller, landlord, tenant, lender, or neighbor?",
            ),
            PackQuestion(
                "property_issue",
                "Describe the problem or transaction, including how it started and where it stands.",
            ),
            PackQuestion(
                "property_documents",
                "What documents govern the property — deed, lease, purchase agreement, HOA rules, survey? Enter none if not.",
            ),
            PackQuestion(
                "payments",
                "What payments are owed or in dispute — rent, mortgage, dues, deposits — and what is the current balance?",
            ),
            PackQuestion(
                "notices_served",
                "Have any notices been given or received, such as a notice to vacate, default, or lien? Give the dates.",
            ),
            PackQuestion(
                "other_occupants",
                "Who else lives in, occupies, or claims an interest in the property?",
            ),
            PackQuestion(
                "property_deadlines",
                "Is there a closing date, hearing, or deadline to cure? Enter none if not.",
            ),
        ),
        (
            PackUpload("property_agreement", "Deed, lease, or purchase agreement"),
            PackUpload(
                "property_notices",
                "Notices, demand letters, or court papers you received",
            ),
            PackUpload(
                "property_payments", "Payment records for rent, mortgage, or dues"
            ),
        ),
    ),
    Practice(
        "immigration",
        "Immigration",
        (
            "immigration",
            "visa",
            "green card",
            "naturalization",
            "citizenship",
            "asylum",
            "removal",
            "deportation",
            "adjustment of status",
            "work permit",
        ),
        (
            PackQuestion(
                "immigration_goal",
                "What are you applying for or defending against, and what is the deadline if you know one?",
            ),
            PackQuestion(
                "entry_history",
                "When and how did you last enter the country, and what status were you given?",
            ),
            PackQuestion(
                "current_status",
                "What is your current immigration status, and when does it expire?",
            ),
            PackQuestion(
                "prior_filings",
                "What applications or petitions have you filed before, and what was the outcome of each? Enter none if not.",
            ),
            PackQuestion(
                "family_ties",
                "List your immediate family members, their status, and where they live.",
            ),
            PackQuestion(
                "criminal_immigration_history",
                "Have you ever been arrested, charged, removed, denied entry, or ordered to appear in immigration court? Enter none if not.",
            ),
            PackQuestion(
                "employment_sponsor",
                "Is an employer or relative sponsoring you? Give their name and relationship, or enter none.",
            ),
            PackQuestion(
                "travel_plans",
                "Do you have travel planned or an appointment scheduled? Give the dates.",
                required=False,
            ),
        ),
        (
            PackUpload(
                "immigration_identity", "Passport, visa, and any entry documents"
            ),
            PackUpload(
                "immigration_notices",
                "Notices or decisions from USCIS, ICE, or an immigration court",
            ),
            PackUpload(
                "immigration_filings", "Copies of prior applications and receipts"
            ),
        ),
    ),
    Practice(
        "bankruptcy",
        "Bankruptcy and debt",
        (
            "bankruptcy",
            "debt",
            "chapter 7",
            "chapter 13",
            "chapter 11",
            "creditor",
            "collections",
            "foreclosure",
            "repossession",
            "garnishment",
        ),
        (
            PackQuestion(
                "debt_summary",
                "What debts are you dealing with? List each creditor and roughly what is owed.",
            ),
            PackQuestion(
                "collection_activity",
                "Are you facing garnishment, repossession, foreclosure, or lawsuits? Describe what is happening and when.",
            ),
            PackQuestion(
                "income_household",
                "What is your household income and who depends on it?",
            ),
            PackQuestion(
                "assets_owned",
                "What do you own — home, vehicles, accounts, retirement, valuables — and roughly what is each worth?",
            ),
            PackQuestion("monthly_expenses", "What are your regular monthly expenses?"),
            PackQuestion(
                "prior_bankruptcy",
                "Have you filed for bankruptcy before? Give the year, chapter, and outcome, or enter none.",
            ),
            PackQuestion(
                "recent_transfers",
                "In the last two years, have you sold, given away, or transferred property, or repaid a family member? Enter none if not.",
            ),
            PackQuestion(
                "bankruptcy_goal",
                "What matters most to you — keeping a home or vehicle, stopping garnishment, or a fresh start?",
            ),
        ),
        (
            PackUpload(
                "bankruptcy_income",
                "Pay stubs for the last six months and the last two tax returns",
            ),
            PackUpload(
                "bankruptcy_debts",
                "Recent statements, collection letters, and any lawsuit papers",
            ),
            PackUpload("bankruptcy_assets", "Titles, deeds, and account statements"),
        ),
    ),
    Practice(
        "litigation",
        "Civil litigation",
        (
            "litigation",
            "civil litigation",
            "dispute",
            "claim",
            "lawsuit",
            "demand",
            "subpoena",
            "appeal",
            "collection",
            "breach of contract",
        ),
        (
            PackQuestion(
                "dispute_facts",
                "Describe the dispute: what was agreed or expected, what went wrong, and when.",
            ),
            PackQuestion(
                "case_posture",
                "Has a case been filed? If so, give the court, case number, and what has happened so far.",
            ),
            PackQuestion(
                "service_status",
                "Were you served with papers or given a deadline to respond? Give the date you received them.",
            ),
            PackQuestion(
                "amount_at_stake",
                "What money or property is at stake, and how did you calculate it?",
            ),
            PackQuestion(
                "agreements_at_issue",
                "What contracts, invoices, or written communications govern the dispute?",
            ),
            PackQuestion(
                "settlement_history",
                "Have there been settlement discussions or demands? Describe any offer made or received.",
            ),
            PackQuestion(
                "evidence_holders",
                "Who holds the documents, messages, or records that matter, and are any at risk of being lost?",
            ),
            PackQuestion(
                "insurance_coverage",
                "Might any insurance policy cover this claim, and have you notified the insurer? Enter none if not.",
                required=False,
            ),
        ),
        (
            PackUpload(
                "litigation_pleadings",
                "Any court papers, demand letters, or subpoenas you received",
            ),
            PackUpload(
                "litigation_agreements", "The contract, invoices, or records at issue"
            ),
            PackUpload(
                "litigation_correspondence", "Correspondence with the other side"
            ),
        ),
    ),
    Practice(
        "mediation",
        "Mediation",
        (
            "mediation",
            "mediator",
            "arbitration",
            "settlement conference",
            "collaborative",
            "alternative dispute resolution",
        ),
        (
            PackQuestion(
                "mediation_dispute",
                "What is the dispute about, and how would each side describe it?",
            ),
            PackQuestion(
                "mediation_participants",
                "Who will attend the mediation, and does anyone attending need authority from someone else to settle?",
            ),
            PackQuestion(
                "representation_status",
                "Is anyone represented by counsel? Give the attorney's name for each represented party, or enter none.",
            ),
            PackQuestion(
                "case_status",
                "Is a case filed or a hearing scheduled? Give the court, case number, and any dates.",
            ),
            PackQuestion(
                "positions_so_far",
                "What has each side proposed so far, and where did the talks break down?",
            ),
            PackQuestion(
                "mediation_priorities",
                "What matters most to you in a resolution, and what could you accept?",
            ),
            PackQuestion(
                "mediation_conflicts",
                "Do you know the mediator, the other participants, or their counsel personally or through business? Enter none if not.",
            ),
            PackQuestion(
                "mediation_logistics",
                "Do you need a remote session, an interpreter, separate rooms, or any accommodation?",
                required=False,
            ),
        ),
        (
            PackUpload(
                "mediation_agreements",
                "Any agreement, contract, or order the dispute concerns",
            ),
            PackUpload(
                "mediation_proposals", "Written offers or proposals exchanged so far"
            ),
        ),
    ),
    Practice(
        "general",
        "General matter",
        ("general", "other", "consultation", "advice"),
        (
            PackQuestion(
                "background_facts",
                "Give the background a lawyer would need to understand your situation from the beginning.",
            ),
            PackQuestion(
                "documents_held",
                "What documents, messages, or records do you have about this matter?",
            ),
            PackQuestion(
                "money_at_stake",
                "Is money, property, or a legal right at stake? Describe what and how much.",
            ),
            PackQuestion(
                "steps_taken",
                "What have you already done about this, and what were you told?",
            ),
        ),
        (
            PackUpload(
                "general_documents",
                "Any documents, letters, or notices about this matter",
            ),
        ),
    ),
)

DEFAULT_PRACTICE = _PRACTICES[-1]

_BY_SLUG: dict[str, Practice] = {practice.slug: practice for practice in _PRACTICES}
_BY_ALIAS: dict[str, Practice] = {
    alias: practice for practice in _PRACTICES for alias in practice.aliases
}


def _normalize(value: str | None) -> str:
    return " ".join(str(value or "").strip().casefold().replace("_", " ").split())


#: Aliases longest first, so a specific phrase decides before a generic word:
#: "breach of contract lawsuit" is litigation, not the "contract" in business.
_RANKED_ALIASES: tuple[tuple[str, Practice], ...] = tuple(
    sorted(
        ((alias, practice) for practice in _PRACTICES for alias in practice.aliases),
        key=lambda entry: (-len(entry[0].split()), -len(entry[0])),
    )
)


def _match(value: str | None) -> Practice | None:
    """Return the practice one free-text label names, or nothing."""

    normalized = _normalize(value)
    if not normalized:
        return None
    direct = _BY_ALIAS.get(normalized) or _BY_SLUG.get(normalized.replace(" ", "_"))
    if direct:
        return direct
    words = set(normalized.split())
    for alias, practice in _RANKED_ALIASES:
        alias_words = alias.split()
        if len(alias_words) > 1:
            if alias in normalized:
                return practice
        elif alias in words:
            return practice
    return None


def resolve_practice(
    matter_type: str | None, practice_area: str | None = None
) -> Practice:
    """Return the practice a matter belongs to, from whichever label carries it.

    Both labels are free text a firm typed, and in real files the signal moves
    between them: a matter typed "general" often carries "Family Law" as its
    practice area.  So the type is read first and the practice area answers when
    the type says nothing recognisable.  An unrecognised matter still falls back
    to the general pack rather than to nothing: a client always gets questions.
    """

    for value in (matter_type, practice_area):
        matched = _match(value)
        if matched is not None and matched is not DEFAULT_PRACTICE:
            return matched
    return _match(matter_type) or _match(practice_area) or DEFAULT_PRACTICE


def practices() -> tuple[Practice, ...]:
    """Return every practice pack, for firm-facing pickers and documentation."""

    return _PRACTICES


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
