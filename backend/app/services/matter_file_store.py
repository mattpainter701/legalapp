"""Matter file store — routes document storage to customer's cloud storage.

Files are stored in the customer's own cloud storage, not ours:
  - MS365 customers → OneDrive: /claritylegal-records/{matter_slug}/{category}/{filename}
  - SharePoint customers → selected document library/folder
  - Google Workspace → Google Drive: claritylegal-records/{matter_slug}/ folder
  - Fallback → local disk (UPLOAD_DIR)

When matter.cloud_folder is provided (pre-provisioned subfolder IDs), uploads skip
the multi-hop folder traversal and go directly to the pre-created folder.
"""

import asyncio
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.provider_http import (
    ProviderAuthError,
    ProviderError,
    ProviderNotFound,
    ProviderThrottled,
)
from app.services.token_vault import get_fresh_token

settings = get_settings()
logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GOOGLE_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"
GOOGLE_DOWNLOAD_BASE = "https://www.googleapis.com/drive/v3"

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


class MatterFileReadError(RuntimeError):
    """Base exception for a fail-closed stored-document read."""


class MatterFileAccessError(MatterFileReadError):
    """The document is outside the requested tenant or local storage root."""


class MatterFileMetadataError(MatterFileReadError):
    """Durable storage metadata is missing or unsupported."""


class MatterFileNotFound(MatterFileReadError):
    """The local document no longer exists."""


class MatterFileTooLarge(MatterFileReadError):
    """The stored document exceeds the bounded read size."""


class MatterFileIntegrityError(MatterFileReadError):
    """Stored bytes do not match persisted size or hash evidence."""


class MatterFileStore:
    """Store matter files in the customer's connected cloud storage."""

    async def read_matter_file_bytes(
        self,
        *,
        db: AsyncSession,
        tenant_id: str,
        document: Any,
        expected_sha256: str | None = None,
        expected_size: int | None = None,
        max_bytes: int | None = None,
    ) -> bytes:
        """Read a tenant-owned MatterDocument using durable provider metadata.

        Cloud ``storage_path`` values are display URLs and are deliberately never
        requested. Google/Microsoft reads are constructed only from provider item
        and drive IDs, using a freshly resolved tenant OAuth token. Local reads are
        constrained to that tenant's upload root. Every path is size-bounded and
        can be cryptographically bound to a caller-supplied SHA-256.
        """
        if document is None or str(getattr(document, "tenant_id", "")) != str(
            tenant_id
        ):
            raise MatterFileAccessError("Document is not owned by this tenant")

        limit = max_bytes or settings.MAX_FILE_SIZE_MB * 1024 * 1024
        if limit <= 0:
            raise MatterFileMetadataError("Document read limit must be positive")

        persisted_size = (
            expected_size
            if expected_size is not None
            else getattr(document, "file_size", None)
        )
        if persisted_size is not None:
            try:
                persisted_size = int(persisted_size)
            except (TypeError, ValueError) as exc:
                raise MatterFileMetadataError(
                    "Document size metadata is invalid"
                ) from exc
            if persisted_size < 0:
                raise MatterFileMetadataError("Document size metadata is invalid")
            if persisted_size > limit:
                raise MatterFileTooLarge("Document exceeds the maximum read size")

        backend = _normalized_read_backend(document)
        if backend == "local":
            raw_path = getattr(document, "storage_path", None)
            if not raw_path:
                raise MatterFileMetadataError("Local document path is missing")
            path = _safe_existing_local_path(str(tenant_id), str(raw_path))
            content = await asyncio.to_thread(_read_local_file_capped, path, limit)
        else:
            item_id = str(getattr(document, "provider_object_id", "") or "").strip()
            if not item_id:
                raise MatterFileMetadataError(
                    "Cloud document is missing its durable provider item ID"
                )
            encoded_item_id = quote(item_id, safe="")
            if backend == "google_drive":
                token_provider = "google"
                provider_label = "Google Drive"
                url = f"{GOOGLE_DOWNLOAD_BASE}/files/{encoded_item_id}?alt=media"
            elif backend in ("onedrive", "sharepoint"):
                token_provider = "microsoft"
                provider_label = "Microsoft Graph"
                drive_id = str(getattr(document, "provider_drive_id", "") or "").strip()
                if backend == "sharepoint" and not drive_id:
                    raise MatterFileMetadataError(
                        "SharePoint document is missing its durable drive ID"
                    )
                if drive_id:
                    url = (
                        f"{GRAPH_BASE}/drives/{quote(drive_id, safe='')}/items/"
                        f"{encoded_item_id}/content"
                    )
                else:
                    url = f"{GRAPH_BASE}/me/drive/items/{encoded_item_id}/content"
            else:
                raise MatterFileMetadataError(
                    f"Unsupported document storage backend: {backend or 'unknown'}"
                )

            token = await get_fresh_token(db, str(tenant_id), token_provider)
            if not token:
                raise ProviderAuthError(f"{provider_label} credentials are unavailable")
            content = await self._download_provider_bytes(
                url=url,
                token=token,
                provider_label=provider_label,
                max_bytes=limit,
            )

        _validate_read_integrity(
            content,
            expected_size=persisted_size,
            expected_sha256=expected_sha256,
        )
        return content

    async def _download_provider_bytes(
        self,
        *,
        url: str,
        token: str,
        provider_label: str,
        max_bytes: int,
    ) -> bytes:
        """Stream provider bytes with a hard cap, including redirected content."""
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                async with client.stream(
                    "GET",
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                ) as response:
                    _raise_for_download_response(response, provider_label)
                    content_length = response.headers.get("Content-Length")
                    if content_length:
                        try:
                            if int(content_length) > max_bytes:
                                raise MatterFileTooLarge(
                                    "Document exceeds the maximum read size"
                                )
                        except ValueError:
                            pass
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise MatterFileTooLarge(
                                "Document exceeds the maximum read size"
                            )
                        chunks.append(chunk)
                    return b"".join(chunks)
        except MatterFileReadError:
            raise
        except ProviderError:
            raise
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProviderError(
                f"{provider_label} document download failed before completion"
            ) from exc

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
        When preferred_provider is set, that configured cloud is exclusive; a
        failed configured-cloud upload falls back to local storage with an
        actionable warning in ``StorageResult.error``. With no preference,
        providers retain their historical first-available cascade.
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

        explicit_provider = bool(str(preferred_provider or "").strip())
        configured_provider = _normalize_configured_provider(preferred_provider)
        providers = (
            [configured_provider]
            if explicit_provider and configured_provider
            else ([] if explicit_provider else _ordered_providers(None))
        )

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

        # Fallback: local disk. An explicit primary cloud never spills into a
        # different cloud provider; surface the configured-provider failure so
        # callers can warn the user while retaining the locally saved bytes.
        local_result = await self._store_local(
            tenant_id, matter_slug, canonical_folder, filename, content
        )
        if not explicit_provider:
            return local_result

        warning = _configured_provider_fallback_warning(configured_provider)
        logger.warning(
            "Configured cloud upload failed for tenant %s via %s; saved %s locally",
            tenant_id,
            configured_provider or "unsupported provider",
            filename,
        )
        return StorageResult(
            provider=local_result.provider,
            backend=local_result.backend,
            storage_path=local_result.storage_path,
            web_url=local_result.web_url,
            provider_item_id=local_result.provider_item_id,
            drive_id=local_result.drive_id,
            parent_id=local_result.parent_id,
            error=warning,
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
        full_path = _safe_new_local_path(
            tenant_id,
            "matters",
            matter_slug,
            category,
            filename,
        )
        await asyncio.to_thread(_write_local_file, full_path, content)
        logger.info("Stored %s locally at %s", filename, full_path)
        return StorageResult(
            provider="local",
            backend="local",
            storage_path=str(full_path),
        )


def _normalized_read_backend(document: Any) -> str | None:
    """Resolve backend labels without ever treating a display URL as a read URL."""
    backend = getattr(document, "_storage_backend", None)
    if not backend:
        backend = getattr(document, "storage_backend", None)
    provider = getattr(document, "storage_provider", None)
    value = str(backend or provider or "").strip().lower().replace("-", "_")
    aliases = {
        "google": "google_drive",
        "gdrive": "google_drive",
        "drive": "google_drive",
        "microsoft": "onedrive",
        "ms_graph": "onedrive",
        "one_drive": "onedrive",
        "share_point": "sharepoint",
    }
    normalized = aliases.get(value, value) or None
    if normalized:
        return normalized

    storage_path = str(getattr(document, "storage_path", "") or "")
    if storage_path and not storage_path.lower().startswith(("http://", "https://")):
        return "local"
    return None


def _tenant_local_root(tenant_id: str) -> Path:
    return (Path(settings.UPLOAD_DIR) / tenant_id).resolve()


def _ensure_within_root(path: Path, root: Path) -> Path:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise MatterFileAccessError(
            "Local document path escapes the tenant storage root"
        ) from exc
    return path


def _safe_existing_local_path(tenant_id: str, raw_path: str) -> Path:
    root = _tenant_local_root(tenant_id)
    path = Path(raw_path).resolve()
    _ensure_within_root(path, root)
    if not path.is_file():
        raise MatterFileNotFound("Local document was not found")
    return path


def _safe_new_local_path(tenant_id: str, *parts: str) -> Path:
    root = _tenant_local_root(tenant_id)
    path = (root.joinpath(*parts)).resolve()
    return _ensure_within_root(path, root)


def _read_local_file_capped(path: Path, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    with path.open("rb") as handle:
        while chunk := handle.read(64 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise MatterFileTooLarge("Document exceeds the maximum read size")
            chunks.append(chunk)
    return b"".join(chunks)


def _write_local_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _validate_read_integrity(
    content: bytes,
    *,
    expected_size: int | None,
    expected_sha256: str | None,
) -> None:
    if expected_size is not None and len(content) != expected_size:
        raise MatterFileIntegrityError(
            "Document bytes do not match persisted size metadata"
        )
    if expected_sha256:
        normalized = str(expected_sha256).strip().lower()
        if len(normalized) != 64 or any(
            ch not in "0123456789abcdef" for ch in normalized
        ):
            raise MatterFileMetadataError("Expected SHA-256 metadata is invalid")
        actual = hashlib.sha256(content).hexdigest()
        if not hmac.compare_digest(actual, normalized):
            raise MatterFileIntegrityError(
                "Document bytes do not match the expected SHA-256"
            )


def _raise_for_download_response(response: httpx.Response, provider_label: str) -> None:
    status = response.status_code
    if status < 400:
        return
    message = f"{provider_label} document download failed with HTTP {status}"
    kwargs = {"status_code": status}
    if status in (401, 403):
        raise ProviderAuthError(message, **kwargs)
    if status == 404:
        raise ProviderNotFound(message, **kwargs)
    if status == 429:
        raise ProviderThrottled(message, **kwargs)
    raise ProviderError(message, **kwargs)


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


def _normalize_configured_provider(preferred: str | None) -> str | None:
    normalized = str(preferred or "").strip().lower().replace("-", "_")
    aliases = {
        "one_drive": "onedrive",
        "share_point": "sharepoint",
        "google": "google_drive",
        "gdrive": "google_drive",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in {"onedrive", "sharepoint", "google_drive"}:
        return normalized
    return None


def _configured_provider_fallback_warning(provider: str | None) -> str:
    labels = {
        "onedrive": "Microsoft OneDrive",
        "sharepoint": "Microsoft SharePoint",
        "google_drive": "Google Drive",
    }
    if provider is None:
        return (
            "The configured cloud provider is unsupported; bytes were saved to "
            "local storage. Select Auto or a supported primary cloud provider, "
            "then retry."
        )
    label = labels[provider]
    return (
        f"Configured {label} upload failed; bytes were saved to local storage. "
        f"Reconnect {label} or verify its folder permissions, then retry."
    )


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
