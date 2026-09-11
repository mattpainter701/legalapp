"""The named categories a client's SMS consent can cover.

Consent is checked as ``category in consent.allowed_categories``, and until now
only one grant existed: intake, recorded when a firm started a client's
paperwork, carrying ``allowed_categories: ["intake"]``.

That is a narrower permission than it looks. A client who agrees to be texted
about onboarding their matter has not agreed to be texted for the life of the
case, and sending case updates under the intake grant would launder one
permission into another. So case updates are their own category with their own
disclosure, granted only when a firm records that the client agreed to it.

Existing consents are safe without a migration: their ``allowed_categories``
does not contain ``case_updates``, so the existing gate refuses on its own.
Widening an old consent requires the client to agree again.
"""

# Onboarding a matter: the paperwork packet, its reminders, the portal welcome.
SMS_CATEGORY_INTAKE = "intake"

# Anything after onboarding: a document sent for signature, a deadline chase,
# a message waiting in the portal.
SMS_CATEGORY_CASE_UPDATES = "case_updates"

# Each disclosure version names the scope the client actually agreed to, so an
# audit can tell an intake-only grant from one that covers the whole case.
DISCLOSURE_INTAKE_ONLY = "intake-notifications-v1"
DISCLOSURE_CASE_UPDATES = "case-notifications-v1"


def granted_categories(*, case_updates: bool) -> list[str]:
    """Categories to record for a fresh consent."""
    if case_updates:
        return [SMS_CATEGORY_INTAKE, SMS_CATEGORY_CASE_UPDATES]
    return [SMS_CATEGORY_INTAKE]


def disclosure_version(*, case_updates: bool) -> str:
    return DISCLOSURE_CASE_UPDATES if case_updates else DISCLOSURE_INTAKE_ONLY


def allows_case_updates(consent) -> bool:
    """Whether this consent covers texting the client about the live case."""
    categories = getattr(consent, "allowed_categories", None)
    return isinstance(categories, list) and SMS_CATEGORY_CASE_UPDATES in categories
