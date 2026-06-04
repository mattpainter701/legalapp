"""Cloud folder initialization for tenant onboarding and matter creation.

Creates the 'claritylegal-records' root folder in the customer's cloud storage
(OneDrive / Google Drive) and per-matter subfolder structures.
"""

import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.token_vault import get_fresh_token

settings = get_settings()
logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GOOGLE_DRIVE_BASE = "https://www.googleapis.com/drive/v3"

MATTER_SUBFOLDERS = ["emails", "documents", "pleadings", "correspondence", "billing"]


async def initialize_cloud_root_folder(
    db: AsyncSession,
    tenant_id: str,
) -> dict:
    """Create 'claritylegal-records' root folder in all connected cloud drives.

    Returns {provider: {id: str, url: str}} for each provider where creation succeeded.
    """
    result = {}

    # Microsoft OneDrive
    ms_token = await get_fresh_token(db, tenant_id, "microsoft")
    if ms_token:
        try:
            folder_id = await _ensure_onedrive_folder(
                ms_token, "claritylegal-records", "root"
            )
            web_url = await _get_onedrive_web_url(ms_token, folder_id)
            result["onedrive"] = {"id": folder_id, "url": web_url}
            logger.info(
                "Created claritylegal-records in OneDrive for tenant %s", tenant_id
            )
        except Exception as exc:
            logger.warning(
                "Failed to create OneDrive root folder for tenant %s: %s",
                tenant_id,
                exc,
            )

    # Google Drive
    g_token = await get_fresh_token(db, tenant_id, "google")
    if g_token:
        try:
            folder_id = await _ensure_gdrive_folder(
                g_token, "claritylegal-records", "root"
            )
            result["google_drive"] = {
                "id": folder_id,
                "url": f"https://drive.google.com/drive/folders/{folder_id}",
            }
            logger.info(
                "Created claritylegal-records in Google Drive for tenant %s", tenant_id
            )
        except Exception as exc:
            logger.warning(
                "Failed to create Google Drive root folder for tenant %s: %s",
                tenant_id,
                exc,
            )

    return result


async def initialize_matter_folders(
    db: AsyncSession,
    tenant_id: str,
    matter_slug: str,
    cloud_root: dict,
) -> dict:
    """Create per-matter subfolder structure under claritylegal-records/{matter_slug}/.

    Returns {provider: {matter_folder_id: str, subfolders: {name: id}}}
    """
    result = {}

    # OneDrive
    if cloud_root.get("onedrive"):
        ms_token = await get_fresh_token(db, tenant_id, "microsoft")
        if ms_token:
            try:
                root_id = cloud_root["onedrive"]["id"]
                matter_folder = await _ensure_onedrive_folder(
                    ms_token, matter_slug, root_id
                )
                subfolders = {}
                for sub in MATTER_SUBFOLDERS:
                    sub_id = await _ensure_onedrive_folder(ms_token, sub, matter_folder)
                    subfolders[sub] = sub_id
                result["onedrive"] = {
                    "matter_folder_id": matter_folder,
                    "subfolders": subfolders,
                }
                logger.info("Created matter folders in OneDrive: %s", matter_slug)
            except Exception as exc:
                logger.warning(
                    "Failed to create OneDrive matter folders for %s: %s",
                    matter_slug,
                    exc,
                )

    # Google Drive
    if cloud_root.get("google_drive"):
        g_token = await get_fresh_token(db, tenant_id, "google")
        if g_token:
            try:
                root_id = cloud_root["google_drive"]["id"]
                matter_folder = await _ensure_gdrive_folder(
                    g_token, matter_slug, root_id
                )
                subfolders = {}
                for sub in MATTER_SUBFOLDERS:
                    sub_id = await _ensure_gdrive_folder(g_token, sub, matter_folder)
                    subfolders[sub] = sub_id
                result["google_drive"] = {
                    "matter_folder_id": matter_folder,
                    "subfolders": subfolders,
                }
                logger.info("Created matter folders in Google Drive: %s", matter_slug)
            except Exception as exc:
                logger.warning(
                    "Failed to create Google Drive matter folders for %s: %s",
                    matter_slug,
                    exc,
                )

    return result


async def share_matter_folders(
    db: AsyncSession,
    tenant_id: str,
    cloud_folder: dict | None,
    user_emails: list[str],
) -> None:
    """Best-effort sharing of matter root folders with assigned firm users."""
    if not cloud_folder or not user_emails:
        return

    unique_emails = sorted({email for email in user_emails if email})

    onedrive = cloud_folder.get("onedrive")
    if onedrive and onedrive.get("matter_folder_id"):
        ms_token = await get_fresh_token(db, tenant_id, "microsoft")
        if ms_token:
            try:
                await _share_onedrive_folder(
                    ms_token, onedrive["matter_folder_id"], unique_emails
                )
            except Exception as exc:
                logger.warning("Failed to share OneDrive matter folder: %s", exc)

    google_drive = cloud_folder.get("google_drive")
    if google_drive and google_drive.get("matter_folder_id"):
        g_token = await get_fresh_token(db, tenant_id, "google")
        if g_token:
            try:
                await _share_gdrive_folder(
                    g_token, google_drive["matter_folder_id"], unique_emails
                )
            except Exception as exc:
                logger.warning("Failed to share Google Drive matter folder: %s", exc)


async def _share_onedrive_folder(token: str, folder_id: str, emails: list[str]) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{GRAPH_BASE}/me/drive/items/{folder_id}/invite",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "recipients": [{"email": email} for email in emails],
                "requireSignIn": True,
                "sendInvitation": False,
                "roles": ["write"],
            },
        )
        if resp.status_code not in (200, 201, 202):
            raise RuntimeError(
                f"OneDrive invite failed: {resp.status_code} {resp.text[:200]}"
            )


async def _share_gdrive_folder(token: str, folder_id: str, emails: list[str]) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        for email in emails:
            resp = await client.post(
                f"{GOOGLE_DRIVE_BASE}/files/{folder_id}/permissions",
                params={"sendNotificationEmail": "false"},
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "type": "user",
                    "role": "writer",
                    "emailAddress": email,
                },
            )
            if resp.status_code not in (200, 201):
                raise RuntimeError(
                    f"Google Drive permission failed for {email}: "
                    f"{resp.status_code} {resp.text[:200]}"
                )


# ── helpers ────────────────────────────────────────────────────────────


async def _ensure_onedrive_folder(token: str, folder_name: str, parent_id: str) -> str:
    """Ensure a folder exists in OneDrive. Returns the folder ID."""
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {token}"}
        # Search for existing
        search_url = (
            f"{GRAPH_BASE}/me/drive/items/{parent_id}/children"
            f"?$filter=name eq '{folder_name}' and folder ne null"
            f"&$select=id,name"
        )
        resp = await client.get(search_url, headers=headers)
        if resp.status_code == 200:
            items = resp.json().get("value", [])
            if items:
                return items[0]["id"]

        # Create
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
            return resp.json()["id"]
        raise RuntimeError(
            f"Failed to create OneDrive folder '{folder_name}': {resp.status_code}"
        )


async def _get_onedrive_web_url(token: str, folder_id: str) -> str:
    """Get web URL for a OneDrive folder."""
    async with httpx.AsyncClient(timeout=15) as client:
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get(
            f"{GRAPH_BASE}/me/drive/items/{folder_id}?$select=webUrl",
            headers=headers,
        )
        if resp.status_code == 200:
            return resp.json().get("webUrl", "")
        return ""


async def _ensure_gdrive_folder(token: str, folder_name: str, parent_id: str) -> str:
    """Ensure a folder exists in Google Drive. Returns the folder ID."""
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {token}"}
        # Search for existing
        query = (
            f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' "
            f"and '{parent_id}' in parents and trashed=false"
        )
        resp = await client.get(
            f"{GOOGLE_DRIVE_BASE}/files",
            params={"q": query, "fields": "files(id,name)"},
            headers=headers,
        )
        if resp.status_code == 200:
            items = resp.json().get("files", [])
            if items:
                return items[0]["id"]

        # Create
        resp = await client.post(
            f"{GOOGLE_DRIVE_BASE}/files",
            json={
                "name": folder_name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id],
            },
            headers=headers,
        )
        if resp.status_code in (200, 201):
            return resp.json()["id"]
        raise RuntimeError(
            f"Failed to create Google Drive folder '{folder_name}': {resp.status_code}"
        )
