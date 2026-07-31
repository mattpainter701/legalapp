import sys
from pathlib import Path
import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from mcp_server.benefits_authority_ingest import BenefitsManifestDocument, fetch_document, load_reviewed_manifest


def test_rejects_non_official_manifest_host(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text('{"documents":[{"source_key":"ssa:poms-hallex-ssr","external_id":"x","document_type":"x","title":"x","canonical_url":"https://example.com/x","jurisdiction":"US","authority_tier":"agency_guidance","label":"agency_guidance"}]}')
    with pytest.raises(ValueError, match="unapproved"):
        load_reviewed_manifest(path)


def test_fetches_reviewed_html_with_provenance():
    doc = BenefitsManifestDocument("ssa:poms-hallex-ssr", "poms-gn-01110", "ssa_poms", "SSI Resources", "https://secure.ssa.gov/apps10/poms.nsf/lnx/0201110000", "US", "agency_guidance", "ssa_internal_guidance")
    html = "<html><body><main><h1>SSI Resources</h1><p>" + "Official policy text. " * 20 + "</p></main></body></html>"
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=html, headers={"content-type":"text/html", "etag":"x"}))) as client:
        result = fetch_document(doc, client=client)
    assert result.metadata["authority_label"] == "ssa_internal_guidance"
    assert result.metadata["etag"] == "x"
    assert result.text.startswith("SSI Resources")
