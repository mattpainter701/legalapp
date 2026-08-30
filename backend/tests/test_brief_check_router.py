import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.models.brief_check import BriefCheck
from app.routers import brief_checks as router
from app.schemas.brief_check import BriefCheckDecision


class Result:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def scalar_one_or_none(self):
        return self.row

    def scalars(self):
        return self

    def all(self):
        return self.rows


class DB:
    def __init__(self, *results):
        self.results = list(results)
        self.added = []

    async def execute(self, _query):
        return self.results.pop(0)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        for value in self.added:
            if isinstance(value, BriefCheck) and value.id is None:
                value.id = uuid.uuid4()

    async def commit(self):
        return None

    async def refresh(self, row):
        row.created_at = row.created_at or datetime.now(timezone.utc)
        row.updated_at = row.updated_at or row.created_at


def user():
    return SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), role="admin")


def upload(name="brief.docx", data=b"brief"):
    return UploadFile(
        filename=name,
        file=__import__("io").BytesIO(data),
        headers=Headers(
            {
                "content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            }
        ),
    )


@pytest.mark.asyncio
async def test_create_upload_is_bounded_and_idempotent(monkeypatch):
    actor = user()
    matter_id = uuid.uuid4()
    monkeypatch.setattr(router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(router, "_matter", AsyncMock())
    monkeypatch.setattr(router, "_text", AsyncMock(return_value="brief text"))
    monkeypatch.setattr(
        router,
        "analyze_brief",
        lambda text, opposing_text=None: {
            "citations": [],
            "quotations": [],
            "omitted_authority_candidates": [],
        },
    )
    db = DB(Result(None))
    created = await router.create_brief_check(
        matter_id, upload(), None, None, db, actor
    )
    assert created["input_filename"] == "brief.docx"
    assert any(type(item).__name__ == "BriefCheckAudit" for item in db.added)

    existing = db.added[0]
    existing.created_at = existing.updated_at = datetime.now(timezone.utc)
    repeat = await router.create_brief_check(
        matter_id, upload(), None, None, DB(Result(existing)), actor
    )
    assert repeat["id"] == existing.id


@pytest.mark.asyncio
async def test_list_decision_and_exports_are_matter_scoped(monkeypatch):
    actor = user()
    matter_id = uuid.uuid4()
    check_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    row = BriefCheck(
        id=check_id,
        tenant_id=actor.tenant_id,
        matter_id=matter_id,
        input_filename="brief.docx",
        input_sha256="a" * 64,
        input_size=5,
        result_json={
            "citations": [
                {
                    "id": "citation-1",
                    "input": "123 F.3d 456",
                    "status": "missing_source",
                    "location": "paragraph 1",
                }
            ],
            "quotations": [],
            "omitted_authority_candidates": [],
        },
        created_at=now,
        updated_at=now,
    )
    monkeypatch.setattr(router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(router, "_matter", AsyncMock())
    listed = await router.list_brief_checks(matter_id, DB(Result(rows=[row])), actor)
    assert listed["items"][0]["id"] == check_id

    db = DB(Result(row))
    decided = await router.decide_brief_check(
        matter_id,
        check_id,
        BriefCheckDecision(item_id="citation-1", decision="accepted", note="reviewed"),
        db,
        actor,
    )
    assert decided["result"]["citations"][0]["attorney_decision"] == "accepted"
    monkeypatch.setattr(router, "markdown_to_docx_bytes", lambda content, name: b"PK")
    for kind in ("report", "toa", "table-of-authorities"):
        response = await router.export_brief_check(
            matter_id, check_id, kind, DB(Result(row)), actor
        )
        assert response.body == b"PK"


@pytest.mark.asyncio
async def test_upload_and_text_failure_states_are_explicit(monkeypatch):
    actor = user()
    matter_id = uuid.uuid4()
    monkeypatch.setattr(router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(router, "_matter", AsyncMock())
    with pytest.raises(Exception) as missing:
        await router.create_brief_check(matter_id, None, None, None, DB(), actor)
    assert getattr(missing.value, "status_code", None) == 400
    with pytest.raises(Exception) as unsupported:
        await router._text("brief.txt", "text/plain", b"text")
    assert unsupported.value.status_code == 415
    with pytest.raises(Exception) as unknown_export:
        await router.export_brief_check(
            matter_id,
            uuid.uuid4(),
            "unknown",
            DB(
                Result(
                    BriefCheck(
                        id=uuid.uuid4(),
                        tenant_id=actor.tenant_id,
                        matter_id=matter_id,
                        input_filename="x",
                        input_sha256="b" * 64,
                        input_size=1,
                        result_json={},
                    )
                )
            ),
            actor,
        )
    assert unknown_export.value.status_code == 404
