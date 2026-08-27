import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import validate_inbound_email_settings
from app.database import set_inbound_email_route_lookup
from app.models.inbound_email import InboundEmail
from app.services import inbound_email as inbound_service
from app.services.inbound_email import (
    ALIAS_LOCAL_PART_RE,
    alias_lookup_hash,
    delivery_signature,
    file_inbound_email,
    generate_alias_local_part,
    inbound_filename,
    parse_raw_email,
    quarantine_path,
    read_quarantined_message,
    remove_quarantined_message,
    verify_delivery_signature,
    write_quarantined_message,
)
from app.services.email_task_tags import email_received_at, parse_email_task_tag
from app.services.matter_file_store import StorageResult


def _raw_email() -> bytes:
    message = EmailMessage()
    message["From"] = "Jane Doe <jane@example.com>"
    message["To"] = "m-example@intake.getlawhand.com"
    message["Subject"] = "Documents for review"
    message["Message-ID"] = "<message-123@example.com>"
    message["Date"] = "Mon, 24 Aug 2026 10:00:00 -0500"
    message["Authentication-Results"] = "mx.example; dmarc=pass; spf=pass"
    message.set_content("Please review the attached documents.")
    return message.as_bytes()


def test_alias_is_opaque_email_safe_and_hashable():
    first = generate_alias_local_part()
    second = generate_alias_local_part()

    assert ALIAS_LOCAL_PART_RE.fullmatch(first)
    assert len(first) == 28
    assert first != second
    assert alias_lookup_hash(first.upper()) == alias_lookup_hash(first)


def test_worker_signature_round_trip_and_tamper_rejection():
    raw = _raw_email()
    secret = "a-random-test-secret-that-is-long-enough"
    now = datetime(2026, 8, 24, 15, 0, tzinfo=timezone.utc)
    timestamp = str(int(now.timestamp()))
    sender = "jane@example.com"
    recipient = "m-abcdefghijklmnopqrstuvwxyz@intake.getlawhand.com"
    signature = delivery_signature(secret, timestamp, sender, recipient, raw)

    assert verify_delivery_signature(
        supplied_signature=signature,
        secret=secret,
        timestamp=timestamp,
        envelope_sender=sender,
        recipient=recipient,
        raw_message=raw,
        now=now,
    )
    assert not verify_delivery_signature(
        supplied_signature=signature,
        secret=secret,
        timestamp=timestamp,
        envelope_sender=sender,
        recipient=recipient,
        raw_message=raw + b"tampered",
        now=now,
    )


def test_worker_signature_rejects_stale_delivery():
    raw = _raw_email()
    secret = "a-random-test-secret-that-is-long-enough"
    delivered = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)
    timestamp = str(int(delivered.timestamp()))
    signature = delivery_signature(
        secret, timestamp, "jane@example.com", "m-token@intake.getlawhand.com", raw
    )

    assert not verify_delivery_signature(
        supplied_signature=signature,
        secret=secret,
        timestamp=timestamp,
        envelope_sender="jane@example.com",
        recipient="m-token@intake.getlawhand.com",
        raw_message=raw,
        now=delivered + timedelta(minutes=6),
        tolerance_seconds=300,
    )


def test_raw_email_parser_extracts_bounded_review_metadata():
    parsed = parse_raw_email(_raw_email())

    assert parsed["subject"] == "Documents for review"
    assert parsed["body_preview"] == "Please review the attached documents."
    assert parsed["participants"]["from"] == "jane@example.com"
    assert parsed["message_id"] == "<message-123@example.com>"
    assert "dmarc=pass" in parsed["authentication_results"]["authentication_results"][0]


def test_task_subject_tag_parses_relative_meeting_from_received_date():
    suggestion = parse_email_task_tag(
        "[TASK] Nigel I need to meet with you in two weeks",
        received_at=datetime(2026, 8, 26, 16, 30, tzinfo=timezone.utc),
    )

    assert suggestion is not None
    assert suggestion.title == "Nigel I need to meet with you"
    assert suggestion.task_type == "follow_up"
    assert suggestion.priority == "medium"
    assert suggestion.due_date.isoformat() == "2026-09-09"


@pytest.mark.parametrize(
    ("subject", "expected_tag", "expected_due"),
    [
        ("[DEADLINE] File response by 09/15/2026", "deadline", "2026-09-15"),
        ("[TASK due=2026-09-12] Call client", "task", "2026-09-12"),
        ("[TASK] Review exhibits tomorrow", "task", "2026-08-27"),
    ],
)
def test_task_subject_tag_supports_bounded_date_forms(
    subject, expected_tag, expected_due
):
    suggestion = parse_email_task_tag(
        subject,
        received_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
    )

    assert suggestion is not None
    assert suggestion.tag == expected_tag
    assert suggestion.due_date.isoformat() == expected_due


@pytest.mark.parametrize(
    "subject",
    [
        "Nigel I need to meet with you in two weeks",
        "Re: [TASK] Meet with Nigel in two weeks",
        "Fwd: [DEADLINE] File response by 2026-09-15",
    ],
)
def test_untagged_replies_and_forwards_do_not_create_tasks(subject):
    assert parse_email_task_tag(subject) is None


def test_email_received_at_normalizes_microsoft_and_google_dates():
    microsoft = email_received_at({"received": "2026-08-26T10:00:00-05:00"})
    google = email_received_at({"date": "Wed, 26 Aug 2026 10:00:00 -0500"})

    assert microsoft.isoformat() == "2026-08-26T15:00:00+00:00"
    assert google == microsoft


def test_signature_and_parser_fail_closed_edge_cases():
    raw = _raw_email()
    assert not verify_delivery_signature(
        supplied_signature="",
        secret="x" * 40,
        timestamp="1",
        envelope_sender="from@example.com",
        recipient="to@example.com",
        raw_message=raw,
    )
    assert not verify_delivery_signature(
        supplied_signature="v1=bad",
        secret="x" * 40,
        timestamp="not-a-time",
        envelope_sender="from@example.com",
        recipient="to@example.com",
        raw_message=raw,
    )

    html = EmailMessage()
    html["From"] = "sender@example.com"
    html["To"] = "recipient@example.com"
    html["Subject"] = "HTML"
    html.set_content("<p>Hello <strong>world</strong></p>", subtype="html")
    html.add_attachment(
        b"secret attachment", maintype="application", subtype="octet-stream"
    )
    parsed = parse_raw_email(html.as_bytes())
    assert parsed["body_preview"] == "Hello world"
    assert "secret attachment" not in parsed["body_preview"]


def test_quarantine_round_trip_integrity_and_path_guards(tmp_path, monkeypatch):
    monkeypatch.setattr(inbound_service.settings, "UPLOAD_DIR", str(tmp_path))
    tenant_id = uuid.uuid4()
    inbound_id = uuid.uuid4()
    raw = b"From: sender@example.com\r\nSubject: Test\r\n\r\nBody"
    path = quarantine_path(tenant_id, inbound_id)
    write_quarantined_message(path, raw)
    assert path.read_bytes() == raw
    with pytest.raises(FileExistsError):
        write_quarantined_message(path, raw)

    item = SimpleNamespace(
        tenant_id=tenant_id,
        raw_storage_path=str(path),
        raw_size=len(raw),
        message_sha256=__import__("hashlib").sha256(raw).hexdigest(),
    )
    assert read_quarantined_message(item) == raw
    assert inbound_filename(
        SimpleNamespace(
            subject="Unsafe / Subject!",
            occurred_at=datetime(2026, 8, 24),
            id=inbound_id,
        )
    ).startswith("2026-08-24_unsafe-subject_")

    path.write_bytes(raw + b"tampered")
    with pytest.raises(ValueError):
        read_quarantined_message(item)
    path.write_bytes(raw)
    remove_quarantined_message(item)
    assert not path.exists()
    remove_quarantined_message(item)

    item.raw_storage_path = str(tmp_path / "outside.eml")
    with pytest.raises(PermissionError):
        read_quarantined_message(item)
    with pytest.raises(PermissionError):
        remove_quarantined_message(item)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"INBOUND_EMAIL_DOMAIN": "invalid"}, "valid email domain"),
        ({"INBOUND_EMAIL_WEBHOOK_SECRET": "short"}, "at least 32"),
        ({"INBOUND_EMAIL_WEBHOOK_SECRET": "change-me-" + "x" * 32}, "placeholder"),
        ({"INBOUND_EMAIL_MAX_BYTES": 100}, "between 1 KiB"),
        ({"INBOUND_EMAIL_SIGNATURE_TOLERANCE_SECONDS": 10}, "between 30 and 900"),
    ],
)
def test_inbound_email_settings_validation(overrides, message):
    values = {
        "INBOUND_EMAIL_ENABLED": True,
        "INBOUND_EMAIL_DOMAIN": "intake.getlawhand.com",
        "INBOUND_EMAIL_WEBHOOK_SECRET": "z" * 64,
        "INBOUND_EMAIL_MAX_BYTES": 1024,
        "INBOUND_EMAIL_SIGNATURE_TOLERANCE_SECONDS": 300,
    }
    values.update(overrides)
    with pytest.raises(ValueError, match=message):
        validate_inbound_email_settings(SimpleNamespace(**values))

    values["INBOUND_EMAIL_ENABLED"] = False
    validate_inbound_email_settings(SimpleNamespace(**values))


@pytest.mark.asyncio
async def test_route_lookup_guc_is_transaction_local():
    session = SimpleNamespace(execute=AsyncMock())
    await set_inbound_email_route_lookup(session, enabled=True)
    await set_inbound_email_route_lookup(session, enabled=False)
    assert session.execute.await_args_list[0].args[1] == {"value": "on"}
    assert session.execute.await_args_list[1].args[1] == {"value": "off"}
    assert "true" in str(session.execute.await_args_list[0].args[0])


class ServiceResult:
    def scalar_one_or_none(self):
        return None


class ServiceDB:
    def __init__(self, *, flush_error_at=None):
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0
        self.flush_error_at = flush_error_at

    async def execute(self, *_args, **_kwargs):
        return ServiceResult()

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flushes += 1
        if self.flush_error_at == self.flushes:
            raise RuntimeError("flush failed")
        value = self.added[-1]
        if getattr(value, "id", None) is None:
            value.id = uuid.uuid4()

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def service_item(tmp_path):
    tenant_id = uuid.uuid4()
    matter_id = uuid.uuid4()
    item_id = uuid.uuid4()
    raw = _raw_email()
    folder = tmp_path / str(tenant_id) / "inbound-email"
    folder.mkdir(parents=True)
    path = folder / f"{item_id}.eml"
    path.write_bytes(raw)
    item = InboundEmail(
        id=item_id,
        tenant_id=tenant_id,
        alias_id=uuid.uuid4(),
        matter_id=matter_id,
        status="pending",
        envelope_sender="jane@example.com",
        recipient="m-abcdefghijklmnopqrstuvwxyz@intake.getlawhand.com",
        subject="Documents for review",
        body_preview="Please review",
        participants={"from": "jane@example.com", "to": []},
        authentication_results={},
        provider_message_id="<id@example.com>",
        message_sha256=__import__("hashlib").sha256(raw).hexdigest(),
        raw_storage_path=str(path),
        raw_size=len(raw),
        occurred_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    matter = SimpleNamespace(
        id=matter_id,
        slug="sample-matter",
        cloud_folder=None,
    )
    return item, matter, path


@pytest.mark.asyncio
async def test_file_inbound_email_moves_verified_message_and_audits(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(inbound_service.settings, "UPLOAD_DIR", str(tmp_path))
    item, matter, path = service_item(tmp_path)
    stored = StorageResult(
        provider="local", backend="local", storage_path=str(tmp_path / "filed.eml")
    )
    store = SimpleNamespace(
        store_matter_file_result=AsyncMock(return_value=stored),
        delete_stored_result=AsyncMock(),
    )
    monkeypatch.setattr(inbound_service, "matter_file_store", store)
    db = ServiceDB()

    filing = await file_inbound_email(
        db,
        item=item,
        matter=matter,
        reviewed_by_user_id=uuid.uuid4(),
    )

    assert filing.communication.id == item.communication_log_id
    assert filing.task is None
    assert item.status == "accepted"
    assert item.raw_storage_path is None
    assert not path.exists()
    assert db.commits == 2
    assert len(db.added) == 2


@pytest.mark.asyncio
async def test_file_inbound_email_atomically_creates_explicit_tagged_task(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(inbound_service.settings, "UPLOAD_DIR", str(tmp_path))
    item, matter, path = service_item(tmp_path)
    item.subject = "[TASK] Nigel I need to meet with you in two weeks"
    item.occurred_at = datetime(2026, 8, 26, 16, 30, tzinfo=timezone.utc)
    stored = StorageResult(
        provider="local", backend="local", storage_path=str(tmp_path / "filed.eml")
    )
    store = SimpleNamespace(
        store_matter_file_result=AsyncMock(return_value=stored),
        delete_stored_result=AsyncMock(),
    )
    monkeypatch.setattr(inbound_service, "matter_file_store", store)
    db = ServiceDB()
    reviewer_id = uuid.uuid4()

    filing = await file_inbound_email(
        db,
        item=item,
        matter=matter,
        reviewed_by_user_id=reviewer_id,
    )

    assert filing.task is not None
    assert filing.task.title == "Nigel I need to meet with you"
    assert filing.task.due_date.isoformat() == "2026-09-09"
    assert filing.task.assigned_to_user_id == reviewer_id
    assert filing.task.external_ref == f"inbound-email:{item.id}"
    assert filing.task.source == "email_subject_tag"
    assert item.status == "accepted"
    assert not path.exists()
    assert db.commits == 2
    assert len(db.added) == 5


@pytest.mark.asyncio
async def test_file_inbound_email_cleans_staged_storage_after_database_failure(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(inbound_service.settings, "UPLOAD_DIR", str(tmp_path))
    item, matter, _ = service_item(tmp_path)
    stored = StorageResult(
        provider="local", backend="local", storage_path=str(tmp_path / "filed.eml")
    )
    store = SimpleNamespace(
        store_matter_file_result=AsyncMock(return_value=stored),
        delete_stored_result=AsyncMock(),
    )
    monkeypatch.setattr(inbound_service, "matter_file_store", store)
    monkeypatch.setattr(inbound_service, "set_tenant_context", AsyncMock())
    db = ServiceDB(flush_error_at=1)

    with pytest.raises(RuntimeError, match="flush failed"):
        await file_inbound_email(
            db,
            item=item,
            matter=matter,
            reviewed_by_user_id=uuid.uuid4(),
        )
    assert db.rollbacks == 1
    store.delete_stored_result.assert_awaited_once()


@pytest.mark.asyncio
async def test_tagged_task_failure_rolls_back_email_filing_and_staged_storage(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(inbound_service.settings, "UPLOAD_DIR", str(tmp_path))
    item, matter, _ = service_item(tmp_path)
    item.subject = "[DEADLINE] File response by 2026-09-15"
    stored = StorageResult(
        provider="local", backend="local", storage_path=str(tmp_path / "filed.eml")
    )
    store = SimpleNamespace(
        store_matter_file_result=AsyncMock(return_value=stored),
        delete_stored_result=AsyncMock(),
    )
    monkeypatch.setattr(inbound_service, "matter_file_store", store)
    monkeypatch.setattr(inbound_service, "set_tenant_context", AsyncMock())
    # Document and communication flushes succeed; task flush fails before the
    # shared transaction can accept the inbound message.
    db = ServiceDB(flush_error_at=3)

    with pytest.raises(RuntimeError, match="flush failed"):
        await file_inbound_email(
            db,
            item=item,
            matter=matter,
            reviewed_by_user_id=uuid.uuid4(),
        )

    assert db.commits == 0
    assert db.rollbacks == 1
    store.delete_stored_result.assert_awaited_once()
