"""Common-password rejection for account creation / password reset.

Not a full HIBP breach-corpus check (that requires an external API call at
signup time, which this deliberately avoids for a legal-industry backend —
sending even a k-anonymity prefix of a new client's password hash to a
third party at registration is a privacy trade-off worth a separate,
explicit decision rather than a quiet dependency). This is a much smaller
local list of extremely common passwords/patterns that length-only policies
(min_length=12) don't catch, since "password12345" and "qwertyuiop123" both
satisfy a 12-character minimum while being trivially guessable.
"""

import re

# Case-insensitive exact matches. Sourced from well-known "most common
# passwords" lists, filtered to variants that could plausibly pass a
# 12-character minimum-length check (i.e. it's not worth listing 6-8 char
# classics like "123456" that min_length already rejects).
_COMMON_PASSWORDS = {
    "password123",
    "password1234",
    "password12345",
    "password123!",
    "qwertyuiop123",
    "qwerty123456",
    "1qaz2wsx3edc",
    "1q2w3e4r5t6y",
    "letmein12345",
    "welcome12345",
    "iloveyou1234",
    "administrator",
    "changeme12345",
    "abcdefghijkl",
    "aaaaaaaaaaaa",
    "111111111111",
    "123456789012",
    "12345678901234",
    "correcthorsebatterystaple",
}

# Simple structural red flags that a small denylist can't enumerate.
_ALL_SAME_CHAR = re.compile(r"^(.)\1+$")
_SEQUENTIAL_DIGITS = re.compile(r"^(?:0123456789|1234567890)+\d*$")


def is_common_password(password: str) -> bool:
    """Return True if `password` is a known-weak value that should be rejected."""
    lowered = password.lower()
    if lowered in _COMMON_PASSWORDS:
        return True
    if _ALL_SAME_CHAR.match(password):
        return True
    digits_only = "".join(ch for ch in password if ch.isdigit())
    if len(digits_only) == len(password) and _SEQUENTIAL_DIGITS.match(password):
        return True
    return False
