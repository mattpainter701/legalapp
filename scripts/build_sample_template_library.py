#!/usr/bin/env python3
"""Build the curated global sample-template library from the raw forms library.

The raw ``legal_forms_library`` contains hundreds of downloaded ``.pdf`` files,
many of which are HTML landing pages (not PDFs), many of which are byte-identical
duplicates (for example, one generic last-will template renamed
``Printable_<State>_Last_Will...`` for every state), and some of which are
statutes or carry third-party source/copyright watermarks.

This script:

* keeps only files that are valid, fillable PDFs (AcroForm fields present) that
  the template studio will actually accept (no active content);
* strips PDF metadata (author/producer/creator) so no source identity leaks;
* rejects forms whose visible text carries third-party source or copyright
  branding (e.g. ``www.aoausa.com``, ``Honoring Choices``, ``ilovepdf``);
* rejects statute/code documents (e.g. ``sec. 3955``);
* deduplicates by SHA-256 so identical content is stored once;
* normalizes a human title and a stable slug;
* assigns a category and attaches the source's state claims as ``jurisdictions``;
* copies each cleaned PDF into ``backend/seed/sample_templates/<category>/``
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
import io
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

from pypdf import PdfReader, PdfWriter  # noqa: E402
from pypdf.generic import NameObject  # noqa: E402

from app.services.pdf_templates import discover_pdf_fields  # noqa: E402


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

# Third-party source/copyright branding that must not ship in the library.
_SOURCE_MARKERS = [
    "ilovepdf",
    "freeforms",
    "made fillable by",
    "freeprintablelegalforms",
    "justia",
    "downloaded from",
    "honoring choices",
    "honoringchoices",
    "vhha.com",
    "aoausa.com",
    "caanet.org",
    "rocketlawyer",
    "legalzoom",
    "lawdepot",
    "formswift",
    "uslegalforms",
    "findlegalforms",
    "templateroller",
    "this form is provided by",
    "provided courtesy of",
    "esign.com",
    "www.esign",
]

_JUNK_TITLES = {
    "adobe", "adobe pdf", "form", "blank form", "untitled", "document",
    "document1", "scan", "scanned", "image", "page", "sheet", "new form",
}
_JUNK_TITLE_PREFIXES = ("adobe", "microsoft", "foxit", "nitro", "untitled", "scan")


def _clean_title(filename: str) -> str:
    base = Path(filename).name
    base = re.sub(r"\.pdf_[0-9a-f]{8}\.pdf$", "", base)
    base = re.sub(r"\.pdf$", "", base)
    base = base.replace("_", " ").replace("-", " ")
    for word in (
        "Free Printable", "Printable", "Print a", "Free", "Blank", "Form",
        "PDF", "PD", "Docx", "Doc", "Template", "Templates",
    ):
        base = re.sub(rf"\b{re.escape(word)}\b", "", base, flags=re.IGNORECASE)
    base = re.sub(r"\b(and Testament|of Attorney)\s+P\b", r"\1", base, flags=re.IGNORECASE)
    base = re.sub(r"\s+", " ", base).strip(" -")
    # Drop a dangling single-letter token left by a truncated filename.
    base = re.sub(r"\s+[A-Za-z]\s*$", "", base)
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


def _is_statute(title: str) -> bool:
    lowered = title.lower().strip()
    if re.match(r"^(sec|section|chapter|title|art)\b", lowered):
        return True
    return bool(re.search(r"\b(u\.?s\.?c\.?|public law|statute|code of|civil code)\b", lowered))


_STRIP_KEYS = (
    "/A", "/AA", "/JavaScript", "/JS", "/Launch", "/SubmitForm",
    "/ImportData", "/GoToR", "/GoToE", "/OpenAction", "/Metadata", "/XFA",
)


def _sanitize(content: bytes) -> bytes:
    """Strip active content and metadata while preserving the AcroForm fields.

    Scraped forms frequently ship hyperlink actions (``/URI``) that the studio
    validator rejects, plus source identity in metadata (``/Author``,
    ``/Producer``) and an XMP stream. This removes those so the form is both
    renderable and free of source info.
    """
    reader = PdfReader(io.BytesIO(content), strict=False)
    if reader.is_encrypted:
        reader.decrypt("")
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)

    seen: set[int] = set()
    stack = [writer._root_object]
    while stack:
        raw = stack.pop()
        try:
            value = raw.get_object() if hasattr(raw, "get_object") else raw
        except Exception:
            continue
        marker = id(value)
        if marker in seen:
            continue
        seen.add(marker)
        if isinstance(value, dict):
            for key in _STRIP_KEYS:
                value.pop(NameObject(key), None)
            stack.extend(value.values())
        elif isinstance(value, (list, tuple)):
            stack.extend(value)

    root = writer._root_object
    names = root.get("/Names")
    if names is not None:
        names = names.get_object() if hasattr(names, "get_object") else names
        if isinstance(names, dict):
            names.pop(NameObject("/EmbeddedFiles"), None)

    writer._info = None
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _extract_text(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content), strict=False)
    if reader.is_encrypted:
        reader.decrypt("")
    parts = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(parts)


def _has_source_branding(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _SOURCE_MARKERS)


def _usable_field_count(content: bytes) -> int | None:
    """Return the renderable field count, or None if the studio rejects it."""
    if not content.startswith(b"%PDF-"):
        return None
    try:
        fields = discover_pdf_fields(content)
    except Exception:
        return None
    return len(fields) or None


def build(source: Path) -> tuple[dict, dict[str, bytes]]:
    groups: dict[str, dict] = defaultdict(lambda: {"titles": [], "content": b""})
    for path in sorted(source.glob("*.pdf")):
        content = path.read_bytes()
        if not content.startswith(b"%PDF-"):
            continue
        # Cheap pre-filter: skip anything that has no AcroForm fields at all
        # before paying for sanitize + discovery on it.
        try:
            reader = PdfReader(io.BytesIO(content), strict=False)
            if reader.is_encrypted:
                reader.decrypt("")
            if not (reader.get_fields() or {}):
                continue
        except Exception:
            continue
        try:
            cleaned = _sanitize(content)
        except Exception:
            continue
        field_count = _usable_field_count(cleaned)
        if field_count is None:
            continue
        digest = hashlib.sha256(cleaned).hexdigest()
        entry = groups[digest]
        entry["titles"].append(_clean_title(path.name))
        entry["content"] = cleaned
        entry["fields"] = field_count
        entry["digest"] = digest

    manifest_forms = []
    cleaned_by_digest: dict[str, bytes] = {}
    seen_slugs: dict[str, int] = {}
    for digest, entry in sorted(groups.items()):
        titles = sorted(entry["titles"], key=lambda t: (len(t), t.lower()))
        title = titles[0]
        lowered = title.lower()
        if len(title) < 4 or lowered in _JUNK_TITLES:
            continue
        if lowered.startswith(_JUNK_TITLE_PREFIXES):
            continue
        if _is_statute(title):
            continue
        if _has_source_branding(_extract_text(entry["content"])):
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
                "size_bytes": len(entry["content"]),
                "field_count": entry["fields"],
            }
        )
        cleaned_by_digest[digest] = entry["content"]

    manifest_forms.sort(key=lambda f: (f["category"], f["title"].lower()))
    return {"forms": manifest_forms}, cleaned_by_digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=SEED_DIR)
    args = parser.parse_args()

    source = args.source
    if not source.is_dir():
        raise SystemExit(f"Source library not found: {source}")

    manifest, cleaned_by_digest = build(source)
    out = args.out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    for form in manifest["forms"]:
        dest = out / form["filename"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(cleaned_by_digest[form["sha256"]])

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
