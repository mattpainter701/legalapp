"""Bounded, metadata-only cloud inventory for storage migration."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import httpx
from dateutil import parser as dateutil_parser

from app.services.token_vault import get_fresh_token

MAX_PAGES = 10_000
MAX_MARKER_BYTES = 4096
GOOGLE_FILES = "https://www.googleapis.com/drive/v3/files"
GRAPH = "https://graph.microsoft.com/v1.0"


def _time(value):
    if not value:
        return None
    try:
        return dateutil_parser.isoparse(str(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_graph_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and parsed.hostname == "graph.microsoft.com"


async def _read_marker(response: httpx.Response) -> dict:
    if response.status_code != 200:
        raise ValueError("Matter marker could not be read safely")
    data = bytearray()
    async for chunk in response.aiter_bytes():
        data.extend(chunk)
        if len(data) > MAX_MARKER_BYTES:
            raise ValueError("Matter marker could not be read safely")
    try:
        value = json.loads(bytes(data).decode("utf-8"))
    except (UnicodeDecodeError, TypeError, ValueError) as exc:
        raise ValueError("Matter marker is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Matter marker is invalid JSON")
    return value


class StorageDiscovery:
    async def discover(
        self, db, tenant_id: str, provider: str, root: dict | None
    ) -> list[dict[str, Any]]:
        if provider == "google_drive":
            return await self._google(db, tenant_id, root or {})
        if provider in {"onedrive", "sharepoint"}:
            return await self._graph(db, tenant_id, provider, root or {})
        raise ValueError(f"Unsupported target cloud provider: {provider}")

    async def _google(self, db, tenant_id: str, root: dict) -> list[dict[str, Any]]:
        token = await get_fresh_token(db, tenant_id, "google")
        if not token:
            raise ValueError("Google credentials are unavailable")
        root_id = root.get("id") or root.get("folder_id")
        if not root_id:
            raise ValueError("Target Google Drive root is not bound")
        headers = {"Authorization": f"Bearer {token}"}
        fields = "id,name,mimeType,parents,webViewLink,driveId,modifiedTime,createdTime,size,sha256Checksum,md5Checksum,version,etag"
        result: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        marker_parents: set[str] = set()
        queue = [(str(root_id), str(root.get("path") or ""))]
        async with httpx.AsyncClient(timeout=30) as client:
            root_response = await client.get(
                f"{GOOGLE_FILES}/{root_id}",
                headers=headers,
                params={"fields": fields, "supportsAllDrives": True},
            )
            if root_response.status_code != 200:
                raise ValueError(
                    f"Google Drive root validation failed: {root_response.status_code}"
                )
            root_item = root_response.json()
            if root_item.get("mimeType") != "application/vnd.google-apps.folder":
                raise ValueError("Google Drive target root is not a folder")
            drive_id = root_item.get("driveId") or root.get("drive_id")
            if not root.get("path"):
                queue[0] = (str(root_id), str(root_item.get("name") or ""))
            base_params: dict[str, Any] = {
                "includeItemsFromAllDrives": True,
                "supportsAllDrives": True,
                "pageSize": 1000,
                "fields": f"nextPageToken,incompleteSearch,files({fields})",
                "corpora": "drive" if drive_id else "user",
            }
            if drive_id:
                base_params["driveId"] = drive_id
            while queue:
                parent_id, parent_path = queue.pop(0)
                page_token = None
                page_seen: set[str] = set()
                for _ in range(MAX_PAGES):
                    params = dict(base_params)
                    escaped = str(parent_id).replace("\\", "\\\\").replace("'", "\\'")
                    params["q"] = f"'{escaped}' in parents and trashed = false"
                    if page_token:
                        if page_token in page_seen:
                            raise ValueError("Google Drive pagination cycle detected")
                        page_seen.add(page_token)
                        params["pageToken"] = page_token
                    response = await client.get(
                        GOOGLE_FILES, headers=headers, params=params
                    )
                    if response.status_code != 200:
                        raise ValueError(
                            f"Google Drive inventory failed: {response.status_code}"
                        )
                    payload = response.json()
                    if payload.get("incompleteSearch"):
                        raise ValueError("Google Drive inventory was incomplete")
                    for item in payload.get("files", []):
                        normalized = self._google_item(item, parent_id, parent_path)
                        if not normalized["id"]:
                            continue
                        if normalized["id"] in seen_ids:
                            raise ValueError(
                                "Google Drive inventory contained duplicate IDs"
                            )
                        seen_ids.add(normalized["id"])
                        if normalized["name"] == ".lawhand-matter.json":
                            marker_parent = normalized["parent_id"] or parent_id
                            if marker_parent in marker_parents:
                                raise ValueError(
                                    "Duplicate matter markers found for folder"
                                )
                            marker_parents.add(marker_parent)
                            marker_response = await client.get(
                                f"{GOOGLE_FILES}/{normalized['id']}",
                                headers=headers,
                                params={"alt": "media", "supportsAllDrives": True},
                            )
                            marker = await _read_marker(marker_response)
                            for prior in reversed(result):
                                if prior["id"] == marker_parent:
                                    prior["marker"] = marker
                                    break
                        if normalized["is_folder"]:
                            queue.append((normalized["id"], normalized["path"]))
                        result.append(normalized)
                    page_token = payload.get("nextPageToken")
                    if not page_token:
                        break
                else:
                    raise ValueError("Google Drive pagination exceeded safety bound")
        return result

    @staticmethod
    def _google_item(item: dict, root_id: str | None, parent_path: str = "") -> dict:
        name = item.get("name", "")
        return {
            "id": item.get("id", ""),
            "name": name,
            "path": f"{parent_path}/{name}".strip("/"),
            "parent_id": (item.get("parents") or [root_id])[0],
            "is_folder": item.get("mimeType") == "application/vnd.google-apps.folder",
            "size": int(item["size"]) if item.get("size") else None,
            "sha256": item.get("sha256Checksum"),
            "md5": item.get("md5Checksum"),
            "etag": item.get("etag"),
            "version": item.get("version"),
            "mime_type": item.get("mimeType"),
            "modified_time": _time(item.get("modifiedTime")),
            "drive_id": item.get("driveId"),
            "marker": None,
            "url": item.get("webViewLink"),
        }

    async def _graph(
        self, db, tenant_id: str, provider: str, root: dict
    ) -> list[dict[str, Any]]:
        token = await get_fresh_token(db, tenant_id, "microsoft")
        if not token:
            raise ValueError("Microsoft credentials are unavailable")
        root_id = root.get("id") or root.get("folder_id")
        drive_id = root.get("drive_id")
        if not root_id:
            raise ValueError("Target Microsoft Drive root is not bound")
        if provider == "sharepoint" and not drive_id:
            raise ValueError("SharePoint drive_id is required")
        base = f"{GRAPH}/drives/{drive_id}" if drive_id else f"{GRAPH}/me/drive"
        headers = {"Authorization": f"Bearer {token}"}
        fields = (
            "id,name,parentReference,file,folder,size,webUrl,fileSystemInfo,eTag,cTag"
        )
        result: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        marker_parents: set[str] = set()
        queue = [
            (f"{base}/items/{root_id}/children", root_id, str(root.get("path") or ""))
        ]
        async with httpx.AsyncClient(timeout=30) as client:
            root_response = await client.get(
                f"{base}/items/{root_id}", headers=headers, params={"$select": fields}
            )
            if root_response.status_code != 200:
                raise ValueError(
                    f"{provider} root validation failed: {root_response.status_code}"
                )
            if "folder" not in root_response.json():
                raise ValueError(f"{provider} target root is not a folder")
            if not root.get("path"):
                queue[0] = (
                    queue[0][0],
                    str(root_response.json().get("name") or ""),
                    queue[0][2],
                )
            seen_urls: set[str] = set()
            for _ in range(MAX_PAGES):
                if not queue:
                    break
                url, parent_id, parent_path = queue.pop(0)
                if url in seen_urls:
                    raise ValueError("Microsoft Drive pagination cycle detected")
                seen_urls.add(url)
                response = await client.get(
                    url,
                    headers=headers,
                    params={"$top": 999, "$select": fields}
                    if url.endswith("/children")
                    else None,
                )
                if response.status_code != 200:
                    raise ValueError(
                        f"Microsoft Drive inventory failed: {response.status_code}"
                    )
                payload = response.json()
                for item in payload.get("value", []):
                    normalized = self._graph_item(
                        item, parent_id, parent_path, drive_id
                    )
                    if not normalized["id"]:
                        continue
                    if normalized["id"] in seen_ids:
                        raise ValueError(
                            "Microsoft Drive inventory contained duplicate IDs"
                        )
                    seen_ids.add(normalized["id"])
                    if normalized["name"] == ".lawhand-matter.json":
                        marker_parent = normalized["parent_id"] or parent_id
                        if marker_parent in marker_parents:
                            raise ValueError(
                                "Duplicate matter markers found for folder"
                            )
                        marker_parents.add(marker_parent)
                        marker_response = await client.get(
                            f"{base}/items/{normalized['id']}/content",
                            headers=headers,
                            follow_redirects=True,
                        )
                        marker = await _read_marker(marker_response)
                        for prior in reversed(result):
                            if prior["id"] == marker_parent:
                                prior["marker"] = marker
                                break
                    if normalized["is_folder"]:
                        queue.append(
                            (
                                f"{base}/items/{normalized['id']}/children",
                                normalized["id"],
                                normalized["path"],
                            )
                        )
                    result.append(normalized)
                continuation = payload.get("@odata.nextLink")
                if continuation:
                    if not _safe_graph_url(continuation):
                        raise ValueError(
                            "Microsoft Drive continuation URL is not trusted"
                        )
                    queue.append((continuation, parent_id, parent_path))
            else:
                raise ValueError("Microsoft Drive pagination exceeded safety bound")
        return result

    @staticmethod
    def _graph_item(
        item: dict, parent_id: str, parent_path: str, drive_id: str | None
    ) -> dict:
        name = item.get("name", "")
        file_data = item.get("file") or {}
        hashes = file_data.get("hashes") or {}
        parent_ref = item.get("parentReference") or {}
        modified = (item.get("fileSystemInfo") or {}).get("lastModifiedDateTime")
        return {
            "id": item.get("id", ""),
            "name": name,
            "path": f"{parent_path}/{name}".strip("/"),
            "parent_id": parent_ref.get("id", parent_id),
            "is_folder": "folder" in item,
            "size": item.get("size"),
            "sha256": hashes.get("sha256Hash"),
            "etag": item.get("eTag"),
            "version": item.get("version") or item.get("cTag"),
            "mime_type": file_data.get("mimeType"),
            "modified_time": _time(modified),
            "drive_id": parent_ref.get("driveId") or drive_id,
            "marker": None,
            "url": item.get("webUrl"),
        }
