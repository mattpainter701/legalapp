"""CSV bounds, explicit joins, and exclusion of unrelated imported content."""

import pytest

from app.services.workflow_history_import import (
    MAX_BYTES,
    file_fingerprint,
    normalize_history,
    parse_csv,
)


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"x" * (MAX_BYTES + 1),
        b"a,a\n1,2",
        b"\n",
        b"a,b\n1,2,3",
        b"\xff",
        b"x" * 201 + b"\nvalue",
    ],
    ids=[
        "empty",
        "oversized",
        "duplicate-header",
        "missing-header",
        "extra-value",
        "invalid-utf8",
        "long-header",
    ],
)
def test_csv_rejects_ambiguous_or_unbounded_input(content):
    with pytest.raises(ValueError):
        parse_csv(content)


def test_bom_and_quoted_csv_are_supported():
    headers, rows = parse_csv(b'\xef\xbb\xbfName,Value\n"A, B",5\n')
    assert headers == ["Name", "Value"] and rows == [{"Name": "A, B", "Value": "5"}]
    with pytest.raises(ValueError, match="5,000"):
        parse_csv(b"A\n" + b"row\n" * 5001)
    with pytest.raises(ValueError, match="100"):
        parse_csv((",".join(str(n) for n in range(101)) + "\n").encode())


def fixture():
    return (
        [
            {
                "key": "A",
                "task": "Request records for Acme",
                "due": "08/15/2026",
                "notes": "private advice",
                "role": "Named person",
            }
        ],
        [{"key": "A", "opened": "2026-08-01", "name": "Acme", "type": "injury"}],
        ["key", "task", "due", "notes", "role"],
        ["key", "opened", "name", "type"],
        {
            "tasks": {
                "matter_key": "key",
                "title": "task",
                "due_date": "due",
                "assignee_role": "role",
            },
            "matters": {
                "matter_key": "key",
                "opened_at": "opened",
                "matter_name": "name",
                "matter_type": "type",
            },
        },
    )


def test_mapped_join_retains_only_scheduling_fields_and_removes_names():
    rows, skipped = normalize_history(*fixture())
    assert skipped == {}
    assert rows[0]["title"] == "Request records for [matter]"
    assert rows[0]["opened_at"] == "2026-08-01"
    assert rows[0]["assignee_role"] == "unassigned"
    assert not {"notes", "matter_name", "name"} & rows[0].keys()
    assert "Named person" not in str(rows)


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_section",
        "unknown_field",
        "bad_column",
        "object_column",
        "missing_key",
        "missing_opened",
        "duplicate_matter",
        "invalid_date",
        "negative_offset",
    ],
)
def test_invalid_mapping_and_join_fail_explicitly(mutation):
    tasks, matters, th, mh, mapping = fixture()
    if mutation == "unknown_section":
        mapping["notes"] = {}
    if mutation == "unknown_field":
        mapping["tasks"]["secret"] = "notes"
    if mutation == "bad_column":
        mapping["tasks"]["title"] = "absent"
    if mutation == "object_column":
        mapping["tasks"]["title"] = {"bad": True}
    if mutation == "missing_key":
        mapping["tasks"].pop("matter_key")
    if mutation == "missing_opened":
        mapping["matters"].pop("opened_at")
    if mutation == "duplicate_matter":
        matters.append(dict(matters[0]))
    if mutation == "invalid_date":
        tasks[0]["due"] = "unknown"
    if mutation == "negative_offset":
        tasks[0]["due"] = "2025-01-01"
    with pytest.raises(ValueError):
        normalize_history(tasks, matters, th, mh, mapping)


def test_skipped_rows_are_counted_and_both_files_bind_the_fingerprint():
    tasks, matters, th, mh, mapping = fixture()
    tasks.append({"key": "missing", "task": "Request records", "due": "2026-08-15"})
    _, skipped = normalize_history(tasks, matters, th, mh, mapping)
    assert skipped == {"missing_or_invalid_mapped_history": 1}
    assert file_fingerprint(b"ab", b"c") != file_fingerprint(b"a", b"bc")
    assert file_fingerprint(b"a", None) != file_fingerprint(b"a", b"b")
