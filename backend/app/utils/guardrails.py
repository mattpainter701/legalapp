import re
from typing import Tuple

PROHIBITED_PHRASES = [
    "as an ai",
    "as an language model",
    "i am an ai",
    "i'm an ai",
    "deepseek",
    "claude",
    "gpt",
    "openai",
    "anthropic",
    "language model",
    "large language model",
    "llm",
    "artificial intelligence",
]

# Replacements for prohibited phrases
PHRASE_REPLACEMENTS = {
    "as an ai": "as a legal research assistant",
    "as an language model": "as a legal research assistant",
    "i am an ai": "I am a legal research assistant",
    "i'm an ai": "I am a legal research assistant",
    "deepseek": "the legal research system",
    "claude": "the legal research system",
    "gpt": "the legal research system",
    "openai": "the legal research provider",
    "anthropic": "the legal research provider",
    "language model": "legal research system",
    "large language model": "legal research system",
    "llm": "legal research system",
    "artificial intelligence": "legal research technology",
}

# Legal citation pattern: Smith v. Jones, 123 F.3d 456, (2023), No. 22-1234
CITATION_PATTERN = re.compile(
    r"""
    (
        \b\w[\w\s,\.]+\s+v\.\s+\w[\w\s,\.]+  # Case name: X v. Y
        |
        \d+\s+[A-Z][a-zA-Z\.]+\s+\d+          # Reporter: 123 F.3d 456
        |
        \(\d{4}\)                               # Year: (2023)
        |
        No\.\s+\d{2}-\d+                        # Docket: No. 22-1234
    )
    """,
    re.VERBOSE,
)


def check_prohibited_phrases(text: str) -> bool:
    """Returns True if any prohibited phrases are found (case-insensitive)."""
    text_lower = text.lower()
    return any(phrase in text_lower for phrase in PROHIBITED_PHRASES)


def sanitize_response(text: str) -> str:
    """Replace prohibited phrases with safe alternatives (case-insensitive)."""
    result = text
    for phrase, replacement in PHRASE_REPLACEMENTS.items():
        # Case-insensitive replacement preserving sentence structure
        pattern = re.compile(re.escape(phrase), re.IGNORECASE)
        result = pattern.sub(replacement, result)
    return result


def check_has_citation(text: str) -> bool:
    """Check if response contains a legal citation pattern."""
    return bool(CITATION_PATTERN.search(text))


def apply_guardrails(text: str) -> Tuple[str, bool]:
    """
    Apply all guardrails to a response.
    Returns (cleaned_text, needs_retry).
    needs_retry is True if prohibited phrases were found and the response
    is so contaminated it should be regenerated.
    """
    has_prohibited = check_prohibited_phrases(text)

    if has_prohibited:
        cleaned = sanitize_response(text)
        # Only request a retry if the original had very heavy AI self-disclosure
        needs_retry = sum(
            1 for phrase in ["deepseek", "claude", "gpt", "openai", "anthropic"]
            if phrase in text.lower()
        ) >= 2
        return cleaned, needs_retry

    return text, False
