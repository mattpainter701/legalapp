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
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.token_vault import get_fresh_token

settings = get_settings()
logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GOOGLE_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"


@dataclass(frozen=True)
class MatterFileStorageResult:
    """Outcome of storing a matter file."""

    storage_path: str
    storage_backend: str
    storage_error: str | None = None


@dataclass(frozen=True)
class _CloudStoreAttempt:
    path: str | None = None
    error: str | None = None


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
    ) -> MatterFileStorageResult:
        """Upload a file. Returns the storage path/URL plus backend metadata.

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
        onedrive_folder_id = _extract_subfolder_id(
            matter_cloud_folder, "onedrive", category
        )
        gdrive_folder_id = _extract_subfolder_id(
            matter_cloud_folder, "google_drive", category
        )

        providers = _ordered_providers(preferred_provider)

        cloud_errors: list[str] = []

        for provider in providers:
            if provider == "onedrive":
                attempt = await self._try_store_onedrive(
                    db,
                    tenant_id,
                    matter_slug,
                    category,
                    filename,
                    content,
                    content_type,
                    folder_id=onedrive_folder_id,
                )
                if attempt.path:
                    return MatterFileStorageResult(
                        storage_path=attempt.path, storage_backend="onedrive"
                    )
                if attempt.error:
                    cloud_errors.append(attempt.error)
            elif provider == "google_drive":
                attempt = await self._try_store_google_drive(
                    db,
                    tenant_id,
                    matter_slug,
                    category,
                    filename,
                    content,
                    content_type,
                    folder_id=gdrive_folder_id,
                )
                if attempt.path:
                    return MatterFileStorageResult(
                        storage_path=attempt.path, storage_backend="google_drive"
                    )
                if attempt.error:
                    cloud_errors.append(attempt.error)

        # Fallback: local disk, preserving the cloud failure reason for UI/admins.
        local_path = await self._store_local(
            tenant_id, matter_slug, category, filename, content
        )
        return MatterFileStorageResult(
            storage_path=local_path,
            storage_backend="local",
            storage_error="; ".join(cloud_errors)[:1000] if cloud_errors else None,
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
    ) -> _CloudStoreAttempt:
        """Try to store in customer's OneDrive. Returns URL or classified error."""
        try:
            token = await get_fresh_token(db, tenant_id, "microsoft")
            if not token:
                return _CloudStoreAttempt(error="onedrive:no_token")

            if folder_id:
                parent_id = folder_id
            else:
                parent_id = await _ensure_onedrive_path(
                    token, ["claritylegal-records", matter_slug, category]
                )

            async def _upload(target_parent_id: str) -> httpx.Response:
                upload_url = f"{GRAPH_BASE}/me/drive/items/{target_parent_id}:/{filename}:/content"
                async with httpx.AsyncClient(timeout=60) as client:
                    return await client.put(
                        upload_url,
                        content=content,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": content_type,
                        },
                    )

            resp = await _upload(parent_id)
            if resp.status_code == 404 and folder_id:
                logger.warning(
                    "OneDrive folder id %s was stale for %s; retrying by path",
                    folder_id,
                    filename,
                )
                parent_id = await _ensure_onedrive_path(
                    token, ["claritylegal-records", matter_slug, category]
                )
                resp = await _upload(parent_id)

            if resp.status_code in (200, 201):
                data = resp.json()
                web_url = data.get("webUrl") or data.get(
                    "@microsoft.graph.downloadUrl", ""
                )
                logger.info("Stored %s in OneDrive: %s", filename, web_url)
                return _CloudStoreAttempt(path=web_url)

            reason = _classify_cloud_http_error("onedrive", resp)
            logger.warning(
                "OneDrive upload failed for %s: %s %s",
                filename,
                resp.status_code,
                resp.text[:200],
            )
            return _CloudStoreAttempt(error=reason)
        except Exception as exc:
            logger.warning("OneDrive storage attempt failed: %s", exc)
            return _CloudStoreAttempt(error=f"onedrive:exception:{type(exc).__name__}")

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
    ) -> _CloudStoreAttempt:
        """Try to store in customer's Google Drive. Returns URL or classified error."""
        try:
            token = await get_fresh_token(db, tenant_id, "google")
            if not token:
                return _CloudStoreAttempt(error="google_drive:no_token")

            if folder_id:
                parent_id = folder_id
            else:
                parent_id = await _ensure_gdrive_path(
                    token, ["claritylegal-records", matter_slug, category]
                )

            boundary = "legalapp_upload_boundary"

            async def _upload(target_parent_id: str) -> httpx.Response:
                upload_metadata = {"name": filename, "parents": [target_parent_id]}
                upload_body = (
                    f"--{boundary}\r\n"
                    f"Content-Type: application/json\r\n\r\n"
                    f"{json.dumps(upload_metadata)}\r\n"
                    f"--{boundary}\r\n"
                    f"Content-Type: {content_type}\r\n\r\n"
                ).encode("utf-8")
                upload_body += content
                upload_body += f"\r\n--{boundary}--\r\n".encode("utf-8")

                async with httpx.AsyncClient(timeout=60) as client:
                    return await client.post(
                        f"{GOOGLE_UPLOAD_BASE}/files?uploadType=multipart",
                        content=upload_body,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": f"multipart/related; boundary={boundary}",
                        },
                    )

            resp = await _upload(parent_id)
            if resp.status_code == 404 and folder_id:
                logger.warning(
                    "Google Drive folder id %s was stale for %s; retrying by path",
                    folder_id,
                    filename,
                )
                parent_id = await _ensure_gdrive_path(
                    token, ["claritylegal-records", matter_slug, category]
                )
                resp = await _upload(parent_id)

            if resp.status_code in (200, 201):
                data = resp.json()
                file_id = data.get("id", "")
                web_link = f"https://drive.google.com/file/d/{file_id}/view"
                logger.info("Stored %s in Google Drive: %s", filename, web_link)
                return _CloudStoreAttempt(path=web_link)

            reason = _classify_cloud_http_error("google_drive", resp)
            logger.warning(
                "Google Drive upload failed for %s: %s %s",
                filename,
                resp.status_code,
                resp.text[:200],
            )
            return _CloudStoreAttempt(error=reason)
        except Exception as exc:
            logger.warning("Google Drive storage attempt failed: %s", exc)
            return _CloudStoreAttempt(
                error=f"google_drive:exception:{type(exc).__name__}"
            )

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


def _classify_cloud_http_error(provider: str, resp: httpx.Response) -> str:
    """Return a stable reason code for cloud upload failures."""
    status = resp.status_code
    if status in (401, 403):
        bucket = "auth"
    elif status == 404:
        bucket = "stale_folder"
    elif status in (408, 409, 423, 425, 429) or status >= 500:
        bucket = "retryable"
    else:
        bucket = "http"
    return f"{provider}:{bucket}:http_{status}"


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
    return subfolders.get(category)


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
