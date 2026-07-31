import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server.nd_authority_ingest import CODE_TARGETS, NDDocument, discover


def test_nd_discovery_is_allowlisted_and_pdf_bounded():
    html = (Path(__file__).parent / "fixtures" / "nd_century_index.html").read_text()
    docs = discover(html, CODE_TARGETS[0], lambda _url: "")
    assert [item.url for item in docs] == [
        "https://ndlegis.gov/cencode/t30c13.pdf",
    ]
    assert docs[0].external_id == "century-code:t30c13"


def test_stable_chapter_id_falls_back_to_hash_when_no_path_stem():
    doc = NDDocument(CODE_TARGETS[0], "https://ndlegis.gov/cencode/", "code", 1)
    assert doc.external_id.startswith("century-code:")
