"""Accept a hand-drawn signature image from the portal.

The signer draws on a canvas in their browser; the page sends the result as a
PNG data URL. The image is stamped onto the executed copy in place of the
typed name, so it is checked here before anything is recorded: it must be a
real PNG of sane size that actually contains ink. The typed legal name is
still required alongside it and is what the evidence certificate records; the
drawing is retained by content hash as part of the same evidence.
"""

from __future__ import annotations

import base64
import binascii
import io
import re

MAX_BYTES = 200 * 1024
MAX_WIDTH = 2000
MAX_HEIGHT = 1000
MIN_WIDTH = 40
MIN_HEIGHT = 20

_DATA_URL = re.compile(r"^data:image/png;base64,", re.IGNORECASE)


class DrawnSignatureError(ValueError):
    """The drawn signature cannot be used; the message is safe to show."""


def decode_drawn_signature(value: str | None) -> bytes | None:
    """Return the PNG bytes of a drawn signature, or None when none was sent."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    text = _DATA_URL.sub("", text, count=1)
    # A data URL is at most 4/3 the size of its payload; refuse early rather
    # than decode a payload that cannot possibly fit.
    if len(text) > MAX_BYTES * 4 // 3 + 4:
        raise DrawnSignatureError(
            "The drawn signature is too large. Clear it and draw again."
        )
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise DrawnSignatureError("The drawn signature could not be read.") from exc
    if len(raw) > MAX_BYTES:
        raise DrawnSignatureError(
            "The drawn signature is too large. Clear it and draw again."
        )
    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as image:
            if (image.format or "").upper() != "PNG":
                raise DrawnSignatureError("The drawn signature must be a PNG image.")
            width, height = image.size
            if width > MAX_WIDTH or height > MAX_HEIGHT:
                raise DrawnSignatureError(
                    "The drawn signature is too large. Clear it and draw again."
                )
            if width < MIN_WIDTH or height < MIN_HEIGHT:
                raise DrawnSignatureError("The drawn signature is too small to use.")
            if not _has_ink(image):
                raise DrawnSignatureError(
                    "Draw your signature before signing, or type your name instead."
                )
    except DrawnSignatureError:
        raise
    except Exception as exc:  # Pillow raises many types for a broken image
        raise DrawnSignatureError("The drawn signature could not be read.") from exc
    return raw


def _has_ink(image) -> bool:
    """Whether anything is drawn: opaque pixels on a transparent canvas, or dark ones on a white one."""
    rgba = image.convert("RGBA")
    alpha_bbox = rgba.getchannel("A").getbbox()
    if alpha_bbox is None:
        return False
    if alpha_bbox != (0, 0, *rgba.size):
        # Transparent canvas with some opaque strokes.
        return True
    # Fully opaque: ink is anything that is not near-white.
    darkest, _ = rgba.convert("L").getextrema()
    return darkest < 200
