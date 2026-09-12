"""Cards: the owner of a template field.

Before cards, a binding was one path out of a flat list.  ``client.name`` and
``party.defendant.name`` sat side by side with nothing saying that one names the
matter's client and the other names a party in a role, and nothing at all could
name the *second* defendant.  That flatness is why an interview cannot be
grouped, why the same answer cannot be shown to span several documents, and why
every new surface — a drafting set, a questionnaire, an AI proposal — has to
re-invent its own idea of what a field is about.

A **card** is one addressable subject in a document: the firm, the matter, its
billing terms, the client, a party in a role, the attorney of record, the person
preparing the document, or the current item of a repeating section.  Fields
belong to cards.  That single move makes "ask the client for the second
defendant's address" expressible.

Three properties hold and must keep holding:

* **The vocabulary stays closed.**  A card path is a lookup key, never an
  expression.  Role cards may be *instantiated* per matter party, but the
  instance is an integer the server resolves against ``matter_parties`` in a
  deterministic order; nothing a customer authors selects a record.
* **Cards are derived over the existing binding catalogue, not a replacement
  for it.**  Every pre-card path resolves to a card field through
  :data:`LEGACY_PATHS`, and resolution *translates* — it never rewrites stored
  schemas.  A published version is immutable, and silently re-pointing its
  bindings would re-source a clause in a document that has already been filed.
* **Aliases are unchanged for everything that exists today.**  A card field
  resolves through the same Smart Fill candidate alias its flat path did, so
  this module adds addressing without moving any value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from app.services.template_bindings import (
    MANUAL_BINDING,
    alias_for_binding,
    binding_label,
    custom_binding,
    is_valid_binding,
)

#: Instance token meaning "every instance of this role, joined".  It is the card
#: spelling of the old plural paths (``party.defendant.names``).
ALL_INSTANCES = "*"

#: A role card cannot address an unbounded number of parties.  The ceiling is a
#: guard on interview size and on alias generation, not a claim about how many
#: parties a matter may have.
MAX_ROLE_INSTANCES = 20


class CardKind(str, Enum):
    """What kind of subject a card names.

    The kind decides how the card resolves, not how it is displayed:
    singletons resolve from one record, ``ROLE`` cards resolve per matter party,
    and ``ITEM`` resolves only inside a repeating region.
    """

    FIRM = "firm"
    MATTER = "matter"
    PERSON = "person"
    ROLE = "role"
    ITEM = "item"


@dataclass(frozen=True)
class CardField:
    """One field on a card.

    ``alias`` is the Smart Fill candidate key this field resolves through for a
    singleton card or the first instance of a role card.  ``all_alias`` is the
    key for :data:`ALL_INSTANCES`.  Both may be empty: an ``ITEM`` field has no
    single record behind it, so it has no alias at all.
    """

    key: str
    label: str
    alias: str = ""
    all_alias: str = ""
    value_kind: str = "text"
    #: The pre-card binding path this field replaces, if any.  Present so
    #: :data:`LEGACY_PATHS` can be generated rather than hand-maintained, which
    #: is what keeps the two vocabularies from drifting apart.
    legacy_path: str = ""
    #: A second legacy path that resolved to the same value (the old catalogue
    #: carried plural variants as separate entries).
    legacy_all_path: str = ""


@dataclass(frozen=True)
class Card:
    """One addressable subject, and the fields that belong to it."""

    key: str
    label: str
    kind: CardKind
    fields: tuple[CardField, ...]
    #: ``matter_parties.role`` this card instantiates.  Empty for non-role cards.
    party_role: str = ""
    #: Presentation order and grouping in the editor rail.
    group: str = ""

    @property
    def max_instances(self) -> int:
        return MAX_ROLE_INSTANCES if self.kind is CardKind.ROLE else 1

    def field(self, key: str) -> CardField | None:
        return next((entry for entry in self.fields if entry.key == key), None)


def _role_card(key: str, label: str) -> Card:
    """Build a caption role card.

    Plaintiff and defendant carry the same field set because the caption
    candidate builder emits the same aliases for both; keeping them generated
    rather than duplicated means a field added here cannot reach one role and
    miss the other.
    """

    return Card(
        key=key,
        label=label,
        kind=CardKind.ROLE,
        party_role=key,
        group="Parties",
        fields=(
            CardField(
                "full_name",
                "Full name",
                alias=f"{key}_name",
                # The flat catalogue resolved the plural through ``{role}_names``;
                # the candidate builder emits ``{role}s`` too, but a card must resolve
                # through the *same* alias its legacy path did or the two spellings
                # could disagree about which records they name.
                all_alias=f"{key}_names",
                legacy_path=f"party.{key}.name",
                legacy_all_path=f"party.{key}.names",
            ),
            CardField("email", "Email", alias=f"{key}_email"),
            CardField("phone", "Phone", alias=f"{key}_phone"),
        ),
    )


_CARDS: tuple[Card, ...] = (
    Card(
        key="firm",
        label="Firm",
        kind=CardKind.FIRM,
        group="Firm profile",
        fields=(
            CardField("name", "Firm name", alias="firm_name", legacy_path="firm.name"),
            CardField(
                "address",
                "Firm address",
                alias="firm_address",
                legacy_path="firm.address",
            ),
            CardField(
                "phone", "Firm phone", alias="firm_phone", legacy_path="firm.phone"
            ),
            CardField(
                "email", "Firm email", alias="firm_email", legacy_path="firm.email"
            ),
            CardField(
                "website",
                "Firm website",
                alias="firm_website",
                legacy_path="firm.website",
            ),
        ),
    ),
    Card(
        key="matter",
        label="Matter",
        kind=CardKind.MATTER,
        group="Matter",
        fields=(
            CardField(
                "name", "Matter name", alias="matter_name", legacy_path="matter.name"
            ),
            CardField(
                "type", "Matter type", alias="matter_type", legacy_path="matter.type"
            ),
            CardField(
                "description",
                "Matter description",
                alias="matter_description",
                legacy_path="matter.description",
            ),
            CardField(
                "status",
                "Matter status",
                alias="matter_status",
                legacy_path="matter.status",
            ),
            CardField(
                "stage",
                "Matter stage",
                alias="matter_stage",
                legacy_path="matter.stage",
            ),
            CardField(
                "jurisdiction",
                "Jurisdiction",
                alias="matter_jurisdiction",
                legacy_path="matter.jurisdiction",
            ),
            CardField("venue", "Venue", alias="venue", legacy_path="matter.venue"),
            CardField(
                "case_number",
                "Case number",
                alias="case_number",
                legacy_path="matter.case_number",
            ),
            CardField("court", "Court", alias="court", legacy_path="matter.court"),
            CardField("judge", "Judge", alias="judge", legacy_path="matter.judge"),
            CardField(
                "counterparty",
                "Counterparty",
                alias="counterparty",
                legacy_path="matter.counterparty",
            ),
            CardField(
                "role",
                "Represented side",
                alias="matter_role",
                legacy_path="matter.role",
            ),
        ),
    ),
    Card(
        key="billing",
        label="Billing",
        kind=CardKind.MATTER,
        group="Billing",
        fields=(
            CardField(
                "method",
                "Billing method",
                alias="billing_method",
                legacy_path="matter.billing_method",
            ),
            CardField(
                "cycle",
                "Billing cycle",
                alias="billing_cycle",
                legacy_path="matter.billing_cycle",
            ),
            CardField(
                "hourly_rate",
                "Hourly rate",
                alias="hourly_rate",
                value_kind="number",
                legacy_path="matter.hourly_rate",
            ),
            CardField(
                "budget_amount",
                "Budget amount",
                alias="budget_amount",
                value_kind="number",
                legacy_path="matter.budget_amount",
            ),
            CardField(
                "contingency_percentage",
                "Contingency percentage",
                alias="contingency_percentage",
                value_kind="number",
                legacy_path="matter.contingency_percentage",
            ),
            # Retainer fields resolve from the matter's current retainer record,
            # the same source the flat bindings name.
            CardField(
                "retainer_amount",
                "Retainer amount",
                alias="retainer_amount",
                value_kind="number",
                legacy_path="matter.retainer_amount",
            ),
            CardField(
                "retainer_minimum_balance",
                "Retainer minimum balance",
                alias="retainer_minimum_balance",
                value_kind="number",
                legacy_path="matter.retainer_minimum_balance",
            ),
        ),
    ),
    Card(
        key="client",
        label="Client",
        kind=CardKind.PERSON,
        group="Client",
        fields=(
            CardField(
                "full_name",
                "Client name",
                alias="client_name",
                legacy_path="client.name",
            ),
            CardField(
                "email",
                "Client email",
                alias="client_email",
                legacy_path="client.email",
            ),
            CardField(
                "phone",
                "Client phone",
                alias="client_phone",
                legacy_path="client.phone",
            ),
            CardField(
                "street",
                "Client street",
                alias="client_street",
                legacy_path="client.address.street",
            ),
            CardField(
                "city",
                "Client city",
                alias="client_city",
                legacy_path="client.address.city",
            ),
            CardField(
                "state",
                "Client state",
                alias="client_state",
                legacy_path="client.address.state",
            ),
            CardField(
                "zip",
                "Client ZIP",
                alias="client_zip",
                legacy_path="client.address.zip",
            ),
            CardField(
                "country",
                "Client country",
                alias="client_country",
                legacy_path="client.address.country",
            ),
        ),
    ),
    _role_card("plaintiff", "Plaintiff"),
    _role_card("defendant", "Defendant"),
    Card(
        key="attorney",
        label="Attorney of record",
        kind=CardKind.PERSON,
        group="People",
        fields=(
            CardField(
                "full_name",
                "Attorney of record",
                alias="attorney_name",
                legacy_path="attorney.name",
            ),
            CardField(
                "email",
                "Attorney email",
                alias="attorney_email",
                legacy_path="attorney.email",
            ),
        ),
    ),
    Card(
        key="preparer",
        label="Preparer",
        kind=CardKind.PERSON,
        group="People",
        fields=(
            CardField(
                "full_name",
                "Current user",
                alias="current_user_name",
                legacy_path="current_user.name",
            ),
            CardField(
                "email",
                "Current user email",
                alias="current_user_email",
                legacy_path="current_user.email",
            ),
            CardField(
                "prepared_by",
                "Prepared by",
                alias="prepared_by",
                legacy_path="current_user.prepared_by",
            ),
        ),
    ),
    Card(
        key="item",
        label="This item",
        kind=CardKind.ITEM,
        group="Repeating section",
        # Item fields resolve once per iteration of a repeating section, not
        # from a matter record, so they carry no alias.  The repeat evaluator
        # supplies their values.
        fields=(
            CardField(
                "party_name", "Party name (this item)", legacy_path="item.party_name"
            ),
            CardField(
                "party_role", "Party role (this item)", legacy_path="item.party_role"
            ),
            CardField(
                "party_email", "Party email (this item)", legacy_path="item.party_email"
            ),
            CardField(
                "party_phone", "Party phone (this item)", legacy_path="item.party_phone"
            ),
        ),
    ),
)

_BY_KEY: dict[str, Card] = {card.key: card for card in _CARDS}


def _build_legacy_paths() -> dict[str, str]:
    """Generate the pre-card to card path map from the catalogue itself.

    Hand-maintaining this map is how the two vocabularies would drift; deriving
    it means a card field that forgets its ``legacy_path`` is caught by the
    exhaustiveness test rather than by a customer with an empty blank.
    """

    mapping: dict[str, str] = {}
    for card in _CARDS:
        for entry in card.fields:
            if entry.legacy_path:
                mapping[entry.legacy_path] = f"{card.key}.{entry.key}"
            if entry.legacy_all_path:
                mapping[entry.legacy_all_path] = (
                    f"{card.key}.{ALL_INSTANCES}.{entry.key}"
                )
    return mapping


#: Every pre-card binding path, mapped to the card path it resolves through.
LEGACY_PATHS: dict[str, str] = _build_legacy_paths()


@dataclass(frozen=True)
class CardFieldRef:
    """A resolved reference to one field on one instance of one card."""

    card: Card
    field: CardField
    #: ``None`` for a singleton card, an integer for one instance of a role
    #: card, or :data:`ALL_INSTANCES` for every instance joined.
    instance: int | str | None = None

    @property
    def path(self) -> str:
        if self.instance is None:
            return f"{self.card.key}.{self.field.key}"
        return f"{self.card.key}.{self.instance}.{self.field.key}"

    @property
    def label(self) -> str:
        if self.instance == ALL_INSTANCES:
            return f"All {self.card.label.lower()}s — {self.field.label}"
        if isinstance(self.instance, int) and self.instance > 1:
            return f"{self.card.label} {self.instance} — {self.field.label}"
        return f"{self.card.label} — {self.field.label}"


_PATH = re.compile(
    r"^(?P<card>[a-z][a-z0-9_]*)"
    r"(?:\.(?P<instance>[1-9][0-9]?|\*))?"
    r"\.(?P<field>[a-z][a-z0-9_]*)$"
)


def cards() -> tuple[Card, ...]:
    """Return every card, in presentation order."""

    return _CARDS


def card(key: str) -> Card | None:
    return _BY_KEY.get(key)


def canonical_path(path: str) -> str:
    """Return the card path a stored binding resolves through.

    Translation only.  Stored schemas keep the path they were published with,
    because a published version is immutable and its bindings are part of what
    was reviewed.
    """

    if not isinstance(path, str):
        return ""
    return LEGACY_PATHS.get(path, path)


def resolve(path: str) -> CardFieldRef | None:
    """Resolve a card path, or a pre-card path, to a card field reference.

    Returns ``None`` for ``manual``, for tenant custom-field paths (which
    :func:`app.services.template_bindings.custom_binding` owns), and for
    anything the catalogue does not recognise.  An unrecognised path must stay
    unrecognised: falling back to a near match would bind a clause to a record
    nobody chose.
    """

    if not isinstance(path, str) or not path or path == MANUAL_BINDING:
        return None
    if custom_binding(path) is not None:
        return None
    match = _PATH.fullmatch(canonical_path(path))
    if not match:
        return None
    found = _BY_KEY.get(match["card"])
    if found is None:
        return None
    entry = found.field(match["field"])
    if entry is None:
        return None

    raw_instance = match["instance"]
    if raw_instance is None:
        instance: int | str | None = None
    elif raw_instance == ALL_INSTANCES:
        instance = ALL_INSTANCES
    else:
        instance = int(raw_instance)

    # Only a role card has instances.  An instance on a singleton would name a
    # record that cannot exist, so it does not resolve at all.
    if instance is not None and found.kind is not CardKind.ROLE:
        return None
    if isinstance(instance, int) and instance > found.max_instances:
        return None
    # "Every instance" is only meaningful where a field has a plural alias.
    if instance == ALL_INSTANCES and not entry.all_alias:
        return None
    return CardFieldRef(card=found, field=entry, instance=instance)


def is_valid_card_path(path: str) -> bool:
    """Return whether ``path`` names a card field this catalogue knows."""

    return resolve(path) is not None


def alias_for(ref: CardFieldRef) -> str:
    """Return the Smart Fill candidate alias a card reference resolves through.

    Instance 1 of a role card resolves through the alias the flat catalogue
    already used, so nothing that exists today changes.  Later instances
    resolve through an indexed alias the caption candidate builder emits
    alongside it (``defendant_2_name``), and :data:`ALL_INSTANCES` through the
    existing plural alias.
    """

    if ref.instance == ALL_INSTANCES:
        return ref.field.all_alias
    if isinstance(ref.instance, int) and ref.instance > 1:
        if not ref.field.alias:
            return ""
        return indexed_alias(ref.card.key, ref.instance, ref.field)
    return ref.field.alias


def indexed_alias(card_key: str, instance: int, entry: CardField) -> str:
    """Return the candidate alias naming one specific instance of a role card.

    The base alias already starts with the role (``defendant_name``), so the
    index is inserted after it rather than appended, keeping the alias readable
    and collision-free against the singular and plural forms.
    """

    prefix = f"{card_key}_"
    suffix = entry.alias[len(prefix) :] if entry.alias.startswith(prefix) else entry.key
    return f"{card_key}_{instance}_{suffix}"


def role_cards() -> tuple[Card, ...]:
    """Return the cards backed by ``matter_parties`` rows."""

    return tuple(entry for entry in _CARDS if entry.kind is CardKind.ROLE)


# --- The boundary the rest of the application uses -------------------------
#
# Callers should not have to know whether a stored path is a card path, a
# pre-card path, ``manual``, or a tenant custom field.  These three functions
# accept all of them, so a template saved before cards and one authored after
# take exactly the same code path.


def is_valid_path(path: str) -> bool:
    """Return whether ``path`` is any binding a template field may declare."""

    return is_valid_binding(path) or is_valid_card_path(path)


def alias_for_path(path: str) -> str | None:
    """Return the Smart Fill candidate alias ``path`` resolves through.

    ``None`` where nothing resolves — an unknown path must stay unresolved so
    the fill reports ``binding_unresolved`` naming it, rather than falling
    through to a coincidental alias and filling a clause from a record nobody
    chose.
    """

    ref = resolve(path)
    if ref is not None:
        return alias_for(ref) or None
    return alias_for_binding(path)


def label_for_path(path: str) -> str | None:
    """Return the human label for ``path``, for provenance and editor copy.

    The pre-card label wins where one exists, so a template published before
    cards keeps the wording its author reviewed.
    """

    legacy = binding_label(path)
    if legacy:
        return legacy
    ref = resolve(path)
    return ref.label if ref is not None else None
