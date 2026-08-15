import asyncio
import hashlib
import logging
import os
import tempfile
from pathlib import Path

import aiofiles
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.token_vault import get_fresh_token

settings = get_settings()
logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GOOGLE_DRIVE_BASE = "https://www.googleapis.com/drive/v3"

LEGAL_EXTENSIONS = {".pdf", ".docx", ".doc", ".docm", ".rtf", ".txt", ".wpd", ".odt"}
LEGAL_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "application/rtf",
    "text/plain",
}


def _sync_source_key(file_info: dict) -> str:
    """Stable, non-secret identity for one provider-side file."""
    provider = str(file_info.get("drive") or "").strip()
    remote_id = str(file_info.get("id") or "").strip()
    drive_id = str(file_info.get("drive_id") or "").strip()
    if not provider or not remote_id:
        raise ValueError("Cloud document is missing a stable provider/item identity")
    if provider in {"onedrive", "sharepoint"} and not drive_id:
        raise ValueError("Microsoft cloud document is missing its drive identity")
    remote_identity = "|".join((provider, drive_id, remote_id))
    return hashlib.sha256(remote_identity.encode("utf-8")).hexdigest()


def _legacy_synced_storage_path(upload_dir: Path, file_info: dict) -> Path:
    """Path used before content-addressed cloud versions were introduced."""
    safe_name = "".join(
        character
        for character in str(file_info.get("name") or "")
        if character.isalnum() or character in "._- "
    )
    return upload_dir / safe_name


def _synced_storage_path(
    upload_dir: Path,
    file_info: dict,
    content: bytes,
) -> Path:
    """Return a stable path for one exact remote-document version."""
    return _synced_storage_path_for_digest(
        upload_dir,
        file_info,
        hashlib.sha256(content).hexdigest(),
    )


def _synced_storage_path_for_digest(
    upload_dir: Path,
    file_info: dict,
    content_digest: str,
) -> Path:
    remote_digest = _sync_source_key(file_info)[:20]
    suffix = Path(str(file_info.get("name") or "")).suffix.lower()
    if suffix not in LEGAL_EXTENSIONS:
        suffix = ""
    return upload_dir / f"{remote_digest}-{content_digest}{suffix}"


def _path_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _install_downloaded_synced_file(
    temporary_path: Path,
    target_path: Path,
    expected_hash: str,
) -> None:
    """Install a completed size-capped temp download without overwriting bytes."""
    if target_path.exists():
        if _path_sha256(target_path) != expected_hash:
            raise RuntimeError("Content-addressed sync path has unexpected bytes")
        return
    with temporary_path.open("r+b") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary_path, target_path)


def _write_immutable_synced_file(path: Path, content: bytes) -> None:
    """Atomically create an immutable content-addressed file."""
    expected_hash = hashlib.sha256(content).hexdigest()
    if path.exists():
        if _path_sha256(path) != expected_hash:
            raise RuntimeError("Content-addressed sync path has unexpected bytes")
        return

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".sync-",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


class DocumentSyncService:
    async def sync_onedrive(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str | None = None,
        folder_path: str = "/",
        max_files: int = 100,
    ) -> list[dict]:
        from app.services.token_vault import get_fresh_user_token

        if user_id:
            token = await get_fresh_user_token(db, tenant_id, user_id, "microsoft")
        else:
            token = await get_fresh_token(db, tenant_id, "microsoft")

        if not token:
            raise RuntimeError("No Microsoft OAuth token available")

        child_url = f"{GRAPH_BASE}/me/drive/root:{folder_path}:/children"
        if folder_path == "/":
            child_url = f"{GRAPH_BASE}/me/drive/root/children"

        results = []
        async with httpx.AsyncClient() as client:
            url = child_url
            params = {
                "$top": min(max_files, 100),
                "$select": "id,name,file,size,lastModifiedDateTime,webUrl,parentReference",
                "$expand": "thumbnails",
            }

            resp = await client.get(
                url, headers={"Authorization": f"Bearer {token}"}, params=params
            )
            if resp.status_code != 200:
                raise RuntimeError(f"OneDrive listing failed: {resp.status_code}")

            for item in resp.json().get("value", []):
                file_info = item.get("file", {})
                if not file_info:
                    continue

                name = item.get("name", "")
                ext = Path(name).suffix.lower()
                mime = (file_info or {}).get("mimeType", "")

                if ext in LEGAL_EXTENSIONS or mime in LEGAL_MIME_TYPES:
                    results.append(
                        {
                            "id": item["id"],
                            "name": name,
                            "size": item.get("size", 0),
                            "modified": item.get("lastModifiedDateTime"),
                            "url": item.get("webUrl"),
                            "drive": "onedrive",
                            "drive_id": (
                                item.get("parentReference", {}).get("driveId")
                            ),
                            "mime_type": mime,
                        }
                    )

            return results[:max_files]

    async def sync_sharepoint(
        self,
        db: AsyncSession,
        tenant_id: str,
        site_id: str | None = None,
        max_files: int = 100,
    ) -> list[dict]:
        token = await get_fresh_token(db, tenant_id, "microsoft")
        if not token:
            raise RuntimeError("No Microsoft OAuth token available")

        results = []
        async with httpx.AsyncClient() as client:
            if not site_id:
                sites_resp = await client.get(
                    f"{GRAPH_BASE}/sites/root",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if sites_resp.status_code == 200:
                    site_id = sites_resp.json().get("id", "")

            if not site_id:
                return results

            drives_resp = await client.get(
                f"{GRAPH_BASE}/sites/{site_id}/drives",
                headers={"Authorization": f"Bearer {token}"},
            )
            if drives_resp.status_code != 200:
                raise RuntimeError(
                    f"SharePoint drives listing failed: {drives_resp.status_code}"
                )

            for drive in drives_resp.json().get("value", []):
                drive_id = drive["id"]
                drive_name = drive.get("name", "Documents")

                items_resp = await client.get(
                    f"{GRAPH_BASE}/drives/{drive_id}/root/children",
                    headers={"Authorization": f"Bearer {token}"},
                    params={
                        "$top": min(max_files, 100),
                        "$select": "id,name,file,size,lastModifiedDateTime,webUrl",
                    },
                )
                if items_resp.status_code != 200:
                    continue

                for item in items_resp.json().get("value", []):
                    file_info = item.get("file", {})
                    if not file_info:
                        continue

                    name = item.get("name", "")
                    ext = Path(name).suffix.lower()
                    mime = (file_info or {}).get("mimeType", "")

                    if ext in LEGAL_EXTENSIONS or mime in LEGAL_MIME_TYPES:
                        results.append(
                            {
                                "id": item["id"],
                                "name": name,
                                "size": item.get("size", 0),
                                "modified": item.get("lastModifiedDateTime"),
                                "url": item.get("webUrl"),
                                "drive": "sharepoint",
                                "drive_id": drive_id,
                                "drive_name": drive_name,
                                "mime_type": mime,
                            }
                        )

            return results[:max_files]

    async def sync_google_drive(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str,
        max_files: int = 100,
    ) -> list[dict]:
        from app.services.token_vault import get_fresh_user_token

        token = await get_fresh_user_token(db, tenant_id, user_id, "google")
        if not token:
            raise RuntimeError("No Google OAuth token available")

        query_parts = [
            "(" + " or ".join(f"mimeType='{m}'" for m in LEGAL_MIME_TYPES) + ")"
        ]
        query_parts.append(
            "("
            + " or ".join(
                f"name contains '.{ext.strip('.')}'" for ext in LEGAL_EXTENSIONS
            )
            + ")"
        )
        q = " and ".join(query_parts)
        q = f"({q}) and trashed=false"

        results = []
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{GOOGLE_DRIVE_BASE}/files",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "q": q,
                    "pageSize": min(max_files, 100),
                    "fields": "files(id,name,size,modifiedTime,webViewLink,mimeType,parents)",
                    "orderBy": "modifiedTime desc",
                },
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Google Drive listing failed: {resp.status_code}")

            for item in resp.json().get("files", []):
                results.append(
                    {
                        "id": item["id"],
                        "name": item.get("name", ""),
                        "size": int(item.get("size", 0)),
                        "modified": item.get("modifiedTime"),
                        "url": item.get("webViewLink"),
                        "drive": "google_drive",
                        "mime_type": item.get("mimeType", ""),
                    }
                )

        return results[:max_files]

    async def download_and_process(
        self,
        db: AsyncSession,
        tenant_id: str,
        file_info: dict,
        user_id: str | None = None,
    ) -> str | None:
        """Download a file from the remote drive, save locally, return the local path."""
        max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
        try:
            declared_size = int(file_info.get("size") or 0)
        except (TypeError, ValueError):
            declared_size = 0
        if declared_size > max_bytes:
            raise RuntimeError(
                f"Cloud document exceeds the {settings.MAX_FILE_SIZE_MB} MB limit"
            )

        if file_info["drive"] in ("onedrive", "sharepoint"):
            from app.services.token_vault import get_fresh_user_token

            if user_id:
                token = await get_fresh_user_token(db, tenant_id, user_id, "microsoft")
            else:
                token = await get_fresh_token(db, tenant_id, "microsoft")

            if not token:
                return None

            drive_id = str(file_info.get("drive_id") or "").strip()
            if not drive_id:
                raise RuntimeError("Microsoft cloud document is missing its drive id")
            download_url = (
                f"{GRAPH_BASE}/drives/{drive_id}/items/{file_info['id']}/content"
            )

        elif file_info["drive"] == "google_drive":
            from app.services.token_vault import get_fresh_user_token

            token = (
                await get_fresh_user_token(db, tenant_id, user_id, "google")
                if user_id
                else None
            )
            if not token:
                return None
            download_url = f"{GOOGLE_DRIVE_BASE}/files/{file_info['id']}?alt=media"
        else:
            return None

        upload_dir = Path(settings.UPLOAD_DIR) / str(tenant_id) / "synced"
        upload_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".sync-download-",
            dir=upload_dir,
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        digest = hashlib.sha256()
        downloaded_size = 0
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=60.0,
            ) as client:
                async with client.stream(
                    "GET",
                    download_url,
                    headers={"Authorization": f"Bearer {token}"},
                ) as response:
                    if response.status_code != 200:
                        logger.warning(
                            "Download failed for %s: %s",
                            file_info["name"],
                            response.status_code,
                        )
                        return None
                    content_length = response.headers.get("Content-Length")
                    if content_length:
                        try:
                            if int(content_length) > max_bytes:
                                raise RuntimeError(
                                    "Cloud document exceeds the configured size limit"
                                )
                        except ValueError:
                            pass
                    async with aiofiles.open(temporary_path, "wb") as handle:
                        async for block in response.aiter_bytes():
                            downloaded_size += len(block)
                            if downloaded_size > max_bytes:
                                raise RuntimeError(
                                    "Cloud document exceeds the configured size limit"
                                )
                            digest.update(block)
                            await handle.write(block)

            local_path = _synced_storage_path_for_digest(
                upload_dir,
                file_info,
                digest.hexdigest(),
            )
            await asyncio.to_thread(
                _install_downloaded_synced_file,
                temporary_path,
                local_path,
                digest.hexdigest(),
            )
            return str(local_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    async def get_sync_stats(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str | None = None,
    ) -> dict:
        """Return counts of legal documents available in connected drives."""
        stats = {"onedrive": 0, "sharepoint": 0, "google_drive": 0}

        try:
            od = await self.sync_onedrive(db, tenant_id, user_id, max_files=500)
            stats["onedrive"] = len(od)
        except Exception as exc:
            logger.warning("OneDrive stat check failed: %s", exc)

        try:
            sp = await self.sync_sharepoint(db, tenant_id, max_files=500)
            stats["sharepoint"] = len(sp)
        except Exception as exc:
            logger.warning("SharePoint stat check failed: %s", exc)

        if user_id:
            try:
                gd = await self.sync_google_drive(db, tenant_id, user_id, max_files=500)
                stats["google_drive"] = len(gd)
            except Exception as exc:
                logger.warning("Google Drive stat check failed: %s", exc)

        return stats


document_sync = DocumentSyncService()
