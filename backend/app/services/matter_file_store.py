"""Matter file store — routes document storage to customer's cloud storage.

Files are stored in the customer's own cloud storage, not ours:
  - MS365 customers → OneDrive: /claritylegal-records/{matter_slug}/{category}/{filename}
  - SharePoint customers → selected document library/folder
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


@dataclass(frozen=True)
class StorageResult:
    """Structured result for matter-file storage.

    `storage_path` is the legacy value existing callers persist today. New
    persistence code should prefer the explicit metadata fields when columns are
    available.
    """

    provider: str
    backend: str
    storage_path: str | None = None
    web_url: str | None = None
    provider_item_id: str | None = None
    drive_id: str | None = None
    parent_id: str | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


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
        """Upload a file. Returns the legacy storage path/URL.

        When matter_cloud_folder is provided, uploads directly to the pre-provisioned
        subfolder ID instead of traversing the folder tree.
        preferred_provider controls which cloud is tried first.
        """
        result = await self.store_matter_file_result(
            db=db,
            tenant_id=tenant_id,
            matter_slug=matter_slug,
            category=category,
            filename=filename,
            content=content,
            content_type=content_type,
            matter_cloud_folder=matter_cloud_folder,
            preferred_provider=preferred_provider,
        )
        return result.storage_path or ""

    async def store_matter_file_result(
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
    ) -> StorageResult:
        """Upload a file and return structured provider metadata.

        This is the integration point for durable provider IDs once model columns
        are available. `store_matter_file()` remains as the legacy string wrapper.
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
        sharepoint_folder_id = _extract_subfolder_id(
            matter_cloud_folder, "sharepoint", canonical_folder
        )
        sharepoint_drive_id = _extract_provider_field(
            matter_cloud_folder, "sharepoint", "drive_id"
        )

        providers = _ordered_providers(preferred_provider)

        for provider in providers:
            if provider == "onedrive":
                result = await self._try_store_onedrive(
                    db,
                    tenant_id,
                    matter_slug,
                    canonical_folder,
                    filename,
                    content,
                    content_type,
                    folder_id=onedrive_folder_id,
                )
                if result and result.succeeded:
                    return result
            elif provider == "google_drive":
                result = await self._try_store_google_drive(
                    db,
                    tenant_id,
                    matter_slug,
                    canonical_folder,
                    filename,
                    content,
                    content_type,
                    folder_id=gdrive_folder_id,
                )
                if result and result.succeeded:
                    return result
            elif provider == "sharepoint":
                result = await self._try_store_sharepoint(
                    db,
                    tenant_id,
                    filename,
                    content,
                    content_type,
                    folder_id=sharepoint_folder_id,
                    drive_id=sharepoint_drive_id,
                )
                if result and result.succeeded:
                    return result

        # Fallback: local disk
        return await self._store_local(
            tenant_id, matter_slug, canonical_folder, filename, content
        )

    # Files larger than this use chunked/resumable upload to avoid timeouts.
    _CHUNK_THRESHOLD_ONEDRIVE = 4 * 1024 * 1024  # 4 MiB
    _CHUNK_THRESHOLD_GOOGLE = 5 * 1024 * 1024  # 5 MiB
    # Chunk size for upload sessions.
    _CHUNK_SIZE = 2 * 1024 * 1024  # 2 MiB

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
    ) -> StorageResult | None:
        """Try to store in customer's OneDrive. Uses upload session for files > 4 MiB."""
        try:
            token = await get_fresh_token(db, tenant_id, "microsoft")
            if not token:
                return StorageResult(
                    provider="microsoft",
                    backend="onedrive",
                    error="Microsoft token unavailable",
                )

            if folder_id:
                parent_id = folder_id
            else:
                parent_id = await _ensure_onedrive_path(
                    token, ["claritylegal-records", matter_slug, category]
                )

            if len(content) > self._CHUNK_THRESHOLD_ONEDRIVE:
                return await self._upload_large_onedrive(
                    token, parent_id, filename, content, content_type
                )

            # Small-file path: single PUT with rename-on-conflict.
            upload_url = (
                f"{GRAPH_BASE}/me/drive/items/{parent_id}:/{filename}:/content"
                "?@microsoft.graph.conflictBehavior=rename"
            )
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
                    result = _storage_result_from_graph_item(
                        data,
                        backend="onedrive",
                        parent_id=parent_id,
                    )
                    logger.info("Stored %s in OneDrive: %s", filename, result.web_url)
                    return result
                else:
                    error = (
                        f"OneDrive upload failed: {resp.status_code} {resp.text[:200]}"
                    )
                    logger.warning(
                        "OneDrive upload failed for %s: %s %s",
                        filename,
                        resp.status_code,
                        resp.text[:200],
                    )
                    return StorageResult(
                        provider="microsoft",
                        backend="onedrive",
                        parent_id=parent_id,
                        error=error,
                    )
        except Exception as exc:
            logger.warning("OneDrive storage attempt failed: %s", exc)
            return StorageResult(
                provider="microsoft",
                backend="onedrive",
                error=str(exc),
            )

    async def _upload_large_onedrive(
        self,
        token: str,
        parent_id: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> StorageResult | None:
        """Upload a large file to OneDrive using the resumable upload session API."""
        try:
            # Create upload session
            session_url = f"{GRAPH_BASE}/me/drive/items/{parent_id}:/{filename}:/createUploadSession"
            async with httpx.AsyncClient(timeout=30) as client:
                session_resp = await client.post(
                    session_url,
                    json={
                        "item": {
                            "@microsoft.graph.conflictBehavior": "rename",
                            "name": filename,
                        }
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )
                if session_resp.status_code != 200:
                    logger.warning(
                        "OneDrive upload session creation failed: %s %s",
                        session_resp.status_code,
                        session_resp.text[:200],
                    )
                    return StorageResult(
                        provider="microsoft",
                        backend="onedrive",
                        parent_id=parent_id,
                        error=f"OneDrive upload session creation failed: {session_resp.status_code}",
                    )

                upload_url = session_resp.json().get("uploadUrl")
                if not upload_url:
                    return StorageResult(
                        provider="microsoft",
                        backend="onedrive",
                        parent_id=parent_id,
                        error="OneDrive upload session missing uploadUrl",
                    )

            # Upload in chunks
            total = len(content)
            offset = 0
            while offset < total:
                end = min(offset + self._CHUNK_SIZE, total)
                chunk = content[offset:end]
                content_range = f"bytes {offset}-{end - 1}/{total}"

                async with httpx.AsyncClient(timeout=120) as client:
                    chunk_resp = await client.put(
                        upload_url,
                        content=chunk,
                        headers={
                            "Content-Length": str(len(chunk)),
                            "Content-Range": content_range,
                        },
                    )
                    if chunk_resp.status_code in (200, 201):
                        data = chunk_resp.json()
                        result = _storage_result_from_graph_item(
                            data,
                            backend="onedrive",
                            parent_id=parent_id,
                        )
                        logger.info("Uploaded large file %s to OneDrive", filename)
                        return result
                    elif chunk_resp.status_code == 202:
                        offset = end
                        continue
                    else:
                        logger.warning(
                            "OneDrive chunk upload failed: %s %s",
                            chunk_resp.status_code,
                            chunk_resp.text[:200],
                        )
                        return StorageResult(
                            provider="microsoft",
                            backend="onedrive",
                            parent_id=parent_id,
                            error=f"OneDrive chunk upload failed: {chunk_resp.status_code}",
                        )
            return StorageResult(
                provider="microsoft",
                backend="onedrive",
                parent_id=parent_id,
                error="OneDrive upload session completed without final item",
            )
        except Exception as exc:
            logger.warning("Large OneDrive upload failed: %s", exc)
            return StorageResult(
                provider="microsoft",
                backend="onedrive",
                parent_id=parent_id,
                error=str(exc),
            )

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
    ) -> StorageResult | None:
        """Try to store in customer's Google Drive. Uses resumable upload for files > 5 MiB."""
        try:
            token = await get_fresh_token(db, tenant_id, "google")
            if not token:
                return StorageResult(
                    provider="google",
                    backend="google_drive",
                    error="Google token unavailable",
                )

            if folder_id:
                parent_id = folder_id
            else:
                parent_id = await _ensure_gdrive_path(
                    token, ["claritylegal-records", matter_slug, category]
                )

            # Check for existing file with same name to avoid duplicates.
            existing = await self._find_gdrive_file(token, parent_id, filename)
            if existing:
                logger.info(
                    "Google Drive file '%s' already exists (id=%s), skipping upload",
                    filename,
                    existing,
                )
                web_link = _gdrive_web_url(existing)
                return StorageResult(
                    provider="google",
                    backend="google_drive",
                    storage_path=web_link,
                    web_url=web_link,
                    provider_item_id=existing,
                    parent_id=parent_id,
                )

            if len(content) > self._CHUNK_THRESHOLD_GOOGLE:
                return await self._upload_large_google_drive(
                    token, parent_id, filename, content, content_type
                )

            # Small-file path: single multipart upload.
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
                    web_link = data.get("webViewLink") or _gdrive_web_url(file_id)
                    result = StorageResult(
                        provider="google",
                        backend="google_drive",
                        storage_path=web_link,
                        web_url=web_link,
                        provider_item_id=file_id or None,
                        parent_id=parent_id,
                    )
                    logger.info("Stored %s in Google Drive: %s", filename, web_link)
                    return result
                else:
                    error = f"Google Drive upload failed: {resp.status_code} {resp.text[:200]}"
                    logger.warning(
                        "Google Drive upload failed for %s: %s %s",
                        filename,
                        resp.status_code,
                        resp.text[:200],
                    )
                    return StorageResult(
                        provider="google",
                        backend="google_drive",
                        parent_id=parent_id,
                        error=error,
                    )
        except Exception as exc:
            logger.warning("Google Drive storage attempt failed: %s", exc)
            return StorageResult(
                provider="google",
                backend="google_drive",
                error=str(exc),
            )

    async def _find_gdrive_file(
        self, token: str, parent_id: str, filename: str
    ) -> str | None:
        """Return the file ID if a file named ``filename`` already exists in
        ``parent_id``, or None."""
        try:
            safe_name = filename.replace("'", "\\'")
            query = (
                f"name = '{safe_name}' "
                f"and '{parent_id}' in parents and trashed = false"
            )
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    "https://www.googleapis.com/drive/v3/files",
                    headers={"Authorization": f"Bearer {token}"},
                    params={
                        "q": query,
                        "fields": "files(id)",
                        "pageSize": 1,
                    },
                )
                if resp.status_code == 200:
                    files = resp.json().get("files", [])
                    return files[0]["id"] if files else None
        except Exception:
            pass
        return None

    async def _upload_large_google_drive(
        self,
        token: str,
        parent_id: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> StorageResult | None:
        """Upload a large file to Google Drive using resumable upload."""
        try:
            # Initiate resumable upload session.
            metadata = {"name": filename, "parents": [parent_id]}
            async with httpx.AsyncClient(timeout=30) as client:
                init_resp = await client.post(
                    "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable",
                    json=metadata,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json; charset=UTF-8",
                        "X-Upload-Content-Type": content_type,
                        "X-Upload-Content-Length": str(len(content)),
                    },
                )
                if init_resp.status_code != 200:
                    logger.warning(
                        "Google Drive resumable session init failed: %s",
                        init_resp.status_code,
                    )
                    return StorageResult(
                        provider="google",
                        backend="google_drive",
                        parent_id=parent_id,
                        error=f"Google Drive resumable session init failed: {init_resp.status_code}",
                    )

                upload_url = init_resp.headers.get("Location")
                if not upload_url:
                    # Some API versions return the URL in the response.
                    upload_url = init_resp.headers.get("location")
                if not upload_url:
                    return StorageResult(
                        provider="google",
                        backend="google_drive",
                        parent_id=parent_id,
                        error="Google Drive resumable session missing upload URL",
                    )

            # Upload in chunks.
            total = len(content)
            offset = 0
            while offset < total:
                end = min(offset + self._CHUNK_SIZE, total)
                chunk = content[offset:end]
                content_range = f"bytes {offset}-{end - 1}/{total}"

                async with httpx.AsyncClient(timeout=120) as client:
                    chunk_resp = await client.put(
                        upload_url,
                        content=chunk,
                        headers={
                            "Content-Length": str(len(chunk)),
                            "Content-Range": content_range,
                        },
                    )
                    if chunk_resp.status_code in (200, 201):
                        data = chunk_resp.json()
                        file_id = data.get("id", "")
                        web_link = data.get("webViewLink") or _gdrive_web_url(file_id)
                        logger.info("Uploaded large file %s to Google Drive", filename)
                        return StorageResult(
                            provider="google",
                            backend="google_drive",
                            storage_path=web_link,
                            web_url=web_link,
                            provider_item_id=file_id or None,
                            parent_id=parent_id,
                        )
                    elif chunk_resp.status_code == 308:
                        # 308 Resume Incomplete — continue.
                        offset = end
                        continue
                    else:
                        logger.warning(
                            "Google Drive chunk upload failed: %s %s",
                            chunk_resp.status_code,
                            chunk_resp.text[:200],
                        )
                        return StorageResult(
                            provider="google",
                            backend="google_drive",
                            parent_id=parent_id,
                            error=f"Google Drive chunk upload failed: {chunk_resp.status_code}",
                        )
            return StorageResult(
                provider="google",
                backend="google_drive",
                parent_id=parent_id,
                error="Google Drive upload session completed without final file",
            )
        except Exception as exc:
            logger.warning("Large Google Drive upload failed: %s", exc)
            return StorageResult(
                provider="google",
                backend="google_drive",
                parent_id=parent_id,
                error=str(exc),
            )

    async def _try_store_sharepoint(
        self,
        db: AsyncSession,
        tenant_id: str,
        filename: str,
        content: bytes,
        content_type: str,
        *,
        folder_id: str | None,
        drive_id: str | None,
    ) -> StorageResult | None:
        """Try to store in a configured SharePoint document library."""
        if not folder_id or not drive_id:
            return StorageResult(
                provider="microsoft",
                backend="sharepoint",
                drive_id=drive_id,
                parent_id=folder_id,
                error="SharePoint drive_id or folder_id unavailable",
            )
        try:
            token = await get_fresh_token(db, tenant_id, "microsoft")
            if not token:
                return StorageResult(
                    provider="microsoft",
                    backend="sharepoint",
                    drive_id=drive_id,
                    parent_id=folder_id,
                    error="Microsoft token unavailable",
                )
            upload_url = (
                f"{GRAPH_BASE}/drives/{drive_id}/items/{folder_id}:/"
                f"{filename}:/content"
            )
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
                result = _storage_result_from_graph_item(
                    data,
                    backend="sharepoint",
                    parent_id=folder_id,
                    drive_id=drive_id,
                )
                logger.info("Stored %s in SharePoint: %s", filename, result.web_url)
                return result
            error = f"SharePoint upload failed: {resp.status_code} {resp.text[:200]}"
            logger.warning(
                "SharePoint upload failed for %s: %s %s",
                filename,
                resp.status_code,
                resp.text[:200],
            )
            return StorageResult(
                provider="microsoft",
                backend="sharepoint",
                drive_id=drive_id,
                parent_id=folder_id,
                error=error,
            )
        except Exception as exc:
            logger.warning("SharePoint storage attempt failed: %s", exc)
            return StorageResult(
                provider="microsoft",
                backend="sharepoint",
                drive_id=drive_id,
                parent_id=folder_id,
                error=str(exc),
            )

    async def _store_local(
        self,
        tenant_id: str,
        matter_slug: str,
        category: str,
        filename: str,
        content: bytes,
    ) -> StorageResult:
        """Store file on local disk. Returns the absolute storage path."""
        rel_path = f"matters/{matter_slug}/{category}/{filename}"
        full_path = Path(settings.UPLOAD_DIR) / tenant_id / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)
        logger.info("Stored %s locally at %s", filename, full_path)
        return StorageResult(
            provider="local",
            backend="local",
            storage_path=str(full_path),
        )


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


def _extract_provider_field(
    cloud_folder: dict | None, provider: str, field: str
) -> str | None:
    if not cloud_folder:
        return None
    provider_data = cloud_folder.get(provider)
    if not isinstance(provider_data, dict):
        return None
    value = provider_data.get(field)
    return str(value) if value else None


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
    all_providers = ["onedrive", "sharepoint", "google_drive"]
    if preferred in all_providers:
        return [preferred] + [p for p in all_providers if p != preferred]
    return all_providers


def _storage_result_from_graph_item(
    data: dict,
    *,
    backend: str,
    parent_id: str | None,
    drive_id: str | None = None,
) -> StorageResult:
    web_url = data.get("webUrl") or data.get("@microsoft.graph.downloadUrl")
    parent_ref = data.get("parentReference") or {}
    resolved_drive_id = drive_id or parent_ref.get("driveId")
    resolved_parent_id = parent_id or parent_ref.get("id")
    return StorageResult(
        provider="microsoft",
        backend=backend,
        storage_path=web_url,
        web_url=web_url,
        provider_item_id=data.get("id"),
        drive_id=resolved_drive_id,
        parent_id=resolved_parent_id,
    )


def _gdrive_web_url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"


async def _ensure_onedrive_path(token: str, folders: list[str]) -> str:
    """Ensure a folder path exists in OneDrive, creating folders as needed.
    Returns the folder ID of the deepest folder.
    """
    from app.services.cloud_init import _ensure_onedrive_folder

    parent_id = "root"
    for folder_name in folders:
        parent_id = await _ensure_onedrive_folder(token, folder_name, parent_id)
    return parent_id


async def _ensure_gdrive_path(token: str, folders: list[str]) -> str:
    """Ensure a folder path exists in Google Drive, creating folders as needed.
    Returns the folder ID of the deepest folder.
    """
    from app.services.cloud_init import _ensure_gdrive_folder

    parent_id = "root"
    for folder_name in folders:
        parent_id = await _ensure_gdrive_folder(token, folder_name, parent_id)
    return parent_id
