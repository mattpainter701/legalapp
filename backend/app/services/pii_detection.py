"""PII detection and scrubbing utilities."""

import re
from typing import List

# PII Pattern definitions
PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),
    "phone": re.compile(r"\b(?:\+?1[-.]?)?(?:\()?(\d{3})(?:\))?[-.\s]?(\d{3})[-.\s]?(\d{4})\b"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    "ip_address": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "passport": re.compile(r"\b[A-Z]{1,2}\d{6,9}\b"),
    "driver_license": re.compile(r"\b[A-Z]{1,2}\d{9,12}\b"),
    "bank_account": re.compile(r"(?<!\d)\d{8,17}(?!\d)"),
}

PLACEHOLDER_MAP = {
    "ssn": "[SSN]",
    "credit_card": "[CREDIT_CARD]",
    "phone": "[PHONE]",
    "email": "[EMAIL]",
    "ip_address": "[IP_ADDRESS]",
    "passport": "[PASSPORT]",
    "driver_license": "[DRIVER_LICENSE]",
    "bank_account": "[ACCOUNT]",
}


def detect_pii(text: str) -> List[dict]:
    """
    Detect PII in text.
    Returns list of dicts with format:
    {
        "type": "ssn" | "credit_card" | etc.,
        "value": "***-**-1234",
        "location": (start, end),
        "risk": "high" | "medium" | "low"
    }
    """
    if not text:
        return []

    findings = []
    for pii_type, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            risk = "high" if pii_type in ["ssn", "credit_card", "bank_account"] else "medium"
            # Mask sensitive parts
            value = match.group()
            if pii_type == "ssn":
                masked = f"{value[:5]}****"
            elif pii_type == "credit_card":
                masked = f"****-****-****-{value[-4:]}"
            elif pii_type == "bank_account":
                masked = f"****{value[-4:]}"
            else:
                masked = f"{value[:3]}***{value[-3:]}"

            findings.append(
                {
                    "type": pii_type,
                    "value": masked,
                    "location": (match.start(), match.end()),
                    "risk": risk,
                }
            )

    return findings


def scrub_pii(text: str) -> str:
    """Replace PII with placeholders, preserving text structure."""
    if not text:
        return text

    result = text
    for pii_type, pattern in PATTERNS.items():
        placeholder = PLACEHOLDER_MAP[pii_type]
        result = pattern.sub(placeholder, result)

    return result


def assess_pii_risk(text: str) -> str:
    """
    Assess overall PII risk in text.
    Returns "low" | "medium" | "high"
    """
    findings = detect_pii(text)
    if not findings:
        return "low"

    high_risk = [f for f in findings if f["risk"] == "high"]
    if high_risk:
        return "high"

    return "medium" if findings else "low"
