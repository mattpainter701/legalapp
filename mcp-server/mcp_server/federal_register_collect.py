"""Inventory and retain official GovInfo Federal Register monthly XML archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import httpx

from .ecfr_adapter import TRANSIENT_STATUS_CODES, USER_AGENT, request_json

JSON_ROOT = "https://www.govinfo.gov/bulkdata/json/FR"
DEFAULT_START_YEAR = 2000
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class FederalRegisterArchive:
    year: int
    month: int
    url: str
    expected_bytes: int
    last_modified: str | None = None

    @property
    def filename(self) -> str:
        return f"FR-{self.year:04d}-{self.month:02d}.zip"


def discover_archives(
    client: httpx.Client,
    *,
    start_year: int,
    end_year: int,
    delay_seconds: float = 0.05,
) -> list[FederalRegisterArchive]:
    root = request_json(client, JSON_ROOT)
    available_years = {
        int(item["name"])
        for item in root.get("files", [])
        if item.get("folder") and str(item.get("name", "")).isdigit()
    }
    archives: list[FederalRegisterArchive] = []
    for year in range(start_year, end_year + 1):
        if year not in available_years:
            continue
        year_payload = request_json(client, f"{JSON_ROOT}/{year}")
        months = sorted(
            int(item["name"])
            for item in year_payload.get("files", [])
            if item.get("folder") and str(item.get("name", "")).isdigit()
        )
        for month in months:
            month_payload = request_json(client, f"{JSON_ROOT}/{year}/{month:02d}")
            filename = f"FR-{year:04d}-{month:02d}.zip"
            match = next(
                (
                    item
                    for item in month_payload.get("files", [])
                    if not item.get("folder") and item.get("name") == filename
                ),
                None,
            )
            if match is not None:
                archives.append(
                    FederalRegisterArchive(
                        year=year,
                        month=month,
                        url=str(match["link"]),
                        expected_bytes=int(match["size"]),
                        last_modified=match.get("formattedLastModifiedTime"),
                    )
                )
            if delay_seconds > 0:
                time.sleep(delay_seconds)
    return archives


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _archive_members(path: Path) -> int:
    try:
        with ZipFile(path) as archive:
            members = [name for name in archive.namelist() if name.lower().endswith(".xml")]
    except BadZipFile as exc:
        raise RuntimeError(f"Invalid Federal Register ZIP: {path.name}") from exc
    if not members:
        raise RuntimeError(f"Federal Register ZIP contains no XML: {path.name}")
    return len(members)


def download_archive(
    client: httpx.Client,
    archive: FederalRegisterArchive,
    target_dir: Path,
    *,
    max_bytes: int = MAX_ARCHIVE_BYTES,
    retries: int = 3,
) -> dict:
    if archive.expected_bytes > max_bytes:
        raise RuntimeError(
            f"Federal Register archive exceeds {max_bytes} byte bound: {archive.filename}"
        )
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / archive.filename
    if path.exists() and path.stat().st_size == archive.expected_bytes:
        try:
            member_count = _archive_members(path)
        except RuntimeError:
            path.unlink()
        else:
            return {
                **asdict(archive),
                "filename": archive.filename,
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
                "xml_member_count": member_count,
                "status": "reused",
            }

    partial = path.with_suffix(f"{path.suffix}.part")
    if partial.exists() and partial.stat().st_size > archive.expected_bytes:
        partial.unlink()
    if partial.exists() and partial.stat().st_size == archive.expected_bytes:
        try:
            member_count = _archive_members(partial)
        except RuntimeError:
            partial.unlink()
        else:
            partial.replace(path)
            return {
                **asdict(archive),
                "filename": archive.filename,
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
                "xml_member_count": member_count,
                "status": "resumed",
            }
    for attempt in range(max(1, retries)):
        existing = partial.stat().st_size if partial.exists() else 0
        headers = {"Range": f"bytes={existing}-"} if existing else {}
        response: httpx.Response | None = None
        try:
            with client.stream("GET", archive.url, headers=headers) as response:
                if (
                    response.status_code in TRANSIENT_STATUS_CODES
                    and attempt + 1 < retries
                ):
                    time.sleep(min(60.0, float(2**attempt)))
                    continue
                response.raise_for_status()
                content_range = response.headers.get("content-range", "")
                range_match = re.match(r"bytes\s+(\d+)-\d+/\d+", content_range)
                append = (
                    existing > 0
                    and response.status_code == 206
                    and range_match is not None
                    and int(range_match.group(1)) == existing
                )
                if not append:
                    existing = 0
                with partial.open("ab" if append else "wb") as handle:
                    total = existing
                    for block in response.iter_bytes():
                        total += len(block)
                        if total > max_bytes or total > archive.expected_bytes:
                            raise RuntimeError(
                                f"Federal Register archive exceeded byte bound: {archive.filename}"
                            )
                        handle.write(block)
        except httpx.RequestError:
            if attempt + 1 >= retries:
                raise
            time.sleep(min(60.0, float(2**attempt)))
            continue
        if partial.stat().st_size == archive.expected_bytes:
            partial.replace(path)
            return {
                **asdict(archive),
                "filename": archive.filename,
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
                "xml_member_count": _archive_members(path),
                "status": "downloaded",
            }
        if attempt + 1 >= retries:
            raise IOError(
                f"Incomplete Federal Register archive {archive.filename}: "
                f"expected {archive.expected_bytes}, got {partial.stat().st_size}"
            )
    raise AssertionError("unreachable")


def collect_archives(
    client: httpx.Client,
    archives: list[FederalRegisterArchive],
    target_dir: Path,
    *,
    workers: int = 4,
) -> dict:
    results: list[dict | None] = [None] * len(archives)

    def collect_one(archive: FederalRegisterArchive) -> dict:
        try:
            return download_archive(client, archive, target_dir)
        except Exception as exc:
            return {
                **asdict(archive),
                "filename": archive.filename,
                "status": "failed",
                "error": str(exc)[-2000:],
            }

    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as executor:
        pending = {
            executor.submit(collect_one, archive): index
            for index, archive in enumerate(archives)
        }
        for future in as_completed(pending):
            results[pending[future]] = future.result()
    completed = [item for item in results if item is not None]
    failures = [item for item in completed if item["status"] == "failed"]
    return {
        "source_key": "govinfo:federal-register",
        "source_url": "https://www.govinfo.gov/bulkdata/FR",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "status": "partial_failure" if failures else "succeeded",
        "archive_count": len(completed),
        "failed_count": len(failures),
        "expected_bytes": sum(item.expected_bytes for item in archives),
        "retained_bytes": sum(item.get("bytes", 0) for item in completed),
        "xml_issue_count": sum(item.get("xml_member_count", 0) for item in completed),
        "archives": completed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inventory or retain GovInfo Federal Register monthly XML archives"
    )
    parser.add_argument("--start-year", type=int, default=DEFAULT_START_YEAR)
    parser.add_argument("--end-year", type=int, default=datetime.now().year)
    parser.add_argument("--delay", type=float, default=0.05)
    parser.add_argument("--workers", type=int, default=4, help="parallel downloads, capped at 8")
    parser.add_argument("--limit", type=int, help="maximum monthly archives after discovery")
    parser.add_argument("--download-dir")
    parser.add_argument("--report-path")
    args = parser.parse_args()
    if args.end_year < args.start_year:
        parser.error("--end-year must be greater than or equal to --start-year")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")
    with httpx.Client(
        timeout=120,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json, application/zip"},
    ) as client:
        archives = discover_archives(
            client,
            start_year=args.start_year,
            end_year=args.end_year,
            delay_seconds=max(0.0, args.delay),
        )
        archives = archives[: args.limit] if args.limit is not None else archives
        if args.download_dir:
            report = collect_archives(
                client,
                archives,
                Path(args.download_dir),
                workers=args.workers,
            )
        else:
            report = {
                "source_key": "govinfo:federal-register",
                "source_url": "https://www.govinfo.gov/bulkdata/FR",
                "status": "inventory",
                "archive_count": len(archives),
                "expected_bytes": sum(item.expected_bytes for item in archives),
                "archives": [asdict(item) | {"filename": item.filename} for item in archives],
            }
    rendered = json.dumps(report, indent=2)
    if args.report_path:
        report_path = Path(args.report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered + "\n", encoding="utf-8")
        console_report = {key: value for key, value in report.items() if key != "archives"}
        console_report["report_path"] = str(report_path)
        print(json.dumps(console_report, indent=2))
    else:
        print(rendered)
    if report.get("failed_count"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
