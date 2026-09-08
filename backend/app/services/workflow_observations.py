"""Bounded, deterministic configuration suggestions from same-firm observations.

Only repeated task labels enter proposed configuration. Source content is not
stored in evidence: it contains counts, offsets, roles, ids and fingerprints.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from statistics import median
import hashlib
import json
import re

TASK_TYPES = frozenset(
    {
        "deadline",
        "hearing",
        "filing",
        "deposition",
        "call",
        "follow_up",
        "intake",
        "review",
        "general",
    }
)
ASSIGNEE_ROLES = frozenset(
    {"matter_owner", "attorney_of_record", "template_applier", "unassigned"}
)


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    value = str(value).strip()
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(
                value[:10] if pattern == "%Y-%m-%d" else value, pattern
            ).date()
        except ValueError:
            pass
    return None


def task_label(value, *, private_terms=()):
    label = " ".join(str(value or "").split())
    if not label or len(label) > 200:
        return None
    for term in private_terms:
        if term and len(str(term)) >= 3:
            label = re.sub(re.escape(str(term)), "[matter]", label, flags=re.IGNORECASE)
    label = re.sub(r"https?://\S+|\b[^\s@]+@[^\s@]+\.[^\s@]+", "[reference]", label)
    label = re.sub(r"\b\d{3,}\b", "[reference]", label)
    return label


@dataclass(frozen=True)
class Observation:
    matter_key: str
    matter_type: str
    practice_area: str | None
    title: str
    anchor_date: date
    due_date: date
    assignee_role: str
    task_type: str
    source_kind: str
    source_id: str
    source_sha256: str
    provider: str = "lawhand"
    stage: str | None = None
    review_policy: str | None = None
    template_id: str | None = None
    trigger_event: str = "matter_created"
    trigger_stage: str | None = None
    timing_basis: str = "due_date"
    context_refs: tuple[tuple[str, str], ...] = ()


def suggest(
    observations: list[Observation],
    *,
    minimum_matters=3,
    minimum_share=0.6,
    max_proposals=5,
):
    groups = defaultdict(list)
    for observation in observations:
        offset = (observation.due_date - observation.anchor_date).days
        if not 0 <= offset <= 3650:
            continue
        group = (
            observation.matter_type.strip().casefold(),
            observation.practice_area.strip().casefold()
            if observation.practice_area
            else None,
            observation.trigger_event,
            observation.trigger_stage,
            observation.provider,
        )
        groups[group].append(observation)
    proposals = []
    for group, rows in sorted(
        groups.items(), key=lambda item: (item[0][-1] != "lawhand", str(item[0]))
    ):
        total_matters = len({row.matter_key for row in rows})
        if total_matters < minimum_matters:
            continue
        by_title = defaultdict(list)
        for row in rows:
            by_title[row.title.casefold()].append(row)
        checklist = []
        items = []
        for label, matching in sorted(by_title.items()):
            # Count matters, not repeated imports or duplicated tasks.
            unique = {}
            for row in sorted(matching, key=lambda row: (row.due_date, row.source_id)):
                unique.setdefault(row.matter_key, row)
            if (
                len(unique) < minimum_matters
                or len(unique) / total_matters < minimum_share
            ):
                continue
            occurrences = list(unique.values())
            offsets = [(row.due_date - row.anchor_date).days for row in occurrences]
            roles = Counter(row.assignee_role for row in occurrences)
            role = roles.most_common(1)[0][0]
            if roles[role] / len(occurrences) < 0.8:
                role = "unassigned"
            types = Counter(row.task_type for row in occurrences)
            title = Counter(row.title for row in occurrences).most_common(1)[0][0]
            offset = int(median(offsets))
            checklist.append(
                dict(
                    item_key="observed_" + digest(label)[:16],
                    stage_key="opening",
                    title=title,
                    description=None,
                    task_type=types.most_common(1)[0][0],
                    priority="medium",
                    due_offset_days=offset,
                    assignee_role=role,
                )
            )
            items.append(
                dict(
                    item_key=checklist[-1]["item_key"],
                    matter_count=len(occurrences),
                    sample_share=round(len(occurrences) / total_matters, 3),
                    median_due_offset_days=offset,
                    due_offset_range=[min(offsets), max(offsets)],
                    assignee_counts=dict(roles),
                    source_counts=dict(Counter(row.provider for row in occurrences)),
                    timing_basis_counts=dict(
                        Counter(row.timing_basis for row in occurrences)
                    ),
                    review_policy_counts=dict(
                        Counter(
                            row.review_policy
                            for row in occurrences
                            if row.review_policy
                        )
                    ),
                    current_stage_counts=dict(
                        Counter(row.stage for row in occurrences if row.stage)
                    ),
                    template_counts=dict(
                        Counter(
                            row.template_id for row in occurrences if row.template_id
                        )
                    ),
                    evidence_refs=[
                        dict(
                            kind=row.source_kind,
                            id=row.source_id,
                            sha256=row.source_sha256,
                            context=[
                                dict(id=identifier, sha256=checksum)
                                for identifier, checksum in row.context_refs
                            ],
                        )
                        for row in occurrences[:50]
                    ],
                )
            )
        if not checklist:
            continue
        checklist.sort(
            key=lambda item: (item["due_offset_days"], item["title"].casefold())
        )
        checklist = checklist[:50]
        item_keys = {item["item_key"] for item in checklist}
        items = [item for item in items if item["item_key"] in item_keys]
        matter_type, practice_area, trigger, trigger_stage, provider = group
        definition = dict(
            initial_stage_key="opening",
            stages=[dict(stage_key="opening", label="Opening")],
            checklist=checklist,
            required_field_definition_ids=[],
        )
        rule = dict(
            trigger_event=trigger,
            trigger_stage=trigger_stage,
            match_matter_type=matter_type or None,
            match_practice_area=practice_area,
        )
        evidence = dict(
            cohort=provider,
            matter_count=total_matters,
            observed_record_count=len(rows),
            items=items,
            source_counts=dict(Counter(row.provider for row in rows)),
            anchor="Observed matter opening or explicit trigger date",
            warning="Patterns describe observed practice; review task wording, timing, and assignments before approval. Source systems are analyzed separately to avoid counting migrated matters twice.",
        )
        proposals.append(
            dict(
                pattern_key=digest(rule),
                definition=definition,
                rule=rule,
                evidence=evidence,
                proposal_sha256=digest(dict(definition=definition, rule=rule)),
                name=f'{matter_type or "General"} workflow from firm history',
            )
        )
        if len(proposals) >= max_proposals:
            break
    return proposals


def amendment(base_definition, observed_definition):
    """Preserve existing stages/requirements and unobserved tasks during drift.

    Limited history is never evidence for deleting established firm controls.
    Repeated observations may suggest additions or changes to timing/assignment.
    """
    result = json.loads(json.dumps(base_definition))
    existing = {item["title"].casefold(): item for item in result["checklist"]}
    changed = []
    for observed in observed_definition["checklist"]:
        current = existing.get(observed["title"].casefold())
        if current:
            differences = {
                key: dict(before=current[key], after=observed[key])
                for key in ("due_offset_days", "assignee_role")
                if current[key] != observed[key]
            }
            if differences:
                changed.append(dict(item_key=current["item_key"], changes=differences))
                for key in differences:
                    current[key] = observed[key]
        elif len(result["checklist"]) < 200:
            item = {**observed, "stage_key": result["initial_stage_key"]}
            if any(old["item_key"] == item["item_key"] for old in result["checklist"]):
                item["item_key"] = "observed_" + digest(item)[:24]
            result["checklist"].append(item)
            changed.append(dict(item_key=item["item_key"], added=True))
    return result, changed


def imported_observations(
    rows, *, provider, matter_rows=(), category_rows=(), mapping=None
):
    """Read exported history using documented Tabs3 fields or explicit mappings.

    `rows` carry id, row_checksum and row_data from the existing import store.
    Clio CSV headers are explicitly mapped; no provider-specific API assumption
    or guess about missing matter-open dates is required.
    """
    mapping = mapping or {}
    matters = {str(row.row_data.get("Client_ID")): row for row in matter_rows}
    categories = {
        str(row.row_data.get("Category_Number")): row for row in category_rows
    }
    observations = []
    skipped = Counter()
    for row in rows:
        data = row.row_data
        context_refs = ()
        if provider == "tabs3" and not mapping and row.source_table == "CMCAL":
            source_matter = str(data.get("Client_ID") or "")
            matter_row = matters.get(source_matter)
            matter = matter_row.row_data if matter_row else {}
            if matter_row:
                context_refs = ((str(matter_row.id), matter_row.row_checksum),)
            category = categories.get(str(matter.get("Category")))
            if category:
                context_refs += ((str(category.id), category.row_checksum),)
            if str(data.get("Private") or "0").strip().lower() not in (
                "0",
                "false",
                "no",
            ) or str(matter.get("Secure_Client") or "0").strip().lower() not in (
                "0",
                "false",
                "no",
            ):
                skipped["private_record"] += 1
                continue
            values = dict(
                matter_key=source_matter,
                title=data.get("Desc"),
                opened_at=matter.get("Date_Open"),
                due_date=data.get("Due_Date"),
                matter_type=(
                    category.row_data.get("Description")
                    if category
                    else matter.get("Category")
                )
                or "general",
                practice_area=matter.get("AOP"),
                assignee_role="unassigned",
                task_type="general",
            )
            private_terms = [
                matter.get(key)
                for key in ("Name", "Client_Full_Name", "Contact_Full_Name")
            ]
        else:
            values = {
                key: data.get(column) for key, column in mapping.items() if column
            }
            private_terms = [values.get("matter_name")]
        anchor = as_date(values.get("opened_at"))
        due = as_date(values.get("due_date"))
        title = task_label(values.get("title"), private_terms=private_terms)
        matter_key = str(values.get("matter_key") or "").strip()
        if not anchor or not due or not title or not matter_key:
            skipped["missing_required_history"] += 1
            continue
        role = values.get("assignee_role")
        if role not in (
            "matter_owner",
            "attorney_of_record",
            "template_applier",
            "unassigned",
        ):
            role = "unassigned"
        task_type = values.get("task_type")
        if task_type not in (
            "deadline",
            "hearing",
            "filing",
            "deposition",
            "call",
            "follow_up",
            "review",
            "general",
        ):
            task_type = "general"
        observations.append(
            Observation(
                matter_key=provider + ":" + digest(matter_key),
                matter_type=str(values.get("matter_type") or "general")[:100],
                practice_area=str(values["practice_area"])[:200]
                if values.get("practice_area")
                else None,
                title=title,
                anchor_date=anchor,
                due_date=due,
                assignee_role=role,
                task_type=task_type,
                source_kind="external_raw_row",
                source_id=str(row.id),
                source_sha256=row.row_checksum,
                provider=provider,
                context_refs=context_refs,
            )
        )
    return observations, dict(skipped)
