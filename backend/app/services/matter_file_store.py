"""Matter file store — routes document storage to customer's cloud (OneDrive/Google Drive).

Files are stored in the customer's own cloud storage, not ours:
  - MS365 customers → OneDrive: /claritylegal-records/{matter_slug}/{category}/{filename}
  - Google Workspace → Google Drive: claritylegal-records/{matter_slug}/ folder
  - Fallback → local disk (UPLOAD_DIR)

When matter.cloud_folder is provided (pre-provisioned subfolder IDs), uploads skip
the multi-hop folder traversal and go directly to the pre-created folder.
"""

import json
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

DOCUMENT_CATEGORY_FOLDER_MAP = {
    "pleading": "pleadings",
    "pleadings": "pleadings",
    "correspondence": "correspondence",
    "billing": "billing",
    "contract": "documents",
    "evidence": "documents",
    "other": "documents",
    "general": "documents",
}


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
        matter_cloud_folder: dict | None = None,
        preferred_provider: str | None = None,
    ) -> str:
        """Upload a file. Returns the storage path/URL.

        When matter_cloud_folder is provided, uploads directly to the pre-provisioned
        subfolder ID instead of traversing the folder tree.
        preferred_provider controls which cloud is tried first ("onedrive" | "google_drive").
        """
        logger.info(
            "Storing %s/%s/%s (%d bytes) for tenant %s via preferred=%s",
            matter_slug,
            category,
            filename,
            len(content),
            tenant_id,
            preferred_provider,
        )

        # Resolve per-provider folder IDs from matter_cloud_folder if available
        canonical_folder = _document_folder_for_category(category)
        onedrive_folder_id = _extract_subfolder_id(
            matter_cloud_folder, "onedrive", canonical_folder
        )
        gdrive_folder_id = _extract_subfolder_id(
            matter_cloud_folder, "google_drive", canonical_folder
        )

        providers = _ordered_providers(preferred_provider)

        for provider in providers:
            if provider == "onedrive":
                path = await self._try_store_onedrive(
                    db,
                    tenant_id,
                    matter_slug,
                    canonical_folder,
                    filename,
                    content,
                    content_type,
                    folder_id=onedrive_folder_id,
                )
                if path:
                    return path
            elif provider == "google_drive":
                path = await self._try_store_google_drive(
                    db,
                    tenant_id,
                    matter_slug,
                    canonical_folder,
                    filename,
                    content,
                    content_type,
                    folder_id=gdrive_folder_id,
                )
                if path:
                    return path

        # Fallback: local disk
        return await self._store_local(
            tenant_id, matter_slug, canonical_folder, filename, content
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
        folder_id: str | None = None,
    ) -> str | None:
        """Try to store in customer's OneDrive. Returns URL or None."""
        try:
            token = await get_fresh_token(db, tenant_id, "microsoft")
            if not token:
                return None

            if folder_id:
                parent_id = folder_id
            else:
                parent_id = await _ensure_onedrive_path(
                    token, ["claritylegal-records", matter_slug, category]
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
        folder_id: str | None = None,
    ) -> str | None:
        """Try to store in customer's Google Drive. Returns URL or None."""
        try:
            token = await get_fresh_token(db, tenant_id, "google")
            if not token:
                return None

            if folder_id:
                parent_id = folder_id
            else:
                parent_id = await _ensure_gdrive_path(
                    token, ["claritylegal-records", matter_slug, category]
                )

            metadata = {"name": filename, "parents": [parent_id]}
            boundary = "legalapp_upload_boundary"
            body = (
                f"--{boundary}\r\n"
                f"Content-Type: application/json\r\n\r\n"
                f"{json.dumps(metadata)}\r\n"
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
        """Store file on local disk. Returns the absolute storage path."""
        rel_path = f"matters/{matter_slug}/{category}/{filename}"
        full_path = Path(settings.UPLOAD_DIR) / tenant_id / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)
        logger.info("Stored %s locally at %s", filename, full_path)
        return str(full_path)


def _extract_subfolder_id(
    cloud_folder: dict | None, provider: str, category: str
) -> str | None:
    """Return the pre-provisioned subfolder ID for a given provider + category, or None."""
    if not cloud_folder:
        return None
    provider_data = cloud_folder.get(provider)
    if not provider_data:
        return None
    subfolders = provider_data.get("subfolders")
    if not subfolders:
        return None
    for key in _folder_lookup_keys(category):
        if key in subfolders:
            return subfolders[key]
    return None


def _document_folder_for_category(category: str | None) -> str:
    """Map UI document categories to provisioned matter cloud folders."""
    normalized = (category or "general").strip().lower()
    return DOCUMENT_CATEGORY_FOLDER_MAP.get(normalized, normalized or "documents")


def _folder_lookup_keys(category: str) -> list[str]:
    """Return compatible subfolder keys across historical matter layouts."""
    keys = [category]
    if category == "documents":
        keys.append("uploads")
    elif category == "pleadings":
        keys.extend(["pleading", "documents", "uploads"])
    elif category != "uploads":
        keys.extend(["documents", "uploads"])
    return list(dict.fromkeys(keys))


def _ordered_providers(preferred: str | None) -> list[str]:
    """Return provider order list based on preference."""
    all_providers = ["onedrive", "google_drive"]
    if preferred in all_providers:
        return [preferred] + [p for p in all_providers if p != preferred]
    return all_providers


async def _ensure_onedrive_path(token: str, folders: list[str]) -> str:
    """Ensure a folder path exists in OneDrive, creating folders as needed.
    Returns the folder ID of the deepest folder.
    """
    parent_id = "root"
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {token}"}
        for folder_name in folders:
            children_url = (
                f"{GRAPH_BASE}/me/drive/root/children"
                if parent_id == "root"
                else f"{GRAPH_BASE}/me/drive/items/{parent_id}/children"
            )
            search_url = (
                f"{children_url}"
                f"?$filter=name eq '{folder_name}' and folder ne null"
                f"&$select=id,name"
            )
            resp = await client.get(search_url, headers=headers)
            if resp.status_code == 200:
                items = resp.json().get("value", [])
                if items:
                    parent_id = items[0]["id"]
                    continue

            resp = await client.post(
                children_url,
                json={
                    "name": folder_name,
                    "folder": {},
                    "@microsoft.graph.conflictBehavior": "rename",
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
                items = resp.json().get("files", [])
                if items:
                    parent_id = items[0]["id"]
                    continue

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
