"""Endpoint-level tests for the template-set router.

These call the handlers directly with a mocked async session, the same way the
sample-template routes are covered. They exercise the branches a customer can
actually hit — a duplicate name, a template that is no longer in the library,
reordering — without needing a live database.
"""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.models.document_template_set import DocumentTemplateSetItem
from app.routers import template_sets as router
from app.schemas.document_template_set import (
    DocumentTemplateSetItemInput,
    DocumentTemplateSetWrite,
)

pytestmark = pytest.mark.asyncio


TENANT = uuid.uuid4()
USER = SimpleNamespace(id=uuid.uuid4(), tenant_id=TENANT)


@pytest.fixture(autouse=True)
def _no_tenant_context(monkeypatch):
    monkeypatch.setattr(router, "set_tenant_context", AsyncMock())


class _Rows:
    """A result whose rows carry the columns _titles selects."""

    def __init__(self, pairs):
        self._rows = [SimpleNamespace(id=key, title=title) for key, title in pairs]

    def __iter__(self):
        return iter(self._rows)


def _record(*items, title="Motion packet"):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        title=title,
        description=None,
        module=None,
        jurisdiction=None,
        items=list(items),
        created_at=now,
        updated_at=now,
    )


def _item(template_id, position=0, pinned=None):
    return SimpleNamespace(
        template_id=template_id, position=position, pinned_version_no=pinned
    )


def _write(*template_ids, title="Motion packet"):
    return DocumentTemplateSetWrite(
        title=title,
        items=[DocumentTemplateSetItemInput(template_id=value) for value in template_ids],
    )


class TestValidation:
    async def test_a_set_lists_each_template_once(self):
        # The same template twice would ask its manual questions twice and
        # produce two identical documents.
        template_id = uuid.uuid4()
        with pytest.raises(ValueError):
            _write(template_id, template_id)

    async def test_a_blank_name_is_refused(self):
        with pytest.raises(ValueError):
            DocumentTemplateSetWrite(title="   ")

    async def test_the_member_ceiling_is_enforced_by_the_schema(self):
        with pytest.raises(ValueError):
            _write(*[uuid.uuid4() for _ in range(router.MAX_SET_MEMBERS + 1)])


class TestCreate:
    async def test_a_duplicate_name_is_a_conflict_not_a_second_set(self):
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=1)
        with pytest.raises(HTTPException) as caught:
            await router.create_set(_write(), current_user=USER, db=db)
        assert caught.value.status_code == 409

    async def test_it_stores_members_in_the_order_given(self, monkeypatch):
        first, second = uuid.uuid4(), uuid.uuid4()
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=0)
        db.execute = AsyncMock(
            return_value=_Rows([(first, "Motion"), (second, "Notice")])
        )
        added = []
        db.add = lambda value: added.append(value)
        monkeypatch.setattr(router, "_set_response", AsyncMock(return_value=None))

        await router.create_set(_write(first, second), current_user=USER, db=db)

        items = [value for value in added if isinstance(value, DocumentTemplateSetItem)]
        # Order is the order documents are produced and reviewed in.
        assert [(item.template_id, item.position) for item in items] == [
            (first, 0),
            (second, 1),
        ]
        assert all(item.tenant_id == TENANT for item in items)

    async def test_a_template_outside_the_library_is_named_not_a_500(self):
        gone = uuid.uuid4()
        db = AsyncMock()
        # add() is synchronous on a SQLAlchemy session.
        db.add = MagicMock()
        db.scalar = AsyncMock(return_value=0)
        db.execute = AsyncMock(return_value=_Rows([]))
        with pytest.raises(HTTPException) as caught:
            await router.create_set(_write(gone), current_user=USER, db=db)
        assert caught.value.status_code == 422
        # The customer who just deleted it needs to read which one.
        assert str(gone) in caught.value.detail

    async def test_deletes_are_flushed_before_inserts(self, monkeypatch):
        # The unique (set_id, position) constraint fires against rows on their
        # way out unless the delete is flushed first.
        template_id = uuid.uuid4()
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=0)
        db.execute = AsyncMock(return_value=_Rows([(template_id, "Motion")]))
        order = []
        db.flush = AsyncMock(side_effect=lambda: order.append("flush"))
        db.add = lambda value: order.append(
            "add-item" if isinstance(value, DocumentTemplateSetItem) else "add-set"
        )
        monkeypatch.setattr(router, "_set_response", AsyncMock(return_value=None))

        await router.create_set(_write(template_id), current_user=USER, db=db)

        # Two flushes happen: one for the new set row, one after the deletes.
        # The last one before any insert is the one that matters.
        assert order.index("add-item") > 0
        assert order[order.index("add-item") - 1] == "flush"


class TestReadAndDelete:
    async def test_a_missing_set_is_a_404(self):
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as caught:
            await router.read_set(uuid.uuid4(), current_user=USER, db=db)
        assert caught.value.status_code == 404

    async def test_deleting_a_set_removes_the_grouping_only(self):
        record = _record()
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=record)
        deleted = []
        db.delete = AsyncMock(side_effect=lambda value: deleted.append(value))
        await router.delete_set(record.id, current_user=USER, db=db)
        # Never a template, and never a document one produced.
        assert deleted == [record]


class TestResponses:
    async def test_a_member_whose_template_is_gone_still_renders(self):
        # The set is readable even while one of its members is not draftable;
        # the interview is where that becomes a reported problem.
        present, gone = uuid.uuid4(), uuid.uuid4()
        record = _record(_item(present, 0), _item(gone, 1))
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_Rows([(present, "Motion")]))
        response = await router._set_response(db, TENANT, record)
        assert [item.title for item in response.items] == [
            "Motion",
            "Unavailable template",
        ]

    async def test_an_empty_set_needs_no_title_lookup(self):
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=AssertionError("should not query"))
        response = await router._set_response(db, TENANT, _record())
        assert response.items == []

    async def test_listing_reports_the_total_separately_from_the_page(self):
        record = _record()
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=7)
        db.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: [record]))
        db.execute = AsyncMock(return_value=_Rows([]))
        response = await router.list_sets(
            limit=1, offset=0, current_user=USER, db=db
        )
        assert response.total == 7
        assert len(response.items) == 1


class TestInterview:
    async def test_a_malformed_matter_id_is_a_422_not_a_500(self):
        # The id is the caller's input; a bad one is their mistake to read
        # about, not a server fault to page someone over.
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=_record())
        with pytest.raises(HTTPException) as caught:
            await router.set_interview(
                uuid.uuid4(), matter_id="not-a-uuid", current_user=USER, db=db
            )
        assert caught.value.status_code == 422
        assert "matter_id" in caught.value.detail

    async def test_an_empty_set_with_a_matter_asks_nothing(self, monkeypatch):
        # No questions, so no Smart Fill pass — but the matter is still echoed.
        record = _record()
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=record)
        monkeypatch.setattr(
            router, "_member_snapshots", AsyncMock(return_value=([], [], None))
        )
        monkeypatch.setattr(
            router,
            "_interview_suggestions",
            AsyncMock(side_effect=AssertionError("nothing to fill")),
        )
        matter_id = uuid.uuid4()
        response = await router.set_interview(
            record.id, matter_id=str(matter_id), current_user=USER, db=db
        )
        assert response.questions == []
        assert response.matter_id == matter_id


class TestReplace:
    async def test_renaming_onto_another_set_is_a_conflict(self):
        record = _record()
        db = AsyncMock()
        db.scalar = AsyncMock(side_effect=[record, 1])
        with pytest.raises(HTTPException) as caught:
            await router.replace_set(
                record.id, _write(title="Taken"), current_user=USER, db=db
            )
        assert caught.value.status_code == 409

    async def test_keeping_its_own_name_is_not_a_conflict(self, monkeypatch):
        record = _record()
        db = AsyncMock()
        db.scalar = AsyncMock(side_effect=[record, 0])
        db.execute = AsyncMock(return_value=_Rows([]))
        monkeypatch.setattr(router, "_set_response", AsyncMock(return_value=None))
        await router.replace_set(
            record.id, _write(title=record.title), current_user=USER, db=db
        )
        assert record.title == "Motion packet"
