import pytest

from clarity_agent.poc_benchmark import classify_coverage, evaluate


def test_coverage_aliases_are_stable():
    assert classify_coverage("failed") == "error"
    assert classify_coverage("timeout") == "timed_out"
    assert classify_coverage("never_seen") == "unknown"


def test_metrics_pages_duplicates_and_latency_are_deterministic():
    judgments = [
        {
            "query_id": "q2",
            "stratum": "scanned",
            "relevant": [{"doc_id": "b", "page": 4}],
        },
        {
            "query_id": "q1",
            "stratum": "born_digital",
            "relevant": [{"doc_id": "a", "page": 2}, {"doc_id": "b", "page": 1}],
        },
    ]
    results = [
        {
            "query_id": "q1",
            "latency_ms": 30,
            "results": [{"doc_id": "a", "page": 2}, {"doc_id": "a", "page": 9}],
        },
        {"query_id": "q2", "latency_ms": 10, "results": [{"doc_id": "b", "page": 3}]},
    ]
    report = evaluate(
        judgments,
        results,
        [{"doc_id": "a", "status": "ready"}, {"doc_id": "b", "status": "unsupported"}],
    )
    assert report["queries"] == 2
    assert report["matched_result_records"] == 2
    assert report["recall@10"] == 0.75
    assert report["precision@10"] == 0.1
    assert report["mrr"] == 1.0
    assert report["correct_page_rate"] == 0.5
    assert report["latency_ms"]["p50"] == 20
    assert report["latency_ms"]["p95"] == 29
    assert report["coverage"] == {"ready": 1, "unsupported": 1}
    assert report["coverage_provided"] is True
    assert report["coverage_terminal_rate"] == 1.0
    assert report["coverage_ready_rate"] == 0.5
    assert report["uncovered_relevant_docs"] == 1
    assert "query text" not in str(report)


def test_empty_and_unanswerable_inputs_do_not_divide_by_zero():
    report = evaluate(
        [{"query_id": "q", "answerable": False, "relevant": []}],
        [{"query_id": "q", "results": []}],
    )
    assert report["recall@10"] is None
    assert report["precision@10"] == 0.0
    assert report["mrr"] == 0.0
    assert report["correct_page_rate"] is None
    assert report["latency_ms"]["p95"] is None
    assert report["coverage_provided"] is False
    assert report["uncovered_relevant_docs"] is None


def test_duplicate_identifiers_are_rejected():
    with pytest.raises(ValueError, match="duplicate query_id"):
        evaluate(
            [
                {"query_id": "q1", "relevant": []},
                {"query_id": "q1", "relevant": []},
            ],
            [],
        )
    with pytest.raises(ValueError, match="duplicate query_id"):
        evaluate(
            [{"query_id": "q1", "relevant": []}],
            [{"query_id": "q1"}, {"query_id": "q1"}],
        )
