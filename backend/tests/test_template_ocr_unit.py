from io import BytesIO
from types import SimpleNamespace

import pytest

from app.services import template_ocr as ocr


def test_ocr_line_fragment_and_safe_scale_bounds():
    line = ocr.OcrLine(2, "Total", 0.8, (10, 20, 30, 40))
    fragment = line.as_pdf_fragment()
    assert fragment["page_index"] == 2
    assert fragment["x"] == 10
    assert fragment["font_size"] == pytest.approx(15.6)
    assert ocr.PdfOcrResult("Total", (line,), 1, 2, 0.8, True).fragments() == [fragment]
    assert ocr._safe_scale(100, 100, 10_000_000) == ocr._TARGET_SCALE
    with pytest.raises(ocr.TemplateOcrError, match="invalid size"):
        ocr._safe_scale(0, 100, 100)
    with pytest.raises(ocr.TemplateOcrError, match="too large"):
        ocr._safe_scale(100, 100, 1)


def _png_bytes(size=(10, 10)):
    from PIL import Image

    image = Image.new("RGB", size, "white")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_image_ocr_rejects_empty_oversize_and_invalid_images(monkeypatch):
    with pytest.raises(ocr.TemplateOcrError, match="empty"):
        ocr.ocr_image(b"")
    monkeypatch.setattr(ocr, "_MAX_IMAGE_SOURCE_BYTES", 1)
    with pytest.raises(ocr.TemplateOcrError, match="10 MB"):
        ocr.ocr_image(b"12")
    monkeypatch.setattr(ocr, "_MAX_IMAGE_SOURCE_BYTES", 10 * 1024 * 1024)
    with pytest.raises(ocr.TemplateOcrError, match="could not be opened"):
        ocr.ocr_image(b"not an image")


def test_image_ocr_downsamples_and_filters_lines(monkeypatch):
    calls = []

    class Engine:
        def __call__(self, image):
            calls.append(image.size)
            return SimpleNamespace(txts=["  Keep  ", "low", ""], scores=[1.2, 0.1, 0.9])

    monkeypatch.setattr(ocr, "_MAX_IMAGE_SOURCE_PIXELS", 100000)
    monkeypatch.setattr(ocr, "_MAX_IMAGE_INFERENCE_PIXELS", 100)
    monkeypatch.setattr(ocr, "_ENGINE", Engine())
    result = ocr.ocr_image(_png_bytes((200, 100)))
    assert calls and calls[0][0] <= 20
    assert result.text == "Keep"
    assert result.lines_detected == 1
    assert result.average_confidence == 1.0


def test_image_ocr_maps_engine_errors(monkeypatch):
    monkeypatch.setattr(
        ocr, "_ENGINE", lambda image: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    with pytest.raises(ocr.TemplateOcrError, match="could not be read"):
        ocr.ocr_image(_png_bytes())


def test_local_ocr_pool_uses_independent_bounded_sessions(monkeypatch):
    first = object()
    second = object()
    created = []

    def create_engine():
        created.append(second)
        return second

    monkeypatch.setattr(ocr, "_new_engine", create_engine)
    pool = ocr._OcrEnginePool(2, first)

    leased_first = pool.acquire()
    leased_second = pool.acquire()
    assert {id(leased_first), id(leased_second)} == {id(first), id(second)}
    assert created == [second]

    pool.release(leased_first)
    pool.release(leased_second)
    assert pool.acquire() in {first, second}
