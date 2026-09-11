"""Filing the firm's own sent email as a document on the matter.

Inbound mail has always been filed; what the firm sent was only a log row.
The two properties that matter here are that the copy is faithful, and that
failing to file can never disturb a send that has already gone out.
"""

import uuid
from email import policy
from email.parser import BytesParser
from types import SimpleNamespace

import pytest

from app.services import correspondence_capture
from app.services.mail_attachment import MailAttachment


class RecordingDb:
    """Enough session for the filing path, recording what it was given."""

    def __init__(self, tenant_settings=None):
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self._tenant_settings = tenant_settings

    async def scalar(self, _statement):
        return self._tenant_settings

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def stored(monkeypatch):
    calls = {}

    async def store(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(
            storage_path="matters/a/correspondence/x.eml",
            provider="local",
            backend="disk",
            provider_item_id=None,
            drive_id=None,
            parent_id=None,
            error=None,
        )

    monkeypatch.setattr(
        correspondence_capture.matter_file_store,
        "store_matter_file_result",
        store,
    )

    async def folder(*_args, **_kwargs):
        return "correspondence-folder"

    monkeypatch.setattr(correspondence_capture, "autofile_folder_id", folder)
    return calls


@pytest.mark.asyncio
async def test_sent_email_is_filed_as_a_faithful_eml(stored):
    db = RecordingDb()
    matter_id = uuid.uuid4()
    communication = SimpleNamespace(document_id=None)

    document = await correspondence_capture.file_outbound_email(
        db,
        tenant_id=uuid.uuid4(),
        matter_id=matter_id,
        matter_slug="smith-v-jones",
        cloud_folder=None,
        actor_user_id=uuid.uuid4(),
        to=["client@example.com"],
        subject="Your hearing date",
        text_body="The hearing is on the 14th.",
        attachments=[
            MailAttachment(
                filename="notice.pdf",
                content=b"%PDF-1.4 notice",
                content_type="application/pdf",
            )
        ],
        communication=communication,
    )

    assert document is not None
    assert document.matter_id == matter_id
    assert document.content_type == "message/rfc822"
    assert document.document_category == "correspondence"
    assert document.folder_id == "correspondence-folder"
    # The log row points at the filed copy, so the timeline and the file agree.
    assert communication.document_id == document.id
    assert db.commits == 1 and db.rollbacks == 0

    message = BytesParser(policy=policy.default).parsebytes(stored["content"])
    assert message["Subject"] == "Your hearing date"
    assert message["To"] == "client@example.com"
    attachments = [
        part
        for part in message.walk()
        if part.get_content_disposition() == "attachment"
    ]
    assert [part.get_filename() for part in attachments] == ["notice.pdf"]
    assert attachments[0].get_payload(decode=True) == b"%PDF-1.4 notice"


@pytest.mark.asyncio
async def test_filing_failure_never_reaches_the_caller(monkeypatch):
    """The email already left; a filing problem cannot become a request error."""
    db = RecordingDb()

    async def explode(**_kwargs):
        raise RuntimeError("storage is unavailable")

    monkeypatch.setattr(
        correspondence_capture.matter_file_store,
        "store_matter_file_result",
        explode,
    )

    result = await correspondence_capture.file_outbound_email(
        db,
        tenant_id=uuid.uuid4(),
        matter_id=uuid.uuid4(),
        matter_slug="smith-v-jones",
        actor_user_id=None,
        to=["client@example.com"],
        subject="Your hearing date",
        text_body="The hearing is on the 14th.",
    )

    assert result is None
    assert db.rollbacks == 1 and db.commits == 0


@pytest.mark.asyncio
async def test_a_session_that_cannot_roll_back_still_returns(monkeypatch):
    """Even the cleanup is not allowed to raise into a completed send."""

    class NoRollbackDb(RecordingDb):
        async def scalar(self, _statement):
            raise RuntimeError("session is unusable")

        async def rollback(self):
            raise RuntimeError("rollback is unusable too")

    result = await correspondence_capture.file_outbound_email(
        NoRollbackDb(),
        tenant_id=uuid.uuid4(),
        matter_id=uuid.uuid4(),
        matter_slug="smith-v-jones",
        actor_user_id=None,
        to=["client@example.com"],
        subject="Your hearing date",
        text_body="The hearing is on the 14th.",
    )

    assert result is None


@pytest.mark.asyncio
async def test_filing_takes_values_not_an_expired_orm_row(stored):
    """Sending can refresh a token, which expires every attribute on the row.

    Filing therefore accepts the matter's id, slug and cloud folder as plain
    values; passing the row itself is what broke once a provider refresh
    committed mid-request.
    """
    db = RecordingDb()
    matter_id = uuid.uuid4()

    document = await correspondence_capture.file_outbound_email(
        db,
        tenant_id=uuid.uuid4(),
        matter_id=matter_id,
        matter_slug="smith-v-jones",
        cloud_folder={"provider": "onedrive"},
        actor_user_id=None,
        to=["client@example.com"],
        subject="Your hearing date",
        text_body="The hearing is on the 14th.",
    )

    assert document is not None
    assert stored["matter_slug"] == "smith-v-jones"
    assert stored["matter_cloud_folder"] == {"provider": "onedrive"}
    assert stored["category"] == "correspondence"
