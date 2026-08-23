"""Structural delimiting for third-party text handed to a model.

Matter documents are authored by clients, opposing counsel, and courts. When
their text is returned through a tool result it sits in the same JSON object as
the product's own fields, which means a line reading "ignore previous
instructions" occupies the same structural position as genuine guidance.

Wrapping the span gives the boundary a shape a model can rely on, and stripping
any counterfeit closing tag stops authored content from ending the wrapper early
and escaping it.
"""

from __future__ import annotations

import re

OPEN_TAG = "<untrusted_document_text sha256={sha}>"
CLOSE_TAG = "</untrusted_document_text>"

# Any casing/spacing variant of the closing tag that content could use to break
# out of the wrapper.
_COUNTERFEIT_CLOSE = re.compile(r"</\s*untrusted_document_text\s*>", re.IGNORECASE)


def wrap_untrusted_text(text: str, content_sha256: str) -> str:
    """Return `text` fenced by tags it cannot forge its way out of."""
    neutralized = _COUNTERFEIT_CLOSE.sub("[removed closing tag]", str(text or ""))
    return "\n".join((OPEN_TAG.format(sha=content_sha256), neutralized, CLOSE_TAG))
