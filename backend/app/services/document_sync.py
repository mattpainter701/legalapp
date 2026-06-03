import logging
from pathlib import Path

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
        if file_info["drive"] in ("onedrive", "sharepoint"):
            from app.services.token_vault import get_fresh_user_token

            if user_id:
                token = await get_fresh_user_token(db, tenant_id, user_id, "microsoft")
            else:
                token = await get_fresh_token(db, tenant_id, "microsoft")

            if not token:
                return None

            download_url = f"{GRAPH_BASE}/me/drive/items/{file_info['id']}/content"
            if file_info["drive"] == "sharepoint":
                download_url = f"{GRAPH_BASE}/drives/{file_info.get('drive_id', '')}/items/{file_info['id']}/content"

            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    download_url, headers={"Authorization": f"Bearer {token}"}
                )
                if resp.status_code != 200:
                    logger.warning(
                        "Download failed for %s: %s",
                        file_info["name"],
                        resp.status_code,
                    )
                    return None
                content = resp.content

        elif file_info["drive"] == "google_drive":
            from app.services.token_vault import get_fresh_user_token

            token = (
                await get_fresh_user_token(db, tenant_id, user_id, "google")
                if user_id
                else None
            )
            if not token:
                return None

            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{GOOGLE_DRIVE_BASE}/files/{file_info['id']}?alt=media",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if resp.status_code != 200:
                    logger.warning(
                        "Download failed for %s: %s",
                        file_info["name"],
                        resp.status_code,
                    )
                    return None
                content = resp.content
        else:
            return None

        upload_dir = Path(settings.UPLOAD_DIR) / str(tenant_id) / "synced"
        upload_dir.mkdir(parents=True, exist_ok=True)

        safe_name = "".join(c for c in file_info["name"] if c.isalnum() or c in "._- ")
        local_path = upload_dir / safe_name
        local_path.write_bytes(content)

        return str(local_path)

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
