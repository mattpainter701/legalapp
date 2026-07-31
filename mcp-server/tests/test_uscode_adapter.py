import io
import sys
import zipfile
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server.uscode_adapter import (
    USCodeArtifact,
    DownloadedArtifact,
    discover_artifacts,
    download_artifact,
    parse_uslm_sections,
)
from mcp_server.uscode_ingest import main as ingest_main

FIXTURES = Path(__file__).with_name("fixtures")


def _html_for_release(release: str, titles: tuple[int, ...]) -> str:
    congress, public_law = release.split("-")
    links = "".join(
        f'<a href="releasepoints/us/pl/{congress}/{public_law}/xml_usc{title:02d}@{release}.zip">Title {title}</a>'
        for title in titles
    )
    return f"<html><body>{links}</body></html>"


def test_discovery_selects_latest_release_and_requested_titles():
    html = _html_for_release("118-200", (26, 42)) + _html_for_release(
        "119-102", (26, 42)
    )

    artifacts = discover_artifacts(html, (26, 42))

    assert [artifact.title for artifact in artifacts] == [26, 42]
    assert {artifact.release_point for artifact in artifacts} == {"119-102"}
    assert artifacts[0].stable_prefix == "usc:119-102:title-26"
    assert artifacts[0].url.endswith("xml_usc26@119-102.zip")


def test_discovery_rejects_incomplete_latest_release():
    html = _html_for_release("119-102", (26,))

    with pytest.raises(RuntimeError, match="missing requested title"):
        discover_artifacts(html, (26, 42))


def test_download_is_bounded_and_hashes_zip_content():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("usc26.xml", "<uscDoc />")
    zip_content = buffer.getvalue()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=zip_content,
            headers={"content-length": str(len(zip_content))},
        )

    artifact = USCodeArtifact(
        title=26,
        release_point="119-102",
        congress=119,
        public_law_number=102,
        url="https://uscode.house.gov/download/releasepoints/us/pl/119/102/xml_usc26@119-102.zip",
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        downloaded = download_artifact(artifact, client=client, max_bytes=4096)

    assert downloaded.content == zip_content
    assert len(downloaded.sha256) == 64


def test_download_rejects_announced_oversized_artifact():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"PK", headers={"content-length": "5000"})

    artifact = USCodeArtifact(26, "119-102", 119, 102, "https://example.gov/usc26.zip")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="exceeds limit"):
            download_artifact(artifact, client=client, max_bytes=100)


def _downloaded_fixture() -> DownloadedArtifact:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("usc26.xml", (FIXTURES / "uscode_title_26.xml").read_bytes())
    artifact = USCodeArtifact(26, "119-102", 119, 102, "https://uscode.house.gov/example/usc26.zip")
    content = buffer.getvalue()
    import hashlib

    return DownloadedArtifact(artifact, content, hashlib.sha256(content).hexdigest())


def test_parse_uslm_fixture_emits_versioned_section_documents():
    sections = parse_uslm_sections(_downloaded_fixture())

    assert [section.section for section in sections] == ["1", "2A"]
    assert sections[0].external_id == "usc:119-102:title-26:section-1"
    assert sections[1].citation == "26 U.S.C. § 2A"
    assert "There is hereby imposed a tax" in sections[0].text
    document = sections[0].document(artifact_sha256="a" * 64)
    assert document["source_key"] == "federal:us-code"
    assert document["metadata"]["release_point"] == "119-102"
    assert document["metadata"]["artifact_sha256"] == "a" * 64


def test_parse_rejects_zip_path_traversal_and_doctype():
    artifact = USCodeArtifact(26, "119-102", 119, 102, "https://example.gov/usc26.zip")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../usc26.xml", "<uscDoc />")
    with pytest.raises(RuntimeError, match="unsafe entry path"):
        parse_uslm_sections(DownloadedArtifact(artifact, buffer.getvalue(), "a" * 64))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("usc26.xml", "<!DOCTYPE uscDoc><uscDoc />")
    with pytest.raises(RuntimeError, match="DTD"):
        parse_uslm_sections(DownloadedArtifact(artifact, buffer.getvalue(), "b" * 64))


def test_parser_keeps_fullest_official_node_for_duplicate_section_number():
    xml = b"""<uslm>
    <section><num value='210'>210</num><heading>Short.</heading></section>
    <section><num value='210'>210</num><heading>Full text.</heading>
    <content>This is the substantially longer official statutory text retained for search.</content>
    </section></uslm>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("usc42.xml", xml)
    artifact = USCodeArtifact(42, "119-102", 119, 102, "https://example.gov/usc42.zip")

    sections = parse_uslm_sections(
        DownloadedArtifact(artifact, buffer.getvalue(), "fixture-sha")
    )

    assert len(sections) == 1
    assert sections[0].external_id == "usc:119-102:title-42:section-210"
    assert "substantially longer" in sections[0].text


def test_production_cli_requires_an_explicit_mode(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["uscode_ingest"])
    with pytest.raises(SystemExit) as error:
        ingest_main()
    assert error.value.code == 2
