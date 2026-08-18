#!/usr/bin/env python3
"""Create and safely verify an immutable LegalApp uploads archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

CHUNK_SIZE = 1024 * 1024
ARCHIVE_PREFIX = "uploads"


class ArtifactError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceFile:
    path: Path
    relative: str
    device: int
    inode: int
    size: int
    mtime_ns: int
    mode: int


class HashingReader:
    def __init__(self, raw: BinaryIO) -> None:
        self.raw = raw
        self.digest = hashlib.sha256()
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        data = self.raw.read(size)
        self.digest.update(data)
        self.bytes_read += len(data)
        return data


def _relative_name(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ArtifactError(f"unsafe upload path: {relative!s}")
    name = PurePosixPath(*relative.parts).as_posix()
    if "\\" in name or "\x00" in name:
        raise ArtifactError(f"upload path is not portable: {name!r}")
    return name


def _scan_source(root: Path) -> list[SourceFile]:
    if not root.exists() or not root.is_dir() or root.is_symlink():
        raise ArtifactError("uploads source must be an existing, non-symlink directory")

    files: list[SourceFile] = []
    for current, dirs, names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for directory in dirs:
            candidate = current_path / directory
            if candidate.is_symlink():
                raise ArtifactError(
                    f"symlinked upload directory is forbidden: {candidate}"
                )
        for name in names:
            candidate = current_path / name
            metadata = candidate.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise ArtifactError(
                    f"only regular upload files are supported: {candidate}"
                )
            files.append(
                SourceFile(
                    path=candidate,
                    relative=_relative_name(root, candidate),
                    device=metadata.st_dev,
                    inode=metadata.st_ino,
                    size=metadata.st_size,
                    mtime_ns=metadata.st_mtime_ns,
                    mode=stat.S_IMODE(metadata.st_mode),
                )
            )
    files.sort(key=lambda item: item.relative)
    return files


def _fingerprint(files: list[SourceFile]) -> list[tuple[str, int, int, int, int]]:
    return [
        (item.relative, item.device, item.inode, item.size, item.mtime_ns)
        for item in files
    ]


def create_artifact(
    source: Path, archive: Path, manifest: Path, archive_prefix: str = ARCHIVE_PREFIX
) -> None:
    if not archive_prefix or "/" in archive_prefix or "\\" in archive_prefix:
        raise ArtifactError("archive prefix must be a single portable path component")
    source = source.resolve(strict=True)
    archive = archive.resolve(strict=False)
    manifest = manifest.resolve(strict=False)
    if archive == manifest or source in archive.parents or source in manifest.parents:
        raise ArtifactError("backup outputs must be outside the uploads source")
    archive.parent.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists() or manifest.exists():
        raise ArtifactError("refusing to overwrite an existing upload backup artifact")

    initial = _scan_source(source)
    archive_fd, archive_tmp_name = tempfile.mkstemp(
        prefix=f".{archive.name}.", dir=archive.parent
    )
    manifest_fd, manifest_tmp_name = tempfile.mkstemp(
        prefix=f".{manifest.name}.", dir=manifest.parent
    )
    os.close(archive_fd)
    os.close(manifest_fd)
    archive_tmp = Path(archive_tmp_name)
    manifest_tmp = Path(manifest_tmp_name)
    records: list[dict[str, object]] = []

    try:
        with tarfile.open(archive_tmp, mode="w", format=tarfile.PAX_FORMAT) as bundle:
            for item in initial:
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(item.path, flags)
                try:
                    opened = os.fstat(descriptor)
                    opened_fingerprint = (
                        opened.st_dev,
                        opened.st_ino,
                        opened.st_size,
                        opened.st_mtime_ns,
                    )
                    expected_fingerprint = (
                        item.device,
                        item.inode,
                        item.size,
                        item.mtime_ns,
                    )
                    if opened_fingerprint != expected_fingerprint:
                        raise ArtifactError(
                            f"upload changed before archival: {item.relative}"
                        )

                    with os.fdopen(descriptor, "rb", closefd=False) as raw:
                        reader = HashingReader(raw)
                        info = tarfile.TarInfo(name=f"{archive_prefix}/{item.relative}")
                        info.size = item.size
                        info.mode = item.mode & 0o777
                        info.mtime = item.mtime_ns // 1_000_000_000
                        info.uid = 0
                        info.gid = 0
                        info.uname = ""
                        info.gname = ""
                        bundle.addfile(info, reader)
                    after = os.fstat(descriptor)
                    if (
                        reader.bytes_read != item.size
                        or (
                            after.st_dev,
                            after.st_ino,
                            after.st_size,
                            after.st_mtime_ns,
                        )
                        != expected_fingerprint
                    ):
                        raise ArtifactError(
                            f"upload changed during archival: {item.relative}"
                        )
                    records.append(
                        {
                            "path": item.relative,
                            "sha256": reader.digest.hexdigest(),
                            "size": item.size,
                        }
                    )
                finally:
                    os.close(descriptor)

        if _fingerprint(_scan_source(source)) != _fingerprint(initial):
            raise ArtifactError("uploads changed while the backup artifact was created")

        manifest_tmp.write_text(
            json.dumps(
                {"schema_version": 1, "files": records},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.chmod(archive_tmp, 0o600)
        os.chmod(manifest_tmp, 0o600)
        os.replace(archive_tmp, archive)
        os.replace(manifest_tmp, manifest)
    except Exception:
        archive_tmp.unlink(missing_ok=True)
        manifest_tmp.unlink(missing_ok=True)
        archive.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        raise


def _safe_manifest_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ArtifactError("manifest contains an invalid path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ArtifactError(f"manifest contains an unsafe path: {value!r}")
    if "\\" in value or "\x00" in value:
        raise ArtifactError(f"manifest contains a non-portable path: {value!r}")
    return value


def verify_artifact(
    archive: Path,
    manifest: Path,
    extract_dir: Path,
    archive_prefix: str = ARCHIVE_PREFIX,
) -> None:
    if not archive_prefix or "/" in archive_prefix or "\\" in archive_prefix:
        raise ArtifactError("archive prefix must be a single portable path component")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("files"), list):
        raise ArtifactError("unsupported upload manifest")

    expected: dict[str, tuple[str, int]] = {}
    ordered_paths: list[str] = []
    for record in payload["files"]:
        if not isinstance(record, dict):
            raise ArtifactError("upload manifest contains an invalid record")
        path = _safe_manifest_path(record.get("path"))
        digest = record.get("sha256")
        size = record.get("size")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
        ):
            raise ArtifactError(f"upload manifest metadata is invalid for {path}")
        if path in expected:
            raise ArtifactError(f"duplicate upload manifest path: {path}")
        expected[path] = (digest, size)
        ordered_paths.append(path)
    if ordered_paths != sorted(ordered_paths):
        raise ArtifactError("upload manifest paths are not sorted")

    extract_dir = extract_dir.resolve(strict=False)
    if extract_dir.exists() and any(extract_dir.iterdir()):
        raise ArtifactError("upload verification target must be empty")
    extract_dir.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    with tarfile.open(archive, mode="r:*") as bundle:
        for member in bundle:
            if not member.isfile():
                raise ArtifactError(f"unsafe non-file archive member: {member.name!r}")
            member_path = PurePosixPath(member.name)
            if (
                member_path.is_absolute()
                or len(member_path.parts) < 2
                or member_path.parts[0] != archive_prefix
                or any(part in {"", ".", ".."} for part in member_path.parts)
                or "\\" in member.name
            ):
                raise ArtifactError(f"unsafe upload archive path: {member.name!r}")
            relative = PurePosixPath(*member_path.parts[1:]).as_posix()
            _safe_manifest_path(relative)
            if relative in seen or relative not in expected:
                raise ArtifactError(
                    f"unexpected or duplicate upload archive path: {relative}"
                )
            seen.add(relative)

            expected_digest, expected_size = expected[relative]
            if member.size != expected_size:
                raise ArtifactError(f"upload size differs from manifest: {relative}")
            destination = extract_dir.joinpath(*PurePosixPath(relative).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if (
                extract_dir != destination.resolve(strict=False)
                and extract_dir not in destination.resolve(strict=False).parents
            ):
                raise ArtifactError(f"upload extraction escaped target: {relative}")
            source = bundle.extractfile(member)
            if source is None:
                raise ArtifactError(
                    f"upload archive member could not be read: {relative}"
                )
            digest = hashlib.sha256()
            written = 0
            with destination.open("xb") as output:
                while chunk := source.read(CHUNK_SIZE):
                    output.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
            if written != expected_size or digest.hexdigest() != expected_digest:
                raise ArtifactError(f"upload hash differs from manifest: {relative}")

    if seen != set(expected):
        missing = sorted(set(expected) - seen)
        raise ArtifactError(f"upload archive is missing manifest paths: {missing[:3]}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--source", type=Path, required=True)
    create.add_argument("--archive", type=Path, required=True)
    create.add_argument("--manifest", type=Path, required=True)
    create.add_argument("--archive-prefix", default=ARCHIVE_PREFIX)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--extract-dir", type=Path, required=True)
    verify.add_argument("--archive-prefix", default=ARCHIVE_PREFIX)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "create":
            create_artifact(
                args.source, args.archive, args.manifest, args.archive_prefix
            )
        else:
            verify_artifact(
                args.archive, args.manifest, args.extract_dir, args.archive_prefix
            )
    except (ArtifactError, OSError, tarfile.TarError, json.JSONDecodeError) as exc:
        print(f"upload backup artifact error: {exc}", file=os.sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
