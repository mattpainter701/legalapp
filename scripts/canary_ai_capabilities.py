#!/usr/bin/env python3
"""Run secret-safe synthetic canaries for LawHand AI capability lanes.

The default mode inventories key state and tests local TXT/DOCX/PDF extraction.
Pass ``--live`` to make small provider requests. No key, customer content,
provider response body, transcript, or document text is printed or persisted.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
KEY_NAMES = (
    "OPENROUTER_API_KEY",
    "OPENCODE_GO_API_KEY",
    "OPENCODE_ZEN_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENCODE_KEY",
    "OPENCODE_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
)


def load_environment(paths: list[Path]) -> tuple[dict[str, str], dict[str, str]]:
    values: dict[str, str] = {}
    sources: dict[str, str] = {}
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if not match:
                continue
            name, value = match.groups()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[name] = value
            sources[name] = path.name
    for name in KEY_NAMES:
        if name in os.environ:
            values[name] = os.environ[name]
            sources[name] = "process_environment"
    return values, sources


def credential_state(value: str | None) -> str:
    if not value:
        return "missing"
    lowered = value.lower()
    placeholder_markers = (
        "placeholder",
        "replace",
        "change-me",
        "change_me",
        "changeme",
        "not configured",
        "not set",
        "production host",
        "example",
        "...",
    )
    if any(char.isspace() for char in value) or any(
        marker in lowered for marker in placeholder_markers
    ):
        return "placeholder_or_note"
    return "configured"


def _error_category(status: int | None) -> str:
    if status == 401:
        return "invalid_credentials"
    if status in (402, 403):
        return "billing_or_provider_policy"
    if status == 429:
        return "rate_limited"
    if status == 400:
        return "unsupported_or_bad_request"
    if status is not None and status >= 500:
        return "provider_unavailable"
    return "network_or_provider_error"


def post_json(
    url: str, key: str, payload: dict[str, Any], timeout: int
) -> tuple[int | None, dict[str, Any] | None, int]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "LawHand-capability-canary/1",
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = json.load(response)
            return (
                response.status,
                body if isinstance(body, dict) else None,
                int((time.monotonic() - started) * 1000),
            )
    except urllib.error.HTTPError as exc:
        return exc.code, None, int((time.monotonic() - started) * 1000)
    except (OSError, TimeoutError, ValueError):
        return None, None, int((time.monotonic() - started) * 1000)


def post_multipart(
    url: str,
    key: str,
    fields: dict[str, str],
    file_field: str,
    file_path: Path,
    timeout: int,
) -> tuple[int | None, dict[str, Any] | None, int]:
    boundary = f"lawhand-canary-{time.time_ns()}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode(),
                b"\r\n",
            ]
        )
    content_type = {
        ".flac": "audio/flac",
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".mp4": "audio/mp4",
        ".mpeg": "audio/mpeg",
        ".mpga": "audio/mpeg",
        ".oga": "audio/ogg",
        ".ogg": "audio/ogg",
        ".wav": "audio/wav",
        ".webm": "audio/webm",
    }.get(file_path.suffix.lower(), "application/octet-stream")
    parts.extend(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{file_path.name}"\r\n'
            ).encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    request = urllib.request.Request(
        url,
        data=b"".join(parts),
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "LawHand-capability-canary/1",
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = json.load(response)
            return (
                response.status,
                body if isinstance(body, dict) else None,
                int((time.monotonic() - started) * 1000),
            )
    except urllib.error.HTTPError as exc:
        return exc.code, None, int((time.monotonic() - started) * 1000)
    except (OSError, TimeoutError, ValueError):
        return None, None, int((time.monotonic() - started) * 1000)


def _result(
    capability: str,
    provider: str,
    model: str,
    status: int | None,
    latency_ms: int,
    passed: bool,
    **metadata: Any,
) -> dict[str, Any]:
    result = {
        "capability": capability,
        "provider": provider,
        "model": model,
        "passed": passed,
        "http_status": status,
        "latency_ms": latency_ms,
    }
    if not passed:
        result["error_category"] = _error_category(status)
    result.update(metadata)
    return result


def text_canary(provider: str, url: str, key: str, model: str, timeout: int):
    status, body, latency = post_json(
        url,
        key,
        {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": "Synthetic API canary. Reply exactly CANARY_OK.",
                }
            ],
            "max_tokens": 20,
            "temperature": 0,
        },
        timeout,
    )
    answer = ""
    if body:
        try:
            answer = str(body["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError):
            pass
    return _result("text", provider, model, status, latency, answer == "CANARY_OK")


def responses_text_canary(provider: str, url: str, key: str, model: str, timeout: int):
    """Exercise providers, such as OpenCode Go, that only expose Responses."""
    status, body, latency = post_json(
        url,
        key,
        {
            "model": model,
            "input": "Synthetic API canary. Reply exactly CANARY_OK.",
            "max_output_tokens": 20,
        },
        timeout,
    )
    answer = str((body or {}).get("output_text") or "").strip()
    if not answer and body:
        parts: list[str] = []
        for output in body.get("output") or []:
            if not isinstance(output, dict):
                continue
            for content in output.get("content") or []:
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    parts.append(content["text"])
        answer = "".join(parts).strip()
    return _result("text", provider, model, status, latency, answer == "CANARY_OK")


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def red_png() -> bytes:
    width = height = 32
    rows = b"".join(b"\x00" + (b"\xff\x00\x00" * width) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(rows))
        + _png_chunk(b"IEND", b"")
    )


def vision_canary(url: str, key: str, model: str, timeout: int):
    image = base64.b64encode(red_png()).decode("ascii")
    status, body, latency = post_json(
        url,
        key,
        {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Synthetic vision canary. Reply exactly RED.",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{image}"},
                        },
                    ],
                }
            ],
            "max_tokens": 20,
            "temperature": 0,
        },
        timeout,
    )
    answer = ""
    if body:
        try:
            answer = str(body["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError):
            pass
    return _result("vision", "openrouter", model, status, latency, answer == "RED")


def _synthetic_pdf() -> bytes:
    from reportlab.pdfgen import canvas

    output = BytesIO()
    page = canvas.Canvas(output)
    page.drawString(72, 720, "PDF_CANARY_8421")
    page.save()
    return output.getvalue()


def pdf_canary(url: str, key: str, model: str, timeout: int):
    document = base64.b64encode(_synthetic_pdf()).decode("ascii")
    status, body, latency = post_json(
        url,
        key,
        {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Read the synthetic PDF. Reply exactly PDF_CANARY_8421.",
                        },
                        {
                            "type": "file",
                            "file": {
                                "filename": "canary.pdf",
                                "file_data": f"data:application/pdf;base64,{document}",
                            },
                        },
                    ],
                }
            ],
            "max_tokens": 30,
            "temperature": 0,
        },
        timeout,
    )
    answer = ""
    if body:
        try:
            answer = str(body["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError):
            pass
    return _result(
        "native_pdf", "openrouter", model, status, latency, answer == "PDF_CANARY_8421"
    )


def embedding_canary(url: str, key: str, model: str, timeout: int):
    status, body, latency = post_json(
        url,
        key,
        {"model": model, "input": "Synthetic embedding canary 8421."},
        timeout,
    )
    dimensions = 0
    if body:
        try:
            dimensions = len(body["data"][0]["embedding"])
        except (KeyError, IndexError, TypeError):
            pass
    return _result(
        "tenant_embedding",
        "openrouter",
        model,
        status,
        latency,
        dimensions == 1536,
        dimensions=dimensions or None,
    )


def transcription_canary(
    url: str,
    key: str,
    model: str,
    audio_file: Path,
    expected: str,
    timeout: int,
):
    status, body, latency = post_multipart(
        url,
        key,
        {"model": model, "language": "en"},
        "file",
        audio_file,
        timeout,
    )
    transcript = str((body or {}).get("text") or "")
    normalized = re.sub(r"[^a-z0-9]+", " ", transcript.lower()).strip()
    expected_normalized = re.sub(r"[^a-z0-9]+", " ", expected.lower()).strip()
    return _result(
        "speech_to_text",
        "openrouter",
        model,
        status,
        latency,
        bool(expected_normalized and expected_normalized in normalized),
    )


def local_document_canaries() -> list[dict[str, Any]]:
    from docx import Document

    text_processing_path = ROOT / "backend" / "app" / "utils" / "text_processing.py"
    spec = importlib.util.spec_from_file_location(
        "lawhand_canary_text_processing", text_processing_path
    )
    if not spec or not spec.loader:
        raise RuntimeError(f"Unable to load {text_processing_path}")
    text_processing = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(text_processing)
    extract_text = text_processing.extract_text

    docx_buffer = BytesIO()
    docx = Document()
    docx.add_paragraph("DOCX_CANARY_8421")
    docx.save(docx_buffer)
    fixtures = (
        ("txt", b"TXT_CANARY_8421", "text/plain", "canary.txt", "TXT_CANARY_8421"),
        (
            "docx",
            docx_buffer.getvalue(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "canary.docx",
            "DOCX_CANARY_8421",
        ),
        ("pdf", _synthetic_pdf(), "application/pdf", "canary.pdf", "PDF_CANARY_8421"),
    )
    results = []
    for capability, payload, content_type, filename, expected in fixtures:
        started = time.monotonic()
        try:
            passed = expected in extract_text(payload, content_type, filename)
        except Exception:  # Evidence is the status, never the parser response/body.
            passed = False
        results.append(
            {
                "capability": f"local_{capability}_extraction",
                "provider": "lawhand",
                "model": None,
                "passed": passed,
                "http_status": None,
                "latency_ms": int((time.monotonic() - started) * 1000),
            }
        )
    return results


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        action="append",
        type=Path,
        default=[],
        help="Explicit dotenv source",
    )
    parser.add_argument("--live", action="store_true", help="Run metered API canaries")
    parser.add_argument(
        "--audio-file", type=Path, help="Synthetic/consented STT fixture"
    )
    parser.add_argument(
        "--expected-transcript", default="LawHand canary seven four two"
    )
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--openrouter-text-model", default="openai/gpt-5.6-luna")
    parser.add_argument("--opencode-text-model", default="gpt-5.6-luna")
    parser.add_argument("--embedding-model", default="openai/text-embedding-3-small")
    parser.add_argument("--transcription-model", default="openai/gpt-transcribe")
    return parser.parse_args(argv)


def build_evidence(args: argparse.Namespace) -> dict[str, Any]:
    env_files = args.env_file or [ROOT / ".env.hypervisor"]
    values, sources = load_environment(env_files)
    inventory = [
        {
            "name": name,
            "state": credential_state(values.get(name)),
            "source": sources.get(name),
        }
        for name in KEY_NAMES
    ]
    results = local_document_canaries()
    blockers: list[str] = []

    if args.live:
        openrouter_key = values.get("OPENROUTER_API_KEY")
        if credential_state(openrouter_key) == "configured":
            chat_url = "https://openrouter.ai/api/v1/chat/completions"
            results.extend(
                [
                    text_canary(
                        "openrouter",
                        chat_url,
                        openrouter_key,
                        args.openrouter_text_model,
                        args.timeout,
                    ),
                    vision_canary(
                        chat_url,
                        openrouter_key,
                        args.openrouter_text_model,
                        args.timeout,
                    ),
                    pdf_canary(
                        chat_url,
                        openrouter_key,
                        args.openrouter_text_model,
                        args.timeout,
                    ),
                    embedding_canary(
                        "https://openrouter.ai/api/v1/embeddings",
                        openrouter_key,
                        args.embedding_model,
                        args.timeout,
                    ),
                ]
            )
            if args.audio_file and args.audio_file.exists():
                results.append(
                    transcription_canary(
                        "https://openrouter.ai/api/v1/audio/transcriptions",
                        openrouter_key,
                        args.transcription_model,
                        args.audio_file,
                        args.expected_transcript,
                        args.timeout,
                    )
                )
            else:
                blockers.append("speech_to_text_fixture_missing")
        else:
            blockers.append("openrouter_key_not_configured")

        opencode_go_key = values.get("OPENCODE_GO_API_KEY") or values.get(
            "DEEPSEEK_API_KEY"
        )
        if credential_state(opencode_go_key) == "configured":
            results.append(
                responses_text_canary(
                    "opencode-go",
                    "https://opencode.ai/zen/go/v1/responses",
                    opencode_go_key,
                    args.opencode_text_model,
                    args.timeout,
                )
            )
        else:
            blockers.append("opencode_go_key_not_configured")

    passed = all(result["passed"] for result in results) and not blockers
    return {
        "schema_version": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "live": args.live,
        "secrets_emitted": False,
        "passed": passed,
        "key_inventory": inventory,
        "blockers": blockers,
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    evidence = build_evidence(args)
    rendered = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    sys.stdout.write(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0 if evidence["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
