"""Matter file store — routes document storage to customer's cloud (OneDrive/Google Drive).

Files are stored in the customer's own cloud storage, not ours:
  - MS365 customers → OneDrive: /LegalApp/Matters/{matter_slug}/{category}/{filename}
  - Google Workspace → Google Drive: LegalApp/Matters/{matter_slug}/ folder
  - Fallback → local disk (UPLOAD_DIR)
"""

import logging
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.token_vault import get_fresh_token

settings = get_settings()
logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GOOGLE_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"


class MatterFileStore:
    """Store matter files in the customer's connected cloud storage."""

    async def store_matter_file(
        self,
        db: AsyncSession,
        tenant_id: str,
        matter_slug: str,
        category: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> str:
        """Upload a file. Returns the storage path/URL."""
        logger.info(
            "Storing %s/%s/%s (%d bytes) for tenant %s",
            matter_slug,
            category,
            filename,
            len(content),
            tenant_id,
        )

        # Try Microsoft OneDrive first
        ms_path = await self._try_store_onedrive(
            db, tenant_id, matter_slug, category, filename, content, content_type
        )
        if ms_path:
            return ms_path

        # Try Google Drive
        gd_path = await self._try_store_google_drive(
            db, tenant_id, matter_slug, category, filename, content, content_type
        )
        if gd_path:
            return gd_path

        # Fallback: local disk
        return await self._store_local(
            tenant_id, matter_slug, category, filename, content
        )

    async def _try_store_onedrive(
        self,
        db: AsyncSession,
        tenant_id: str,
        matter_slug: str,
        category: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> str | None:
        """Try to store in customer's OneDrive. Returns path or None."""
        try:
            token = await get_fresh_token(db, tenant_id, "microsoft")
            if not token:
                return None

            # Build folder path: /LegalApp/Matters/{slug}/{category}
            parent_id = await _ensure_onedrive_path(
                token, ["LegalApp", "Matters", matter_slug, category]
            )

            upload_url = f"{GRAPH_BASE}/me/drive/items/{parent_id}:/{filename}:/content"
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.put(
                    upload_url,
                    content=content,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": content_type,
                    },
                )
                if resp.status_code in (200, 201):
                    data = resp.json()
                    web_url = data.get("webUrl") or data.get(
                        "@microsoft.graph.downloadUrl", ""
                    )
                    logger.info("Stored %s in OneDrive: %s", filename, web_url)
                    return web_url
                else:
                    logger.warning(
                        "OneDrive upload failed for %s: %s %s",
                        filename,
                        resp.status_code,
                        resp.text[:200],
                    )
                    return None
        except Exception as exc:
            logger.warning("OneDrive storage attempt failed: %s", exc)
            return None

    async def _try_store_google_drive(
        self,
        db: AsyncSession,
        tenant_id: str,
        matter_slug: str,
        category: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> str | None:
        """Try to store in customer's Google Drive. Returns path or None."""
        try:
            token = await get_fresh_token(db, tenant_id, "google")
            if not token:
                return None

            parent_id = await _ensure_gdrive_path(
                token, ["LegalApp", "Matters", matter_slug, category]
            )

            # Upload file
            metadata = {
                "name": filename,
                "parents": [parent_id],
            }
            boundary = "legalapp_upload_boundary"
            body = (
                f"--{boundary}\r\n"
                f"Content-Type: application/json\r\n\r\n"
                f"{__import__('json').dumps(metadata)}\r\n"
                f"--{boundary}\r\n"
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
            body += content
            body += f"\r\n--{boundary}--\r\n".encode("utf-8")

            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{GOOGLE_UPLOAD_BASE}/files?uploadType=multipart",
                    content=body,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": f"multipart/related; boundary={boundary}",
                    },
                )
                if resp.status_code in (200, 201):
                    data = resp.json()
                    file_id = data.get("id", "")
                    web_link = f"https://drive.google.com/file/d/{file_id}/view"
                    logger.info("Stored %s in Google Drive: %s", filename, web_link)
                    return web_link
                else:
                    logger.warning(
                        "Google Drive upload failed for %s: %s %s",
                        filename,
                        resp.status_code,
                        resp.text[:200],
                    )
                    return None
        except Exception as exc:
            logger.warning("Google Drive storage attempt failed: %s", exc)
            return None

    async def _store_local(
        self,
        tenant_id: str,
        matter_slug: str,
        category: str,
        filename: str,
        content: bytes,
    ) -> str:
        """Store file on local disk. Returns the relative storage path."""
        rel_path = f"matters/{matter_slug}/{category}/{filename}"
        full_path = Path(settings.UPLOAD_DIR) / tenant_id / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)
        logger.info("Stored %s locally at %s", filename, full_path)
        return str(full_path)


async def _ensure_onedrive_path(token: str, folders: list[str]) -> str:
    """Ensure a folder path exists in OneDrive, creating folders as needed.
    Returns the folder ID of the deepest folder.
    """
    parent_id = "root"
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {token}"}
        for folder_name in folders:
            # Search for existing folder
            search_url = (
                f"{GRAPH_BASE}/me/drive/items/{parent_id}/children"
                f"?$filter=name eq '{folder_name}' and folder ne null"
                f"&$select=id,name"
            )
            resp = await client.get(search_url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("value", [])
                if items:
                    parent_id = items[0]["id"]
                    continue

            # Create folder
            create_url = f"{GRAPH_BASE}/me/drive/items/{parent_id}/children"
            resp = await client.post(
                create_url,
                json={
                    "name": folder_name,
                    "folder": {},
                    "@microsoft.graph.conflictBehavior": "replace",
                },
                headers=headers,
            )
            if resp.status_code in (200, 201):
                parent_id = resp.json()["id"]
            else:
                raise RuntimeError(
                    f"Failed to create OneDrive folder '{folder_name}': "
                    f"{resp.status_code} {resp.text[:200]}"
                )
    return parent_id


async def _ensure_gdrive_path(token: str, folders: list[str]) -> str:
    """Ensure a folder path exists in Google Drive, creating folders as needed.
    Returns the folder ID of the deepest folder.
    """
    parent_id = "root"
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {token}"}
        for folder_name in folders:
            # Search for existing folder
            query = (
                f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' "
                f"and '{parent_id}' in parents and trashed=false"
            )
            resp = await client.get(
                "https://www.googleapis.com/drive/v3/files",
                params={"q": query, "fields": "files(id,name)"},
                headers=headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("files", [])
                if items:
                    parent_id = items[0]["id"]
                    continue

            # Create folder
            resp = await client.post(
                "https://www.googleapis.com/drive/v3/files",
                json={
                    "name": folder_name,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [parent_id],
                },
                headers=headers,
            )
            if resp.status_code in (200, 201):
                parent_id = resp.json()["id"]
            else:
                raise RuntimeError(
                    f"Failed to create Google Drive folder '{folder_name}': "
                    f"{resp.status_code} {resp.text[:200]}"
                )
    return parent_id
