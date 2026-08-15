"""Tests for guardrails utility."""

from app.utils.guardrails import (
    apply_guardrails,
    build_citation_annotations,
    check_has_citation,
    prepare_provider_messages,
    prepare_provider_text,
    sanitize_response,
    validate_citation_confidence,
)
from app.services.llm import SYSTEM_PROMPT_TEMPLATE


def test_sanitize_preserves_substantive_ai_terms():
    text = "The OpenAI agreement governs an artificial intelligence service."
    result = sanitize_response(text)
    assert result == text


def test_system_prompt_is_transparent_and_requires_source_backed_tags():
    assert "AI-assisted legal research tool" in SYSTEM_PROMPT_TEMPLATE
    assert "not an AI" not in SYSTEM_PROMPT_TEMPLATE
    assert "A faithful paraphrase is allowed" in SYSTEM_PROMPT_TEMPLATE
    assert "[cited]" in SYSTEM_PROMPT_TEMPLATE
    assert "[source: <source_id>]" in SYSTEM_PROMPT_TEMPLATE
    assert (
        "**Source note:** This response uses general legal knowledge"
        in SYSTEM_PROMPT_TEMPLATE
    )
    assert (
        "Do not repeat [model knowledge] after every factual claim"
        in SYSTEM_PROMPT_TEMPLATE
    )
    assert "Except for a response with empty SOURCE MATERIALS" in SYSTEM_PROMPT_TEMPLATE


def test_citation_detected():
    assert check_has_citation("Smith v. Jones, 123 F.3d 456 (9th Cir. 2020)") is True
    assert check_has_citation("per the ruling in (2023)") is True
    assert check_has_citation("See Brown v. Board, 347 U.S. 483") is True


def test_no_citation():
    assert check_has_citation("The weather is nice today.") is False
    assert check_has_citation("Please review the attached document.") is False


def test_guardrails_clean_response():
    text = (
        "Under *Smith v. Jones*, 123 F.3d 456 (2020) [settled], the test applies.\n\n"
        "*This is not legal advice. Please consult a qualified attorney.*"
    )
    cleaned, needs_retry, _ = apply_guardrails(text)
    assert needs_retry is False
    assert "not legal advice" in cleaned.lower()


def test_guardrails_does_not_rewrite_or_retry_substantive_terms():
    text = "As an AI language model, I think the contract is fine."
    cleaned, needs_retry, _ = apply_guardrails(text)
    assert cleaned == text
    assert needs_retry is False


def test_guardrails_rewrites_internal_context_tags():
    text = (
        "State v. Robertson discusses search warrants. "
        "[FIRM CONTEXT: State v. Robertson]"
    )

    cleaned, needs_retry, _ = apply_guardrails(text)

    assert needs_retry is False
    assert "FIRM CONTEXT" not in cleaned
    assert "[cited by context: State v. Robertson]" in cleaned


def test_provider_boundary_scrubs_all_messages_and_context():
    messages = [
        {"role": "assistant", "content": "Email jane@example.com"},
        {"role": "user", "content": "Call 701-555-1212 re 123-45-6789"},
    ]
    prepared = prepare_provider_messages(messages, privacy_mode=True)
    assert "jane@example.com" not in str(prepared)
    assert "701-555-1212" not in str(prepared)
    assert "123-45-6789" not in str(prepared)
    assert prepare_provider_text("jane@example.com", True) == "[EMAIL]"
    # Inputs are copied, not mutated; originals remain available for storage.
    assert messages[0]["content"] == "Email jane@example.com"


def test_unsupported_settled_claim_is_downgraded():
    text, count = validate_citation_confidence(
        "The limitations period is four years. [settled]", []
    )
    assert text == "The limitations period is four years. [verify]"
    assert count == 1


def test_citation_metadata_alone_does_not_prove_support():
    text, count = validate_citation_confidence(
        "Smith v. Jones, 123 F.3d 456 controls. [settled]",
        [{"id": "authority-1", "citation": "123 F.3d 456"}],
    )
    assert text.endswith("[verify]")
    assert count == 1


def test_legacy_settled_tag_is_normalized_to_cited_for_matching_quote():
    quote = "The moving party must establish irreparable harm"
    text, count = validate_citation_confidence(
        f'The court held "{quote}." [source: authority-1] [settled]',
        [
            {
                "id": "authority-1",
                "citation": "123 F.3d 456",
                "excerpt": f"In this case, {quote}. The judgment was affirmed.",
            }
        ],
    )
    assert text.endswith("[cited]")
    assert count == 0


def test_cited_paraphrase_survives_when_it_materially_overlaps_exact_source():
    text, count = validate_citation_confidence(
        "The moving party has to establish irreparable harm. "
        "[source: authority-1] [cited]",
        [
            {
                "id": "authority-1",
                "excerpt": "A moving party must establish that irreparable harm is likely.",
            }
        ],
    )

    assert text.endswith("[cited]")
    assert count == 0


def test_cited_tag_is_downgraded_when_passage_is_unrelated():
    text, count = validate_citation_confidence(
        "The limitations period is four years. [source: authority-1] [cited]",
        [
            {
                "id": "authority-1",
                "excerpt": "The opinion concerns personal jurisdiction.",
            }
        ],
    )

    assert text.endswith("[verify]")
    assert count == 1


def test_build_citation_annotations_binds_claim_tag_and_known_source_offsets():
    text = (
        "The statute requires notice. "
        "[source: authority:nd-2] [cited]\n\n"
        "Application to these facts remains uncertain. [verify]"
    )
    annotations = build_citation_annotations(
        text,
        [{"source_id": "authority:nd-2"}],
    )

    assert [item["support"] for item in annotations] == ["cited", "verify"]
    assert annotations[0]["source_ids"] == ["authority:nd-2"]
    marker = annotations[0]["source_markers"][0]
    assert text[marker["start"] : marker["end"]] == "[source: authority:nd-2]"
    tag = annotations[0]["support_tag"]
    assert text[tag["start"] : tag["end"]] == "[cited]"
    assert annotations[1]["source_ids"] == []


def test_retrieval_alone_does_not_prove_support():
    text, count = validate_citation_confidence(
        "The limitations period is four years. [settled]",
        [{"id": "unrelated", "citation": "999 F.2d 1"}],
    )
    assert text.endswith("[verify]")
    assert count == 1
