"""Guard customer-visible comparison claims against unsupported absolutes."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _public_claim_files() -> list[Path]:
    files = [
        ROOT / "README.md",
        ROOT / "frontend" / "index.html",
        ROOT / "demo" / "cybersafeadvisor-corporate-pack" / "README.md",
        ROOT
        / "demo"
        / "cybersafeadvisor-corporate-pack"
        / "prospect-demo-runbook.md",
    ]
    files.extend((ROOT / "frontend" / "src" / "marketing").glob("*.js"))
    files.extend((ROOT / "frontend" / "src" / "seo").glob("*.js"))
    for name in (
        "HomePage.jsx",
        "ProductPage.jsx",
        "ProductChatPage.jsx",
        "McpProductPage.jsx",
        "PricingPage.jsx",
        "DemoRequestPage.jsx",
        "LegalNoticePage.jsx",
    ):
        files.append(ROOT / "frontend" / "src" / "pages" / name)
    return sorted({path for path in files if path.is_file()})


FORBIDDEN_PUBLIC_CLAIMS = {
    "retired blanket AI superiority": re.compile(
        r"ahead\s+of\s+every\s+incumbent|no\s+incumbent\s+matches|"
        r"best[- ]in[- ]class\s+(?:legal\s+)?ai",
        re.IGNORECASE,
    ),
    "Westlaw replacement": re.compile(
        r"(?:replaces?|acts\s+as|serves\s+as|offers)\s+(?:a\s+)?Westlaw\s+replacement|"
        r"replaces?\s+Westlaw",
        re.IGNORECASE,
    ),
    "unsupported LawHand coverage absolute": re.compile(
        r"LawHand.{0,140}(?:provides?|offers?|has|includes|covers?)"
        r".{0,80}(?:comprehensive|nationwide|all[- ]jurisdiction)",
        re.IGNORECASE | re.DOTALL,
    ),
    "unsupported good-law determination": re.compile(
        r"LawHand(?![^.]{0,140}\b(?:does\s+not|cannot|never)\b)"
        r".{0,140}(?:determines?|guarantees?|verifies?|confirms?)"
        r".{0,80}good\s+law",
        re.IGNORECASE | re.DOTALL,
    ),
    "unearned public service or certification claim": re.compile(
        r"LawHand.{0,160}(?:99\.9%\s+uptime|24/7\s+support|"
        r"SOC\s*2\s+certified|ISO\s*27001\s+certified|SLA[- ]backed)",
        re.IGNORECASE | re.DOTALL,
    ),
}


def test_customer_visible_claims_do_not_use_unsupported_absolutes() -> None:
    findings: list[str] = []
    for path in _public_claim_files():
        source = path.read_text(encoding="utf-8")
        for label, pattern in FORBIDDEN_PUBLIC_CLAIMS.items():
            match = pattern.search(source)
            if match:
                relative = path.relative_to(ROOT).as_posix()
                excerpt = " ".join(match.group(0).split())[:220]
                findings.append(f"{relative}: {label}: {excerpt!r}")

    assert not findings, "unsupported customer-visible claims:\n" + "\n".join(findings)


def test_competitive_register_has_current_official_evidence_and_boundaries() -> None:
    register = (
        ROOT / "docs" / "competitive-gap-analysis.md"
    ).read_text(encoding="utf-8")

    for required in (
        "**Claim owner:** Product & Commercial",
        "**Evidence reviewed:** 2026-08-27",
        "https://www.clio.com/features/",
        "https://www.clio.com/work/ai-legal-research/",
        "https://legal.thomsonreuters.com/en/products/westlaw/keycite",
        "https://legal.thomsonreuters.com/en/products/westlaw-edge/quick-check",
        "https://legal.thomsonreuters.com/en/products/practical-law/features",
        "https://legal.thomsonreuters.com/en/products/westlaw/dockets-coverage",
        "platform-operator/internal only",
        "license/partner only",
    ):
        assert required in register

    lowered = register.lower()
    assert "ahead of every incumbent" not in lowered
    assert "no incumbent matches" not in lowered


def test_readme_publishes_all_four_states_and_internal_api_boundary() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for state in ("Implemented", "Controlled pilot", "Planned", "Partner-dependent"):
        assert state in readme
    assert "platform-operator/internal only" in readme
    assert "license/partner only" in readme
