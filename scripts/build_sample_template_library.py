#!/usr/bin/env python3
"""Build the curated global sample-template library from the raw forms library.

The raw ``legal_forms_library`` contains hundreds of downloaded ``.pdf`` files,
many of which are HTML landing pages (not PDFs), and many of which are
byte-identical duplicates (for example, one generic last-will template renamed
``Printable_<State>_Last_Will...`` for every state).

This script:

* keeps only files that are valid, fillable PDFs (AcroForm fields present);
* deduplicates by SHA-256 so identical content is stored once;
* normalizes a human title and a stable slug;
* assigns a category (``business_forms`` / ``court_forms`` /
  ``wills_trusts`` / ``power_of_attorney`` / ``other``);
* attaches the source's state claims as ``jurisdictions`` tags;
* copies each unique PDF into ``backend/seed/sample_templates/<category>/``
  and writes ``backend/seed/sample_templates/manifest.json``.

The manifest is metadata only. Field schemas are derived at seed time by
``scripts/seed_sample_templates.py`` (via ``discover_pdf_fields``) so the single
source of truth remains the PDF + application code.

Run:  python scripts/build_sample_template_library.py [--source PATH]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT.parent / "legal_forms_library"
SEED_DIR = REPO_ROOT / "backend" / "seed" / "sample_templates"

# "Fillable" is not enough: the template studio also rejects PDFs with active
# content (embedded files, /URI links, /OpenAction, /AA, XFA, etc.). Use the
# app's own discovery path as the single source of truth for what is renderable.
sys.path.insert(0, str(REPO_ROOT / "backend"))
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/db")
os.environ.setdefault(
    "SECRET_KEY", "build-script-only-0000000000000000000000000000000000000000"
)
os.environ.setdefault(
    "TOKEN_ENCRYPTION_KEY",
    base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
)

from app.services.pdf_templates import TemplatePdfError, discover_pdf_fields  # noqa: E402



_US_STATES = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine",
    "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi",
    "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey",
    "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio",
    "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina",
    "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia",
    "Washington", "West Virginia", "Wisconsin", "Wyoming", "Washington DC",
]

# Ordered so more specific matches win.
_CATEGORY_RULES = [
    ("power_of_attorney", ["Power of Attorney", "IRS Power", "Revocation of Power"]),
    ("wills_trusts", [
        "Last Will and Testament", "Living Will", "Codicil", "Pour Over Will",
        "Will For", "Simple Will", "Do It Yourself Will", "Example of a Will",
        "Blank Will",
    ]),
    ("business_forms", [
        "Confidentiality Agreement", "Partnership Agreement", "Prenuptial",
        "Promissory Note", "Independent Contractor",
    ]),
    ("court_forms", ["Motion", "Order", "Notice", "Complaint", "Answer",
                     "Affidavit", "Waiver", "Petition", "Writ"]),
]


def _clean_title(filename: str) -> str:
    base = Path(filename).name
    base = re.sub(r"\.pdf_[0-9a-f]{8}\.pdf$", "", base)
    base = re.sub(r"\.pdf$", "", base)
    base = base.replace("_", " ")
    base = re.sub(r"\b(Free )?Printable\b", "", base, flags=re.IGNORECASE)
    base = re.sub(r"\bPrint an?\b", "", base, flags=re.IGNORECASE)
    base = re.sub(r"\bBlank\b", "", base, flags=re.IGNORECASE)
    base = re.sub(r"\b(Form|PDF|PD|Docx?)\b", "", base, flags=re.IGNORECASE)
    base = re.sub(r"\b(and Testament|of Attorney) P\b", r"\1", base, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", base).strip(" -")


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "form"


def _category(title: str) -> str:
    lowered = title.lower()
    for category, needles in _CATEGORY_RULES:
        if any(needle.lower() in lowered for needle in needles):
            return category
    return "other"


def _states(title: str) -> list[str]:
    found = [state for state in _US_STATES if state.lower() in title.lower()]
    return found or []


_JUNK_TITLES = {
    "adobe", "adobe pdf", "form", "blank form", "untitled", "document",
    "document1", "scan", "scanned", "image", "page", "sheet", "new form",
}
_JUNK_TITLE_PREFIXES = ("adobe", "microsoft", "foxit", "nitro", "untitled", "scan")


def _usable_field_count(content: bytes) -> int | None:
    """Return the renderable field count, or None if the studio rejects it."""
    if not content.startswith(b"%PDF-"):
        return None
    try:
        fields = discover_pdf_fields(content)
    except TemplatePdfError:
        return None
    return len(fields) or None


def build(source: Path) -> dict:
    groups: dict[str, dict] = defaultdict(
        lambda: {"titles": [], "size": 0, "fields": 0}
    )
    for path in sorted(source.glob("*.pdf")):
        content = path.read_bytes()
        field_count = _usable_field_count(content)
        if field_count is None:
            continue
        digest = hashlib.sha256(content).hexdigest()
        entry = groups[digest]
        entry["titles"].append(_clean_title(path.name))
        entry["size"] = len(content)
        entry["fields"] = field_count
        entry["digest"] = digest

    manifest_forms = []
    seen_slugs: dict[str, int] = {}
    for digest, entry in sorted(groups.items()):
        # Canonical title: prefer the shortest non-state-stamped name so the
        # generic form is not pinned to a single state.
        titles = sorted(entry["titles"], key=lambda t: (len(t), t.lower()))
        title = titles[0]
        lowered = title.lower()
        if len(title) < 4 or lowered in _JUNK_TITLES:
            continue
        if lowered.startswith(_JUNK_TITLE_PREFIXES):
            continue
        all_states = sorted({s for t in titles for s in _states(t)})
        if len(all_states) > 1:
            # Identical content marketed under many states -> a generic form;
            # the states belong in the jurisdiction tags, not the title.
            state_alternation = "|".join(_US_STATES)
            title = re.sub(
                rf"^\s*({state_alternation})\s+",
                "",
                title,
                flags=re.IGNORECASE,
            )
            title = title.strip(" -") or title
        slug = _slugify(title)
        if slug in seen_slugs:
            seen_slugs[slug] += 1
            slug = f"{slug}-{seen_slugs[slug]}"
        else:
            seen_slugs[slug] = 1
        category = _category(title)
        manifest_forms.append(
            {
                "slug": slug,
                "title": title,
                "category": category,
                "jurisdictions": all_states,
                "description": (
                    f"{title}. Fillable starter form from the public sample "
                    "library; verify jurisdiction-specific requirements before use."
                ),
                "filename": f"{category}/{slug}.pdf",
                "sha256": digest,
                "size_bytes": entry["size"],
                "field_count": entry["fields"],
            }
        )

    manifest_forms.sort(key=lambda f: (f["category"], f["title"].lower()))
    return {"forms": manifest_forms}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=SEED_DIR)
    args = parser.parse_args()

    source = args.source
    if not source.is_dir():
        raise SystemExit(f"Source library not found: {source}")

    manifest = build(source)
    out = args.out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    for form in manifest["forms"]:
        # Locate the original by digest (any duplicate copy is identical).
        src = next(
            p for p in sorted(source.glob("*.pdf"))
            if hashlib.sha256(p.read_bytes()).hexdigest() == form["sha256"]
        )
        dest = out / form["filename"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(f"Curated {len(manifest['forms'])} unique fillable sample templates -> {out}")
    by_cat: dict[str, int] = defaultdict(int)
    for form in manifest["forms"]:
        by_cat[form["category"]] += 1
    for category, count in sorted(by_cat.items()):
        print(f"  {category}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
