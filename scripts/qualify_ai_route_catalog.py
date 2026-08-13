#!/usr/bin/env python3
"""Qualify paid AI route candidates against a timestamped provider catalog.

This is a catalog gate, not a legal-quality benchmark. It intentionally uses
only the Python standard library, never reads provider keys, and emits no
customer content. A passing result proves price/capability/context metadata is
present at the observation time; synthetic completions and the BK24 legal eval
must still pass before activation.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATES = ROOT / "ops" / "ai-route-candidates.v1.json"
DEFAULT_TIMEOUT_SECONDS = 30


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return payload


def fetch_catalog(url: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "LawHand-route-qualifier/1",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("Provider catalog response must be a JSON object")
    return payload


def _is_free(model_id: str, prompt: Decimal | None, completion: Decimal | None) -> bool:
    normalized = model_id.lower()
    return (
        ":free" in normalized
        or normalized.endswith("-free")
        or (prompt == 0 and completion == 0)
    )


def qualify_candidates(
    candidates: dict[str, Any],
    catalog: dict[str, Any],
    *,
    observed_at: str | None = None,
) -> dict[str, Any]:
    policy = candidates.get("policy") or {}
    minimum_context = int(policy.get("minimum_context_tokens") or 0)
    required = set(policy.get("required_parameters") or [])
    required_any = set(policy.get("required_any_parameters") or [])
    scenario_input = int(policy.get("scenario_input_tokens") or 0)
    scenario_output = int(policy.get("scenario_output_tokens") or 0)

    rows = catalog.get("data") or catalog.get("models") or []
    index = {
        str(row.get("id")): row
        for row in rows
        if isinstance(row, dict) and row.get("id")
    }
    results: list[dict[str, Any]] = []

    for tier, tier_candidates in (candidates.get("tiers") or {}).items():
        for candidate in tier_candidates or []:
            model_id = str(candidate.get("model") or "")
            row = index.get(model_id)
            reasons: list[str] = []
            pricing = row.get("pricing") if isinstance(row, dict) else {}
            pricing = pricing if isinstance(pricing, dict) else {}
            prompt = _decimal(pricing.get("prompt"))
            completion = _decimal(pricing.get("completion"))
            context = int((row or {}).get("context_length") or 0)
            parameters = set((row or {}).get("supported_parameters") or [])

            if row is None:
                reasons.append("model_not_found")
            if prompt is None or completion is None:
                reasons.append("price_missing")
            elif _is_free(model_id, prompt, completion):
                reasons.append("free_capacity")
            if context < minimum_context:
                reasons.append("context_below_minimum")
            for parameter in sorted(required - parameters):
                reasons.append(f"missing_parameter:{parameter}")
            if required_any and not (required_any & parameters):
                reasons.append(
                    "missing_any_parameter:" + ",".join(sorted(required_any))
                )

            per_million = Decimal(1_000_000)
            input_per_million = prompt * per_million if prompt is not None else None
            output_per_million = (
                completion * per_million if completion is not None else None
            )
            scenario_cost = None
            if prompt is not None and completion is not None:
                scenario_cost = prompt * scenario_input + completion * scenario_output

            results.append(
                {
                    "tier": tier,
                    "role": candidate.get("role"),
                    "provider_id": candidate.get("provider_id"),
                    "model": model_id,
                    "catalog_found": row is not None,
                    "context_length": context,
                    "supported_parameters": sorted(parameters),
                    "input_usd_per_million": (
                        str(input_per_million)
                        if input_per_million is not None
                        else None
                    ),
                    "output_usd_per_million": (
                        str(output_per_million)
                        if output_per_million is not None
                        else None
                    ),
                    "scenario_cost_usd": (
                        str(scenario_cost) if scenario_cost is not None else None
                    ),
                    "catalog_qualified": not reasons,
                    "reasons": reasons,
                }
            )

    timestamp = observed_at or datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": 1,
        "candidate_revision": candidates.get("revision"),
        "catalog_url": candidates.get("catalog_url"),
        "observed_at": timestamp,
        "policy": policy,
        "catalog_qualified": bool(results)
        and all(result["catalog_qualified"] for result in results),
        "activation_approved": False,
        "activation_blockers": [
            "synthetic_completion_not_run",
            "legal_benchmark_not_run",
            "privacy_and_provider_redundancy_not_approved",
        ],
        "candidates": results,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument(
        "--catalog-file",
        type=Path,
        help="Use a saved provider catalog instead of the configured HTTPS endpoint.",
    )
    parser.add_argument(
        "--output", type=Path, help="Also write the JSON evidence here."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    candidates = _load_json(args.candidates)
    catalog = (
        _load_json(args.catalog_file)
        if args.catalog_file
        else fetch_catalog(str(candidates["catalog_url"]))
    )
    evidence = qualify_candidates(candidates, catalog)
    rendered = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    sys.stdout.write(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0 if evidence["catalog_qualified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
