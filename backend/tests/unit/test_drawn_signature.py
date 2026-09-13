"""A drawn signature is accepted only when it is a usable PNG with ink on it."""

import base64
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from app.services.esign.drawn_signature import (
    MAX_BYTES,
    DrawnSignatureError,
    decode_drawn_signature,
)


def _png(size=(320, 120), *, ink=True, background=(0, 0, 0, 0), mode="RGBA") -> bytes:
    image = Image.new(mode, size, background)
    if ink:
        ImageDraw.Draw(image).line(
            [(10, 100), (150, 20), (300, 90)], fill=(10, 10, 60, 255), width=4
        )
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


def test_nothing_sent_means_no_drawing():
    assert decode_drawn_signature(None) is None
    assert decode_drawn_signature("   ") is None


def test_a_data_url_or_bare_base64_round_trips_to_the_png_bytes():
    png = _png()
    assert decode_drawn_signature(_data_url(png)) == png
    assert decode_drawn_signature(base64.b64encode(png).decode()) == png


def test_a_drawing_on_a_white_canvas_counts_as_ink():
    png = _png(background=(255, 255, 255, 255))
    assert decode_drawn_signature(_data_url(png)) == png


@pytest.mark.parametrize(
    "value, message",
    [
        (_data_url(_png(ink=False)), "Draw your signature"),
        (
            _data_url(_png(ink=False, background=(255, 255, 255, 255))),
            "Draw your signature",
        ),
        (_data_url(_png(size=(20, 10))), "too small"),
        (_data_url(_png(size=(2400, 200))), "too large"),
        ("data:image/png;base64,not*base64", "could not be read"),
        (_data_url(b"\x89PNG not really"), "could not be read"),
    ],
)
def test_unusable_drawings_are_refused_with_a_reason(value, message):
    with pytest.raises(DrawnSignatureError) as caught:
        decode_drawn_signature(value)
    assert message in str(caught.value)


def test_a_jpeg_is_not_a_png():
    image = Image.new("RGB", (320, 120), (255, 255, 255))
    ImageDraw.Draw(image).line([(10, 100), (300, 20)], fill=(0, 0, 0), width=4)
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    with pytest.raises(DrawnSignatureError) as caught:
        decode_drawn_signature(_data_url(buffer.getvalue()))
    assert "PNG" in str(caught.value)


def test_oversized_payloads_are_refused_before_decoding():
    with pytest.raises(DrawnSignatureError) as caught:
        decode_drawn_signature("A" * (MAX_BYTES * 2))
    assert "too large" in str(caught.value)
