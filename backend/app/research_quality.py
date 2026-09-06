"""Offline research acceptance scoring; never supplies attorney judgments.

Observations are captured outputs, not a live provider call. Mechanical checks
are reported separately from a human review bound to their exact digest.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


def evidence_digest(observation: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            observation, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _identity(value: object) -> bool:
    return isinstance(value, str) and value.strip().casefold() not in {
        "",
        "unknown",
        "tbd",
        "pending",
        "none",
    }


def _number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def evaluate_research(dataset: dict, run: dict) -> dict:
    """Fail closed on missing evidence; accepted means this set only, not parity.

    Proposition-to-source semantics and legal treatment remain human judgments.
    Exact quote containment is only a mechanical integrity check.
    """
    cases = dataset["cases"]
    ids = [case["id"] for case in cases]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("A nonempty evaluation set with unique case IDs is required")
    thresholds = dataset["thresholds"]
    for key in ("min_authority_recall", "max_latency_ms", "max_cost_usd"):
        if not _number(thresholds.get(key)):
            raise ValueError(f"A finite nonnegative {key} threshold is required")
    if not 0 < thresholds["min_authority_recall"] <= 1:
        raise ValueError("min_authority_recall must be in (0, 1]")
    observations = run["observations"]
    if len(observations) != len({row["id"] for row in observations}):
        raise ValueError("Observation IDs must be unique")
    observed = {row["id"]: row for row in observations}
    run_failures = []
    if set(observed) != set(ids):
        run_failures.append("observation_set_mismatch")
    corpus = run.get("corpus", {})
    if not all(
        corpus.get(key)
        for key in ("version", "manifest_hash", "as_of", "coverage_artifact")
    ):
        run_failures.append("missing_corpus_evidence")
    if not run.get("candidate_sha") or not run.get("captured_at"):
        run_failures.append("missing_run_identity")
    results = []
    for case in cases:
        row = observed.get(case["id"])
        failures = []
        if row is None:
            results.append(
                {"id": case["id"], "passed": False, "failures": ["missing_observation"]}
            )
            continue
        sources = row.get("sources", [])
        by_id = {source["id"]: source for source in sources}
        if len(by_id) != len(sources):
            failures.append("duplicate_source_ids")
        expected = set(case.get("expected_authorities", []))
        found = {source.get("authority_id") for source in sources}
        recall = len(expected & found) / len(expected) if expected else None
        if recall is not None and recall < thresholds["min_authority_recall"]:
            failures.append("authority_recall")
        allowed = case.get("allowed_jurisdictions", [])
        if not allowed or any(
            source.get("jurisdiction") not in allowed for source in sources
        ):
            failures.append("jurisdiction")
        if any(
            not source.get("url", "").startswith(("https://", "http://"))
            or not source.get("content")
            for source in sources
        ):
            failures.append("source_evidence")
        unverified = {
            source["id"]
            for source in sources
            if source.get("currentness") != "attorney_verified"
        }
        if unverified and "currentness_not_established" not in row.get(
            "disclosed_gaps", []
        ):
            failures.append("undisclosed_currentness")
        claims = row.get("claims", [])
        claim_ids = [claim["id"] for claim in claims]
        if len(claim_ids) != len(set(claim_ids)):
            failures.append("duplicate_claim_ids")
        for claim in claims:
            evidence = claim.get("source_ids", [])
            if not evidence or any(source_id not in by_id for source_id in evidence):
                failures.append("unresolved_claim_citation")
            if claim.get("asserts_current_law") and any(
                source_id in unverified for source_id in evidence
            ):
                failures.append("unsupported_current_law_claim")
            for quote in claim.get("quotes", []):
                source = by_id.get(quote.get("source_id"), {})
                if (
                    not quote.get("text")
                    or quote["text"] not in source.get("content", "")
                    or quote.get("source_id") not in evidence
                ):
                    failures.append("quote_integrity")
        abstain = case.get("must_abstain") is True
        if row.get("abstained") is not abstain or (abstain and claims):
            failures.append("abstention")
        expected_propositions = set(case.get("expected_propositions", []))
        if not abstain and (not expected or not expected_propositions or not claims):
            failures.append("missing_question_expectations_or_answer")
        if any(
            gap not in row.get("disclosed_gaps", [])
            for gap in case.get("required_gaps", [])
        ):
            failures.append("undisclosed_gap")
        for field, threshold in (
            ("latency_ms", "max_latency_ms"),
            ("cost_usd", "max_cost_usd"),
        ):
            if not _number(row.get(field)) or row[field] > thresholds[threshold]:
                failures.append(field)
        # A signed-in application identity is not asserted here: this is an
        # operator evidence file, whose provenance must be checked at acceptance.
        review = run.get("reviews", {}).get(case["id"], {})
        reviewed = bool(
            _identity(review.get("reviewer"))
            and review.get("reviewed_at")
            and review.get("observation_digest") == evidence_digest(row)
        )
        required_checks = (
            "jurisdiction_correct",
            "proposition_support",
            "treatment_currentness",
            "uncertainty_appropriate",
            "answer_complete",
        )
        if not reviewed or any(review.get(key) is not True for key in required_checks):
            failures.append("attorney_review_required_or_failed")
        if not expected_propositions.issubset(
            set(review.get("supported_propositions", []))
        ):
            failures.append("proposition_support")
        if set(review.get("supported_claims", [])) != set(claim_ids):
            failures.append("claim_support")
        results.append(
            {
                "id": case["id"],
                "passed": not failures,
                "authority_recall": recall,
                "failures": sorted(set(failures)),
                "observation_digest": evidence_digest(row),
            }
        )
    synthetic = (
        dataset.get("evidence_kind") != "attorney_defined"
        or run.get("evidence_kind") != "customer_capture"
    )
    if synthetic:
        run_failures.append("synthetic_or_unclassified_evidence")
    if not _identity(dataset.get("approved_by")) or not dataset.get("approved_at"):
        run_failures.append("evaluation_set_unapproved")
    return {
        "schema_version": 1,
        "dataset_id": dataset.get("id"),
        "dataset_digest": evidence_digest(dataset),
        "run_digest": evidence_digest(run),
        "evidence_kind": "synthetic" if synthetic else "customer_capture",
        "accepted": not run_failures and all(result["passed"] for result in results),
        "run_failures": run_failures,
        "cases": results,
        "notice": "Mechanical checks are not legal accuracy judgments. Acceptance applies only to this reviewed set, corpus and candidate; no parity or good-law claim.",
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Score offline research evidence; never calls providers or ingests content"
    )
    parser.add_argument("dataset", type=Path)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = evaluate_research(
        json.loads(args.dataset.read_text(encoding="utf-8")),
        json.loads(args.run.read_text(encoding="utf-8")),
    )
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
