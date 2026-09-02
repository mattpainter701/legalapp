"""JSON-in/JSON-out parser child. Never import this module in an API process."""

from __future__ import annotations

import json
import os
import sys
from importlib.resources import files
from pathlib import Path

from .config import Limits
from .contracts import TerminalStatus
from .parser import ParseFailure, Parser, serialize_sections


def _limits(payload: dict) -> Limits:
    allowed = set(Limits.__dataclass_fields__)
    values = payload.get("limits")
    if not isinstance(values, dict) or set(values) != allowed:
        raise ValueError("invalid limits contract")
    return Limits(**{key: int(values[key]) for key in allowed})


def main() -> int:
    # A parser has no legitimate proxy/network configuration. The production
    # container also has network_mode:none; clearing these prevents accidental
    # use in explicitly permitted test sandboxes.
    for key in tuple(os.environ):
        if key.lower().endswith("_proxy") or key.lower() in {"no_proxy", "all_proxy"}:
            os.environ.pop(key, None)
    try:
        payload = json.loads(sys.stdin.buffer.read(64 * 1024))
        path = Path(payload["source_path"])
        limits = _limits(payload)
        threshold = int(payload.get("low_text_chars_per_page", 80))
        if not 0 <= threshold <= 5000:
            raise ValueError("invalid low-text threshold")
        tika_jar_raw = os.getenv("SEARCH_NODE_TIKA_APP_JAR")
        tika_jar = Path(tika_jar_raw) if tika_jar_raw else None
        tika_config = Path(str(files("search_node").joinpath("tika-config.xml")))
        parser = Parser(
            limits,
            low_text_chars_per_page=threshold,
            tika_app_jar=tika_jar,
            tika_config=tika_config,
        )
        sections, media_type, ocr_pages = parser.parse(path)
        result = {
            "status": TerminalStatus.INDEXED_READY.value,
            "media_type": media_type,
            "sections": serialize_sections(sections),
            "ocr_candidate_pages": list(ocr_pages),
        }
    except ParseFailure as exc:
        result = {"status": exc.status.value, "error_code": exc.code, "sections": []}
    except Exception:
        result = {
            "status": TerminalStatus.CORRUPT.value,
            "error_code": "parser-child-invalid-request",
            "sections": [],
        }
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
