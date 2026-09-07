"""Small tenant/matter identity anchors stored alongside customer documents."""

import json
from urllib.parse import quote

import httpx

MARKER_NAME = ".lawhand-matter.json"
MAX_MARKER_BYTES = 4096


class MatterMarkerConflict(ValueError):
    """The folder is owned by another matter, or its marker cannot be trusted."""


def validate_marker(content: bytes, expected: dict) -> None:
    try:
        if len(content) > MAX_MARKER_BYTES:
            raise ValueError("oversize")
        value = json.loads(content)
        if not isinstance(value, dict) or any(
            value.get(key) != expected[key]
            for key in ("schema_version", "tenant_id", "matter_id")
        ):
            raise ValueError("identity mismatch")
    except (ValueError, TypeError, KeyError) as exc:
        raise MatterMarkerConflict(
            "The folder marker does not identify this tenant and matter; choose the correct folder."
        ) from exc


async def _bounded_content(client, url, *, headers, params=None):
    async with client.stream("GET", url, headers=headers, params=params) as response:
        response.raise_for_status()
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > MAX_MARKER_BYTES:
                raise MatterMarkerConflict(
                    "The folder identity marker exceeds 4096 bytes"
                )
        return bytes(content)


async def ensure_marker(token: str, provider: str, binding: dict, marker: dict) -> None:
    """Create an absent marker; validate existing markers without overwriting them."""
    headers = {"Authorization": f"Bearer {token}"}
    folder_id = quote(str(binding["matter_folder_id"]), safe="")
    payload = json.dumps(marker, separators=(",", ":")).encode()
    if len(payload) > MAX_MARKER_BYTES:
        raise ValueError("Matter marker is too large")
    async with httpx.AsyncClient(timeout=30) as client:
        if provider == "google_drive":
            base = "https://www.googleapis.com/drive/v3/files"
            # IDs come from provider metadata, but are still escaped as query literals.
            parent = (
                str(binding["matter_folder_id"])
                .replace("\\", "\\\\")
                .replace("'", "\\'")
            )
            response = await client.get(
                base,
                headers=headers,
                params={
                    "q": f"'{parent}' in parents and name='{MARKER_NAME}' and trashed=false",
                    "fields": "files(id),nextPageToken",
                    "pageSize": 2,
                    "supportsAllDrives": "true",
                    "includeItemsFromAllDrives": "true",
                },
            )
            response.raise_for_status()
            data = response.json()
            items = data.get("files", [])
            if len(items) > 1 or data.get("nextPageToken"):
                raise MatterMarkerConflict(
                    "Multiple matter identity markers exist in this folder"
                )
            if items:
                content = await _bounded_content(
                    client,
                    f"{base}/{quote(items[0]['id'], safe='')}",
                    headers=headers,
                    params={"alt": "media", "supportsAllDrives": "true"},
                )
                validate_marker(content, marker)
                return
            boundary = "lawhand_matter_marker"
            metadata = json.dumps(
                {
                    "name": MARKER_NAME,
                    "mimeType": "application/json",
                    "parents": [binding["matter_folder_id"]],
                }
            )
            body = (
                (
                    f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{metadata}\r\n--{boundary}\r\nContent-Type: application/json\r\n\r\n"
                ).encode()
                + payload
                + f"\r\n--{boundary}--\r\n".encode()
            )
            response = await client.post(
                "https://www.googleapis.com/upload/drive/v3/files",
                headers={
                    **headers,
                    "Content-Type": f"multipart/related; boundary={boundary}",
                },
                params={"uploadType": "multipart", "supportsAllDrives": "true"},
                content=body,
            )
            response.raise_for_status()
            return
        if provider not in ("onedrive", "sharepoint"):
            raise ValueError("Unsupported marker provider")
        drive = binding.get("drive_id")
        base = (
            f"https://graph.microsoft.com/v1.0/drives/{quote(str(drive), safe='')}"
            if drive
            else "https://graph.microsoft.com/v1.0/me/drive"
        )
        item_url = f"{base}/items/{folder_id}:/{MARKER_NAME}"
        response = await client.get(item_url, headers=headers)
        if response.status_code == 200:
            # Graph content uses a preauthenticated redirect; credentials must not
            # be forwarded to it. Read the size-bounded provider download URL.
            item = response.json()
            url = item.get("@microsoft.graph.downloadUrl")
            if not url or not url.startswith("https://"):
                raise MatterMarkerConflict(
                    "The provider did not return a readable folder marker"
                )
            content = await _bounded_content(client, url, headers={})
            validate_marker(content, marker)
            return
        if response.status_code != 404:
            response.raise_for_status()
        response = await client.put(
            f"{item_url}:/content",
            headers={**headers, "Content-Type": "application/json"},
            params={"@microsoft.graph.conflictBehavior": "fail"},
            content=payload,
        )
        if response.status_code == 409:
            raise MatterMarkerConflict(
                "A folder marker was created concurrently; retry the binding"
            )
        response.raise_for_status()
