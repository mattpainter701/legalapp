"""Cloud folder initialization for tenant onboarding and matter creation.

Creates the 'claritylegal-records' root folder in the customer's cloud storage
(OneDrive / Google Drive) and per-matter subfolder structures.
"""

import logging
import base64
import re
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.tenant import TenantSettings
from app.services.token_vault import get_fresh_token

settings = get_settings()
logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GOOGLE_DRIVE_BASE = "https://www.googleapis.com/drive/v3"

ROOT_FOLDER_NAME = "claritylegal-records"
MATTER_SUBFOLDERS = [
    "emails",
    "client_uploads",
    "documents",
    "pleadings",
    "correspondence",
    "billing",
]


def matter_relative_path(matter_slug: str) -> str:
    """Return the canonical tenant-relative matter path."""
    return f"{ROOT_FOLDER_NAME}/{matter_slug}"


async def initialize_cloud_root_folder(
    db: AsyncSession,
    tenant_id: str,
) -> dict:
    """Create or discover the root folder in all connected cloud drives.

    Returns {provider: {id: str, url: str}} for each provider where creation succeeded.
    """
    result = {}

    # Microsoft OneDrive
    ms_token = await get_fresh_token(db, tenant_id, "microsoft")
    if ms_token:
        try:
            folder_id = await _ensure_onedrive_folder(
                ms_token, ROOT_FOLDER_NAME, "root"
            )
            folder_meta = await _get_onedrive_folder_metadata(ms_token, folder_id)
            result["onedrive"] = {
                "id": folder_id,
                "folder_name": folder_meta.get("name") or ROOT_FOLDER_NAME,
                "url": folder_meta.get("webUrl") or "",
            }
            logger.info(
                "Ensured %s in OneDrive for tenant %s", ROOT_FOLDER_NAME, tenant_id
            )
        except Exception as exc:
            logger.warning(
                "Failed to create OneDrive root folder for tenant %s: %s",
                tenant_id,
                exc,
            )
        try:
            binding = await _get_sharepoint_binding(db, tenant_id)
            if binding and binding.get("drive_id"):
                folder_id = await _ensure_sharepoint_folder(
                    ms_token,
                    binding["drive_id"],
                    ROOT_FOLDER_NAME,
                    binding.get("root_item_id") or "root",
                )
                folder_meta = await _get_sharepoint_folder_metadata(
                    ms_token, binding["drive_id"], folder_id
                )
                result["sharepoint"] = {
                    "id": folder_id,
                    "drive_id": binding["drive_id"],
                    "site_id": binding.get("site_id"),
                    "folder_name": folder_meta.get("name") or ROOT_FOLDER_NAME,
                    "url": folder_meta.get("webUrl") or "",
                }
                logger.info(
                    "Ensured %s in SharePoint for tenant %s",
                    ROOT_FOLDER_NAME,
                    tenant_id,
                )
        except Exception as exc:
            logger.warning(
                "Failed to create SharePoint root folder for tenant %s: %s",
                tenant_id,
                exc,
            )

    # Google Drive
    g_token = await get_fresh_token(db, tenant_id, "google")
    if g_token:
        try:
            folder_id = await _ensure_gdrive_folder(g_token, ROOT_FOLDER_NAME, "root")
            folder_meta = await _get_gdrive_folder_metadata(g_token, folder_id)
            result["google_drive"] = {
                "id": folder_id,
                "folder_name": folder_meta.get("name") or ROOT_FOLDER_NAME,
                "url": folder_meta.get("webViewLink")
                or f"https://drive.google.com/drive/folders/{folder_id}",
            }
            logger.info(
                "Ensured %s in Google Drive for tenant %s", ROOT_FOLDER_NAME, tenant_id
            )
        except Exception as exc:
            logger.warning(
                "Failed to create Google Drive root folder for tenant %s: %s",
                tenant_id,
                exc,
            )

    if result:
        result["path"] = ROOT_FOLDER_NAME
        result["subfolders"] = list(MATTER_SUBFOLDERS)

    return result


async def initialize_matter_folders(
    db: AsyncSession,
    tenant_id: str,
    matter_slug: str,
    cloud_root: dict,
    folder_name: str | None = None,
) -> dict:
    """Create per-matter subfolder structure under claritylegal-records/{folder_name}/.

    Returns {provider: {matter_folder_id: str, subfolders: {name: id}}}
    """
    result = {}
    # Use the human-readable folder_name when provided; fall back to slug for
    # backward compatibility with existing matters that were provisioned with slugs.
    matter_folder_name = folder_name or matter_slug

    # OneDrive
    if cloud_root.get("onedrive"):
        ms_token = await get_fresh_token(db, tenant_id, "microsoft")
        if ms_token:
            try:
                root_id = (
                    cloud_root["onedrive"].get("id")
                    or cloud_root["onedrive"].get("matters_folder_id")
                    or cloud_root["onedrive"].get("matter_folder_id")
                )
                if not root_id:
                    raise RuntimeError("OneDrive cloud root missing folder id")
                matter_folder = await _ensure_onedrive_folder(
                    ms_token, matter_folder_name, root_id
                )
                folder_meta = await _get_onedrive_folder_metadata(
                    ms_token, matter_folder
                )
                subfolders = {}
                for sub in MATTER_SUBFOLDERS:
                    sub_id = await _ensure_onedrive_folder(ms_token, sub, matter_folder)
                    subfolders[sub] = sub_id
                result["onedrive"] = {
                    "matter_folder_id": matter_folder,
                    "folder_name": folder_meta.get("name") or matter_slug,
                    "url": folder_meta.get("webUrl") or "",
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
                root_id = (
                    cloud_root["google_drive"].get("id")
                    or cloud_root["google_drive"].get("matters_folder_id")
                    or cloud_root["google_drive"].get("matter_folder_id")
                )
                if not root_id:
                    raise RuntimeError("Google Drive cloud root missing folder id")
                matter_folder = await _ensure_gdrive_folder(
                    g_token, matter_folder_name, root_id
                )
                folder_meta = await _get_gdrive_folder_metadata(g_token, matter_folder)
                subfolders = {}
                for sub in MATTER_SUBFOLDERS:
                    sub_id = await _ensure_gdrive_folder(g_token, sub, matter_folder)
                    subfolders[sub] = sub_id
                result["google_drive"] = {
                    "matter_folder_id": matter_folder,
                    "folder_name": folder_meta.get("name") or matter_folder_name,
                    "url": folder_meta.get("webViewLink")
                    or f"https://drive.google.com/drive/folders/{matter_folder}",
                    "subfolders": subfolders,
                }
                logger.info("Created matter folders in Google Drive: %s", matter_slug)
            except Exception as exc:
                logger.warning(
                    "Failed to create Google Drive matter folders for %s: %s",
                    matter_slug,
                    exc,
                )

    # SharePoint
    if cloud_root.get("sharepoint"):
        ms_token = await get_fresh_token(db, tenant_id, "microsoft")
        if ms_token:
            try:
                drive_id = cloud_root["sharepoint"].get("drive_id")
                root_id = (
                    cloud_root["sharepoint"].get("id")
                    or cloud_root["sharepoint"].get("matters_folder_id")
                    or cloud_root["sharepoint"].get("matter_folder_id")
                )
                if not drive_id or not root_id:
                    raise RuntimeError("SharePoint cloud root missing drive/folder id")
                matter_folder = await _ensure_sharepoint_folder(
                    ms_token, drive_id, matter_slug, root_id
                )
                folder_meta = await _get_sharepoint_folder_metadata(
                    ms_token, drive_id, matter_folder
                )
                subfolders = {}
                for sub in MATTER_SUBFOLDERS:
                    sub_id = await _ensure_sharepoint_folder(
                        ms_token, drive_id, sub, matter_folder
                    )
                    subfolders[sub] = sub_id
                result["sharepoint"] = {
                    "matter_folder_id": matter_folder,
                    "drive_id": drive_id,
                    "site_id": cloud_root["sharepoint"].get("site_id"),
                    "folder_name": folder_meta.get("name") or matter_slug,
                    "url": folder_meta.get("webUrl") or "",
                    "subfolders": subfolders,
                }
                logger.info("Created matter folders in SharePoint: %s", matter_slug)
            except Exception as exc:
                logger.warning(
                    "Failed to create SharePoint matter folders for %s: %s",
                    matter_slug,
                    exc,
                )

    if result:
        result["path"] = matter_relative_path(matter_slug)
        result["subfolder_paths"] = {
            sub: f"{matter_relative_path(matter_slug)}/{sub}"
            for sub in MATTER_SUBFOLDERS
        }

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

    context_folders = [
        folder
        for folder in (cloud_folder.get("context_folders") or [])
        if isinstance(folder, dict)
    ]

    onedrive = cloud_folder.get("onedrive")
    onedrive_folder_roles: dict[str, str] = {}
    if onedrive and onedrive.get("matter_folder_id"):
        onedrive_folder_roles[onedrive["matter_folder_id"]] = "write"
    for folder in context_folders:
        if folder.get("provider") == "onedrive" and folder.get("matter_folder_id"):
            onedrive_folder_roles.setdefault(folder["matter_folder_id"], "read")
    if onedrive_folder_roles:
        ms_token = await get_fresh_token(db, tenant_id, "microsoft")
        if ms_token:
            for folder_id, role in sorted(onedrive_folder_roles.items()):
                try:
                    await _share_onedrive_folder(
                        ms_token, folder_id, unique_emails, role=role
                    )
                except Exception as exc:
                    logger.warning("Failed to share OneDrive matter folder: %s", exc)

    google_drive = cloud_folder.get("google_drive")
    google_folder_roles: dict[str, str] = {}
    if google_drive and google_drive.get("matter_folder_id"):
        google_folder_roles[google_drive["matter_folder_id"]] = "writer"
    for folder in context_folders:
        if folder.get("provider") == "google_drive" and folder.get("matter_folder_id"):
            google_folder_roles.setdefault(folder["matter_folder_id"], "reader")
    if google_folder_roles:
        g_token = await get_fresh_token(db, tenant_id, "google")
        if g_token:
            for folder_id, role in sorted(google_folder_roles.items()):
                try:
                    await _share_gdrive_folder(
                        g_token, folder_id, unique_emails, role=role
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to share Google Drive matter folder: %s", exc
                    )


async def _share_onedrive_folder(
    token: str, folder_id: str, emails: list[str], role: str = "write"
) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{GRAPH_BASE}/me/drive/items/{folder_id}/invite",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "recipients": [{"email": email} for email in emails],
                "requireSignIn": True,
                "sendInvitation": False,
                "roles": [role],
            },
        )
        if resp.status_code not in (200, 201, 202):
            raise RuntimeError(
                f"OneDrive invite failed: {resp.status_code} {resp.text[:200]}"
            )


async def _share_gdrive_folder(
    token: str, folder_id: str, emails: list[str], role: str = "writer"
) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        for email in emails:
            resp = await client.post(
                f"{GOOGLE_DRIVE_BASE}/files/{folder_id}/permissions",
                params={"sendNotificationEmail": "false"},
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "type": "user",
                    "role": role,
                    "emailAddress": email,
                },
            )
            if resp.status_code not in (200, 201):
                raise RuntimeError(
                    f"Google Drive permission failed for {email}: "
                    f"{resp.status_code} {resp.text[:200]}"
                )


# ── helpers ────────────────────────────────────────────────────────────


def _folder_name_rank(name: str | None, target: str) -> tuple[int, int] | None:
    """Rank exact folder-name matches before historic OneDrive-style duplicates."""
    if not name:
        return None
    normalized = name.strip()
    if normalized.casefold() == target.casefold():
        return (0, 0)
    match = re.fullmatch(
        rf"{re.escape(target)}\s+(\d+)", normalized, flags=re.IGNORECASE
    )
    if match:
        return (1, int(match.group(1)))
    return None


def _choose_existing_folder(items: list[dict], folder_name: str) -> dict | None:
    """Choose the best reusable folder from a provider child listing."""
    ranked = []
    for item in items:
        rank = _folder_name_rank(item.get("name"), folder_name)
        if rank is not None:
            created = item.get("createdDateTime") or item.get("createdTime") or "9999"
            ranked.append((rank[0], rank[1], created, item))
    if not ranked:
        return None
    ranked.sort(key=lambda row: (row[0], row[1], row[2]))
    return ranked[0][3]


def _validate_folder_name(folder_name: str) -> str:
    name = (folder_name or "").strip()
    if not name:
        raise ValueError("Folder name is required")
    if "/" in name or "\\" in name:
        raise ValueError("Folder name cannot contain slashes")
    if len(name) > 200:
        raise ValueError("Folder name is too long")
    return name


async def _ensure_onedrive_folder(token: str, folder_name: str, parent_id: str) -> str:
    """Ensure a folder exists in OneDrive without creating numbered duplicates."""
    folder_name = _validate_folder_name(folder_name)
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {token}"}
        children_url = _onedrive_children_url(parent_id)

        existing = _choose_existing_folder(
            await _list_onedrive_child_folders(client, headers, parent_id), folder_name
        )
        if existing:
            return existing["id"]

        resp = await client.post(
            children_url,
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
            existing = _choose_existing_folder(
                await _list_onedrive_child_folders(client, headers, parent_id),
                folder_name,
            )
            if existing:
                return existing["id"]
        raise RuntimeError(
            f"Failed to create OneDrive folder '{folder_name}': "
            f"{resp.status_code} {resp.text[:200]}"
        )


async def _get_sharepoint_binding(db: AsyncSession, tenant_id: str) -> dict | None:
    result = await db.execute(
        select(TenantSettings).where(
            TenantSettings.tenant_id == uuid.UUID(str(tenant_id))
        )
    )
    settings_row = result.scalar_one_or_none()
    if not settings_row or not isinstance(settings_row.custom_config, dict):
        return None
    binding = settings_row.custom_config.get("sharepoint_binding")
    return binding if isinstance(binding, dict) else None


def _sharepoint_children_url(drive_id: str, parent_id: str) -> str:
    return (
        f"{GRAPH_BASE}/drives/{drive_id}/root/children"
        if parent_id == "root"
        else f"{GRAPH_BASE}/drives/{drive_id}/items/{parent_id}/children"
    )


async def _list_sharepoint_child_folders(
    client: httpx.AsyncClient, headers: dict, drive_id: str, parent_id: str
) -> list[dict]:
    url = _sharepoint_children_url(drive_id, parent_id)
    params = {"$select": "id,name,folder,webUrl,createdDateTime", "$top": "200"}
    folders: list[dict] = []
    while url:
        resp = await client.get(url, headers=headers, params=params)
        params = None
        if resp.status_code != 200:
            return folders
        data = resp.json()
        folders.extend(item for item in data.get("value", []) if item.get("folder"))
        url = data.get("@odata.nextLink")
    return folders


async def _ensure_sharepoint_folder(
    token: str, drive_id: str, folder_name: str, parent_id: str
) -> str:
    folder_name = _validate_folder_name(folder_name)
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {token}"}
        existing = _choose_existing_folder(
            await _list_sharepoint_child_folders(client, headers, drive_id, parent_id),
            folder_name,
        )
        if existing:
            return existing["id"]
        resp = await client.post(
            _sharepoint_children_url(drive_id, parent_id),
            headers=headers,
            json={
                "name": folder_name,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "fail",
            },
        )
        if resp.status_code in (200, 201):
            return resp.json()["id"]
        if resp.status_code == 409:
            existing = _choose_existing_folder(
                await _list_sharepoint_child_folders(
                    client, headers, drive_id, parent_id
                ),
                folder_name,
            )
            if existing:
                return existing["id"]
        raise RuntimeError(
            f"Failed to create SharePoint folder '{folder_name}': "
            f"{resp.status_code} {resp.text[:200]}"
        )


async def _get_sharepoint_folder_metadata(
    token: str, drive_id: str, folder_id: str
) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{GRAPH_BASE}/drives/{drive_id}/items/{folder_id}",
            params={"$select": "id,name,webUrl,folder,parentReference"},
            headers={"Authorization": f"Bearer {token}"},
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"SharePoint folder lookup failed: {resp.status_code} {resp.text[:200]}"
        )
    item = resp.json()
    if not item.get("folder"):
        raise RuntimeError("SharePoint item is not a folder")
    return item


async def _get_onedrive_web_url(token: str, folder_id: str) -> str:
    """Get web URL for a OneDrive folder."""
    try:
        item = await _get_onedrive_folder_metadata(token, folder_id)
        return item.get("webUrl", "")
    except Exception:
        return ""


async def _ensure_gdrive_folder(token: str, folder_name: str, parent_id: str) -> str:
    """Ensure a folder exists in Google Drive. Returns the folder ID."""
    folder_name = _validate_folder_name(folder_name)
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {token}"}
        existing = _choose_existing_folder(
            await _list_gdrive_child_folders(client, headers, parent_id), folder_name
        )
        if existing:
            return existing["id"]

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
        if resp.status_code == 409:
            existing = _choose_existing_folder(
                await _list_gdrive_child_folders(client, headers, parent_id),
                folder_name,
            )
            if existing:
                return existing["id"]
        raise RuntimeError(
            f"Failed to create Google Drive folder '{folder_name}': "
            f"{resp.status_code} {resp.text[:200]}"
        )


async def build_matter_folder_metadata(
    db: AsyncSession,
    tenant_id: str,
    provider: str,
    folder_id: str,
    ensure_subfolders: bool = True,
) -> dict:
    """Build Matter.cloud_folder provider metadata for an existing provider folder."""
    if provider == "onedrive":
        token = await get_fresh_token(db, tenant_id, "microsoft")
        if not token:
            raise RuntimeError("Microsoft credentials are not connected")
        item = await _get_onedrive_folder_metadata(token, folder_id)
        subfolders = {}
        if ensure_subfolders:
            subfolders = {
                sub: await _ensure_onedrive_folder(token, sub, item["id"])
                for sub in MATTER_SUBFOLDERS
            }
        return {
            "matter_folder_id": item["id"],
            "folder_name": item.get("name") or "",
            "url": item.get("webUrl") or "",
            "subfolders": subfolders,
        }

    if provider == "google_drive":
        token = await get_fresh_token(db, tenant_id, "google")
        if not token:
            raise RuntimeError("Google credentials are not connected")
        item = await _get_gdrive_folder_metadata(token, folder_id)
        subfolders = {}
        if ensure_subfolders:
            subfolders = {
                sub: await _ensure_gdrive_folder(token, sub, item["id"])
                for sub in MATTER_SUBFOLDERS
            }
        return {
            "matter_folder_id": item["id"],
            "folder_name": item.get("name") or "",
            "url": item.get("webViewLink")
            or f"https://drive.google.com/drive/folders/{item['id']}",
            "subfolders": subfolders,
        }

    if provider == "sharepoint":
        token = await get_fresh_token(db, tenant_id, "microsoft")
        if not token:
            raise RuntimeError("Microsoft credentials are not connected")
        binding = await _get_sharepoint_binding(db, tenant_id)
        drive_id = (binding or {}).get("drive_id")
        if not drive_id:
            raise RuntimeError("SharePoint drive is not configured")
        item = await _get_sharepoint_folder_metadata(token, drive_id, folder_id)
        subfolders = {}
        if ensure_subfolders:
            subfolders = {
                sub: await _ensure_sharepoint_folder(token, drive_id, sub, item["id"])
                for sub in MATTER_SUBFOLDERS
            }
        return {
            "matter_folder_id": item["id"],
            "drive_id": drive_id,
            "site_id": (binding or {}).get("site_id"),
            "folder_name": item.get("name") or "",
            "url": item.get("webUrl") or "",
            "subfolders": subfolders,
        }

    raise ValueError(f"Unsupported cloud provider: {provider}")


async def rename_cloud_folder(
    db: AsyncSession,
    tenant_id: str,
    provider: str,
    folder_id: str,
    new_name: str,
) -> dict:
    """Rename a provider folder, then return refreshed matter folder metadata."""
    new_name = _validate_folder_name(new_name)
    if provider == "onedrive":
        token = await get_fresh_token(db, tenant_id, "microsoft")
        if not token:
            raise RuntimeError("Microsoft credentials are not connected")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.patch(
                f"{GRAPH_BASE}/me/drive/items/{folder_id}",
                headers={"Authorization": f"Bearer {token}"},
                json={"name": new_name},
            )
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"OneDrive rename failed: {resp.status_code} {resp.text[:200]}"
            )
        return await build_matter_folder_metadata(db, tenant_id, provider, folder_id)

    if provider == "google_drive":
        token = await get_fresh_token(db, tenant_id, "google")
        if not token:
            raise RuntimeError("Google credentials are not connected")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.patch(
                f"{GOOGLE_DRIVE_BASE}/files/{folder_id}",
                params={"fields": "id,name,webViewLink,mimeType"},
                headers={"Authorization": f"Bearer {token}"},
                json={"name": new_name},
            )
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"Google Drive rename failed: {resp.status_code} {resp.text[:200]}"
            )
        return await build_matter_folder_metadata(db, tenant_id, provider, folder_id)

    if provider == "sharepoint":
        token = await get_fresh_token(db, tenant_id, "microsoft")
        if not token:
            raise RuntimeError("Microsoft credentials are not connected")
        binding = await _get_sharepoint_binding(db, tenant_id)
        drive_id = (binding or {}).get("drive_id")
        if not drive_id:
            raise RuntimeError("SharePoint drive is not configured")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.patch(
                f"{GRAPH_BASE}/drives/{drive_id}/items/{folder_id}",
                headers={"Authorization": f"Bearer {token}"},
                json={"name": new_name},
            )
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"SharePoint rename failed: {resp.status_code} {resp.text[:200]}"
            )
        return await build_matter_folder_metadata(db, tenant_id, provider, folder_id)

    raise ValueError(f"Unsupported cloud provider: {provider}")


async def resolve_cloud_folder_reference(
    db: AsyncSession,
    tenant_id: str,
    provider: str,
    cloud_root: dict,
    *,
    folder_id: str | None = None,
    folder_url: str | None = None,
    folder_name: str | None = None,
    create_if_missing: bool = False,
    ensure_subfolders: bool = True,
) -> dict:
    """Resolve a UI remap request into matter-folder provider metadata."""
    if provider == "onedrive":
        token = await get_fresh_token(db, tenant_id, "microsoft")
        if not token:
            raise RuntimeError("Microsoft credentials are not connected")
        resolved_id = folder_id
        if folder_url:
            resolved_id = (await _resolve_onedrive_share_url(token, folder_url))["id"]
        elif folder_name:
            root_id = _cloud_root_provider_id(cloud_root, provider)
            if not root_id:
                raise RuntimeError("OneDrive root folder is not configured")
            match = await _find_onedrive_child_folder(token, folder_name, root_id)
            if match:
                resolved_id = match["id"]
            elif create_if_missing:
                resolved_id = await _ensure_onedrive_folder(token, folder_name, root_id)
        if not resolved_id:
            raise RuntimeError("OneDrive folder was not found")
        return await build_matter_folder_metadata(
            db, tenant_id, provider, resolved_id, ensure_subfolders=ensure_subfolders
        )

    if provider == "google_drive":
        token = await get_fresh_token(db, tenant_id, "google")
        if not token:
            raise RuntimeError("Google credentials are not connected")
        resolved_id = folder_id
        if folder_url:
            resolved_id = _parse_google_drive_folder_id(folder_url)
        elif folder_name:
            root_id = _cloud_root_provider_id(cloud_root, provider)
            if not root_id:
                raise RuntimeError("Google Drive root folder is not configured")
            match = await _find_gdrive_child_folder(token, folder_name, root_id)
            if match:
                resolved_id = match["id"]
            elif create_if_missing:
                resolved_id = await _ensure_gdrive_folder(token, folder_name, root_id)
        if not resolved_id:
            raise RuntimeError("Google Drive folder was not found")
        return await build_matter_folder_metadata(
            db, tenant_id, provider, resolved_id, ensure_subfolders=ensure_subfolders
        )

    if provider == "sharepoint":
        token = await get_fresh_token(db, tenant_id, "microsoft")
        if not token:
            raise RuntimeError("Microsoft credentials are not connected")
        binding = await _get_sharepoint_binding(db, tenant_id)
        drive_id = (binding or {}).get("drive_id")
        if not drive_id:
            raise RuntimeError("SharePoint drive is not configured")
        resolved_id = folder_id
        if folder_url:
            item = await _resolve_sharepoint_share_url(token, folder_url)
            resolved_id = item["id"]
        elif folder_name:
            root_id = _cloud_root_provider_id(cloud_root, provider)
            if not root_id:
                raise RuntimeError("SharePoint root folder is not configured")
            match = await _find_sharepoint_child_folder(
                token, drive_id, folder_name, root_id
            )
            if match:
                resolved_id = match["id"]
            elif create_if_missing:
                resolved_id = await _ensure_sharepoint_folder(
                    token, drive_id, folder_name, root_id
                )
        if not resolved_id:
            raise RuntimeError("SharePoint folder was not found")
        return await build_matter_folder_metadata(
            db, tenant_id, provider, resolved_id, ensure_subfolders=ensure_subfolders
        )

    raise ValueError(f"Unsupported cloud provider: {provider}")


def _onedrive_children_url(parent_id: str) -> str:
    return (
        f"{GRAPH_BASE}/me/drive/root/children"
        if parent_id == "root"
        else f"{GRAPH_BASE}/me/drive/items/{parent_id}/children"
    )


async def _list_onedrive_child_folders(
    client: httpx.AsyncClient, headers: dict, parent_id: str
) -> list[dict]:
    url = _onedrive_children_url(parent_id)
    params = {
        "$select": "id,name,folder,webUrl,createdDateTime",
        "$top": "200",
    }
    folders: list[dict] = []
    while url:
        resp = await client.get(url, headers=headers, params=params)
        params = None
        if resp.status_code != 200:
            return folders
        data = resp.json()
        folders.extend(item for item in data.get("value", []) if item.get("folder"))
        url = data.get("@odata.nextLink")
    return folders


async def _find_onedrive_child_folder(
    token: str, folder_name: str, parent_id: str
) -> dict | None:
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {token}"}
        return _choose_existing_folder(
            await _list_onedrive_child_folders(client, headers, parent_id),
            _validate_folder_name(folder_name),
        )


async def _find_sharepoint_child_folder(
    token: str, drive_id: str, folder_name: str, parent_id: str
) -> dict | None:
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {token}"}
        return _choose_existing_folder(
            await _list_sharepoint_child_folders(client, headers, drive_id, parent_id),
            _validate_folder_name(folder_name),
        )


async def _get_onedrive_folder_metadata(token: str, folder_id: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{GRAPH_BASE}/me/drive/items/{folder_id}",
            params={"$select": "id,name,webUrl,folder"},
            headers={"Authorization": f"Bearer {token}"},
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"OneDrive folder lookup failed: {resp.status_code} {resp.text[:200]}"
        )
    item = resp.json()
    if not item.get("folder"):
        raise RuntimeError("OneDrive item is not a folder")
    return item


async def _resolve_onedrive_share_url(token: str, folder_url: str) -> dict:
    share_id = "u!" + base64.urlsafe_b64encode(folder_url.encode()).decode().rstrip("=")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{GRAPH_BASE}/shares/{share_id}/driveItem",
            params={"$select": "id,name,webUrl,folder"},
            headers={"Authorization": f"Bearer {token}"},
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"OneDrive shared folder lookup failed: {resp.status_code} {resp.text[:200]}"
        )
    item = resp.json()
    if not item.get("folder"):
        raise RuntimeError("OneDrive link does not point to a folder")
    return item


async def _resolve_sharepoint_share_url(token: str, folder_url: str) -> dict:
    share_id = "u!" + base64.urlsafe_b64encode(folder_url.encode()).decode().rstrip("=")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{GRAPH_BASE}/shares/{share_id}/driveItem",
            params={"$select": "id,name,webUrl,folder,parentReference"},
            headers={"Authorization": f"Bearer {token}"},
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"SharePoint shared folder lookup failed: {resp.status_code} {resp.text[:200]}"
        )
    item = resp.json()
    if not item.get("folder"):
        raise RuntimeError("SharePoint link does not point to a folder")
    return item


async def _list_gdrive_child_folders(
    client: httpx.AsyncClient, headers: dict, parent_id: str
) -> list[dict]:
    folders: list[dict] = []
    page_token = None
    query = (
        "mimeType='application/vnd.google-apps.folder' "
        f"and '{parent_id}' in parents and trashed=false"
    )
    while True:
        params = {
            "q": query,
            "fields": "nextPageToken,files(id,name,webViewLink,createdTime)",
            "pageSize": "200",
        }
        if page_token:
            params["pageToken"] = page_token
        resp = await client.get(
            f"{GOOGLE_DRIVE_BASE}/files",
            params=params,
            headers=headers,
        )
        if resp.status_code != 200:
            return folders
        data = resp.json()
        folders.extend(data.get("files", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            return folders


async def _find_gdrive_child_folder(
    token: str, folder_name: str, parent_id: str
) -> dict | None:
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {token}"}
        return _choose_existing_folder(
            await _list_gdrive_child_folders(client, headers, parent_id),
            _validate_folder_name(folder_name),
        )


async def _get_gdrive_folder_metadata(token: str, folder_id: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{GOOGLE_DRIVE_BASE}/files/{folder_id}",
            params={"fields": "id,name,webViewLink,mimeType"},
            headers={"Authorization": f"Bearer {token}"},
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Google Drive folder lookup failed: {resp.status_code} {resp.text[:200]}"
        )
    item = resp.json()
    if item.get("mimeType") != "application/vnd.google-apps.folder":
        raise RuntimeError("Google Drive item is not a folder")
    return item


def _parse_google_drive_folder_id(folder_url: str) -> str:
    parsed = urlparse(folder_url)
    match = re.search(r"/folders/([^/?#]+)", parsed.path)
    if match:
        return match.group(1)
    query_id = parse_qs(parsed.query).get("id")
    if query_id:
        return query_id[0]
    if parsed.scheme or parsed.netloc:
        raise RuntimeError("Google Drive folder URL does not contain a folder id")
    return folder_url.strip()


def _cloud_root_provider_id(cloud_root: dict, provider: str) -> str | None:
    provider_data = (cloud_root or {}).get(provider) or {}
    return (
        provider_data.get("id")
        or provider_data.get("matter_folder_id")
        or provider_data.get("matters_folder_id")
    )
