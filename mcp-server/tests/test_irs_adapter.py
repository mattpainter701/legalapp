import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server.irs_adapter import discover_estate_products, discover_irb_issues, irb_documents

FIXTURES = Path(__file__).with_name("fixtures")


def test_discovers_only_official_irb_issue_links():
    issues = discover_irb_issues((FIXTURES / "irs_irb_index.html").read_text())
    assert len(issues) == 1
    assert issues[0].external_id == "irs:irb:2026-31"


def test_irb_is_split_into_discrete_guidance_items():
    issue = discover_irb_issues((FIXTURES / "irs_irb_index.html").read_text())[0]
    documents = irb_documents(issue, (FIXTURES / "irs_irb_issue.html").read_text())
    assert [doc.external_id for doc in documents] == [
        "irs:irb:2026-31:rev-rul:2026-10",
        "irs:irb:2026-31:notice:2026-11",
    ]
    assert documents[0].metadata["stable_id"] == "irs:rev-rul:2026-10"


def test_estate_product_discovery_is_allowlisted_and_product_bounded():
    artifacts = discover_estate_products((FIXTURES / "irs_estate_index.html").read_text())
    assert {artifact.stable_id for artifact in artifacts} == {"irs:form:706", "irs:instructions:706"}
    assert all("irs.gov" in artifact.canonical_url for artifact in artifacts)
