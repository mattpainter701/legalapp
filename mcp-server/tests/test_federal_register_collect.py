import sys
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server.federal_register_collect import (  # noqa: E402
    FederalRegisterArchive,
    discover_archives,
    download_archive,
)


def test_discovers_one_monthly_zip_instead_of_duplicate_daily_xml():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/json/FR"):
            return httpx.Response(200, json={"files": [{"name": "2025", "folder": True}]})
        if path.endswith("/json/FR/2025"):
            return httpx.Response(200, json={"files": [{"name": "01", "folder": True}]})
        return httpx.Response(
            200,
            json={
                "files": [
                    {
                        "name": "FR-2025-01-02.xml",
                        "folder": False,
                        "size": 500,
                        "link": "https://www.govinfo.gov/bulkdata/FR/2025/01/day.xml",
                    },
                    {
                        "name": "FR-2025-01.zip",
                        "folder": False,
                        "size": 100,
                        "link": "https://www.govinfo.gov/bulkdata/FR/2025/01/FR-2025-01.zip",
                        "formattedLastModifiedTime": "01-Feb-2025 00:00",
                    },
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        archives = discover_archives(
            client,
            start_year=2025,
            end_year=2025,
            delay_seconds=0,
        )

    assert archives == [
        FederalRegisterArchive(
            2025,
            1,
            "https://www.govinfo.gov/bulkdata/FR/2025/01/FR-2025-01.zip",
            100,
            "01-Feb-2025 00:00",
        )
    ]


def test_download_rejects_archive_above_bound(tmp_path):
    archive = FederalRegisterArchive(2025, 1, "https://example.gov/file.zip", 101)
    with httpx.Client() as client:
        with pytest.raises(RuntimeError, match="byte bound"):
            download_archive(client, archive, tmp_path, max_bytes=100)


def test_download_restarts_when_server_returns_wrong_content_range(tmp_path):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive_file:
        archive_file.writestr("FR-2025-01-02.xml", "<FEDREG />")
    payload = buffer.getvalue()
    archive = FederalRegisterArchive(2025, 1, "https://example.gov/file.zip", len(payload))
    partial = tmp_path / "FR-2025-01.zip.part"
    partial.write_bytes(b"stale")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["range"] == "bytes=5-"
        return httpx.Response(
            206,
            content=payload,
            headers={"Content-Range": f"bytes 0-{len(payload) - 1}/{len(payload)}"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = download_archive(client, archive, tmp_path)

    assert result["status"] == "downloaded"
    assert result["xml_member_count"] == 1
    assert (tmp_path / archive.filename).read_bytes() == payload
