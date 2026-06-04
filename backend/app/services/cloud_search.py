"""Live RAG search across Google Drive, Gmail, OneDrive, SharePoint, and Outlook.

Dispatches RetrievalPlan dicts to provider APIs live, returns ranked CloudHit
objects. Never stores full document content — searches on-demand and returns
snippets + metadata.
"""

import base64
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import set_tenant_context
from app.models.cloud_metadata import CloudMetadata
from app.services.token_vault import get_fresh_token, get_fresh_user_token

# Map index (provider, object_type) → CloudHit.source used by fetch_content.
_INDEX_SOURCE_MAP = {
    ("google", "file"): "drive",
    ("google", "email"): "gmail",
    ("microsoft", "file"): "onedrive",
    ("microsoft", "email"): "outlook",
}

settings = get_settings()
logger = logging.getLogger(__name__)

GOOGLE_DRIVE_BASE = "https://www.googleapis.com/drive/v3"
GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"


# ── Data model ────────────────────────────────────────────────────────────


@dataclass
class CloudHit:
    """A single search result from cloud search."""

    provider: str  # "google" | "microsoft"
    source: str  # "drive" | "gmail" | "onedrive" | "sharepoint" | "outlook"
    object_id: str  # provider-native ID
    title: str  # file name, email subject
    snippet: str  # body preview or first N chars
    url: str  # web URL to the item
    modified_time: str  # ISO datetime
    mime_type: str  # file MIME or "message/rfc822" for emails
    participants: list[str] = field(default_factory=list)  # sender + recipients
    relevance_score: float = 0.0  # provider ranking normalized 0-1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Service ───────────────────────────────────────────────────────────────


class CloudSearchService:
    """Unified search across Google and Microsoft cloud services.

    Dispatches a RetrievalPlan to connected providers and returns merged,
    relevance-ranked CloudHit results.
    """

    def __init__(self) -> None:
        pass

    # ── Public API ──────────────────────────────────────────────────────

    async def search(
        self,
        db: AsyncSession,
        plan: dict,
        tenant_id: str,
        user_id: str | None = None,
    ) -> list[CloudHit]:
        """Execute search plan across all connected providers.

        Returns merged, de-duplicated, relevance-ranked hits. Each provider
        is called independently; failures are logged and swallowed per source.
        """
        max_hits = plan.get("max_hits", settings.CLOUD_SEARCH_MAX_HITS)
        keywords = plan.get("keywords", [])
        date_after = plan.get("date_after", "")
        sources = plan.get("sources", None)

        results: list[CloudHit] = []
        tasks = []

        if sources is None or "google" in sources:
            if _source_enabled(sources, "drive"):
                tasks.append(
                    self._search_google_drive(
                        db, keywords, date_after, max_hits, tenant_id, user_id
                    )
                )
            if _source_enabled(sources, "gmail"):
                tasks.append(
                    self._search_gmail(
                        db, keywords, date_after, max_hits, tenant_id, user_id
                    )
                )

        if sources is None or "microsoft" in sources:
            if _source_enabled(sources, "onedrive") or _source_enabled(
                sources, "sharepoint"
            ):
                tasks.append(
                    self._search_graph(
                        db, keywords, date_after, max_hits, tenant_id, user_id
                    )
                )
            elif sources is None:
                # No microsoft sub-source filter — run full Graph search
                tasks.append(
                    self._search_graph(
                        db, keywords, date_after, max_hits, tenant_id, user_id
                    )
                )

        # Always include the locally-synced metadata index. It is a reliable
        # fallback when live tokens are limited and gives the sync subsystem a
        # consumer.
        tasks.append(self.search_index(db, plan, tenant_id))

        for batch in tasks:
            try:
                hits = await batch
                results.extend(hits)
            except Exception:
                logger.exception("Cloud search task failed unexpectedly")

        # Deduplicate by (provider, object_id), keep highest score
        seen: set[tuple[str, str]] = set()
        deduped: list[CloudHit] = []
        for h in sorted(results, key=lambda x: x.relevance_score, reverse=True):
            key = (h.provider, h.object_id)
            if key not in seen:
                seen.add(key)
                deduped.append(h)

        # Re-sort by relevance
        deduped.sort(key=lambda x: x.relevance_score, reverse=True)
        return deduped[:max_hits]

    async def search_index(
        self,
        db: AsyncSession,
        plan: dict,
        tenant_id: str,
    ) -> list[CloudHit]:
        """Search the locally-synced ``cloud_metadata_index`` for matching items.

        Filters by the plan's sources/keywords/date and returns CloudHits ranked
        by keyword-match count and recency. Full content is still fetched live by
        ``fetch_content`` when needed — only metadata is read here.
        """
        try:
            return await self._search_index_impl(db, plan, tenant_id)
        except Exception:
            logger.exception("search_index failed for tenant %s", tenant_id)
            return []

    async def _search_index_impl(
        self,
        db: AsyncSession,
        plan: dict,
        tenant_id: str,
    ) -> list[CloudHit]:
        keywords = [k for k in plan.get("keywords", []) if k]
        date_after = plan.get("date_after") or ""
        sources = plan.get("sources", None)
        max_hits = plan.get("max_hits", settings.CLOUD_SEARCH_MAX_HITS)

        # Restrict to the (provider, object_type) pairs the plan asked for.
        allowed: set[tuple[str, str]] = set()
        for (provider, object_type), source in _INDEX_SOURCE_MAP.items():
            if sources is None or source in sources:
                allowed.add((provider, object_type))
        if not allowed:
            return []

        await set_tenant_context(db, tenant_id)

        stmt = select(CloudMetadata).where(CloudMetadata.tenant_id == tenant_id)
        stmt = stmt.where(
            or_(
                *[
                    (CloudMetadata.provider == p) & (CloudMetadata.object_type == t)
                    for (p, t) in allowed
                ]
            )
        )
        if keywords:
            kw_clauses = []
            for kw in keywords:
                like = f"%{_escape_ilike(kw)}%"
                kw_clauses.append(CloudMetadata.title.ilike(like))
                kw_clauses.append(CloudMetadata.snippet.ilike(like))
            stmt = stmt.where(or_(*kw_clauses))
        if date_after:
            parsed = _parse_index_date(date_after)
            if parsed:
                stmt = stmt.where(CloudMetadata.modified_time >= parsed)

        stmt = stmt.order_by(CloudMetadata.modified_time.desc().nullslast()).limit(
            max_hits * 3
        )

        rows = (await db.execute(stmt)).scalars().all()

        hits: list[CloudHit] = []
        for row in rows:
            source = _INDEX_SOURCE_MAP.get((row.provider, row.object_type), "drive")
            # Score: keyword-match density (title weighted) lightly biased recent.
            title_l = (row.title or "").lower()
            snippet_l = (row.snippet or "").lower()
            matches = sum(
                (2 if kw.lower() in title_l else 0)
                + (1 if kw.lower() in snippet_l else 0)
                for kw in keywords
            )
            score = 0.5 + min(matches, 10) * 0.05  # 0.5–1.0 band, below live hits
            participants = (
                list(row.participants.values())
                if isinstance(row.participants, dict)
                else []
            )
            hits.append(
                CloudHit(
                    provider=row.provider,
                    source=source,
                    object_id=row.object_id,
                    title=row.title or "",
                    snippet=row.snippet or "",
                    url=row.web_url or "",
                    modified_time=row.modified_time.isoformat()
                    if row.modified_time
                    else "",
                    mime_type=row.mime_type or "",
                    participants=[p for p in participants if p],
                    relevance_score=min(score, 0.99),
                )
            )

        return hits

    async def fetch_content(
        self,
        db: AsyncSession,
        hit: CloudHit,
        tenant_id: str,
        max_chars: int = 2000,
    ) -> str | None:
        """Fetch full text content for a single hit.

        Returns the content truncated to *max_chars*, or ``None`` if the
        provider is unreachable or the content cannot be extracted.
        """
        if not settings.CLOUD_SEARCH_ENABLED:
            return None

        try:
            if hit.provider == "google":
                if hit.source == "drive":
                    return await self._fetch_google_drive_content(
                        db, hit, tenant_id, max_chars
                    )
                if hit.source == "gmail":
                    return await self._fetch_gmail_content(
                        db, hit, tenant_id, max_chars
                    )
            elif hit.provider == "microsoft":
                if hit.source in ("onedrive", "sharepoint"):
                    return await self._fetch_onedrive_content(
                        db, hit, tenant_id, max_chars
                    )
                if hit.source == "outlook":
                    return await self._fetch_outlook_content(
                        db, hit, tenant_id, max_chars
                    )
        except Exception:
            logger.exception(
                "fetch_content failed for %s/%s", hit.provider, hit.object_id
            )
        return None

    async def fetch_contents(
        self,
        db: AsyncSession,
        hits: list[CloudHit],
        tenant_id: str,
        max_chars: int = 2000,
    ) -> list[dict]:
        """Fetch content for multiple hits.

        Returns ``[{hit: CloudHit, content: str | None}]``.  Each hit is
        fetched independently; a failure for one does not affect the others.
        """
        results: list[dict] = []
        for hit in hits:
            content = await self.fetch_content(db, hit, tenant_id, max_chars)
            results.append({"hit": hit, "content": content})
        return results

    # ── Google Drive ────────────────────────────────────────────────────

    async def _search_google_drive(
        self,
        db: AsyncSession,
        keywords: list[str],
        date_after: str,
        max_hits: int,
        tenant_id: str,
        user_id: str | None,
    ) -> list[CloudHit]:
        token = await self._get_google_token(db, tenant_id, user_id)
        if not token:
            return []

        clauses: list[str] = []
        for kw in keywords:
            sanitised = kw.replace("'", "\\'")
            clauses.append(f"fullText contains '{sanitised}'")
        if date_after:
            clauses.append(f"modifiedTime > '{date_after}'")
        clauses.append("trashed = false")
        query = " and ".join(clauses)

        params: dict[str, Any] = {
            "q": query,
            "fields": "files(id,name,mimeType,webViewLink,modifiedTime,owners)",
            "pageSize": min(max_hits, 100),
            "orderBy": "modifiedTime desc",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.get(
                    f"{GOOGLE_DRIVE_BASE}/files",
                    headers={"Authorization": f"Bearer {token}"},
                    params=params,
                )
                if resp.status_code != 200:
                    logger.warning(
                        "Google Drive search failed: %s %s",
                        resp.status_code,
                        resp.text[:300],
                    )
                    return []

                data = resp.json()
            except httpx.RequestError as exc:
                logger.warning("Google Drive search request error: %s", exc)
                return []

        hits: list[CloudHit] = []
        for f in data.get("files", []):
            snippet = await self._get_drive_snippet(token, f["id"])
            owners = f.get("owners", [])
            participants = [
                o.get("emailAddress", "") for o in owners if o.get("emailAddress")
            ]
            hits.append(
                CloudHit(
                    provider="google",
                    source="drive",
                    object_id=f["id"],
                    title=f.get("name", ""),
                    snippet=snippet,
                    url=f.get("webViewLink", ""),
                    modified_time=f.get("modifiedTime", ""),
                    mime_type=f.get("mimeType", ""),
                    participants=participants,
                    relevance_score=1.0,  # Drive results are already ordered
                )
            )

        return hits

    async def _get_drive_snippet(self, token: str, file_id: str) -> str:
        """Return first 500 characters of the file description as snippet."""
        async with httpx.AsyncClient(timeout=15) as client:
            try:
                resp = await client.get(
                    f"{GOOGLE_DRIVE_BASE}/files/{file_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"fields": "description"},
                )
                if resp.status_code == 200:
                    desc = (resp.json().get("description") or "").strip()
                    return desc[:500]
            except httpx.RequestError:
                pass
        return ""

    # ── Gmail ────────────────────────────────────────────────────────────

    async def _search_gmail(
        self,
        db: AsyncSession,
        keywords: list[str],
        date_after: str,
        max_hits: int,
        tenant_id: str,
        user_id: str | None,
    ) -> list[CloudHit]:
        token = await self._get_google_token(db, tenant_id, user_id)
        if not token:
            return []

        query_parts: list[str] = []
        for kw in keywords:
            sanitised = kw.replace('"', '\\"')
            if "@" in sanitised:
                query_parts.append(f"from:{sanitised} OR to:{sanitised}")
            else:
                query_parts.append(f"subject:{sanitised} OR {sanitised}")
        if date_after:
            query_parts.append(f"after:{date_after.replace('-', '/')[:10]}")
        query = " ".join(query_parts)

        params: dict[str, Any] = {
            "q": query,
            "maxResults": min(max_hits, 50),
        }

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                list_resp = await client.get(
                    f"{GMAIL_BASE}/users/me/messages",
                    headers={"Authorization": f"Bearer {token}"},
                    params=params,
                )
                if list_resp.status_code != 200:
                    logger.warning(
                        "Gmail search failed: %s %s",
                        list_resp.status_code,
                        list_resp.text[:300],
                    )
                    return []
                msg_ids = [m["id"] for m in list_resp.json().get("messages", [])]
            except httpx.RequestError as exc:
                logger.warning("Gmail search request error: %s", exc)
                return []

        # Reverse-chronological scoring: first in list gets highest score
        total = len(msg_ids)
        hits: list[CloudHit] = []
        for idx, msg_id in enumerate(msg_ids):
            try:
                detail = await self._get_gmail_metadata(token, msg_id)
                if detail is None:
                    continue

                headers = detail.get("headers", {})
                snippet = detail.get("snippet", "")[:500]
                participants_raw = {
                    h.get("value", "")
                    for h in detail.get("header_list", [])
                    if h.get("name", "").lower() in ("from", "to", "cc")
                }
                participants = [p for p in participants_raw if p]

                hits.append(
                    CloudHit(
                        provider="google",
                        source="gmail",
                        object_id=msg_id,
                        title=headers.get("Subject", "(no subject)"),
                        snippet=snippet,
                        url=f"https://mail.google.com/#all/{msg_id}",
                        modified_time=headers.get("Date", ""),
                        mime_type="message/rfc822",
                        participants=participants,
                        relevance_score=(1.0 - (idx / max(total, 1))) * 0.9,
                    )
                )
            except Exception:
                logger.debug(
                    "Failed to fetch Gmail metadata for %s", msg_id, exc_info=True
                )

        return hits

    async def _get_gmail_metadata(self, token: str, msg_id: str) -> dict | None:
        """Fetch message metadata and headers."""
        async with httpx.AsyncClient(timeout=15) as client:
            try:
                resp = await client.get(
                    f"{GMAIL_BASE}/users/me/messages/{msg_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    params={
                        "format": "metadata",
                        "metadataHeaders": "From,To,Cc,Subject,Date",
                    },
                )
                if resp.status_code != 200:
                    return None

                msg = resp.json()
                payload = msg.get("payload", {})
                header_list = payload.get("headers", [])
                headers: dict[str, str] = {}
                for h in header_list:
                    headers[h["name"]] = h["value"]

                return {
                    "headers": headers,
                    "header_list": header_list,
                    "snippet": msg.get("snippet", ""),
                    "label_ids": msg.get("labelIds", []),
                }
            except httpx.RequestError:
                return None

    # ── Microsoft Graph Search ───────────────────────────────────────────

    async def _search_graph(
        self,
        db: AsyncSession,
        keywords: list[str],
        date_after: str,
        max_hits: int,
        tenant_id: str,
        user_id: str | None,
    ) -> list[CloudHit]:
        token = await self._get_microsoft_token(db, tenant_id, user_id)
        if not token:
            return []

        query_string = " ".join(keywords) if keywords else "*"

        body: dict[str, Any] = {
            "requests": [
                {
                    "entityTypes": ["driveItem", "listItem", "message"],
                    "query": {"queryString": query_string},
                    "from": 0,
                    "size": min(max_hits, 200),
                    "fields": [
                        "title",
                        "subject",
                        "bodyPreview",
                        "webUrl",
                        "lastModifiedDateTime",
                        "name",
                        "from",
                        "toRecipients",
                        "createdDateTime",
                    ],
                }
            ]
        }

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.post(
                    f"{GRAPH_BASE}/search/query",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                if resp.status_code != 200:
                    logger.warning(
                        "Graph search failed: %s %s",
                        resp.status_code,
                        resp.text[:300],
                    )
                    return []

                data = resp.json()
            except httpx.RequestError as exc:
                logger.warning("Graph search request error: %s", exc)
                return []

        hits: list[CloudHit] = []
        search_sets = data.get("value", [{}])[0].get("hitsContainers", [])
        for container in search_sets:
            total_results = container.get("total", 0)
            for raw_hit in container.get("hits", []):
                try:
                    hit = self._parse_graph_hit(raw_hit, total_results)
                    if hit:
                        hits.append(hit)
                except Exception:
                    logger.debug("Failed to parse Graph hit", exc_info=True)

        return hits

    def _parse_graph_hit(self, raw: dict, total_results: int) -> CloudHit | None:
        """Convert a Microsoft Graph search hit to a ``CloudHit``."""
        resource = raw.get("resource", {})
        rank = raw.get("rank", 0)
        summary = raw.get("summary", "")
        hit_id = raw.get("hitId", "")

        entity_type = resource.get("@odata.type", "")
        if "driveItem" in entity_type:
            web_url = resource.get("webUrl") or resource.get("webDavUrl", "")
            name = resource.get("name", "")
            modified = resource.get("lastModifiedDateTime", "")
            mime = (
                resource.get("file", {}).get("mimeType", "")
                if isinstance(resource.get("file"), dict)
                else ""
            )
            parent_ref = resource.get("parentReference", {}) or {}
            # Determine OneDrive vs SharePoint by the drive type hint
            drive_type = parent_ref.get("driveType", "")
            source = "sharepoint" if drive_type == "documentLibrary" else "onedrive"

            return CloudHit(
                provider="microsoft",
                source=source,
                object_id=resource.get("id", hit_id),
                title=name,
                snippet=summary,
                url=web_url,
                modified_time=modified,
                mime_type=mime or "application/octet-stream",
                participants=[],
                relevance_score=1.0 - (rank / max(total_results, 1))
                if total_results
                else 1.0,
            )

        if "message" in entity_type:
            subject = resource.get("subject", "")
            body_preview = resource.get("bodyPreview", "")
            from_addr = ""
            if isinstance(resource.get("from"), dict):
                from_email = resource.get("from", {}).get("emailAddress", {})
                if isinstance(from_email, dict):
                    from_addr = from_email.get("address", "")

            to_addrs: list[str] = []
            for r in resource.get("toRecipients", []):
                if isinstance(r, dict):
                    addr = r.get("emailAddress", {}).get("address", "")
                    if addr:
                        to_addrs.append(addr)

            participants = [p for p in [from_addr] + to_addrs if p]
            received = resource.get("receivedDateTime", "")
            web_url = resource.get("webUrl", "")

            return CloudHit(
                provider="microsoft",
                source="outlook",
                object_id=resource.get("id", hit_id),
                title=subject or "(no subject)",
                snippet=summary or body_preview,
                url=web_url,
                modified_time=received,
                mime_type="message/rfc822",
                participants=participants,
                relevance_score=1.0 - (rank / max(total_results, 1))
                if total_results
                else 1.0,
            )

        return None

    # ── Content fetching ────────────────────────────────────────────────

    async def _fetch_google_drive_content(
        self,
        db: AsyncSession,
        hit: CloudHit,
        tenant_id: str,
        max_chars: int,
    ) -> str | None:
        token = await self._get_google_token(db, tenant_id, None)
        if not token:
            return hit.snippet or None

        async with httpx.AsyncClient(timeout=30) as client:
            # Try export for Google-native formats (Docs, Sheets, etc.)
            export_mime = self._drive_export_mime(hit.mime_type)
            if export_mime:
                url = f"{GOOGLE_DRIVE_BASE}/files/{hit.object_id}/export"
                params = {"mimeType": export_mime}
            else:
                url = f"{GOOGLE_DRIVE_BASE}/files/{hit.object_id}"
                params = {"alt": "media"}

            try:
                resp = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    params=params,
                    follow_redirects=True,
                )
                if resp.status_code == 200:
                    content = resp.text
                    return content[:max_chars]
            except httpx.RequestError:
                pass

        return hit.snippet or None

    async def _fetch_gmail_content(
        self,
        db: AsyncSession,
        hit: CloudHit,
        tenant_id: str,
        max_chars: int,
    ) -> str | None:
        token = await self._get_google_token(db, tenant_id, None)
        if not token:
            return hit.snippet or None

        async with httpx.AsyncClient(timeout=15) as client:
            try:
                resp = await client.get(
                    f"{GMAIL_BASE}/users/me/messages/{hit.object_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"format": "full"},
                )
                if resp.status_code != 200:
                    return hit.snippet or None

                msg = resp.json()
                payload = msg.get("payload", {})
                body_text = self._extract_gmail_body(payload)
                return body_text[:max_chars] if body_text else hit.snippet or None
            except httpx.RequestError:
                return hit.snippet or None

    async def _fetch_onedrive_content(
        self,
        db: AsyncSession,
        hit: CloudHit,
        tenant_id: str,
        max_chars: int,
    ) -> str | None:
        token = await self._get_microsoft_token(db, tenant_id, None)
        if not token:
            return hit.snippet or None

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.get(
                    f"{GRAPH_BASE}/me/drive/items/{hit.object_id}/content",
                    headers={"Authorization": f"Bearer {token}"},
                    follow_redirects=True,
                )
                if resp.status_code == 200:
                    content = resp.text
                    return content[:max_chars]
            except httpx.RequestError:
                pass

        return hit.snippet or None

    async def _fetch_outlook_content(
        self,
        db: AsyncSession,
        hit: CloudHit,
        tenant_id: str,
        max_chars: int,
    ) -> str | None:
        token = await self._get_microsoft_token(db, tenant_id, None)
        if not token:
            return hit.snippet or None

        async with httpx.AsyncClient(timeout=15) as client:
            try:
                resp = await client.get(
                    f"{GRAPH_BASE}/me/messages/{hit.object_id}/$value",
                    headers={"Authorization": f"Bearer {token}"},
                    follow_redirects=True,
                )
                if resp.status_code == 200:
                    content = resp.text
                    return content[:max_chars]
            except httpx.RequestError:
                pass

        return hit.snippet or None

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _extract_gmail_body(payload: dict) -> str | None:
        """Recursively extract plain-text body from a Gmail message payload."""
        # Check body data on this part
        body_data = payload.get("body", {}).get("data")
        if body_data:
            try:
                decoded = base64.urlsafe_b64decode(body_data).decode(
                    "utf-8", errors="replace"
                )
                if decoded.strip():
                    return decoded
            except Exception:
                pass

        # Recurse into parts
        for part in payload.get("parts", []):
            result = CloudSearchService._extract_gmail_body(part)
            if result:
                return result

        return None

    @staticmethod
    def _drive_export_mime(mime_type: str) -> str | None:
        """Return the export MIME for Google-native formats, or ``None`` for
        binary files that should use ``alt=media`` download."""
        export_map = {
            "application/vnd.google-apps.document": "text/plain",
            "application/vnd.google-apps.spreadsheet": "text/csv",
            "application/vnd.google-apps.presentation": "text/plain",
            "application/vnd.google-apps.drawing": "image/png",
            "application/vnd.google-apps.script": "application/vnd.google-apps.script+json",
        }
        return export_map.get(mime_type)

    @staticmethod
    async def _get_google_token(
        db: AsyncSession,
        tenant_id: str,
        user_id: str | None,
    ) -> str | None:
        """Retrieve a Google access token, preferring user-level if available."""
        if user_id:
            token = await get_fresh_user_token(db, tenant_id, user_id, "google")
            if token:
                return token
        return await get_fresh_token(db, tenant_id, "google")

    @staticmethod
    async def _get_microsoft_token(
        db: AsyncSession,
        tenant_id: str,
        user_id: str | None,
    ) -> str | None:
        """Retrieve a Microsoft access token, preferring user-level if available."""
        if user_id:
            token = await get_fresh_user_token(db, tenant_id, user_id, "microsoft")
            if token:
                return token
        return await get_fresh_token(db, tenant_id, "microsoft")


# ── Module-level helpers ──────────────────────────────────────────────────


def _escape_ilike(value: str) -> str:
    """Escape ``%`` and ``_`` wildcards before ILIKE to prevent unintended matches."""
    return value.replace("%", "\\%").replace("_", "\\_")


def _source_enabled(sources: list[str] | None, name: str) -> bool:
    """Check whether a specific sub-source is in the list of requested sources."""
    if sources is None:
        return True
    return name in sources


def _parse_index_date(value: str) -> datetime | None:
    """Parse a planner ``date_after`` (e.g. ``2026-01-01``) into a tz-aware datetime."""
    try:
        dt = datetime.fromisoformat(value[:19])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None
