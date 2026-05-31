"""Tests for guardrails utility."""

from app.utils.guardrails import (
    apply_guardrails,
    check_has_citation,
    check_prohibited_phrases,
    sanitize_response,
)


def test_detects_ai_disclosure():
    assert check_prohibited_phrases("As an AI, I can help") is True
    assert check_prohibited_phrases("I am an AI language model") is True
    assert check_prohibited_phrases("As an AI language model, I suggest") is True
    assert check_prohibited_phrases("deepseek cannot do that") is True
    assert check_prohibited_phrases("Claude here to help") is True


def test_clean_text_passes():
    assert check_prohibited_phrases("The court held in Smith v. Jones") is False
    assert check_prohibited_phrases("Under 35 U.S.C. § 101, the claim is invalid") is False


def test_sanitize_removes_phrases():
    text = "As an AI, I can confirm the court ruled..."
    result = sanitize_response(text)
    assert "as an ai" not in result.lower()
    assert "court ruled" in result.lower()


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
    cleaned, needs_retry = apply_guardrails(text)
    assert needs_retry is False
    assert "not legal advice" in cleaned.lower()


def test_guardrails_triggers_retry():
    text = "As an AI language model, I think the contract is fine."
    _, needs_retry = apply_guardrails(text)
    assert needs_retry is True
