from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

from app.services.workflow_observations import (
    Observation,
    amendment,
    imported_observations,
    suggest,
    task_label,
)


def rows(count=4):
    return [
        Observation(
            str(index),
            "Probate",
            None,
            "Request inventory",
            date(2026, 8, 1),
            date(2026, 8, 11),
            "matter_owner",
            "review",
            "task",
            str(uuid4()),
            "a" * 64,
        )
        for index in range(count)
    ]


def test_repeated_practice_produces_only_bounded_configuration():
    result = suggest(rows())[0]
    item = result["definition"]["checklist"][0]
    assert item["due_offset_days"] == 10
    assert item["assignee_role"] == "matter_owner"
    assert result["rule"]["trigger_event"] == "matter_created"
    assert result["evidence"]["items"][0]["matter_count"] == 4
    assert "content_text" not in str(result["evidence"])


def test_duplicate_rows_are_not_additional_matters():
    observation = rows(1)[0]
    assert suggest([observation] * 10) == []


def test_migrated_and_native_copies_cannot_satisfy_sample_threshold_together():
    native = rows(2)
    imported = [
        Observation(
            **{
                **row.__dict__,
                "provider": "clio",
                "matter_key": "clio:" + row.matter_key,
            }
        )
        for row in native
    ]
    assert suggest(native + imported) == []


def test_mixed_assignees_require_manual_assignment():
    source = rows()
    source = [
        Observation(
            **{
                **row.__dict__,
                "assignee_role": "unassigned" if index % 2 else "matter_owner",
            }
        )
        for index, row in enumerate(source)
    ]
    assert (
        suggest(source)[0]["definition"]["checklist"][0]["assignee_role"]
        == "unassigned"
    )


def test_drift_does_not_delete_controls_absent_from_sample():
    observed = suggest(rows())[0]["definition"]
    original = {
        **observed,
        "required_field_definition_ids": [str(uuid4())],
        "checklist": [
            {**observed["checklist"][0], "due_offset_days": 5},
            {
                **observed["checklist"][0],
                "item_key": "conflicts",
                "title": "Complete conflict review",
            },
        ],
    }
    revised, changes = amendment(original, observed)
    assert revised["checklist"][0]["due_offset_days"] == 10
    assert revised["checklist"][1]["title"] == "Complete conflict review"
    assert (
        revised["required_field_definition_ids"]
        == original["required_field_definition_ids"]
    )
    assert original["checklist"][0]["due_offset_days"] == 5
    assert changes[0]["changes"]["due_offset_days"] == {"before": 5, "after": 10}


def raw(data, table):
    return SimpleNamespace(
        id=uuid4(), row_data=data, row_checksum="c" * 64, source_table=table
    )


def test_tabs3_uses_installed_vendor_schema_and_excludes_private_calendar_rows():
    matter = raw(
        {
            "Client_ID": "P001",
            "Date_Open": "08/01/2026",
            "Category": "Probate",
            "Name": "Private client",
        },
        "CMCLIENT",
    )
    calendar = raw(
        {
            "Client_ID": "P001",
            "Due_Date": "08/11/2026",
            "Desc": "Request inventory for Private client",
        },
        "CMCAL",
    )
    private = raw({**calendar.row_data, "Private": 1}, "CMCAL")
    observed, skipped = imported_observations(
        [calendar, private], provider="tabs3", matter_rows=[matter]
    )
    assert len(observed) == 1 and observed[0].title == "Request inventory for [matter]"
    assert observed[0].anchor_date == date(2026, 8, 1)
    assert skipped == {"private_record": 1}


def test_mapped_clio_export_reports_missing_dates_without_inventing_offsets():
    mapping = {
        "matter_key": "Matter",
        "title": "Task",
        "opened_at": "Opened",
        "due_date": "Due",
        "matter_type": "Type",
    }
    source = raw(
        {
            "Matter": "A",
            "Task": "Request records",
            "Opened": "2026-08-01",
            "Due": "2026-08-15",
            "Type": "Injury",
        },
        "WORKFLOW_HISTORY",
    )
    missing = raw(
        {"Matter": "B", "Task": "Request records", "Due": "2026-08-15"},
        "WORKFLOW_HISTORY",
    )
    observed, skipped = imported_observations(
        [source, missing], provider="clio", mapping=mapping
    )
    assert observed[0].due_date - observed[0].anchor_date == timedelta(days=14)
    assert skipped == {"missing_required_history": 1}


def test_labels_remove_identifying_references_and_reject_long_free_text():
    assert (
        task_label("Call client@example.invalid about 123456")
        == "Call [reference] about [reference]"
    )
    assert task_label("a" * 201) is None
