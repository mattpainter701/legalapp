"""Shared resolution of free-text matter labels to a known practice.

``Matter.matter_type`` and ``Matter.practice_area`` are free text a firm
typed, and several subsystems need the same reading of them: the intake
starter pack, plugin suggestions, and workflow-automation rules.  They used
to fuzzy-match independently; this module is the single alias table they
all resolve through, so one label resolves the same way everywhere.

Resolution is longest-alias-wins: a specific phrase decides before a
generic word, so "breach of contract lawsuit" is litigation, not the
"contract" in business.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from typing import Any


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
class Practice:
    """A matter-type family and the questions it needs answered."""

    slug: str
    label: str
    aliases: tuple[str, ...]
    questions: tuple[PackQuestion, ...]
    uploads: tuple[PackUpload, ...] = dataclass_field(default_factory=tuple)


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


def practice_key(value: str | None) -> str | None:
    """Return the canonical slug for a recognised practice label, else ``None``.

    Values the alias table does not know return ``None`` so callers can fall
    back to their own comparison of the raw text.
    """

    matched = _match(value)
    return matched.slug if matched is not None else None
