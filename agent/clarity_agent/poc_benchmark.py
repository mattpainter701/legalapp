"""Engine-agnostic evaluator for a local file-search PoC.

The evaluator consumes two JSONL files.  Judgments contain ``query_id`` and
``relevant`` (a list of ``{"doc_id": ..., "page": ...}`` objects); result
records contain ``query_id``, ``results`` (the ranked list of ``doc_id`` and
optional ``page``), and ``latency_ms``.  Optional ``coverage`` records may be
supplied as ``{"doc_id": ..., "status": ...}`` in a third JSONL file.

Only identifiers, counts, statuses, and timings are emitted.  Query text and
document content are deliberately neither required nor copied to output.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


TERMINAL_STATES = {
    "ready",
    "partial",
    "unsupported",
    "error",
    "timed_out",
    "skipped",
    "deleted",
}
READY_STATES = {"ready", "partial"}


def classify_coverage(status: Any) -> str:
    """Normalize an ingestion status into a stable reporting class."""
    value = str(status or "unknown").strip().lower().replace("-", "_")
    aliases = {
        "failed": "error",
        "timeout": "timed_out",
        "timedout": "timed_out",
        "in_progress": "pending",
    }
    value = aliases.get(value, value)
    return value if value in TERMINAL_STATES | {"pending", "unknown"} else "unknown"


def _objects(path: str | Path) -> Iterable[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError(f"line {number} is not a JSON object")
            yield item


def _doc_id(item: Any) -> str:
    if isinstance(item, dict):
        value = item.get("doc_id", item.get("file_id", item.get("id")))
    else:
        value = item
    return str(value) if value is not None else ""


def _page(item: Any) -> Any:
    return item.get("page", item.get("page_number")) if isinstance(item, dict) else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return round(ordered[low], 3)
    result = ordered[low] + (ordered[high] - ordered[low]) * (position - low)
    return round(result, 3)


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 3) if values else None


def _ndcg_at_k(ranked_ids: list[str], relevant: set[str], k: int) -> float | None:
    if not relevant:
        return None
    gain = sum(
        1 / math.log2(rank + 1)
        for rank, doc_id in enumerate(ranked_ids[:k], 1)
        if doc_id in relevant
    )
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(k, len(relevant)) + 1))
    return round(gain / ideal, 6) if ideal else None


def evaluate(
    judgments: Iterable[dict[str, Any]],
    result_records: Iterable[dict[str, Any]],
    coverage_records: Iterable[dict[str, Any]] = (),
    *,
    ks: tuple[int, ...] = (10, 50),
) -> dict[str, Any]:
    """Return deterministic aggregate metrics for judged queries."""
    judgment_map: dict[str, dict[str, Any]] = {}
    for item in judgments:
        query_id = str(item.get("query_id", ""))
        if not query_id:
            raise ValueError("judgment is missing query_id")
        if query_id in judgment_map:
            raise ValueError("judgments contain a duplicate query_id")
        relevant = [
            _doc_id(value)
            for value in item.get("relevant", item.get("relevant_doc_ids", []))
        ]
        pages = {
            _doc_id(value): _page(value)
            for value in item.get("relevant", [])
            if _doc_id(value) and _page(value) is not None
        }
        judgment_map[query_id] = {
            "relevant": {value for value in relevant if value},
            "pages": pages,
            "answerable": bool(item.get("answerable", bool(relevant))),
            "stratum": str(item.get("stratum", "unspecified")),
        }

    result_map: dict[str, dict[str, Any]] = {}
    for item in result_records:
        query_id = str(item.get("query_id", ""))
        if not query_id:
            raise ValueError("result record is missing query_id")
        if query_id in result_map:
            raise ValueError("results contain a duplicate query_id")
        result_map[query_id] = item

    coverage = Counter()
    coverage_docs: dict[str, str] = {}
    for item in coverage_records:
        doc_id = _doc_id(item)
        if not doc_id:
            raise ValueError("coverage record is missing doc_id")
        if doc_id in coverage_docs:
            raise ValueError("coverage contains a duplicate doc_id")
        status = classify_coverage(item.get("status"))
        coverage[status] += 1
        coverage_docs[doc_id] = status

    all_metrics: list[dict[str, Any]] = []
    latencies: list[float] = []
    for query_id in sorted(judgment_map):
        judgment = judgment_map[query_id]
        record = result_map.get(query_id, {})
        ranked = record.get("results", []) or []
        ranked_items: list[tuple[str, Any]] = []
        seen_ids: set[str] = set()
        for value in ranked:
            doc_id = _doc_id(value)
            if doc_id and doc_id not in seen_ids:
                seen_ids.add(doc_id)
                ranked_items.append((doc_id, _page(value)))
        ranked_ids = [doc_id for doc_id, _ in ranked_items]
        relevant = judgment["relevant"]
        first_rank = next(
            (rank for rank, doc_id in enumerate(ranked_ids, 1) if doc_id in relevant),
            None,
        )
        query_metrics: dict[str, Any] = {
            "query_id": query_id,
            "stratum": judgment["stratum"],
            "answerable": judgment["answerable"],
        }
        for k in ks:
            top = ranked_ids[:k]
            found = len(set(top) & relevant)
            query_metrics[f"recall@{k}"] = (
                round(found / len(relevant), 6) if relevant else None
            )
            query_metrics[f"precision@{k}"] = round(found / k, 6) if k else None
            query_metrics[f"ndcg@{k}"] = _ndcg_at_k(ranked_ids, relevant, k)
        query_metrics["mrr"] = round(1 / first_rank, 6) if first_rank else 0.0
        pages = judgment["pages"]
        query_metrics["correct_page"] = (
            bool(
                first_rank
                and ranked_items[first_rank - 1][0] in pages
                and ranked_items[first_rank - 1][1]
                == pages[ranked_items[first_rank - 1][0]]
            )
            if pages
            else None
        )
        if "latency_ms" in record and record["latency_ms"] is not None:
            latency = float(record["latency_ms"])
            if math.isfinite(latency) and latency >= 0:
                latencies.append(latency)
        all_metrics.append(query_metrics)

    def average(name: str, rows: list[dict[str, Any]]) -> float | None:
        values = [float(row[name]) for row in rows if row.get(name) is not None]
        return round(statistics.fmean(values), 6) if values else None

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in all_metrics:
        groups.setdefault(str(row["stratum"]), []).append(row)
    coverage_total = sum(coverage.values())
    coverage_terminal = sum(coverage[state] for state in TERMINAL_STATES)
    coverage_ready = sum(coverage[state] for state in READY_STATES)
    aggregate: dict[str, Any] = {
        "queries": len(all_metrics),
        "matched_result_records": sum(q in result_map for q in judgment_map),
        "coverage_provided": bool(coverage_docs),
        "coverage": dict(sorted(coverage.items())),
        "coverage_terminal_rate": (
            round(coverage_terminal / coverage_total, 6) if coverage_total else None
        ),
        "coverage_ready_rate": (
            round(coverage_ready / coverage_total, 6) if coverage_total else None
        ),
    }
    for k in ks:
        aggregate[f"recall@{k}"] = average(f"recall@{k}", all_metrics)
        aggregate[f"precision@{k}"] = average(f"precision@{k}", all_metrics)
        aggregate[f"ndcg@{k}"] = average(f"ndcg@{k}", all_metrics)
    aggregate["mrr"] = average("mrr", all_metrics)
    page_values = [
        row["correct_page"] for row in all_metrics if row["correct_page"] is not None
    ]
    aggregate["correct_page_rate"] = (
        round(sum(page_values) / len(page_values), 6) if page_values else None
    )
    aggregate["latency_ms"] = {
        "count": len(latencies),
        "p50": _percentile(latencies, 50),
        "p95": _percentile(latencies, 95),
        "p99": _percentile(latencies, 99),
        "mean": _mean(latencies),
    }
    aggregate["by_stratum"] = {
        name: {
            "queries": len(rows),
            **{f"recall@{k}": average(f"recall@{k}", rows) for k in ks},
            **{f"precision@{k}": average(f"precision@{k}", rows) for k in ks},
            **{f"ndcg@{k}": average(f"ndcg@{k}", rows) for k in ks},
            "mrr": average("mrr", rows),
        }
        for name, rows in sorted(groups.items())
    }
    relevant_docs = {
        doc_id for judgment in judgment_map.values() for doc_id in judgment["relevant"]
    }
    aggregate["uncovered_relevant_docs"] = (
        sum(coverage_docs.get(doc_id) not in READY_STATES for doc_id in relevant_docs)
        if coverage_docs
        else None
    )
    return aggregate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("judgments", help="judged-query JSONL")
    parser.add_argument("results", help="result-record JSONL")
    parser.add_argument("--coverage", help="optional ingestion coverage JSONL")
    parser.add_argument(
        "--output", help="write aggregate JSON here; defaults to stdout"
    )
    args = parser.parse_args(argv)
    output = evaluate(
        _objects(args.judgments),
        _objects(args.results),
        _objects(args.coverage) if args.coverage else (),
    )
    encoded = json.dumps(output, sort_keys=True, separators=(",", ":"))
    if args.output:
        Path(args.output).write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)
    return 0


if __name__ == "__main__":
    sys.exit(main())
