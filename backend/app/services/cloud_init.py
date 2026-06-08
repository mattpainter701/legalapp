"""Cloud folder initialization for tenant onboarding and matter creation.

Creates the tenant-owned ``claritylegal`` root folder in each connected cloud
provider (Microsoft 365 / Google Drive) and per-matter subfolder structures.
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

CLARITY_ROOT_FOLDER = "claritylegal"
MATTERS_FOLDER = "matters"
MATTER_SUBFOLDERS = ["emails", "uploads", "msgs", "chat history"]


def matter_relative_path(matter_slug: str) -> str:
    """Return the canonical tenant-relative matter file path."""
    return f"{CLARITY_ROOT_FOLDER}/{MATTERS_FOLDER}/{matter_slug}"


async def initialize_cloud_root_folder(
    db: AsyncSession,
    tenant_id: str,
    existing_root: dict | None = None,
) -> dict:
    """Create the root storage hierarchy in every connected cloud drive.

    The tenant can have both Microsoft 365 and Google connected at the same
    time. This function never treats providers as mutually exclusive: it merges
    any newly provisioned provider metadata with existing metadata and returns a
    shape like::

        {
          "onedrive": {"id": "...", "matters_folder_id": "...", "path": "claritylegal", ...},
          "google_drive": {...},
          "path": "claritylegal",
          "matters_path": "claritylegal/matters",
          "subfolders": ["emails", "uploads", "msgs", "chat history"]
        }
    """
    result: dict = dict(existing_root or {})

    ms_token = await get_fresh_token(db, tenant_id, "microsoft")
    if ms_token:
        try:
            root_id = await _ensure_onedrive_folder(
                ms_token, CLARITY_ROOT_FOLDER, "root"
            )
            matters_id = await _ensure_onedrive_folder(
                ms_token, MATTERS_FOLDER, root_id
            )
            web_url = await _get_onedrive_web_url(ms_token, root_id)
            result["onedrive"] = {
                "id": root_id,
                "root_folder_id": root_id,
                "matters_folder_id": matters_id,
                "url": web_url,
                "path": CLARITY_ROOT_FOLDER,
                "matters_path": f"{CLARITY_ROOT_FOLDER}/{MATTERS_FOLDER}",
            }
            logger.info(
                "Provisioned claritylegal root in OneDrive for tenant %s", tenant_id
            )
        except Exception as exc:
            logger.warning(
                "Failed to provision OneDrive root folder for tenant %s: %s",
                tenant_id,
                exc,
            )

    g_token = await get_fresh_token(db, tenant_id, "google")
    if g_token:
        try:
            root_id = await _ensure_gdrive_folder(g_token, CLARITY_ROOT_FOLDER, "root")
            matters_id = await _ensure_gdrive_folder(g_token, MATTERS_FOLDER, root_id)
            result["google_drive"] = {
                "id": root_id,
                "root_folder_id": root_id,
                "matters_folder_id": matters_id,
                "url": f"https://drive.google.com/drive/folders/{root_id}",
                "path": CLARITY_ROOT_FOLDER,
                "matters_path": f"{CLARITY_ROOT_FOLDER}/{MATTERS_FOLDER}",
            }
            logger.info(
                "Provisioned claritylegal root in Google Drive for tenant %s", tenant_id
            )
        except Exception as exc:
            logger.warning(
                "Failed to provision Google Drive root folder for tenant %s: %s",
                tenant_id,
                exc,
            )

    if result.get("onedrive") or result.get("google_drive"):
        result.setdefault("path", CLARITY_ROOT_FOLDER)
        result.setdefault("matters_path", f"{CLARITY_ROOT_FOLDER}/{MATTERS_FOLDER}")
        result.setdefault("subfolders", MATTER_SUBFOLDERS.copy())

    return result


async def initialize_matter_folders(
    db: AsyncSession,
    tenant_id: str,
    matter_slug: str,
    cloud_root: dict,
) -> dict:
    """Create per-matter folders under ``claritylegal/matters/{matter_slug}``.

    Returns provider-specific folder IDs/URLs plus canonical path metadata. The
    metadata is intended to be stored on ``Matter.cloud_folder`` so uploads,
    search, UI links, and downstream agents can address the matter consistently
    regardless of whether the tenant has Microsoft 365, Google Drive, or both.
    """
    result: dict = {
        "path": matter_relative_path(matter_slug),
        "matter_slug": matter_slug,
        "subfolder_names": MATTER_SUBFOLDERS.copy(),
        "subfolder_paths": {
            sub: f"{matter_relative_path(matter_slug)}/{sub}"
            for sub in MATTER_SUBFOLDERS
        },
    }

    if cloud_root.get("onedrive"):
        ms_token = await get_fresh_token(db, tenant_id, "microsoft")
        if ms_token:
            try:
                root_meta = cloud_root["onedrive"]
                matters_root_id = root_meta.get("matters_folder_id")
                if not matters_root_id:
                    root_id = root_meta.get("root_folder_id") or root_meta.get("id")
                    matters_root_id = await _ensure_onedrive_folder(
                        ms_token, MATTERS_FOLDER, root_id
                    )
                    root_meta["matters_folder_id"] = matters_root_id
                matter_folder = await _ensure_onedrive_folder(
                    ms_token, matter_slug, matters_root_id
                )
                subfolders = {}
                for sub in MATTER_SUBFOLDERS:
                    sub_id = await _ensure_onedrive_folder(ms_token, sub, matter_folder)
                    subfolders[sub] = {
                        "id": sub_id,
                        "path": f"{matter_relative_path(matter_slug)}/{sub}",
                    }
                web_url = await _get_onedrive_web_url(ms_token, matter_folder)
                result["onedrive"] = {
                    "matter_folder_id": matter_folder,
                    "url": web_url,
                    "path": matter_relative_path(matter_slug),
                    "subfolders": subfolders,
                }
                logger.info("Created matter folders in OneDrive: %s", matter_slug)
            except Exception as exc:
                logger.warning(
                    "Failed to create OneDrive matter folders for %s: %s",
                    matter_slug,
                    exc,
                )

    if cloud_root.get("google_drive"):
        g_token = await get_fresh_token(db, tenant_id, "google")
        if g_token:
            try:
                root_meta = cloud_root["google_drive"]
                matters_root_id = root_meta.get("matters_folder_id")
                if not matters_root_id:
                    root_id = root_meta.get("root_folder_id") or root_meta.get("id")
                    matters_root_id = await _ensure_gdrive_folder(
                        g_token, MATTERS_FOLDER, root_id
                    )
                    root_meta["matters_folder_id"] = matters_root_id
                matter_folder = await _ensure_gdrive_folder(
                    g_token, matter_slug, matters_root_id
                )
                subfolders = {}
                for sub in MATTER_SUBFOLDERS:
                    sub_id = await _ensure_gdrive_folder(g_token, sub, matter_folder)
                    subfolders[sub] = {
                        "id": sub_id,
                        "path": f"{matter_relative_path(matter_slug)}/{sub}",
                    }
                result["google_drive"] = {
                    "matter_folder_id": matter_folder,
                    "url": f"https://drive.google.com/drive/folders/{matter_folder}",
                    "path": matter_relative_path(matter_slug),
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


def _escape_graph_filter_value(value: str) -> str:
    return value.replace("'", "''")


def _escape_gdrive_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


async def _ensure_onedrive_folder(token: str, folder_name: str, parent_id: str) -> str:
    """Ensure a folder exists in OneDrive. Returns the folder ID."""
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {token}"}
        escaped = _escape_graph_filter_value(folder_name)
        search_url = (
            f"{GRAPH_BASE}/me/drive/items/{parent_id}/children"
            f"?$filter=name eq '{escaped}' and folder ne null"
            f"&$select=id,name"
        )
        resp = await client.get(search_url, headers=headers)
        if resp.status_code == 200:
            items = resp.json().get("value", [])
            if items:
                return items[0]["id"]

        create_url = f"{GRAPH_BASE}/me/drive/items/{parent_id}/children"
        resp = await client.post(
            create_url,
            json={
                "name": folder_name,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "fail",
            },
            headers=headers,
        )
        if resp.status_code in (200, 201):
            return resp.json()["id"]
        if resp.status_code == 409:
            # Race or prior unfiltered name collision: retry lookup once.
            resp = await client.get(search_url, headers=headers)
            if resp.status_code == 200 and resp.json().get("value"):
                return resp.json()["value"][0]["id"]
        raise RuntimeError(
            f"Failed to create OneDrive folder '{folder_name}': "
            f"{resp.status_code} {resp.text[:200]}"
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
        escaped = _escape_gdrive_query_value(folder_name)
        query = (
            f"name='{escaped}' and mimeType='application/vnd.google-apps.folder' "
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
            f"Failed to create Google Drive folder '{folder_name}': "
            f"{resp.status_code} {resp.text[:200]}"
        )
