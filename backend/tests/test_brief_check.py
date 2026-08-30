from app.services.brief_check import (
    analyze_brief,
    report_markdown,
    table_of_authorities_markdown,
)


def test_brief_check_normalizes_missing_short_form_and_preserves_limits():
    result = analyze_brief(
        'The court followed 410 N.W. 2d 123, 130. "A quoted proposition that is not available" Smith at 44.'
    )
    assert result["review_first"] is True
    assert result["coverage"]["comprehensive"] is False
    assert any(item["status"] == "missing_source" for item in result["citations"])
    assert any(item["status"] == "ambiguous" for item in result["citations"])
    assert result["treatment_currentness"]["status"] == "unknown"
    assert "good-law determination" in report_markdown(result).lower()


def test_brief_check_exact_quote_and_opposing_comparison_are_source_linked():
    source = {
        "source_id": "courtlistener:1",
        "citation": "410 N.W. 2d 123",
        "source_tier": "official",
        "text": "A quoted proposition that is available in the source.",
        "retrieved_at": "2026-08-29T00:00:00Z",
        "corpus_version": "test-v1",
    }
    result = analyze_brief(
        'See 410 N.W. 2d 123. "A quoted proposition that is available in the source."',
        sources=[source],
        opposing_text="See 410 N.W. 2d 123.",
    )
    assert result["citations"][0]["status"] == "resolved"
    assert result["citations"][0]["source_identity"] == "courtlistener:1"
    assert result["quotations"][0]["status"] == "verified"
    assert result["opposing_brief_comparison"]["shared_citations"] == [
        "410 n.w. 2d 123"
    ]
    assert "Attorney review required" in table_of_authorities_markdown(result)


def test_brief_check_is_deterministic_for_same_content_shape():
    first = analyze_brief("See 123 U.S.C. § 456.")
    second = analyze_brief("See 123 U.S.C. § 456.")
    assert [(x["canonical"], x["status"]) for x in first["citations"]] == [
        (x["canonical"], x["status"]) for x in second["citations"]
    ]
