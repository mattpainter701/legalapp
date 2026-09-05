"""Template field data bindings.

A binding says where a template field's value comes from: one path out of a
closed, server-owned catalogue of matter, client, party, and user records.

Before bindings, Smart Fill matched a fixed alias dictionary against the *field
name*, so it only ever fired when a customer happened to name a field the way
the server hardcoded it.  A firm's own engagement letter with a
``client_full_name`` field resolved to nothing.  A binding moves that knowledge
into the template, where the customer states it once and it holds for the life
of the template.

The catalogue is deliberately closed.  A binding is a lookup key, never an
expression, so nothing a customer authors can reach a renderer or a query.
"""

from __future__ import annotations

from dataclasses import dataclass

#: A field the customer always types by hand.  Declaring it suppresses the
#: legacy name-matching fallback, so an intentionally manual field stops being
#: silently auto-filled by a coincidental name collision.
MANUAL_BINDING = "manual"


@dataclass(frozen=True)
class TemplateBinding:
    """One catalogue entry.

    ``alias`` is the key the existing Smart Fill candidate builder already
    resolves.  Keeping the binding path separate from the alias lets the
    catalogue present a stable, self-describing vocabulary to customers while
    the resolver keeps using the reviewed candidate map underneath.
    """

    path: str
    alias: str
    label: str
    group: str


_CATALOGUE: tuple[TemplateBinding, ...] = (
    # Matter
    TemplateBinding("matter.name", "matter_name", "Matter name", "Matter"),
    TemplateBinding("matter.type", "matter_type", "Matter type", "Matter"),
    TemplateBinding("matter.description", "matter_description", "Matter description", "Matter"),
    TemplateBinding("matter.status", "matter_status", "Matter status", "Matter"),
    TemplateBinding("matter.stage", "matter_stage", "Matter stage", "Matter"),
    TemplateBinding("matter.jurisdiction", "matter_jurisdiction", "Jurisdiction", "Matter"),
    TemplateBinding("matter.case_number", "case_number", "Case number", "Matter"),
    TemplateBinding("matter.court", "court", "Court", "Matter"),
    TemplateBinding("matter.judge", "judge", "Judge", "Matter"),
    TemplateBinding("matter.counterparty", "counterparty", "Counterparty", "Matter"),
    TemplateBinding("matter.role", "matter_role", "Represented side", "Matter"),
    # Billing
    TemplateBinding("matter.billing_method", "billing_method", "Billing method", "Billing"),
    TemplateBinding("matter.billing_cycle", "billing_cycle", "Billing cycle", "Billing"),
    TemplateBinding("matter.hourly_rate", "hourly_rate", "Hourly rate", "Billing"),
    TemplateBinding("matter.budget_amount", "budget_amount", "Budget amount", "Billing"),
    # Client contact
    TemplateBinding("client.name", "client_name", "Client name", "Client"),
    TemplateBinding("client.email", "client_email", "Client email", "Client"),
    TemplateBinding("client.phone", "client_phone", "Client phone", "Client"),
    TemplateBinding("client.address.street", "client_street", "Client street", "Client"),
    TemplateBinding("client.address.city", "client_city", "Client city", "Client"),
    TemplateBinding("client.address.state", "client_state", "Client state", "Client"),
    TemplateBinding("client.address.zip", "client_zip", "Client ZIP", "Client"),
    TemplateBinding("client.address.country", "client_country", "Client country", "Client"),
    # Caption parties
    TemplateBinding("party.plaintiff.name", "plaintiff_name", "Plaintiff (first listed)", "Parties"),
    TemplateBinding("party.plaintiff.names", "plaintiff_names", "Plaintiffs (all)", "Parties"),
    TemplateBinding("party.defendant.name", "defendant_name", "Defendant (first listed)", "Parties"),
    TemplateBinding("party.defendant.names", "defendant_names", "Defendants (all)", "Parties"),
    # People
    TemplateBinding("attorney.name", "attorney_name", "Attorney of record", "People"),
    TemplateBinding("attorney.email", "attorney_email", "Attorney email", "People"),
    TemplateBinding("current_user.name", "current_user_name", "Current user", "People"),
    TemplateBinding("current_user.email", "current_user_email", "Current user email", "People"),
    TemplateBinding("current_user.prepared_by", "prepared_by", "Prepared by", "People"),
    # Item bindings resolve once per iteration of a repeating section, not from
    # the matter, so they have no alias: there is no single record behind them.
    TemplateBinding("item.party_name", "", "Party name (this item)", "Repeating section"),
    TemplateBinding("item.party_role", "", "Party role (this item)", "Repeating section"),
    TemplateBinding("item.party_email", "", "Party email (this item)", "Repeating section"),
    TemplateBinding("item.party_phone", "", "Party phone (this item)", "Repeating section"),
)

#: Prefix marking a binding that is resolved per repeat item.
ITEM_BINDING_PREFIX = "item."


def is_item_binding(path: str) -> bool:
    """Return whether a binding resolves per iteration of a repeating section."""

    return isinstance(path, str) and path.startswith(ITEM_BINDING_PREFIX)


def item_key(path: str) -> str:
    """Return the collection item field an item binding names."""

    return path[len(ITEM_BINDING_PREFIX):] if is_item_binding(path) else ""

_BY_PATH: dict[str, TemplateBinding] = {entry.path: entry for entry in _CATALOGUE}


def catalogue() -> tuple[TemplateBinding, ...]:
    """Return every binding a customer may declare, in presentation order."""

    return _CATALOGUE


def is_valid_binding(path: str) -> bool:
    """Return whether ``path`` is the manual marker or a catalogue entry."""

    return path == MANUAL_BINDING or path in _BY_PATH


def alias_for_binding(path: str) -> str | None:
    """Return the Smart Fill candidate alias a binding resolves through.

    ``manual`` and unknown paths resolve to nothing: a manual field has no
    record behind it, and an unknown path must never fall through to a
    coincidental alias.
    """

    entry = _BY_PATH.get(path)
    return entry.alias if entry else None


def binding_label(path: str) -> str | None:
    """Return the human label for a binding, for provenance and UI copy."""

    if path == MANUAL_BINDING:
        return "Entered by hand"
    entry = _BY_PATH.get(path)
    return entry.label if entry else None


def declared_bindings(variable_schema: dict | None) -> dict[str, str]:
    """Return ``{field name: binding path}`` for every field that declares one.

    A path this catalogue no longer recognises is still returned, and still
    counts as declared. Save-time validation rejects unknown paths, so a stale
    one can only mean the catalogue itself changed under an existing template —
    and there the honest outcome is a blank field that names the source it can
    no longer reach. Quietly falling back to name matching would re-source a
    clause in a legal document without telling anyone.

    Tolerates malformed stored schemas: this runs on the read path for
    templates saved before bindings existed, so anything that is not a
    non-empty string on a named field is skipped rather than raising.
    """

    if not isinstance(variable_schema, dict):
        return {}
    fields = variable_schema.get("fields")
    if not isinstance(fields, list):
        return {}
    bindings: dict[str, str] = {}
    for field in fields:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "").strip()
        binding = field.get("binding")
        if not name or not isinstance(binding, str) or not binding.strip():
            continue
        bindings[name] = binding.strip()
    return bindings


@dataclass(frozen=True)
class TemplateCollection:
    """One repeatable record set a ``{{#each}}`` block may iterate.

    ``item_fields`` are the placeholders available inside the block.  Keeping
    them declared rather than inferred means the editor can show a customer
    exactly what a repeating section can say before they write it.
    """

    name: str
    label: str
    item_fields: tuple[str, ...]


_COLLECTIONS: tuple[TemplateCollection, ...] = (
    TemplateCollection(
        "parties",
        "All matter parties",
        ("party_name", "party_role", "party_email", "party_phone"),
    ),
    TemplateCollection(
        "plaintiffs",
        "Plaintiffs",
        ("party_name", "party_role", "party_email", "party_phone"),
    ),
    TemplateCollection(
        "defendants",
        "Defendants",
        ("party_name", "party_role", "party_email", "party_phone"),
    ),
)

_COLLECTIONS_BY_NAME: dict[str, TemplateCollection] = {
    entry.name: entry for entry in _COLLECTIONS
}


def collections() -> tuple[TemplateCollection, ...]:
    """Return every collection a repeating section may iterate."""

    return _COLLECTIONS


def is_valid_collection(name: str) -> bool:
    """Return whether ``name`` is a known repeatable collection."""

    return name in _COLLECTIONS_BY_NAME
