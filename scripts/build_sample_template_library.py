#!/usr/bin/env python3
"""Build the curated global sample-template library from the AcroForms library.

The curated ``legal_forms_library_acro`` directory holds a fixed set of fillable
AcroForm PDFs plus a ``catalog.json`` describing each form (human name, state,
category, provenance). Many forms are byte-identical duplicates marketed under
different state names (one generic last-will template renamed per state), and
many carry a "Made Fillable by FreeForms.com" watermark drawn from a Form
XObject.

This script:

* keeps only forms the template studio will accept (valid PDF, AcroForm fields,
  no active content) using the app's own ``discover_pdf_fields`` as the arbiter;
* removes source watermarks by decoding each text run (via the font's ToUnicode
  CMap when present) and blanking runs that carry source branding, preserving
  layout by substituting equal-length spaces;
* strips PDF metadata (author/producer/creator) so no source identity leaks;
* deduplicates by SHA-256 so identical content is stored once, attaching the
  source's state claims as ``jurisdictions``;
* normalizes a human title and stable slug from the catalog's form names;
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
DEFAULT_SOURCE = REPO_ROOT.parent / "legal_forms_library_acro"
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
from pypdf.generic import (  # noqa: E402
    ContentStream,
    DecodedStreamObject,
    NameObject,
)

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
    "District of Columbia",
]

_STATE_CODES = {
    "al": "Alabama", "ak": "Alaska", "az": "Arizona", "ar": "Arkansas",
    "ca": "California", "co": "Colorado", "ct": "Connecticut", "de": "Delaware",
    "fl": "Florida", "ga": "Georgia", "hi": "Hawaii", "id": "Idaho",
    "il": "Illinois", "in": "Indiana", "ia": "Iowa", "ks": "Kansas",
    "ky": "Kentucky", "la": "Louisiana", "me": "Maine", "md": "Maryland",
    "ma": "Massachusetts", "mi": "Michigan", "mn": "Minnesota", "ms": "Mississippi",
    "mo": "Missouri", "mt": "Montana", "ne": "Nebraska", "nv": "Nevada",
    "nh": "New Hampshire", "nj": "New Jersey", "nm": "New Mexico", "ny": "New York",
    "nc": "North Carolina", "nd": "North Dakota", "oh": "Ohio", "ok": "Oklahoma",
    "or": "Oregon", "pa": "Pennsylvania", "ri": "Rhode Island", "sc": "South Carolina",
    "sd": "South Dakota", "tn": "Tennessee", "tx": "Texas", "ut": "Utah",
    "vt": "Vermont", "va": "Virginia", "wa": "Washington", "wv": "West Virginia",
    "wi": "Wisconsin", "wy": "Wyoming", "dc": "Washington DC",
}

# Third-party source/copyright branding that must not ship in the library. The
# same list is asserted by backend/tests/test_sample_template_library.py.
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


def _clean_title(name: str) -> str:
    base = Path(name).name
    # Both "Name.pdf deadbeef" (catalog form_name) and "Name.pdf_deadbeef.pdf".
    base = re.sub(r"\.pdf[_ ][0-9a-f]{8}\.pdf$", "", base, flags=re.IGNORECASE)
    base = re.sub(r"\.pdf[_ ][0-9a-f]{8}$", "", base, flags=re.IGNORECASE)
    base = re.sub(r"\.pdf$", "", base, flags=re.IGNORECASE)
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


def _jurisdiction(state: str | None) -> str | None:
    if not state:
        return None
    s = state.strip()
    return _STATE_CODES.get(s.lower(), s.title())


def _load_catalog(source: Path) -> dict[str, dict]:
    catalog_path = source / "catalog.json"
    if not catalog_path.is_file():
        return {}
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {f["filename"]: f for f in data.get("forms", []) if f.get("filename")}


# ---------------------------------------------------------------------------
# Watermark removal
# ---------------------------------------------------------------------------
#
# Source watermarks ("Made Fillable by FreeForms.com", "www.freeforms.com", …)
# are drawn as real text, usually inside a Flate-compressed Form XObject, and
# often glyph-encoded via a subset font's ToUnicode CMap (so a raw byte search
# finds nothing). We decode every text-showing operand and blank the runs that
# carry source branding, substituting equal-length spaces so layout, field
# positions, and the AcroForm are untouched.


def _parse_tounicode(data: bytes) -> dict[int, str]:
    text = data.decode("latin-1", errors="replace")
    cmap: dict[int, str] = {}
    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        for m in re.finditer(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            dst = m.group(2)
            cmap[int(m.group(1), 16)] = "".join(
                chr(int(dst[i:i + 4], 16)) for i in range(0, len(dst), 4)
            )
    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        for line in block.strip().splitlines():
            m = re.match(
                r"\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", line
            )
            if m:
                lo, hi = int(m.group(1), 16), int(m.group(2), 16)
                dst0 = int(m.group(3), 16)
                for i, code in enumerate(range(lo, hi + 1)):
                    cmap[code] = chr(dst0 + i)
                continue
            m = re.match(
                r"\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[(.*?)\]", line
            )
            if m:
                lo, hi = int(m.group(1), 16), int(m.group(2), 16)
                arr = re.findall(r"<([0-9A-Fa-f]+)>", m.group(3))
                for i, code in enumerate(range(lo, hi + 1)):
                    if i < len(arr):
                        dst = arr[i]
                        cmap[code] = "".join(
                            chr(int(dst[j:j + 4], 16)) for j in range(0, len(dst), 4)
                        )
    return cmap


def _font_cmap(font) -> dict[int, str] | None:
    try:
        font = font.get_object()
    except Exception:
        pass
    tu = font.get("/ToUnicode")
    if tu is None:
        desc = font.get("/DescendantFonts")
        if desc:
            try:
                tu = desc[0].get_object().get("/ToUnicode")
            except Exception:
                tu = None
    if tu is None:
        return None
    try:
        return _parse_tounicode(tu.get_object().get_data())
    except Exception:
        return None


def _decode_bytes(b: bytes, cmap: dict[int, str] | None) -> str:
    if cmap:
        out, i = [], 0
        while i < len(b):
            for n in (4, 3, 2, 1):
                code = int.from_bytes(b[i:i + n], "big") if i + n <= len(b) else None
                if code in cmap:
                    out.append(cmap[code])
                    i += n
                    break
            else:
                out.append("?")
                i += 1
        return "".join(out)
    return b.decode("latin-1", errors="replace")


def _escape_pdf_string(s: str) -> bytes:
    return (
        s.encode("latin-1", errors="replace")
        .replace(b"\\", b"\\\\")
        .replace(b"(", b"\\(")
        .replace(b")", b"\\)")
    )


def _write_operand(out, opnd) -> None:
    # Containers first: blanked plain str/bytes inside arrays must be written
    # element-wise (pypdf's own container writer can't handle mixed types).
    if isinstance(opnd, dict):
        out.write(b"<<")
        for k, v in opnd.items():
            out.write(b" ")
            _write_operand(out, k)
            out.write(b" ")
            _write_operand(out, v)
        out.write(b" >>")
    elif isinstance(opnd, (list, tuple)):
        out.write(b"[")
        for i, el in enumerate(opnd):
            if i:
                out.write(b" ")
            _write_operand(out, el)
        out.write(b"]")
    elif hasattr(opnd, "write_to_stream"):
        opnd.write_to_stream(out, None)
    elif isinstance(opnd, str):
        out.write(b"(" + _escape_pdf_string(opnd) + b")")
    elif isinstance(opnd, (bytes, bytearray)):
        out.write(b"(" + _escape_pdf_string(bytes(opnd).decode("latin-1")) + b")")
    elif isinstance(opnd, bool):
        out.write(b"true" if opnd else b"false")
    elif isinstance(opnd, int):
        out.write(str(opnd).encode())
    elif isinstance(opnd, float):
        out.write(repr(opnd).encode())
    else:
        out.write(str(opnd).encode("latin-1", errors="replace"))


def _serialize_operations(operations) -> bytes:
    out = io.BytesIO()
    for operands, op in operations:
        for opnd in operands:
            _write_operand(out, opnd)
            out.write(b" ")
        out.write(op if isinstance(op, bytes) else op.encode())
        out.write(b"\n")
    return out.getvalue()


def _operand_text(raw, fonts, cur_font) -> str | None:
    """Decode a text operand to unicode using the current font's ToUnicode CMap.

    pypdf may hand us either bytes or a str (glyph bytes decoded as latin-1);
    re-encode str to bytes so glyph-encoded subset fonts decode correctly.
    """
    cmap = fonts.get(cur_font)
    if isinstance(raw, str):
        return _decode_bytes(raw.encode("latin-1", errors="replace"), cmap)
    if isinstance(raw, (bytes, bytearray)):
        return _decode_bytes(bytes(raw), cmap)
    return None


def _blank_ops(cs, fonts) -> bool:
    """Blank source-branding text. Granularity is the BT...ET text block: the
    watermark phrase is split across several Tj ops with Td positioning between
    words, so operands are accumulated per block and blanked together."""
    cur_font = None
    changed = False
    buf: list = []  # (container, index, decoded_text)

    def _flush():
        nonlocal changed, buf
        full = "".join(t for _, _, t in buf).lower()
        if any(mk in full for mk in _SOURCE_MARKERS):
            for container, idx, _ in buf:
                raw = container[idx]
                container[idx] = (
                    " " * len(raw) if isinstance(raw, str) else b" " * len(raw)
                )
            changed = True
        buf = []

    for operands, op in cs.operations:
        if op == b"Tf":
            try:
                cur_font = operands[0]
            except Exception:
                cur_font = None
        elif op == b"BT":
            buf = []
        elif op == b"ET":
            _flush()
        elif op in (b"Tj", b"'", b'"'):
            text = _operand_text(operands[-1], fonts, cur_font)
            if text is not None:
                buf.append((operands, len(operands) - 1, text))
        elif op == b"TJ":
            if not operands:
                continue
            for j, el in enumerate(operands[0]):
                text = _operand_text(el, fonts, cur_font)
                if text is not None:
                    buf.append((operands[0], j, text))
    _flush()
    return changed


def _fonts_of(resources) -> dict:
    fonts = {}
    if not resources:
        return fonts
    try:
        resources = resources.get_object()
    except Exception:
        pass
    fdict = resources.get("/Font") or {}
    try:
        fdict = fdict.get_object()
    except Exception:
        pass
    if isinstance(fdict, dict):
        for name, fobj in fdict.items():
            fonts[name] = _font_cmap(fobj)
    return fonts


def _blank_stream_in_place(stream_obj, pdf, fonts) -> bool:
    """Blank marker runs in a content stream, mutating the stream object so the
    change survives writing (content streams are indirect objects)."""
    try:
        cs = ContentStream(stream_obj, pdf)
    except Exception:
        return False
    if not _blank_ops(cs, fonts):
        return False
    try:
        stream_obj.pop(NameObject("/Filter"), None)
        stream_obj.pop(NameObject("/DecodeParms"), None)
        stream_obj._data = _serialize_operations(cs.operations)
        if hasattr(stream_obj, "_decoded_cache"):
            stream_obj._decoded_cache = None
        return True
    except Exception:
        return False


def _process_content(container, pdf, fonts):
    """Blank markers in the stream(s) at container['/Contents'].

    /Contents may be a single stream or an array of byte-fragment streams that
    only form a valid content stream when concatenated (some producers split one
    logical stream into pieces), so join before parsing and write back a single
    replacement stream only when something was blanked.
    """
    try:
        contents = container[NameObject("/Contents")]
    except Exception:
        return False
    items = contents if isinstance(contents, (list, tuple)) else [contents]
    datas = []
    for item in items:
        try:
            obj = item.get_object() if hasattr(item, "get_object") else item
            datas.append(obj.get_data())
        except Exception:
            return False
    combined = b"".join(datas)
    tmp = DecodedStreamObject()
    tmp.set_data(combined)
    try:
        cs = ContentStream(tmp, pdf)
    except Exception:
        return False
    if not _blank_ops(cs, fonts):
        return False
    new_stream = DecodedStreamObject()
    new_stream.set_data(_serialize_operations(cs.operations))
    container[NameObject("/Contents")] = new_stream
    return True


def _process_node(node, pdf, visited) -> bool:
    """Process a page or Form-XObject dict: blank its streams, recurse into Forms."""
    try:
        node = node.get_object() if hasattr(node, "get_object") else node
    except Exception:
        return False
    vid = id(node)
    if vid in visited:
        return False
    visited.add(vid)
    if not isinstance(node, dict):
        return False

    fonts = _fonts_of(node.get("/Resources"))
    changed = False

    # A Form XObject's content is the object's own stream data (no /Contents key).
    if hasattr(node, "get_data") and "/Contents" not in node:
        if _blank_stream_in_place(node, pdf, fonts):
            changed = True

    if "/Contents" in node:
        if _process_content(node, pdf, fonts):
            changed = True

    # recurse into Form XObjects
    res = node.get("/Resources")
    xo = None
    if res is not None:
        try:
            xo = res.get_object().get("/XObject")
            if xo is not None:
                xo = xo.get_object()
        except Exception:
            xo = None
    if isinstance(xo, dict):
        for name in list(xo.keys()):
            try:
                sub = xo[name]
                sub_obj = sub.get_object() if hasattr(sub, "get_object") else sub
                if isinstance(sub_obj, dict) and sub_obj.get("/Subtype") == "/Form":
                    if _process_node(sub_obj, pdf, visited):
                        changed = True
            except Exception:
                pass
    return changed


def _remove_watermarks(content: bytes) -> bytes:
    """Return ``content`` with source-branding text blanked (layout preserved)."""
    # Clone first, then mutate the writer's own object graph. Mutating before a
    # clone is unreliable (clone_document_from_reader re-resolves page contents to
    # their original indirect streams); the writer serializes exactly what its
    # page/xobject dicts hold at write time.
    reader = PdfReader(io.BytesIO(content), strict=False)
    if reader.is_encrypted:
        reader.decrypt("")
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)

    visited: set = set()
    changed = False
    for page in writer.pages:
        if _process_node(page, writer, visited):
            changed = True
    if not changed:
        return content
    writer._info = None
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Sanitize (strip active content + metadata)
# ---------------------------------------------------------------------------

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
    catalog = _load_catalog(source)
    groups: dict[str, dict] = defaultdict(
        lambda: {"titles": [], "states": set(), "categories": [], "content": b""}
    )
    for path in sorted(source.glob("*.pdf")):
        meta = catalog.get(path.name, {})
        content = path.read_bytes()
        if not content.startswith(b"%PDF-"):
            continue
        # Cheap pre-filter: skip anything that has no AcroForm fields at all
        # before paying for watermark removal + sanitize + discovery on it.
        try:
            reader = PdfReader(io.BytesIO(content), strict=False)
            if reader.is_encrypted:
                reader.decrypt("")
            if not (reader.get_fields() or {}):
                continue
        except Exception:
            continue
        try:
            cleaned = _sanitize(_remove_watermarks(content))
        except Exception:
            continue
        field_count = _usable_field_count(cleaned)
        if field_count is None:
            continue
        digest = hashlib.sha256(cleaned).hexdigest()
        entry = groups[digest]
        entry["titles"].append(_clean_title(meta.get("form_name") or path.name))
        jurisdiction = _jurisdiction(meta.get("state"))
        if jurisdiction:
            entry["states"].add(jurisdiction)
        category = (meta.get("category") or "other").strip().lower() or "other"
        if category not in entry["categories"]:
            entry["categories"].append(category)
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
        if _has_source_branding(_extract_text(entry["content"])):
            continue
        all_states = sorted(entry["states"])
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
        category = sorted(entry["categories"])[0]
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
