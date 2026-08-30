"""Deterministic Brief Check analysis with explicit evidence boundaries."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher


MAX_BYTES = 15 * 1024 * 1024
MAX_CHARS = 1_500_000
MAX_CITATIONS = 500
_CASE = re.compile(
    r"\b(?P<volume>\d{1,4})\s+(?P<reporter>[A-Z][A-Za-z0-9. ]{1,30}?)\s+(?P<page>\d{1,5})(?:\s*,\s*(?P<pin>\d{1,5}))?\b"
)
_STATUTE = re.compile(
    r"\b(?P<title>\d{1,3})\s+(?P<code>U\.S\.C\.|C\.F\.R\.|R\.\s*C\.)\s*(?:§|sec\.?|section)\s*(?P<section>[\w.-]+)",
    re.I,
)
_SHORT = re.compile(r"\b(?P<name>[A-Z][A-Za-z'’-]{2,40})\s+at\s+(?P<pin>\d{1,5})\b")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _snippet(text: str, start: int, end: int, radius: int = 180) -> str:
    return (
        text[max(0, start - radius) : min(len(text), end + radius)]
        .replace("\n", " ")
        .strip()
    )


def _citation_items(text: str) -> list[dict]:
    items: list[dict] = []
    occupied: list[tuple[int, int]] = []
    for match in list(_CASE.finditer(text)) + list(_STATUTE.finditer(text)):
        raw = " ".join(match.group(0).split())
        kind = "case" if "volume" in match.groupdict() else "statute"
        canonical = raw.replace("sec.", "§").replace("section", "§")
        item_id = f"citation-{len(items) + 1}"
        items.append(
            {
                "id": item_id,
                "input": raw,
                "canonical": canonical,
                "base_canonical": re.sub(r",\s*\d+$", "", canonical),
                "kind": kind,
                "status": "unknown",
                "confidence": 0.0,
                "ambiguity": "not_resolved",
                "source_identity": None,
                "source_tier": None,
                "retrieved_at": None,
                "corpus_version": None,
                "location": {
                    "char_start": match.start(),
                    "char_end": match.end(),
                    "pin": match.groupdict().get("pin"),
                },
                "limitations": ["No accessible source text was supplied to this run."],
            }
        )
        occupied.append((match.start(), match.end()))
    for match in _SHORT.finditer(text):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        items.append(
            {
                "id": f"citation-{len(items) + 1}",
                "input": match.group(0),
                "canonical": match.group(0),
                "base_canonical": match.group(0),
                "kind": "short_form",
                "status": "ambiguous",
                "confidence": 0.2,
                "ambiguity": "short_form_requires_context",
                "source_identity": None,
                "source_tier": None,
                "retrieved_at": None,
                "corpus_version": None,
                "location": {
                    "char_start": match.start(),
                    "char_end": match.end(),
                    "pin": match.group("pin"),
                },
                "limitations": [
                    "Short form cannot be resolved without a preceding full citation."
                ],
            }
        )
    return items[:MAX_CITATIONS]


def _resolve(items: list[dict], sources: list[dict], retrieved_at: str) -> None:
    for item in items:
        matches = []
        for source in sources:
            aliases = [str(source.get("citation", "")), str(source.get("title", ""))]
            if any(
                item["canonical"].lower() in alias.lower()
                or alias.lower() in item["canonical"].lower()
                for alias in aliases
                if alias
            ):
                matches.append(source)
        if len(matches) == 1:
            source = matches[0]
            item.update(
                status="resolved",
                confidence=0.95,
                ambiguity=None,
                source_identity=source.get("source_id"),
                source_tier=source.get("source_tier", "unknown"),
                retrieved_at=source.get("retrieved_at", retrieved_at),
                corpus_version=source.get("corpus_version"),
            )
            item["limitations"] = list(source.get("limitations", []))
        elif len(matches) > 1:
            item.update(
                status="ambiguous", confidence=0.35, ambiguity="multiple_sources"
            )
            item["limitations"] = [
                "Multiple accessible records matched; attorney selection is required."
            ]
        elif item["status"] == "unknown":
            item.update(status="missing_source", ambiguity="no_accessible_match")
            item["limitations"] = [
                "No accessible source record matched; absence is not evidence that the authority does not exist."
            ]


def _verify_quotes(text: str, sources: list[dict]) -> list[dict]:
    quotes = []
    for index, match in enumerate(re.finditer(r"[\"“](.{25,500}?)[\"”]", text, re.S)):
        quote = " ".join(match.group(1).split())
        candidates = []
        for source in sources:
            source_text = " ".join(str(source.get("text", "")).split())
            if not source_text:
                continue
            ratio = SequenceMatcher(None, quote.lower(), source_text.lower()).ratio()
            if quote.lower() in source_text.lower():
                ratio = 1.0
            candidates.append((ratio, source))
        candidates.sort(key=lambda pair: pair[0], reverse=True)
        best = candidates[0] if candidates else (0.0, {})
        status = (
            "verified"
            if best[0] == 1.0
            else "mismatch"
            if best[0] >= 0.35
            else "unknown"
        )
        quotes.append(
            {
                "id": f"quote-{index + 1}",
                "quoted_text": quote,
                "status": status,
                "confidence": round(best[0], 3),
                "source_identity": best[1].get("source_id"),
                "source_tier": best[1].get("source_tier"),
                "location": {"char_start": match.start(), "char_end": match.end()},
                "mismatch_context": _snippet(
                    str(best[1].get("text", "")),
                    0,
                    min(240, len(str(best[1].get("text", "")))),
                )
                if status == "mismatch"
                else None,
                "limitations": ["Exact verification requires accessible source text."]
                if status == "unknown"
                else [],
            }
        )
    return quotes[:MAX_CITATIONS]


def analyze_brief(
    text: str,
    *,
    sources: list[dict] | None = None,
    opposing_text: str | None = None,
    corpus_version: str | None = None,
) -> dict:
    text = text[:MAX_CHARS]
    sources = sources or []
    retrieved_at = datetime.now(timezone.utc).isoformat()
    citations = _citation_items(text)
    _resolve(citations, sources, retrieved_at)
    quotes = _verify_quotes(text, sources)
    candidate_queries = [
        item["canonical"]
        for item in citations
        if item["status"] in {"resolved", "missing_source"}
    ][:25]
    suggestions = [
        {
            "id": f"authority-{i + 1}",
            "query": query,
            "type": "supporting_or_contrary_candidate",
            "status": "candidate_only",
            "confidence": 0.25,
            "source_identity": None,
            "source_tier": None,
            "retrieved_at": retrieved_at,
            "corpus_version": corpus_version,
            "limitations": [
                "Bounded citation-led retrieval only; this is not comprehensive coverage.",
                "Candidate requires attorney review and source-text verification.",
            ],
        }
        for i, query in enumerate(candidate_queries)
    ]
    comparison = None
    if opposing_text is not None:
        left = {item["base_canonical"].lower() for item in citations}
        right = {
            item["base_canonical"].lower() for item in _citation_items(opposing_text)
        }
        comparison = {
            "shared_citations": sorted(left & right),
            "only_in_brief": sorted(left - right),
            "only_in_opposing_brief": sorted(right - left),
            "limitations": [
                "Comparison is citation-set based and does not determine legal significance."
            ],
        }
    return {
        "schema_version": 1,
        "review_first": True,
        "generated_at": retrieved_at,
        "corpus_version": corpus_version,
        "citations": citations,
        "quotations": quotes,
        "treatment_currentness": {
            "status": "unknown",
            "signals": [],
            "limitations": [
                "No negative treatment or currentness conclusion is inferred from absence of evidence."
            ],
        },
        "omitted_authority_candidates": suggestions,
        "opposing_brief_comparison": comparison,
        "coverage": {
            "mode": "bounded",
            "comprehensive": False,
            "limitations": [
                "Results depend on configured sources, access rights, query bounds, and corpus freshness."
            ],
        },
        "customer_visible_limitations": [
            "Brief Check is a review aid, not legal advice or a good-law determination.",
            "Attorney review is required for every unresolved, mismatched, stale, or candidate item.",
        ],
    }


def report_markdown(result: dict) -> str:
    lines = [
        "# Brief Check Review Report",
        "",
        "> Review-first output. No item is labeled good law from absence of a negative record.",
        "",
        f"Generated: {result.get('generated_at')}",
        "",
        "## Citations",
    ]
    for item in result.get("citations", []):
        lines.append(
            f"- **{item['id']}** `{item['input']}` → **{item['status']}**; source `{item.get('source_identity') or 'none'}`; confidence {item.get('confidence', 0)}. Location: {item['location']}."
        )
    lines += ["", "## Quotations"]
    for item in result.get("quotations", []):
        lines.append(
            f"- **{item['id']}** {item['status']}; confidence {item['confidence']}; source `{item.get('source_identity') or 'none'}`. `{item['quoted_text'][:240]}`"
        )
    lines += [
        "",
        "## Treatment/currentness",
        "- Status: **unknown unless an accessible, timestamped signal is present**.",
        "",
        "## Omitted-authority candidates",
    ]
    for item in result.get("omitted_authority_candidates", []):
        lines.append(
            f"- `{item['query']}` — candidate only; bounded retrieval, not comprehensive coverage."
        )
    lines += ["", "## Limitations"] + [
        f"- {value}" for value in result.get("customer_visible_limitations", [])
    ]
    return "\n".join(lines) + "\n"


def table_of_authorities_markdown(result: dict) -> str:
    lines = [
        "# Table of Authorities Draft",
        "",
        "| Citation | Status | Source | Review state |",
        "|---|---|---|---|",
    ]
    for item in result.get("citations", []):
        lines.append(
            f"| {item['canonical']} | {item['status']} | {item.get('source_identity') or 'Not resolved'} | Attorney review required |"
        )
    return "\n".join(lines) + "\n"
