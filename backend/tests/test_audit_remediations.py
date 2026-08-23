"""Regressions for the findings raised by the Aug 20-23 merge-window audit.

Each test here fails against the code as it stood before its fix.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.middleware.rate_limit import AUTH_GET_LIMITS, AUTH_LIMITS
from app.routers.clients import _csv_safe
from app.schemas.marketing import DemoRequestCreate
from app.utils.sql_filters import escape_like
from app.utils.text_processing import extract_text, extract_text_from_path


# ── CSV formula injection ───────────────────────────────────────────────────


@pytest.mark.parametrize("lead", ["=", "+", "-", "@", "\t", "\r"])
def test_csv_export_quotes_every_formula_lead(lead: str) -> None:
    """Tab and carriage return are formula leads too: Excel strips them first."""
    assert _csv_safe(f"{lead}cmd|'/bin/sh'").startswith("'")


def test_csv_export_leaves_ordinary_values_untouched() -> None:
    assert _csv_safe("Redwood Outdoor Supply") == "Redwood Outdoor Supply"
    assert _csv_safe(None) == ""
    assert _csv_safe(42) == "42"


# ── LIKE wildcard escaping ──────────────────────────────────────────────────


def test_escape_like_neutralizes_wildcards() -> None:
    assert escape_like("100%") == "100\\%"
    assert escape_like("client_id") == "client\\_id"


def test_escape_like_escapes_the_escape_character_first() -> None:
    """A literal backslash must not be read as escaping the character after it."""
    assert escape_like("a\\b") == "a\\\\b"
    assert escape_like("\\%") == "\\\\\\%"


def test_escape_like_is_a_no_op_for_ordinary_search_text() -> None:
    assert escape_like("Ada Lovelace") == "Ada Lovelace"


# ── Marketing lead notification ─────────────────────────────────────────────


@pytest.mark.parametrize("field", ["name", "firm_name", "phone", "team_size"])
def test_single_line_lead_fields_reject_embedded_newlines(field: str) -> None:
    """A newline here made the notification unsendable and lost the lead silently.

    Python refuses to serialize a header containing an embedded one, so
    ``send_email`` returned FAILED while the request row was still stored.
    """
    payload = {
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "firm_name": "Lovelace LLP",
        field: "value\nBcc: attacker@example.com",
    }
    with pytest.raises(ValidationError, match="single line"):
        DemoRequestCreate(**payload)


def test_multiline_message_body_is_still_accepted() -> None:
    """Only the header-bound fields are single-line; the message is free text."""
    request = DemoRequestCreate(
        name="Ada Lovelace",
        email="ada@example.com",
        firm_name="Lovelace LLP",
        message="First line.\nSecond line.",
    )
    assert "\n" in request.message


# ── Legacy .doc handling ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("content_type", "filename"),
    [("application/msword", "brief.doc"), ("", "brief.doc"), ("application/msword", "")],
)
def test_both_extractors_reject_legacy_doc_with_the_same_message(
    content_type: str, filename: str
) -> None:
    """The path-based extractor used to hand .doc to python-docx instead."""
    with pytest.raises(ValueError, match="Legacy .doc files are not supported"):
        extract_text(b"\xd0\xcf\x11\xe0", content_type, filename)

    with pytest.raises(ValueError, match="Legacy .doc files are not supported"):
        extract_text_from_path("/nonexistent/brief.doc", content_type, filename)


def test_docx_is_not_caught_by_the_legacy_doc_guard(tmp_path) -> None:
    """`.docx` must still route to the DOCX reader, not the `.doc` refusal."""
    staged = tmp_path / "brief.docx"
    staged.write_bytes(b"not really a docx")

    # Reaches python-docx and fails there on the container, which is the point:
    # it is not refused as a legacy .doc.
    with pytest.raises(Exception) as caught:
        extract_text_from_path(str(staged), "", "brief.docx")
    assert "Legacy .doc" not in str(caught.value)


# ── Unauthenticated GET rate limiting ───────────────────────────────────────


def test_oauth_authorize_has_a_get_side_rate_limit() -> None:
    """AUTH_LIMITS is POST-only, so a GET endpoint needs the GET table."""
    assert AUTH_GET_LIMITS["/api/workspace-mcp/oauth/authorize"] == (30, 300)
    assert "/api/workspace-mcp/oauth/authorize" not in AUTH_LIMITS
