"""Synthetic regression evidence: no legal conclusions or live provider calls."""

import copy
import json
from pathlib import Path

import pytest

from app.research_quality import evaluate_research, evidence_digest, main


def evidence():
    dataset = {
        "id": "synthetic-research-contract-v1",
        "evidence_kind": "synthetic",
        "thresholds": {
            "min_authority_recall": 1,
            "max_latency_ms": 1000,
            "max_cost_usd": 0.1,
        },
        "cases": [
            {
                "id": "fixture-question",
                "question": "What does the fictional rule require?",
                "allowed_jurisdictions": ["ND"],
                "expected_authorities": ["fictional:rule"],
                "expected_propositions": ["fixture-notice"],
                "must_abstain": False,
            }
        ],
    }
    row = {
        "id": "fixture-question",
        "answer": "The fictional rule requires notice.",
        "sources": [
            {
                "id": "source-1",
                "authority_id": "fictional:rule",
                "jurisdiction": "ND",
                "url": "https://example.invalid/rule",
                "content": "The fictional rule requires notice.",
                "currentness": "unknown",
            }
        ],
        "claims": [
            {
                "id": "claim-1",
                "source_ids": ["source-1"],
                "quotes": [{"source_id": "source-1", "text": "requires notice"}],
            }
        ],
        "abstained": False,
        "disclosed_gaps": ["currentness_not_established"],
        "latency_ms": 200,
        "cost_usd": 0.01,
    }
    run = {
        "evidence_kind": "synthetic",
        "candidate_sha": "fixture-sha",
        "captured_at": "2026-09-06",
        "corpus": {
            "version": "fixture",
            "manifest_hash": "fixture-hash",
            "as_of": "2026-09-06",
            "coverage_artifact": "synthetic.json",
        },
        "observations": [row],
        "reviews": {},
    }
    return dataset, run


def review(run):
    row = run["observations"][0]
    run["reviews"][row["id"]] = {
        "reviewer": "synthetic-reviewer",
        "reviewed_at": "2026-09-06",
        "observation_digest": evidence_digest(row),
        "jurisdiction_correct": True,
        "proposition_support": True,
        "treatment_currentness": True,
        "uncertainty_appropriate": True,
        "answer_complete": True,
        "supported_propositions": ["fixture-notice"],
        "supported_claims": [claim["id"] for claim in row["claims"]],
    }


def test_synthetic_success_cannot_be_customer_acceptance():
    dataset, run = evidence()
    review(run)
    report = evaluate_research(dataset, run)
    assert report["cases"][0]["passed"]
    assert not report["accepted"]
    assert report["evidence_kind"] == "synthetic"
    assert "synthetic_or_unclassified_evidence" in report["run_failures"]


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda r: r["sources"][0].update(jurisdiction="CA"), "jurisdiction"),
        (lambda r: r["sources"][0].pop("jurisdiction"), "jurisdiction"),
        (
            lambda r: r["sources"][0].update(authority_id="fictional:other"),
            "authority_recall",
        ),
        (lambda r: r["sources"][0].update(url=""), "source_evidence"),
        (
            lambda r: r["claims"][0].update(source_ids=["missing"]),
            "unresolved_claim_citation",
        ),
        (
            lambda r: r["claims"][0]["quotes"][0].update(text="forged quote"),
            "quote_integrity",
        ),
        (lambda r: r.update(abstained=True), "abstention"),
        (lambda r: r.update(claims=[]), "missing_question_expectations_or_answer"),
        (lambda r: r.update(latency_ms=1001), "latency_ms"),
        (lambda r: r.update(cost_usd=None), "cost_usd"),
        (lambda r: r.update(disclosed_gaps=[]), "undisclosed_currentness"),
        (
            lambda r: r["claims"][0].update(asserts_current_law=True),
            "unsupported_current_law_claim",
        ),
        (
            lambda r: r["sources"].append(copy.deepcopy(r["sources"][0])),
            "duplicate_source_ids",
        ),
        (
            lambda r: r["claims"].append(copy.deepcopy(r["claims"][0])),
            "duplicate_claim_ids",
        ),
    ],
)
def test_mechanical_regressions_fail_even_with_review(mutation, reason):
    dataset, run = evidence()
    mutation(run["observations"][0])
    review(run)
    assert reason in evaluate_research(dataset, run)["cases"][0]["failures"]


def test_review_is_bound_to_answer_sources_and_cost_not_just_case_id():
    dataset, run = evidence()
    review(run)
    run["observations"][0]["answer"] = "Changed conclusion."
    assert (
        "attorney_review_required_or_failed"
        in evaluate_research(dataset, run)["cases"][0]["failures"]
    )


def test_missing_or_failed_semantic_review_never_implied_by_quote_match():
    dataset, run = evidence()
    report = evaluate_research(dataset, run)
    assert "attorney_review_required_or_failed" in report["cases"][0]["failures"]
    assert "claim_support" in report["cases"][0]["failures"]
    review(run)
    run["reviews"]["fixture-question"].update(
        proposition_support=False, supported_propositions=[]
    )
    assert (
        "proposition_support" in evaluate_research(dataset, run)["cases"][0]["failures"]
    )


def test_missing_corpus_run_identity_and_observations_fail_closed():
    dataset, run = evidence()
    run.update(corpus={}, observations=[], candidate_sha=None)
    report = evaluate_research(dataset, run)
    assert set(report["run_failures"]) >= {
        "missing_corpus_evidence",
        "missing_run_identity",
        "observation_set_mismatch",
    }
    assert report["cases"][0]["failures"] == ["missing_observation"]


def test_expected_abstention_and_outage_disclosure():
    dataset, run = evidence()
    dataset["cases"][0].update(
        must_abstain=True,
        expected_authorities=[],
        expected_propositions=[],
        required_gaps=["source_unavailable"],
    )
    run["observations"][0].update(
        sources=[], claims=[], abstained=True, disclosed_gaps=["source_unavailable"]
    )
    review(run)
    assert evaluate_research(dataset, run)["cases"][0]["passed"]
    run["observations"][0]["disclosed_gaps"] = []
    assert "undisclosed_gap" in evaluate_research(dataset, run)["cases"][0]["failures"]


@pytest.mark.parametrize(
    "key,value",
    [
        ("max_cost_usd", float("inf")),
        ("max_latency_ms", -1),
        ("max_cost_usd", True),
        ("min_authority_recall", 0),
        ("min_authority_recall", 2),
    ],
)
def test_invalid_thresholds_rejected(key, value):
    dataset, run = evidence()
    dataset["thresholds"][key] = value
    with pytest.raises(ValueError):
        evaluate_research(dataset, run)


def test_duplicate_ids_and_empty_dataset_rejected():
    dataset, run = evidence()
    run["observations"] *= 2
    with pytest.raises(ValueError, match="Observation"):
        evaluate_research(dataset, run)
    dataset["cases"] *= 2
    with pytest.raises(ValueError, match="unique"):
        evaluate_research(dataset, run)
    dataset["cases"] = []
    with pytest.raises(ValueError, match="nonempty"):
        evaluate_research(dataset, run)


def test_cli_writes_report_and_fails_for_synthetic(tmp_path):
    dataset, run = evidence()
    paths = [tmp_path / name for name in ("set.json", "run.json", "report.json")]
    paths[0].write_text(json.dumps(dataset))
    paths[1].write_text(json.dumps(run))
    assert main([str(paths[0]), str(paths[1]), "--output", str(paths[2])]) == 1
    assert not json.loads(paths[2].read_text())["accepted"]


def test_only_complete_reviewed_customer_contract_accepts():
    # Simulates the acceptance schema, not an actual customer or attorney result.
    dataset, run = evidence()
    dataset.update(
        evidence_kind="attorney_defined",
        approved_by="fixture-attorney",
        approved_at="2026-09-06",
    )
    run["evidence_kind"] = "customer_capture"
    review(run)
    assert evaluate_research(dataset, run)["accepted"]


def test_committed_synthetic_example_remains_rejected():
    root = Path(__file__).parent / "fixtures" / "research_quality"
    report = evaluate_research(
        json.loads((root / "synthetic-set.json").read_text()),
        json.loads((root / "synthetic-run.json").read_text()),
    )
    assert not report["accepted"]
    assert len(report["cases"]) == 3
    assert all(
        "attorney_review_required_or_failed" in case["failures"]
        for case in report["cases"]
    )


def test_unknown_reviewer_cannot_approve():
    dataset, run = evidence()
    review(run)
    run["reviews"]["fixture-question"]["reviewer"] = "unknown"
    assert not evaluate_research(dataset, run)["cases"][0]["passed"]
