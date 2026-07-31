"""Preview/sync only allowlisted official IRS bulletin and estate-product indexes."""
from __future__ import annotations
import argparse, json
from datetime import datetime, timezone
from pathlib import Path
import httpx
from .authority_adapter_store import refresh_source_status, upsert_adapter_document
from .database import connect
from .irs_adapter import *
from .loader import init_schema
from .source_catalog import load_catalog, seed_catalog

def _state(path: Path, key: str):
    try: return json.loads((path / f"irs-{key}.json").read_text())
    except (OSError, ValueError): return {"completed": 0, "status": "new"}
def _save(path: Path, key: str, state: dict):
    path.mkdir(parents=True, exist_ok=True); state["updated_at"] = datetime.now(timezone.utc).isoformat(); (path / f"irs-{key}.json").write_text(json.dumps(state), encoding="utf-8")
def main():
    parser=argparse.ArgumentParser(description="Preview or sync official IRS IRB and estate/gift form products")
    mode=parser.add_mutually_exclusive_group(required=True); mode.add_argument("--preview", action="store_true"); mode.add_argument("--sync", action="store_true")
    parser.add_argument("--irb", action="store_true", help="discover current Internal Revenue Bulletins")
    parser.add_argument("--forms", action="store_true", help="discover estate/gift/fiduciary form products")
    parser.add_argument("--limit", type=int); parser.add_argument("--db-url"); parser.add_argument("--checkpoint-dir", default=".legalapp-checkpoints")
    args=parser.parse_args()
    if not args.irb and not args.forms: parser.error("choose --irb and/or --forms")
    if args.limit is not None and args.limit <= 0: parser.error("--limit must be positive")
    docs=[]; checkpoints=Path(args.checkpoint_dir)
    with httpx.Client(timeout=90, follow_redirects=True, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf;q=0.9"}) as client:
        if args.irb:
            index_raw, _, _ = fetch_bounded(client, IRB_INDEX_URL)
            issues=discover_irb_issues(index_raw.decode("utf-8", errors="replace"))[:args.limit]
            for issue in issues:
                issue_raw, issue_media, _ = fetch_bounded(client, issue.canonical_url)
                if issue_media == "application/pdf" or issue.canonical_url.lower().endswith(".pdf"):
                    from io import BytesIO
                    from pypdf import PdfReader
                    issue_text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(issue_raw)).pages)
                else:
                    issue_text = issue_raw.decode("utf-8", errors="replace")
                docs.extend(irb_documents(issue, issue_text))
        if args.forms:
            forms_raw, _, _ = fetch_bounded(client, ESTATE_FORMS_INDEX_URL)
            artifacts=discover_estate_products(forms_raw.decode("utf-8", errors="replace"))[:args.limit]
            docs.extend(fetch_product(item, client=client) for item in artifacts)
    if args.preview:
        print(json.dumps([{"external_id": d.external_id,"title":d.title,"dry_run":True} for d in docs], indent=2)); return
    init_schema(args.db_url)
    with connect(args.db_url) as conn:
        seed_catalog(conn, load_catalog()); conn.commit()
        for index, doc in enumerate(docs, 1):
            upsert_adapter_document(conn, doc); _save(checkpoints, doc.source_key.replace(":", "-"), {"completed": index, "status":"running"}); conn.commit()
        with conn.cursor() as cursor:
            for doc in docs:
                cursor.execute(
                    """UPDATE legal_documents SET document_status='superseded',
                       termination_date=CURRENT_DATE, updated_at=now()
                       WHERE source_key=%s AND metadata->>'stable_id'=%s
                         AND external_id<>%s AND document_status='current'""",
                    [doc.source_key, doc.metadata.get("stable_id"), doc.external_id],
                )
        for key in {d.source_key for d in docs}: _save(checkpoints, key.replace(":", "-"), {"completed": len([d for d in docs if d.source_key==key]), "status":"complete"})
        refresh_source_status(conn, {doc.source_key for doc in docs})
        conn.commit()
    print(json.dumps({"documents":len(docs),"status":"complete"}, indent=2))
if __name__ == "__main__": main()
