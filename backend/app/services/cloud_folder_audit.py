"""Read-only audit of matter folder identity across connected cloud roots."""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plugin import Matter
from app.models.tenant import Tenant, TenantSettings
from app.models.tenant_credential import TenantCredential
from app.services.cloud_init import (
    GOOGLE_DRIVE_BASE,
    GRAPH_BASE,
    canonical_matter_folder_name,
)
from app.services.token_vault import get_fresh_token

PROVIDER_LABELS = {
    "onedrive": "Microsoft OneDrive",
    "sharepoint": "Microsoft SharePoint",
    "google_drive": "Google Drive",
}
_CLOUD_TOKEN_PROVIDER = {
    "onedrive": "microsoft",
    "sharepoint": "microsoft",
    "google_drive": "google",
}


def classify_matter_folders(
    *,
    matter_id: str,
    matter_name: str,
    matter_slug: str,
    current_binding_id: str | None,
    children: list[dict[str, Any]],
    matter_number: str | None = None,
) -> dict[str, Any]:
    """Classify children without selecting, changing, or repairing a folder.

    A folder is recognised under either the current name (matter number when
    present, otherwise the UUID prefix) or the historic UUID-prefixed name, so
    folders provisioned before matter numbers existed are not reported as
    unbound or ambiguous.
    """
    id_name = canonical_matter_folder_name(
        matter_name, matter_id, matter_slug, matter_number
    )
    legacy_name = canonical_matter_folder_name(matter_name, matter_id, matter_slug)
    slug_name = (matter_slug or "").strip()
    id_matches = [item for item in children if item.get("name") == id_name]
    legacy_matches = (
        []
        if legacy_name == id_name
        else [item for item in children if item.get("name") == legacy_name]
    )
    slug_matches = [item for item in children if item.get("name") == slug_name]
    all_matches = {
        str(item.get("id"))
        for item in id_matches + legacy_matches + slug_matches
        if item.get("id")
    }
    binding_in_matches = bool(
        current_binding_id and str(current_binding_id) in all_matches
    )
    reasons: list[str] = []
    if (id_matches or legacy_matches) and slug_matches:
        reasons.append("both_slug_and_id_named_folders")
    if len(id_matches) + len(legacy_matches) > 1 or len(slug_matches) > 1:
        reasons.append("duplicate_matching_names")
    if current_binding_id and not binding_in_matches:
        reasons.append("binding_not_found_among_matching_folders")
    return {
        "matter_id": str(matter_id),
        "matter_name": matter_name,
        "matter_slug": matter_slug,
        "expected_id_named": id_name,
        "expected_legacy_named": legacy_name,
        "expected_slug_named": slug_name,
        "current_binding_id": current_binding_id,
        "id_named_matches": id_matches,
        "legacy_named_matches": legacy_matches,
        "slug_named_matches": slug_matches,
        "ambiguous": bool(reasons),
        "ambiguity_reasons": reasons,
    }


async def _list_children(
    provider: str, token: str, root: dict[str, Any]
) -> list[dict[str, Any]]:
    """List folders under a root and raise on provider failures."""
    headers = {"Authorization": f"Bearer {token}"}
    if provider == "google_drive":
        parent = root.get("id")
        if not parent:
            raise RuntimeError("Google Drive root folder ID is missing")
        url = f"{GOOGLE_DRIVE_BASE}/files"
        params: dict[str, Any] | None = {
            "q": f"'{parent}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
            "fields": "nextPageToken,incompleteSearch,files(id,name,mimeType,webViewLink)",
            "pageSize": 1000,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        items: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=30) as client:
            while url:
                response = await client.get(url, headers=headers, params=params)
                if response.status_code != 200:
                    raise RuntimeError(
                        f"Google Drive child listing failed: HTTP {response.status_code}"
                    )
                payload = response.json()
                if payload.get("incompleteSearch"):
                    raise RuntimeError("Google Drive inventory is incomplete")
                items.extend(payload.get("files") or [])
                token_value = payload.get("nextPageToken")
                if not token_value:
                    break
                params["pageToken"] = token_value
        return items

    parent = root.get("id")
    drive_id = root.get("drive_id") if provider == "sharepoint" else None
    if not parent or (provider == "sharepoint" and not drive_id):
        raise RuntimeError(f"{provider} root folder identity is incomplete")
    if provider == "sharepoint":
        url = f"{GRAPH_BASE}/drives/{drive_id}/items/{parent}/children"
    else:
        url = f"{GRAPH_BASE}/me/drive/items/{parent}/children"
    params: dict[str, Any] | None = {"$select": "id,name,folder,webUrl", "$top": "200"}
    items = []
    async with httpx.AsyncClient(timeout=30) as client:
        while url:
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com":
                raise RuntimeError("Invalid Microsoft inventory continuation")
            response = await client.get(url, headers=headers, params=params)
            if response.status_code != 200:
                label = {"onedrive": "OneDrive", "sharepoint": "SharePoint"}.get(
                    provider, provider.replace("_", " ").title()
                )
                raise RuntimeError(
                    f"{label} child listing failed: HTTP {response.status_code}"
                )
            payload = response.json()
            items.extend(item for item in payload.get("value", []) if "folder" in item)
            url = payload.get("@odata.nextLink")
            params = None
    return items


async def bound_storage_readiness(db: AsyncSession, tenant_id: str) -> dict[str, Any]:
    """Explain why the firm's configured cloud storage may be unusable.

    A storage failure during signing or intake returns a deliberately generic
    503 so a client never sees provider detail. This read-only diagnosis gives
    an operator the matching explanation: which provider the firm is bound to,
    whether an active credential exists for it, and which matters still lack a
    folder binding. It performs no provider I/O and changes nothing.
    """
    tenant_uuid = uuid.UUID(str(tenant_id))
    tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_uuid))
    if tenant is None:
        raise ValueError(f"Tenant not found: {tenant_id}")
    configured = await db.scalar(
        select(TenantSettings.primary_cloud_provider).where(
            TenantSettings.tenant_id == tenant_uuid
        )
    )
    provider = str(configured or "").strip().lower().replace("-", "_") or None
    credential_rows = (
        await db.execute(
            select(TenantCredential.provider, TenantCredential.is_active).where(
                TenantCredential.tenant_id == tenant_uuid
            )
        )
    ).all()
    active = {
        str(row_provider).strip().lower()
        for row_provider, is_active in credential_rows
        if row_provider and is_active
    }
    token_provider = _CLOUD_TOKEN_PROVIDER.get(provider or "")
    credential_active = bool(token_provider and token_provider in active)

    matters = (
        (
            await db.execute(
                select(Matter)
                .where(Matter.tenant_id == tenant_uuid)
                .order_by(Matter.slug)
            )
        )
        .scalars()
        .all()
    )
    unbound: list[dict[str, Any]] = []
    bound_count = 0
    if provider:
        for matter in matters:
            folder = (
                matter.cloud_folder if isinstance(matter.cloud_folder, dict) else {}
            )
            binding = folder.get(provider) if isinstance(folder, dict) else None
            if isinstance(binding, dict) and binding.get("matter_folder_id"):
                bound_count += 1
            else:
                unbound.append(
                    {
                        "matter_id": str(matter.id),
                        "matter_name": matter.matter_name,
                        "matter_slug": matter.slug,
                    }
                )

    label = PROVIDER_LABELS.get(provider or "", provider or "the configured provider")
    if provider is None:
        status = "unconfigured"
        reason = (
            "No primary cloud provider is configured; uploads use Auto/local "
            "storage and do not fail closed."
        )
    elif not credential_active:
        status = "needs_reauth"
        reason = (
            f"The firm is bound to {label} but its credential is not active. "
            f"Reconnect {label} in Integrations, then retry the upload."
        )
    elif unbound:
        status = "folders_unbound"
        reason = (
            f"{label} is connected, but {len(unbound)} matter folder(s) are not "
            "bound to it. Reprovision those matter folders in File Shares."
        )
    else:
        status = "ok"
        reason = f"{label} is connected and every matter folder is bound."

    return {
        "tenant_id": str(tenant_uuid),
        "configured_provider": provider,
        "credential_provider": token_provider,
        "credential_active": credential_active,
        "status": status,
        "reason": reason,
        "matter_count": len(matters),
        "bound_matter_folders": bound_count,
        "unbound_matters": unbound,
        "mutations_performed": False,
    }


async def audit_matter_cloud_folders(
    db: AsyncSession, tenant_id: str
) -> dict[str, Any]:
    """Return a JSON-serializable audit; provider failures make it incomplete."""
    tenant_uuid = uuid.UUID(str(tenant_id))
    tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_uuid))
    if tenant is None:
        raise ValueError(f"Tenant not found: {tenant_id}")
    matters = (
        (
            await db.execute(
                select(Matter)
                .where(Matter.tenant_id == tenant_uuid)
                .order_by(Matter.slug)
            )
        )
        .scalars()
        .all()
    )
    roots = (
        tenant.cloud_root_folder if isinstance(tenant.cloud_root_folder, dict) else {}
    )
    providers: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for provider in ("onedrive", "google_drive", "sharepoint"):
        root = roots.get(provider)
        if not root:
            providers[provider] = {
                "status": "not_connected",
                "root": None,
                "matters": [],
            }
            continue
        try:
            token = await get_fresh_token(
                db,
                str(tenant_uuid),
                "google" if provider == "google_drive" else "microsoft",
            )
            if not token:
                raise RuntimeError("provider token unavailable")
            children = await _list_children(provider, token, root)
            provider_rows = []
            for matter in matters:
                binding = (
                    matter.cloud_folder.get(provider)
                    if isinstance(matter.cloud_folder, dict)
                    else None
                )
                row = classify_matter_folders(
                    matter_id=str(matter.id),
                    matter_name=matter.matter_name,
                    matter_slug=matter.slug,
                    current_binding_id=(binding or {}).get("matter_folder_id")
                    if isinstance(binding, dict)
                    else None,
                    children=children,
                    matter_number=getattr(matter, "matter_number", None),
                )
                provider_rows.append(row)
                rows.append({"provider": provider, **row})
            providers[provider] = {
                "status": "ok",
                "root": root,
                "child_folder_count": len(children),
                "matters": provider_rows,
            }
        except Exception as exc:
            providers[provider] = {
                "status": "error",
                "root": root,
                "error": str(exc),
                "matters": [],
            }
    incomplete = any(item.get("status") == "error" for item in providers.values())
    return {
        "tenant_id": str(tenant_uuid),
        "status": "incomplete" if incomplete else "complete",
        "complete": not incomplete,
        "matter_count": len(matters),
        "providers": providers,
        "rows": rows,
        "mutations_performed": False,
    }
