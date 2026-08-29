from __future__ import annotations

import asyncio
import fnmatch
import io
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PureWindowsPath

import smbclient
from pypdf import PdfReader
from docx import Document

from clarity_agent.config import AgentConfig
from clarity_agent.db import FileLedger
from clarity_agent.smb_auth import ShareCredential, connect, session_kwargs
from clarity_agent.utils import (
    compute_short_hash,
    format_smb_path,
    truncate_snippet,
)

logger = logging.getLogger("clarity_agent.scanner")

LEGAL_EXTENSIONS = {".pdf", ".docx", ".doc", ".docm", ".rtf", ".txt", ".wpd", ".odt"}


def _normalized_extensions(values: list[str] | None) -> set[str]:
    if values is None:
        return set(LEGAL_EXTENSIONS)
    normalized: set[str] = set()
    for extension in values:
        value = str(extension).strip().lower()
        if value:
            normalized.add(value if value.startswith(".") else "." + value)
    return normalized


_MIME_MAP = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".docm": "application/vnd.ms-word.document.macroEnabled.12",
    ".rtf": "application/rtf",
    ".txt": "text/plain",
    ".wpd": "application/wordperfect",
    ".odt": "application/vnd.oasis.opendocument.text",
}


def _read_smb_prefix(path: str, max_bytes: int, **kwargs) -> bytes:
    """Blocking SMB read, intended for ``asyncio.to_thread``."""
    with smbclient.open_file(path, mode="rb", **kwargs) as handle:
        return handle.read(max_bytes)


def _extract_text(ext: str, content: bytes) -> str:
    """CPU-bound document extraction, intended for ``asyncio.to_thread``."""
    if ext == ".pdf":
        reader = PdfReader(io.BytesIO(content))
        pages = [page.extract_text() for page in reader.pages]
        return "\n".join(text for text in pages if text)
    if ext in (".docx", ".docm"):
        doc = Document(io.BytesIO(content))
        return "\n".join(paragraph.text for paragraph in doc.paragraphs)
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("latin-1")


@dataclass
class ScanResult:
    new_files: list[dict] = field(default_factory=list)
    changed_files: list[dict] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    unchanged_files: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class ChangeSet:
    new_files: list[dict] = field(default_factory=list)
    changed_files: list[dict] = field(default_factory=list)
    deleted_paths: list[str] = field(default_factory=list)
    unchanged_files: list[dict] = field(default_factory=list)


class SmbScanner:
    def __init__(self, config: AgentConfig, ledger: FileLedger):
        self.config = config
        self.ledger = ledger

    async def scan_share(
        self,
        share_config: dict,
        file_extensions: list[str] | None = None,
    ) -> ScanResult:
        server = share_config["server"]
        share = share_config["share"]
        share_id = share_config.get("share_id", f"{server}/{share}")
        max_depth = share_config.get("max_depth", 10)
        result = ScanResult()
        connection_cache: dict = {}

        credential = ShareCredential.from_share(share_config, self.config)
        operation_kwargs = {
            **session_kwargs(credential),
            "connection_cache": connection_cache,
        }
        session, error = await self._connect_smb(
            server, share, credential, connection_cache
        )
        if session is None and error:
            result.errors.append(error)
            await self._close_cache(connection_cache)
            return result

        try:
            # A share may be scoped to a subfolder rather than the whole export.
            root = format_smb_path(server, share, share_config.get("root_path") or "")
            current_files = []
            allowed_exts = _normalized_extensions(file_extensions)
            exclude_patterns = share_config.get("exclude_patterns") or []

            walker = self._walk_directory(
                session,
                root,
                max_depth=max_depth,
                allowed_extensions=allowed_exts,
                exclude_patterns=exclude_patterns,
                share_id=share_id,
                operation_kwargs=operation_kwargs,
            )
            try:
                async for finfo in walker:
                    finfo["share_id"] = share_id
                    current_files.append(finfo)
            except Exception as exc:
                logger.error("Error walking share \\\\%s\\%s: %s", server, share, exc)
                result.errors.append(str(exc))

            # Keep a partial SMB walk from being treated as a complete snapshot.
            result.errors.extend(walker.errors)

            changeset = await self._detect_changes(share_id, current_files)

            result.new_files = changeset.new_files
            result.changed_files = changeset.changed_files
            result.unchanged_files = changeset.unchanged_files

            # Deletions are only committed locally after the SaaS acknowledges
            # them. Keep these paths active in the ledger so a failed sync retries
            # them on the next scan.
            if not result.errors:
                result.deleted_files = changeset.deleted_paths

            return result
        finally:
            await self._close_cache(connection_cache)

    @staticmethod
    async def _close_cache(connection_cache: dict) -> None:
        await asyncio.to_thread(
            smbclient.reset_connection_cache,
            connection_cache=connection_cache,
            fail_on_error=False,
        )

    async def _connect_smb(
        self,
        server: str,
        share: str,
        credential: ShareCredential | None = None,
        connection_cache: dict | None = None,
    ) -> tuple[object | None, str | None]:
        """Open a session for a share. Returns ``(session, error_message)``.

        The error message is propagated verbatim to the SaaS so an admin sees
        "logon failure" or "network path not found" rather than a generic
        failure they cannot act on.
        """
        credential = credential or ShareCredential.from_share({}, self.config)
        try:
            session = await asyncio.to_thread(
                connect,
                server,
                credential,
                smbclient_module=smbclient,
                connection_cache=connection_cache,
            )
            return session, None
        except Exception as exc:
            message = (
                f"Failed to connect to \\\\{server}\\{share} "
                f"as {credential.describe}: {exc}"
            )
            logger.error("%s", message)
            return None, message

    def _walk_directory(
        self,
        session,
        path: str,
        max_depth: int = 10,
        allowed_extensions: set[str] | None = None,
        exclude_patterns: list[str] | None = None,
        share_id: str = "",
        operation_kwargs: dict | None = None,
    ) -> _AsyncFileIterator:
        return _AsyncFileIterator(
            self,
            session,
            path,
            max_depth,
            allowed_extensions,
            exclude_patterns,
            share_id,
            operation_kwargs,
        )

    async def _detect_changes(
        self, share_id: str, current_files: list[dict]
    ) -> ChangeSet:
        cs = ChangeSet()
        known_paths = await self.ledger.get_all_paths(share_id)
        existing_files = await self.ledger.get_files([f["path"] for f in current_files])

        for finfo in current_files:
            existing = existing_files.get(finfo["path"])
            if existing is None:
                cs.new_files.append(finfo)
            elif (
                existing.get("content_hash") != finfo.get("content_hash")
                or existing.get("size_bytes") != finfo.get("size_bytes")
                or existing.get("modified_time") != finfo.get("modified_time")
                or existing.get("is_deleted")
            ):
                cs.changed_files.append(finfo)
            else:
                cs.unchanged_files.append(finfo)

        scanned_paths = {f["path"] for f in current_files}
        for path in known_paths - scanned_paths:
            cs.deleted_paths.append(path)

        return cs

    async def _compute_short_hash(
        self, session, path: str, operation_kwargs: dict | None = None
    ) -> str:
        try:
            data = await asyncio.to_thread(
                _read_smb_prefix, path, 4096, **(operation_kwargs or {})
            )
            return compute_short_hash(data)
        except Exception as exc:
            logger.warning("Failed to hash %s: %s", path, exc)
            return ""

    async def _extract_snippet(
        self,
        session,
        path: str,
        max_chars: int = 500,
        operation_kwargs: dict | None = None,
    ) -> str:
        ext = PureWindowsPath(path).suffix.lower()
        try:
            content = await asyncio.to_thread(
                _read_smb_prefix, path, 512000, **(operation_kwargs or {})
            )
            text = await asyncio.to_thread(_extract_text, ext, content)
            return truncate_snippet(text, max_chars)
        except Exception as exc:
            logger.warning("Failed to extract snippet from %s: %s", path, exc)
            return ""


class _AsyncFileIterator:
    def __init__(
        self,
        scanner: SmbScanner,
        session,
        path: str,
        max_depth: int,
        allowed_extensions: set[str] | None = None,
        exclude_patterns: list[str] | None = None,
        share_id: str = "",
        operation_kwargs: dict | None = None,
    ):
        self.scanner = scanner
        self.session = session
        self.path = path
        self.max_depth = max_depth
        self.allowed_extensions = (
            set(LEGAL_EXTENSIONS) if allowed_extensions is None else allowed_extensions
        )
        self.exclude_patterns = exclude_patterns or []
        self.share_id = share_id
        self.operation_kwargs = operation_kwargs or {}
        self.errors: list[str] = []
        self._queue: asyncio.Queue[dict | None] = asyncio.Queue()

    def __aiter__(self):
        return self

    async def __anext__(self) -> dict:
        if not hasattr(self, "_task"):
            self._task = asyncio.create_task(self._walk())
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def _walk(self):
        try:
            await self._walk_dir(self.path, 0)
        finally:
            await self._queue.put(None)

    async def _walk_dir(self, dir_path: str, depth: int):
        if depth > self.max_depth:
            return
        try:
            dir_mtime = await self._get_dir_mtime(dir_path)
            cached_mtime = await self.scanner.ledger.get_dir_mtime(dir_path)
            skip = (
                dir_mtime is not None
                and cached_mtime is not None
                and dir_mtime == cached_mtime
            )

            entries = []
            try:
                entries = await asyncio.to_thread(
                    lambda: list(smbclient.scandir(dir_path, **self.operation_kwargs))
                )
            except Exception as exc:
                logger.warning("Cannot list %s: %s", dir_path, exc)
                self.errors.append(f"Cannot list {dir_path}: {exc}")
                return

            for entry in entries:
                if entry.is_dir():
                    child = dir_path + "\\" + entry.name
                    if self._excluded(entry.name, child):
                        logger.debug("Skipping excluded directory %s", child)
                        continue
                    await self._walk_dir(child, depth + 1)
                elif entry.is_file():
                    ext = os.path.splitext(entry.name)[1].lower()
                    if ext not in self.allowed_extensions:
                        continue
                    if self._excluded(entry.name, dir_path + "\\" + entry.name):
                        continue
                    if skip:
                        existing = await self.scanner.ledger.get_file(
                            dir_path + "\\" + entry.name
                        )
                        entry_size = getattr(entry, "st_size", 0)
                        entry_mtime = (
                            datetime.fromtimestamp(
                                entry.st_mtime, tz=timezone.utc
                            ).isoformat()
                            if getattr(entry, "st_mtime", 0)
                            else ""
                        )
                        if (
                            existing
                            and existing.get("content_hash")
                            and not existing.get("is_deleted")
                            and existing.get("size_bytes") == entry_size
                            and existing.get("modified_time") == entry_mtime
                        ):
                            # A directory mtime is only a shortcut for the
                            # expensive content work. Keep the cached file in
                            # this scan's complete snapshot; omitting it makes
                            # cleanup_deleted interpret an unchanged file as
                            # removed.
                            await self._queue.put(existing)
                            continue

                    fpath = dir_path + "\\" + entry.name
                    stat = entry
                    content_hash = await self.scanner._compute_short_hash(
                        self.session, fpath, self.operation_kwargs
                    )
                    snippet = await self.scanner._extract_snippet(
                        self.session, fpath, operation_kwargs=self.operation_kwargs
                    )

                    finfo = {
                        "path": fpath,
                        "share_id": self.share_id,
                        "filename": entry.name,
                        "ext": ext,
                        "mime_type": _MIME_MAP.get(ext, "application/octet-stream"),
                        "snippet": snippet,
                        "owner": getattr(stat, "file_owner", "") or "",
                        "size_bytes": stat.st_size if hasattr(stat, "st_size") else 0,
                        "modified_time": datetime.fromtimestamp(
                            stat.st_mtime, tz=timezone.utc
                        ).isoformat()
                        if hasattr(stat, "st_mtime") and stat.st_mtime
                        else None,
                        "created_time": datetime.fromtimestamp(
                            stat.st_ctime, tz=timezone.utc
                        ).isoformat()
                        if hasattr(stat, "st_ctime") and stat.st_ctime
                        else None,
                        "content_hash": content_hash,
                        "dir_mtime": dir_mtime or "",
                        "synced_at": datetime.now(tz=timezone.utc).isoformat(),
                        "is_deleted": False,
                    }
                    await self._queue.put(finfo)

            if dir_mtime:
                await self.scanner.ledger.upsert_file(
                    {
                        "path": dir_path,
                        "share_id": self.share_id,
                        "filename": PureWindowsPath(dir_path).name,
                        "ext": None,
                        "mime_type": None,
                        "snippet": None,
                        "owner": None,
                        "size_bytes": None,
                        "modified_time": dir_mtime,
                        "created_time": None,
                        "content_hash": None,
                        "dir_mtime": dir_mtime,
                        "synced_at": datetime.now(tz=timezone.utc).isoformat(),
                        "is_deleted": False,
                    }
                )
        except Exception as exc:
            logger.error("Error walking %s: %s", dir_path, exc)
            self.errors.append(f"Error walking {dir_path}: {exc}")

    def _excluded(self, name: str, full_path: str) -> bool:
        """True when a name or path matches any configured exclude glob."""
        for pattern in self.exclude_patterns:
            if not pattern:
                continue
            if fnmatch.fnmatch(name.lower(), pattern.lower()) or fnmatch.fnmatch(
                full_path.lower(), pattern.lower()
            ):
                return True
        return False

    async def _get_dir_mtime(self, dir_path: str) -> str | None:
        try:
            stat = await asyncio.to_thread(
                smbclient.stat, dir_path, **self.operation_kwargs
            )
            if hasattr(stat, "st_mtime") and stat.st_mtime:
                return datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat()
        except Exception:
            pass
        return None
