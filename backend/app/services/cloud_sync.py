"""
CloudSyncService — incremental metadata sync from Google Drive, Gmail,
Microsoft OneDrive, SharePoint, and Outlook Mail into cloud_metadata_index.

Called from:
  - scheduler.py (cron interval)
  - cloud_admin.py (manual trigger via admin endpoint)

Key patterns:
  - Token resolution: user-scoped first (get_fresh_user_token), fall back to
    tenant-scoped (get_fresh_token).
  - Upsert via PostgreSQL ON CONFLICT DO UPDATE on the unique constraint
    (tenant_id, provider, object_type, object_id).
  - snippet is always capped at 500 characters.
  - File caps: 500 files per source, 200 emails per source.
  - Per-provider errors are caught individually so a single provider failure
    does not prevent others from syncing.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dateutil import parser as dateutil_parser
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import set_tenant_context
from app.models.cloud_metadata import CloudMetadata
from app.services.token_vault import get_fresh_token, get_fresh_user_token

settings = get_settings()
logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GOOGLE_DRIVE_BASE = "https://www.googleapis.com/drive/v3"
GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1"

LEGAL_EXTENSIONS = {".pdf", ".docx", ".doc", ".docm", ".rtf", ".txt", ".wpd", ".odt"}
LEGAL_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "application/rtf",
    "text/plain",
}

EMAIL_MIME_TYPE = "message/rfc822"

MAX_FILES = 500
MAX_EMAILS = 200


class CloudSyncService:
    """Incremental metadata sync from cloud providers into cloud_metadata_index."""

    # ── Public API ──────────────────────────────────────────────────────────

    async def sync_all(self, db: AsyncSession, tenant_id: str) -> dict:
        """Sync Google + Microsoft for a tenant.

        Returns ``{google: {files, emails}, microsoft: {files, emails}}``
        with counts of records upserted per provider group.
        Per-provider errors are caught individually so a single failure
        does not prevent the other providers from syncing.
        """
        await set_tenant_context(db, tenant_id)

        result: dict = {
            "google": {"files": 0, "emails": 0},
            "microsoft": {"files": 0, "emails": 0},
        }

        # Google
        try:
            result["google"]["files"] = await self.sync_google_drive(db, tenant_id)
        except Exception as exc:
            logger.warning("Google Drive sync failed for tenant %s: %s", tenant_id, exc)
        try:
            result["google"]["emails"] = await self.sync_gmail_metadata(db, tenant_id)
        except Exception as exc:
            logger.warning("Gmail sync failed for tenant %s: %s", tenant_id, exc)

        # Microsoft
        try:
            result["microsoft"]["files"] = await self.sync_onedrive(db, tenant_id)
        except Exception as exc:
            logger.warning("OneDrive sync failed for tenant %s: %s", tenant_id, exc)
        try:
            result["microsoft"]["files"] += await self.sync_sharepoint(db, tenant_id)
        except Exception as exc:
            logger.warning("SharePoint sync failed for tenant %s: %s", tenant_id, exc)
        try:
            result["microsoft"]["emails"] = await self.sync_outlook_mail(db, tenant_id)
        except Exception as exc:
            logger.warning("Outlook mail sync failed for tenant %s: %s", tenant_id, exc)

        return result

    async def sync_google_drive(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str | None = None,
    ) -> int:
        """Sync file metadata from Google Drive.

        Uses the Drive v3 ``files.list`` endpoint with ``fields`` restricted
        to ``files(id,name,mimeType,webViewLink,modifiedTime,createdTime,
        size,owners,parents)``. Filters to legal file types (LEGAL_MIME_TYPES
        + LEGAL_EXTENSIONS). Caps at 500 files per sync.

        Returns count of upserted records, or 0 on failure.
        """
        token = await self._get_token(db, tenant_id, "google", user_id)
        if not token:
            return 0

        # Build Drive API query — union of MIME-type and extension predicates
        mime_clause = " or ".join(f"mimeType='{m}'" for m in LEGAL_MIME_TYPES)
        ext_clause = " or ".join(
            f"name contains '.{ext.strip('.')}'" for ext in LEGAL_EXTENSIONS
        )
        q = f"(({mime_clause}) or ({ext_clause})) and trashed=false"

        count = 0
        page_token: str | None = None
        async with httpx.AsyncClient() as client:
            try:
                while count < MAX_FILES:
                    params = {
                        "q": q,
                        "pageSize": 100,
                        "fields": (
                            "nextPageToken,files(id,name,mimeType,webViewLink,"
                            "modifiedTime,createdTime,size,owners,parents)"
                        ),
                        "orderBy": "modifiedTime desc",
                    }
                    if page_token:
                        params["pageToken"] = page_token

                    resp = await client.get(
                        f"{GOOGLE_DRIVE_BASE}/files",
                        headers={"Authorization": f"Bearer {token}"},
                        params=params,
                    )
                    if resp.status_code != 200:
                        logger.warning(
                            "Google Drive listing failed: status=%d body=%.200s",
                            resp.status_code,
                            resp.text,
                        )
                        break

                    body = resp.json()
                    files = body.get("files", [])
                    for item in files:
                        if count >= MAX_FILES:
                            break
                        owner_email = None
                        owners = item.get("owners")
                        if owners:
                            owner_email = owners[0].get("emailAddress")

                        snippet = _make_snippet(
                            item.get("name", ""),
                            item.get("mimeType"),
                        )

                        await self._upsert(
                            db,
                            tenant_id,
                            provider="google",
                            object_type="file",
                            object_id=item["id"],
                            title=item.get("name"),
                            parent_id=(
                                item["parents"][0] if item.get("parents") else None
                            ),
                            owner_email=owner_email,
                            modified_time=_parse_dt(item.get("modifiedTime")),
                            created_time=_parse_dt(item.get("createdTime")),
                            mime_type=item.get("mimeType"),
                            snippet=snippet,
                            size_bytes=_int_or_none(item.get("size")),
                            web_url=item.get("webViewLink"),
                        )
                        count += 1

                    page_token = body.get("nextPageToken")
                    if not page_token:
                        break

                await db.commit()
            except Exception as exc:
                await db.rollback()
                logger.warning("Google Drive sync error: %s", exc)
                return 0

        logger.info("Google Drive synced %d files for tenant %s", count, tenant_id)
        return count

    async def sync_gmail_metadata(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str | None = None,
    ) -> int:
        """Sync email metadata from Gmail.

        Fetches messages from the last 7 days via ``messages.list`` with
        ``q=after:YYYY/MM/DD``. For each message retrieves metadata headers
        (From, To, Cc, Subject, Date) using ``format=metadata`` and uses
        Gmail's built-in ``snippet``. Caps at 200 emails per sync.
        """
        token = await self._get_token(db, tenant_id, "google", user_id)
        if not token:
            return 0

        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y/%m/%d")
        count = 0
        page_token: str | None = None

        async with httpx.AsyncClient() as client:
            try:
                # Page through the message list (Gmail returns up to 500 ids/page)
                messages: list[dict] = []
                while len(messages) < MAX_EMAILS:
                    list_params: dict = {
                        "q": f"after:{since}",
                        "maxResults": min(500, MAX_EMAILS - len(messages)),
                    }
                    if page_token:
                        list_params["pageToken"] = page_token

                    list_resp = await client.get(
                        f"{GMAIL_BASE}/users/me/messages",
                        headers={"Authorization": f"Bearer {token}"},
                        params=list_params,
                    )
                    if list_resp.status_code != 200:
                        logger.warning(
                            "Gmail listing failed: status=%d body=%.200s",
                            list_resp.status_code,
                            list_resp.text,
                        )
                        if not messages:
                            return 0
                        break

                    list_body = list_resp.json()
                    messages.extend(list_body.get("messages", []))
                    page_token = list_body.get("nextPageToken")
                    if not page_token:
                        break

                for msg in messages[:MAX_EMAILS]:
                    msg_id = msg["id"]

                    meta_resp = await client.get(
                        f"{GMAIL_BASE}/users/me/messages/{msg_id}",
                        headers={"Authorization": f"Bearer {token}"},
                        params={
                            "format": "metadata",
                            "metadataHeaders": "From,To,Cc,Subject,Date",
                        },
                    )
                    if meta_resp.status_code != 200:
                        continue

                    payload = meta_resp.json()
                    headers = {
                        h["name"].lower(): h["value"]
                        for h in payload.get("payload", {}).get("headers", [])
                    }

                    subject = headers.get("subject", "")
                    from_addr = headers.get("from", "") or ""
                    to_addr = headers.get("to", "") or ""
                    cc_addr = headers.get("cc", "") or ""
                    date_str = headers.get("date", "")

                    participants: dict[str, str] = {}
                    if from_addr:
                        participants["from"] = from_addr
                    if to_addr:
                        participants["to"] = to_addr
                    if cc_addr:
                        participants["cc"] = cc_addr

                    snippet = (payload.get("snippet") or "")[:500]

                    await self._upsert(
                        db,
                        tenant_id,
                        provider="google",
                        object_type="email",
                        object_id=msg_id,
                        title=subject,
                        owner_email=from_addr,
                        participants=participants or None,
                        modified_time=_parse_dt(date_str),
                        created_time=_parse_dt(date_str),
                        mime_type=EMAIL_MIME_TYPE,
                        snippet=snippet,
                        sync_cursor=str(payload.get("historyId", "")),
                    )
                    count += 1

                await db.commit()
            except Exception as exc:
                await db.rollback()
                logger.warning("Gmail sync error: %s", exc)
                return 0

        logger.info("Gmail synced %d emails for tenant %s", count, tenant_id)
        return count

    async def sync_onedrive(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str | None = None,
    ) -> int:
        """Sync file metadata from OneDrive via MS Graph.

        Lists ``/me/drive/root/children`` with ``$select`` limiting fields to
        ``id,name,file,size,lastModifiedDateTime,createdDateTime,webUrl,
        parentReference,createdBy``. Filters to legal file types.
        Caps at 500 files per sync.
        """
        token = await self._get_token(db, tenant_id, "microsoft", user_id)
        if not token:
            return 0

        return await self._sync_graph_files(
            db,
            tenant_id,
            token,
            f"{GRAPH_BASE}/me/drive/root/children",
            "file",
        )

    async def sync_sharepoint(
        self,
        db: AsyncSession,
        tenant_id: str,
        site_id: str | None = None,
    ) -> int:
        """Sync file metadata from SharePoint document libraries.

        Resolves the root site via ``/sites/root``, enumerates its drives
        (document libraries), and lists children from each. Caps at 500
        files overall.
        """
        token = await self._get_token(db, tenant_id, "microsoft")
        if not token:
            return 0

        count = 0
        async with httpx.AsyncClient() as client:
            try:
                if not site_id:
                    sites_resp = await client.get(
                        f"{GRAPH_BASE}/sites/root",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    if sites_resp.status_code == 200:
                        site_id = sites_resp.json().get("id")

                if not site_id:
                    return 0

                drives_resp = await client.get(
                    f"{GRAPH_BASE}/sites/{site_id}/drives",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if drives_resp.status_code != 200:
                    logger.warning(
                        "SharePoint drives listing failed: status=%d",
                        drives_resp.status_code,
                    )
                    return 0

                for drive in drives_resp.json().get("value", []):
                    if count >= MAX_FILES:
                        break

                    drive_id = drive["id"]
                    drive_name = drive.get("name", "Documents")
                    children_url = f"{GRAPH_BASE}/drives/{drive_id}/root/children"

                    item_count = await self._sync_graph_files(
                        db,
                        tenant_id,
                        token,
                        children_url,
                        "file",
                        drive_name=drive_name,
                    )
                    count += item_count
                    # _sync_graph_files commits each page internally; no extra
                    # commit needed here.
            except Exception as exc:
                await db.rollback()
                logger.warning("SharePoint sync error: %s", exc)
                return 0

        final = min(count, MAX_FILES)
        logger.info("SharePoint synced %d files for tenant %s", final, tenant_id)
        return final

    async def sync_outlook_mail(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str | None = None,
    ) -> int:
        """Sync email metadata from Outlook / Microsoft Graph.

        Fetches ``/me/messages`` ordered by ``receivedDateTime desc``
        with ``$select=id,subject,bodyPreview,from,toRecipients,
        ccRecipients,receivedDateTime,hasAttachments,conversationId``.
        Snippet uses the ``bodyPreview`` field. Caps at 200 emails.
        """
        token = await self._get_token(db, tenant_id, "microsoft", user_id)
        if not token:
            return 0

        count = 0
        next_url: str | None = f"{GRAPH_BASE}/me/messages"
        first_params: dict | None = {
            "$select": (
                "id,subject,bodyPreview,from,toRecipients,"
                "ccRecipients,receivedDateTime,hasAttachments,conversationId"
            ),
            "$top": 100,
            "$orderby": "receivedDateTime desc",
        }
        async with httpx.AsyncClient() as client:
            try:
                # Page through messages (following @odata.nextLink) up to MAX_EMAILS
                messages: list[dict] = []
                while next_url and len(messages) < MAX_EMAILS:
                    resp = await client.get(
                        next_url,
                        headers={"Authorization": f"Bearer {token}"},
                        params=first_params,
                    )
                    first_params = None
                    if resp.status_code != 200:
                        logger.warning(
                            "Outlook mail listing failed: status=%d body=%.200s",
                            resp.status_code,
                            resp.text,
                        )
                        if not messages:
                            return 0
                        break

                    body = resp.json()
                    messages.extend(body.get("value", []))
                    next_url = body.get("@odata.nextLink")

                for msg in messages[:MAX_EMAILS]:
                    from_field = msg.get("from", {}) or {}
                    from_email = from_field.get("emailAddress", {}).get("address", "")

                    to_recipients = msg.get("toRecipients", []) or []
                    to_addrs = [
                        r.get("emailAddress", {}).get("address", "")
                        for r in to_recipients
                        if r.get("emailAddress")
                    ]

                    cc_recipients = msg.get("ccRecipients", []) or []
                    cc_addrs = [
                        r.get("emailAddress", {}).get("address", "")
                        for r in cc_recipients
                        if r.get("emailAddress")
                    ]

                    participants: dict[str, str] = {}
                    if from_email:
                        participants["from"] = from_email
                    if to_addrs:
                        participants["to"] = ",".join(to_addrs)
                    if cc_addrs:
                        participants["cc"] = ",".join(cc_addrs)

                    snippet = (msg.get("bodyPreview") or "")[:500]

                    await self._upsert(
                        db,
                        tenant_id,
                        provider="microsoft",
                        object_type="email",
                        object_id=msg["id"],
                        title=msg.get("subject"),
                        owner_email=from_email,
                        participants=participants or None,
                        modified_time=_parse_dt(msg.get("receivedDateTime")),
                        created_time=_parse_dt(msg.get("receivedDateTime")),
                        mime_type=EMAIL_MIME_TYPE,
                        snippet=snippet,
                        sync_cursor=msg.get("conversationId"),
                    )
                    count += 1

                await db.commit()
            except Exception as exc:
                await db.rollback()
                logger.warning("Outlook mail sync error: %s", exc)
                return 0

        logger.info("Outlook mail synced %d emails for tenant %s", count, tenant_id)
        return count

    # ── Internal helpers ────────────────────────────────────────────────────

    async def _get_token(
        self,
        db: AsyncSession,
        tenant_id: str,
        provider: str,
        user_id: str | None = None,
    ) -> str | None:
        """Resolve an access token.

        Tries ``get_fresh_user_token`` first (user-scoped OAuth), falls back
        to ``get_fresh_token`` (tenant-scoped / service account).
        Returns ``None`` when no token is available — caller should skip.
        """
        if user_id:
            token = await get_fresh_user_token(db, tenant_id, user_id, provider)
            if token:
                return token
        return await get_fresh_token(db, tenant_id, provider)

    async def _upsert(
        self,
        db: AsyncSession,
        tenant_id: str,
        **data,
    ) -> None:
        """Atomic upsert into ``cloud_metadata_index``.

        Uses PostgreSQL ``ON CONFLICT ... DO UPDATE`` on the unique constraint
        ``(tenant_id, provider, object_type, object_id)``.
        Identity columns and ``created_at`` are excluded from the update set;
        ``last_synced`` is always refreshed to the current time.
        """
        identity_keys = {"tenant_id", "provider", "object_type", "object_id"}

        data["tenant_id"] = uuid.UUID(str(tenant_id))

        # Always stamp last_synced so repeated syncs refresh the timestamp.
        data.setdefault("last_synced", datetime.now(timezone.utc))

        stmt = pg_insert(CloudMetadata).values(**data)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_cloud_metadata_tenant_provider_object",
            set_={
                k: stmt.excluded[k]
                for k in data
                if k not in identity_keys | {"created_at"}
            },
        )
        await db.execute(stmt)

    async def _sync_graph_files(
        self,
        db: AsyncSession,
        tenant_id: str,
        token: str,
        children_url: str,
        object_type: str,
        drive_name: str | None = None,
    ) -> int:
        """List files from a MS Graph ``children`` endpoint and upsert metadata.

        Shared by ``sync_onedrive`` and ``sync_sharepoint``.
        Filters to ``LEGAL_EXTENSIONS`` and ``LEGAL_MIME_TYPES``.
        """
        count = 0
        next_url: str | None = children_url
        first_params: dict | None = {
            "$top": 100,
            "$select": (
                "id,name,file,size,lastModifiedDateTime,"
                "createdDateTime,webUrl,parentReference,createdBy"
            ),
        }
        async with httpx.AsyncClient() as client:
            try:
                while next_url and count < MAX_FILES:
                    resp = await client.get(
                        next_url,
                        headers={"Authorization": f"Bearer {token}"},
                        params=first_params,
                    )
                    # Subsequent pages use the @odata.nextLink (params baked in)
                    first_params = None
                    if resp.status_code != 200:
                        logger.warning(
                            "Graph files listing failed (%s): status=%d",
                            next_url,
                            resp.status_code,
                        )
                        break

                    body = resp.json()
                    items = body.get("value", [])
                    next_url = body.get("@odata.nextLink")
                    for item in items:
                        if count >= MAX_FILES:
                            break

                        file_info = item.get("file")
                        if not file_info:
                            continue

                        name = item.get("name", "")
                        ext = Path(name).suffix.lower()
                        mime = file_info.get("mimeType", "")

                        if ext not in LEGAL_EXTENSIONS and mime not in LEGAL_MIME_TYPES:
                            continue

                        owner_email = None
                        created_by = item.get("createdBy", {})
                        if created_by:
                            user_info = created_by.get("user", {})
                            if user_info:
                                owner_email = user_info.get("email")

                        parent_ref = item.get("parentReference", {})
                        parent_id = parent_ref.get("id")

                        # Build a logical path from parent path + drive name
                        raw_path = (parent_ref.get("path") or "").replace(
                            "/drive/root:", "/"
                        )
                        if drive_name:
                            path_segments = f"/{drive_name}{raw_path}/{name}"
                        else:
                            path_segments = f"{raw_path}/{name}"

                        snippet = _make_snippet(name, mime)

                        await self._upsert(
                            db,
                            tenant_id,
                            provider="microsoft",
                            object_type=object_type,
                            object_id=item["id"],
                            title=name,
                            parent_id=parent_id,
                            path=path_segments,
                            owner_email=owner_email,
                            modified_time=_parse_dt(item.get("lastModifiedDateTime")),
                            created_time=_parse_dt(item.get("createdDateTime")),
                            mime_type=mime,
                            snippet=snippet,
                            size_bytes=item.get("size"),
                            web_url=item.get("webUrl"),
                        )
                        count += 1

                await db.commit()
            except Exception as exc:
                await db.rollback()
                logger.warning("Graph files sync error (%s): %s", children_url, exc)
                return 0

        return count


# ── Standalone helpers ──────────────────────────────────────────────────────


def _parse_dt(dt_str: str | None) -> datetime | None:
    """Parse an ISO-8601 string into a timezone-aware datetime."""
    if not dt_str:
        return None
    try:
        dt = dateutil_parser.parse(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _make_snippet(title: str, mime_type: str | None) -> str:
    """Build a human-readable snippet (max 500 chars).

    Combines the file/email title with a short MIME-type label.
    """
    if not title:
        return ""
    if mime_type:
        label = mime_type.split("/")[-1].replace("-", " ")
        result = f"{title} — {label}"
    else:
        result = title
    return result[:500]


def _int_or_none(value) -> int | None:
    """Safely coerce a value to int, returning None on failure."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


# ── Module-level singleton ──────────────────────────────────────────────────

cloud_sync = CloudSyncService()
