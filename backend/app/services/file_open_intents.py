"""Creation and atomic redemption of short-lived file-open launch handles."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import set_tenant_context
from app.models.file_open_intent import FileOpenIntent
from app.models.smb_agent import SmbAgent
from app.models.smb_file_index import SmbFileIndex
from app.models.smb_share import SmbShare
from app.services.native_authorization import (
    NativeAuthorizationError,
    require_matter_authorization,
)
from app.services.smb import _normalize_folder_path, path_is_within_root


class OpenIntentError(ValueError):
    pass


def _hash(handle: str) -> str:
    return hashlib.sha256(handle.encode("utf-8")).hexdigest()


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise OpenIntentError("File-open identity is invalid") from exc


async def create_intent(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    file_id: str,
    matter_id: str | None,
    action: str,
) -> tuple[FileOpenIntent, str]:
    settings = get_settings()
    if not settings.FILE_OPEN_ENABLED:
        raise OpenIntentError("File opening is not enabled")
    await set_tenant_context(db, tenant_id)
    tenant_uuid = _uuid(tenant_id)
    user_uuid = _uuid(user_id)
    file_uuid = _uuid(file_id)
    stmt = (
        select(SmbFileIndex, SmbShare, SmbAgent)
        .join(SmbShare, SmbShare.id == SmbFileIndex.share_id)
        .join(SmbAgent, SmbAgent.id == SmbFileIndex.agent_id)
        .where(
            SmbFileIndex.id == file_uuid,
            SmbFileIndex.tenant_id == tenant_uuid,
            SmbFileIndex.is_deleted.is_(False),
            SmbFileIndex.source_id.is_not(None),
            SmbFileIndex.file_revision.is_not(None),
            SmbShare.tenant_id == tenant_uuid,
            SmbShare.is_enabled.is_(True),
            SmbAgent.tenant_id == tenant_uuid,
            SmbAgent.status == "active",
        )
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        raise OpenIntentError("File opener is unavailable until the file is resynced")
    file_entry, share, agent = row
    if matter_id:
        from app.models.matter_smb_share import MatterSmbShare

        # A matter-scoped open carries that matter's restriction/ethical-wall
        # policy, so it is authorized exactly as every other matter-scoped read
        # path is, and before the binding below. Without this a caller could
        # both reach a walled matter's binding and stamp its id onto the
        # durable intent and audit row.
        #
        # A matter-free open stays deliberately unauthorized here: on-premises
        # files commonly belong to no matter, and their access control is the
        # share DACL, which the node enforces by impersonating the signed-in
        # Windows user before it will even disclose the path.
        try:
            await require_matter_authorization(db, tenant_id, user_id, matter_id)
        except NativeAuthorizationError as exc:
            raise OpenIntentError("File is not bound to this matter") from exc

        bindings = (
            (
                await db.execute(
                    select(MatterSmbShare).where(
                        MatterSmbShare.tenant_id == tenant_uuid,
                        MatterSmbShare.matter_id == _uuid(matter_id),
                        MatterSmbShare.share_id == share.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        candidate = str(file_entry.path or "")
        share_root = str(share.share_path or "")
        allowed = False
        for binding in bindings:
            folder = _normalize_folder_path(binding.folder_path)
            folder_windows = folder.replace("/", "\\") if folder else None
            root = share_root.rstrip("\\/") + (
                f"\\{folder_windows}" if folder_windows else ""
            )
            if path_is_within_root(candidate, root):
                allowed = True
                break
        if not allowed:
            raise OpenIntentError("File is not bound to this matter")
    handle = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    intent = FileOpenIntent(
        tenant_id=tenant_uuid,
        user_id=user_uuid,
        file_id=file_entry.id,
        source_id=file_entry.source_id,
        agent_id=agent.id,
        share_id=share.id,
        matter_id=_uuid(matter_id) if matter_id else None,
        revision=file_entry.file_revision,
        action=action,
        handle_hash=_hash(handle),
        nonce=secrets.token_urlsafe(24),
        expires_at=now + timedelta(seconds=90),
    )
    db.add(intent)
    await db.commit()
    await db.refresh(intent)
    return intent, handle


async def redeem_intent(
    db: AsyncSession,
    *,
    tenant_id: str,
    agent_id: str,
    handle: str,
    action: str,
    session_id: str,
    user_sid: str,
) -> FileOpenIntent:
    settings = get_settings()
    if not settings.FILE_OPEN_ENABLED:
        raise OpenIntentError("File opening is not enabled")
    await set_tenant_context(db, tenant_id)
    tenant_uuid = _uuid(tenant_id)
    agent_uuid = _uuid(agent_id)
    now = datetime.now(timezone.utc)

    async def reject(intent: FileOpenIntent, outcome: str, message: str):
        intent.last_failure = outcome
        intent.last_failure_at = now
        intent.failure_count = int(intent.failure_count or 0) + 1
        await db.commit()
        raise OpenIntentError(message)

    # FOR UPDATE serializes competing redemptions; only the winner marks it used.
    result = await db.execute(
        select(FileOpenIntent)
        .where(
            FileOpenIntent.handle_hash == _hash(handle),
            FileOpenIntent.tenant_id == tenant_uuid,
        )
        .with_for_update()
    )
    intent = result.scalar_one_or_none()
    if intent is None:
        raise OpenIntentError("Open handle is invalid or expired")
    if intent.agent_id != agent_uuid:
        await reject(intent, "agent_mismatch", "Open agent does not match the intent")
    if intent.expires_at <= now:
        await reject(intent, "expired", "Open handle is invalid or expired")
    if intent.redeemed_at is not None:
        await reject(intent, "replay", "Open handle has already been redeemed")
    if intent.action != action:
        await reject(intent, "action_mismatch", "Open action does not match the intent")
    user_sid_hash = hashlib.sha256(user_sid.encode("utf-8")).hexdigest()
    live = await db.execute(
        select(SmbFileIndex.id)
        .join(SmbShare, SmbShare.id == SmbFileIndex.share_id)
        .join(SmbAgent, SmbAgent.id == SmbFileIndex.agent_id)
        .where(
            SmbFileIndex.id == intent.file_id,
            SmbFileIndex.tenant_id == tenant_uuid,
            SmbFileIndex.source_id == intent.source_id,
            SmbFileIndex.file_revision == intent.revision,
            SmbFileIndex.is_deleted.is_(False),
            SmbShare.id == intent.share_id,
            SmbShare.is_enabled.is_(True),
            SmbAgent.id == agent_uuid,
            SmbAgent.status == "active",
        )
    )
    if live.scalar_one_or_none() is None:
        await reject(intent, "moved_or_offline", "File or share is no longer available")
    intent.redeemed_at = now
    intent.redeemed_session_id = session_id
    intent.redeemed_user_sid_hash = user_sid_hash
    await db.commit()
    return intent


async def record_outcome(
    db: AsyncSession, *, tenant_id: str, agent_id: str, intent_id: str, outcome: str
) -> None:
    await set_tenant_context(db, tenant_id)
    tenant_uuid = _uuid(tenant_id)
    agent_uuid = _uuid(agent_id)
    intent_uuid = _uuid(intent_id)
    result = await db.execute(
        select(FileOpenIntent)
        .where(
            FileOpenIntent.id == intent_uuid,
            FileOpenIntent.tenant_id == tenant_uuid,
            FileOpenIntent.agent_id == agent_uuid,
        )
        .with_for_update()
    )
    intent = result.scalar_one_or_none()
    if intent is None or intent.redeemed_at is None:
        raise OpenIntentError("Open handle is not redeemed")
    intent.outcome = outcome
    await db.commit()
