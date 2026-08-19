"""Extract document artifacts from LLM responses.

The model emits artifacts as fenced blocks:

    :::artifact title="Some Title"
    ...markdown content...
    :::

This module parses those blocks so the chat pipeline can persist them as
ChatArtifact rows and the frontend can render document cards.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ARTIFACT_OPEN_RE = re.compile(
    r'^:::artifact[ \t]+title="([^"]*)"[ \t]*$', re.MULTILINE
)
_ARTIFACT_BLOCK_RE = re.compile(
    r'^:::artifact[ \t]+title="([^"]*)"[ \t]*\r?\n(.*?)^:::[ \t]*$',
    re.MULTILINE | re.DOTALL,
)

_MAX_ARTIFACTS_PER_MESSAGE = 3
_MAX_TITLE_LEN = 500
_MAX_ARTIFACT_CHARS = 200_000


@dataclass(frozen=True)
class ExtractedArtifact:
    title: str
    content: str


def extract_artifacts(text: str) -> list[ExtractedArtifact]:
    """Pull all well-formed artifact blocks out of an assistant response.

    Malformed blocks (missing close fence, empty content, missing title) are
    skipped silently — extraction must never fail the chat flow.
    """
    if not text or ":::artifact" not in text:
        return []

    open_count = len(_ARTIFACT_OPEN_RE.findall(text))
    matches = list(_ARTIFACT_BLOCK_RE.finditer(text))
    if not matches or len(matches) != open_count:
        return []
    if len(matches) > _MAX_ARTIFACTS_PER_MESSAGE:
        return []

    artifacts: list[ExtractedArtifact] = []
    for match in matches:
        title = match.group(1).strip()
        content = match.group(2).strip("\r\n")
        if (
            not title
            or len(title) > _MAX_TITLE_LEN
            or not content.strip()
            or len(content) > _MAX_ARTIFACT_CHARS
        ):
            return []
        artifacts.append(ExtractedArtifact(title=title, content=content))

    return artifacts


def strip_artifacts(text: str) -> str:
    """Remove artifact blocks from the visible message body.

    The conversational text remains; the document content is shown separately
    as artifact cards in the UI.
    """
    if not text or ":::artifact" not in text:
        return text

    # Use the same fail-closed validation as extraction. If any block is
    # malformed, oversized, or exceeds the per-message limit, leave the model
    # response untouched rather than hiding content that was not persisted.
    if not extract_artifacts(text):
        return text

    cleaned = _ARTIFACT_BLOCK_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()
