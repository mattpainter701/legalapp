from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.config import validate_template_ocr_settings
from app.services import template_ocr
from app.services import template_ocr_azure as azure
from app.services.template_ocr import PdfOcrResult, TemplateOcrError


def _settings(**kwargs):
    values = dict(
        TEMPLATE_OCR_PROVIDER="azure",
        AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=(
            "https://westus.api.cognitive.microsoft.com"
        ),
        AZURE_DOCUMENT_INTELLIGENCE_KEY="secret-key",
        AZURE_DOCUMENT_INTELLIGENCE_API_VERSION="2024-11-30",
        TEMPLATE_OCR_AZURE_TIMEOUT_SECONDS=5.0,
        TEMPLATE_OCR_AZURE_MAX_POLL_SECONDS=10.0,
        TEMPLATE_OCR_AZURE_MAX_POLL_INTERVAL_SECONDS=2.0,
    )
    values.update(kwargs)
    return SimpleNamespace(**values)


def _blank_pdf() -> bytes:
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=letter)
    pdf.showPage()
    pdf.save()
    return output.getvalue()


def test_local_is_default_and_azure_dispatch_is_explicit(monkeypatch):
    class EmptyEngine:
        def __call__(self, _image):
            return SimpleNamespace(boxes=None, txts=(), scores=())

    monkeypatch.setattr(
        template_ocr,
        "get_settings",
        lambda: SimpleNamespace(TEMPLATE_OCR_PROVIDER="local"),
    )
    monkeypatch.setattr(template_ocr, "_engine", lambda: EmptyEngine())
    assert template_ocr.ocr_pdf(_blank_pdf()).provider == "local"

    sentinel = PdfOcrResult("cloud", (), 1, 1, 0.9, False, "azure")
    monkeypatch.setattr(
        template_ocr,
        "get_settings",
        lambda: SimpleNamespace(TEMPLATE_OCR_PROVIDER="azure"),
    )
    monkeypatch.setattr(azure, "ocr_pdf_azure", lambda _content, max_pages: sentinel)
    assert template_ocr.ocr_pdf(b"cloud dispatch", max_pages=3) is sentinel


def test_azure_configuration_requires_safe_endpoint_key_and_version():
    validate_template_ocr_settings(_settings(TEMPLATE_OCR_PROVIDER="local"))
    for endpoint in (
        "http://bad.example",
        "https://user:pass@bad.example",
        "https://safe.example?redirect=bad",
        "https://safe.example/#fragment",
    ):
        with pytest.raises(ValueError):
            validate_template_ocr_settings(
                _settings(AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=endpoint)
            )
    with pytest.raises(ValueError):
        validate_template_ocr_settings(
            _settings(AZURE_DOCUMENT_INTELLIGENCE_KEY="")
        )
    with pytest.raises(ValueError):
        validate_template_ocr_settings(
            _settings(AZURE_DOCUMENT_INTELLIGENCE_API_VERSION="latest")
        )


def test_azure_polling_polygon_and_word_confidence_conversion(monkeypatch):
    settings = _settings()
    monkeypatch.setattr(azure, "get_settings", lambda: settings)
    calls = []
    sleeps = []
    responses = [
        httpx.Response(
            202,
            headers={
                "Operation-Location": (
                    settings.AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT + "/result/1"
                )
            },
        ),
        httpx.Response(
            200,
            headers={"Retry-After": "99"},
            json={"status": "running"},
        ),
        httpx.Response(
            200,
            json={
                "status": "succeeded",
                "analyzeResult": {
                    "pages": [{
                        "pageNumber": 1,
                        "unit": "inch",
                        "width": 8.5,
                        "height": 11,
                        "lines": [{
                            "content": "Ada Lovelace",
                            "polygon": [1, 2, 2, 2, 2, 3, 1, 3],
                            "spans": [{"offset": 0, "length": 12}],
                        }],
                        "words": [
                            {
                                "content": "Ada",
                                "confidence": 0.8,
                                "span": {"offset": 0, "length": 3},
                            },
                            {
                                "content": "Lovelace",
                                "confidence": 0.9,
                                "span": {"offset": 4, "length": 8},
                            },
                        ],
                    }],
                },
            },
        ),
    ]

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def post(self, *args, **kwargs):
            calls.append(("post", args, kwargs))
            return responses.pop(0)

        def get(self, *args, **kwargs):
            calls.append(("get", args, kwargs))
            return responses.pop(0)

    monkeypatch.setattr(azure.httpx, "Client", lambda **_kwargs: Client())
    monkeypatch.setattr(azure.time, "sleep", sleeps.append)
    result = azure.ocr_pdf_azure(b"not a parseable PDF")

    assert result.provider == "azure"
    assert result.lines[0].rect == (72.0, 576.0, 144.0, 648.0)
    assert result.lines[0].score == pytest.approx(0.85)
    assert calls[0][2]["content"] == b"not a parseable PDF"
    assert calls[0][2]["params"]["pages"] == "1-25"
    assert sleeps == [2.0]


def test_azure_point_units_and_invalid_geometry_are_handled():
    result = azure._result({
        "analyzeResult": {
            "pages": [
                {
                    "pageNumber": 2,
                    "unit": "point",
                    "width": 612,
                    "height": 792,
                    "lines": [
                        {
                            "content": "Valid",
                            "confidence": 0.7,
                            "polygon": [72, 100, 140, 100, 140, 120, 72, 120],
                        },
                        {
                            "content": "Degenerate",
                            "polygon": [10, 10, 10, 10, 10, 10, 10, 10],
                        },
                        {"content": "No polygon", "polygon": []},
                        {"content": ""},
                    ],
                },
                {
                    "pageNumber": 3,
                    "unit": "pixel",
                    "width": 1000,
                    "height": 1000,
                    "lines": [{
                        "content": "Cannot map safely",
                        "polygon": [1, 1, 2, 1, 2, 2, 1, 2],
                    }],
                },
            ],
        }
    })
    assert len(result.lines) == 1
    assert result.lines[0].page_index == 1
    assert result.lines[0].rect == (72.0, 672.0, 140.0, 692.0)
    assert result.lines[0].score == pytest.approx(0.7)


@pytest.mark.parametrize(
    "location",
    [
        "http://westus.api.cognitive.microsoft.com/x",
        "https://evil.example/x",
        "https://user:pass@westus.api.cognitive.microsoft.com/x",
        "not-a-url",
    ],
)
def test_operation_location_is_restricted(monkeypatch, location):
    settings = _settings()
    monkeypatch.setattr(azure, "get_settings", lambda: settings)

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def post(self, *_args, **_kwargs):
            return httpx.Response(202, headers={"Operation-Location": location})

    monkeypatch.setattr(azure.httpx, "Client", lambda **_kwargs: Client())
    with pytest.raises(TemplateOcrError, match="invalid operation"):
        azure.ocr_pdf_azure(b"secret document text")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="client document text"),
        httpx.Response(202),
    ],
)
def test_azure_start_failures_are_generic_and_do_not_leak(monkeypatch, response):
    settings = _settings()
    monkeypatch.setattr(azure, "get_settings", lambda: settings)

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def post(self, *_args, **_kwargs):
            return response

    monkeypatch.setattr(azure.httpx, "Client", lambda **_kwargs: Client())
    with pytest.raises(TemplateOcrError) as exc:
        azure.ocr_pdf_azure(b"private text")
    message = str(exc.value).lower()
    assert "private" not in message
    assert "client document" not in message
    assert "secret-key" not in message


def test_azure_failed_result_and_timeout_are_bounded(monkeypatch):
    settings = _settings()
    monkeypatch.setattr(azure, "get_settings", lambda: settings)
    operation = settings.AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT + "/result/1"

    class FailedClient:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def post(self, *_args, **_kwargs):
            return httpx.Response(202, headers={"Operation-Location": operation})

        def get(self, *_args, **_kwargs):
            return httpx.Response(200, json={"status": "failed"})

    monkeypatch.setattr(azure.httpx, "Client", lambda **_kwargs: FailedClient())
    with pytest.raises(TemplateOcrError, match="could not read"):
        azure.ocr_pdf_azure(b"document")

    ticks = iter([0.0, 0.0, 10.0, 10.0])
    monkeypatch.setattr(azure.time, "monotonic", lambda: next(ticks))

    class RunningClient(FailedClient):
        def get(self, *_args, **_kwargs):
            return httpx.Response(200, json={"status": "running"})

    monkeypatch.setattr(azure.httpx, "Client", lambda **_kwargs: RunningClient())
    monkeypatch.setattr(azure.time, "sleep", lambda _seconds: None)
    with pytest.raises(TemplateOcrError, match="timed out"):
        azure.ocr_pdf_azure(b"document")
