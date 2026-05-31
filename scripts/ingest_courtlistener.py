"""
Ingests CourtListener bulk JSON opinions into PostgreSQL/pgvector.
Usage: python ingest_courtlistener.py --file /path/to/opinions.json.gz --batch-size 256

CourtListener bulk data: https://www.courtlistener.com/api/bulk-info/
"""

import argparse
import gzip
import json
import os
import sys
import time
import uuid
from typing import Iterator, List, Optional

import psycopg2
import psycopg2.extras
import tiktoken
from openai import OpenAI
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Tenant ID reserved for public case law
PUBLIC_TENANT_ID = "00000000-0000-0000-0000-000000000001"

# Federal and state appellate courts worth ingesting
ALLOWED_COURT_IDS = {
    # US Supreme Court
    "scotus",
    # US Courts of Appeals
    "ca1", "ca2", "ca3", "ca4", "ca5", "ca6", "ca7", "ca8",
    "ca9", "ca10", "ca11", "cadc", "cafc",
    # US Court of Federal Claims
    "uscfc",
    # State supreme courts (major)
    "cal", "ny", "tex", "fla", "ill", "pa", "ohio", "mich",
    "nj", "ga", "nc", "va", "wash", "mass", "ariz", "colo",
    "minn", "mo", "ind", "wis", "md", "conn", "la", "ky",
    "ore", "okla", "nev", "neb", "miss", "ark", "utah",
    "kan", "nm", "wva", "idaho", "haw", "me", "nhsc",
    "ri", "mont", "del", "sdsc", "ndsc", "alaska", "wyo",
    "vt", "dc",
    # State appellate courts
    "calctapp", "nycivct", "nyappterm", "nyappdiv",
    "texapp", "flaapp", "illappct", "pacommwct", "pasupct",
    "ohioctapp", "michctapp",
}

CHUNK_TOKENS = 500
CHUNK_OVERLAP_TOKENS = 50
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIMENSIONS = 1536


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_text(text: str, enc, max_tokens: int = CHUNK_TOKENS, overlap: int = CHUNK_OVERLAP_TOKENS) -> List[str]:
    """Split text into overlapping token-bounded chunks."""
    if not text or not text.strip():
        return []

    tokens = enc.encode(text)
    if len(tokens) == 0:
        return []

    chunks = []
    start = 0
    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        chunk_tokens = tokens[start:end]
        chunk_text_str = enc.decode(chunk_tokens).strip()
        if chunk_text_str:
            chunks.append(chunk_text_str)
        if end == len(tokens):
            break
        start = end - overlap

    return chunks


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_connection(db_url: str):
    """Create a psycopg2 connection from a SQLAlchemy-style URL."""
    # Convert asyncpg URL to psycopg2 format if needed
    url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    url = url.replace("postgresql+psycopg2://", "postgresql://")
    return psycopg2.connect(url)


def ensure_schema(conn):
    """Create tables if they don't exist."""
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public_chunks (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL,
                opinion_id BIGINT,
                case_name TEXT,
                citation TEXT,
                court_id TEXT,
                date_filed DATE,
                chunk_index INTEGER NOT NULL DEFAULT 0,
                content TEXT NOT NULL,
                embedding vector(1536),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_public_chunks_tenant
            ON public_chunks (tenant_id);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_public_chunks_opinion
            ON public_chunks (opinion_id);
        """)
        conn.commit()


def opinion_already_ingested(conn, opinion_id: int) -> bool:
    """Return True if at least one chunk for this opinion already exists."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM public_chunks WHERE opinion_id = %s LIMIT 1;",
            (opinion_id,)
        )
        return cur.fetchone() is not None


def bulk_insert_chunks(conn, rows: List[dict]):
    """Insert a batch of chunk rows using ON CONFLICT DO NOTHING."""
    if not rows:
        return
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO public_chunks
                (id, tenant_id, opinion_id, case_name, citation, court_id, date_filed,
                 chunk_index, content, embedding)
            VALUES %s
            ON CONFLICT DO NOTHING;
            """,
            [
                (
                    str(uuid.uuid4()),
                    row["tenant_id"],
                    row["opinion_id"],
                    row["case_name"],
                    row["citation"],
                    row["court_id"],
                    row["date_filed"],
                    row["chunk_index"],
                    row["content"],
                    row["embedding"],
                )
                for row in rows
            ],
            template=(
                "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector"
            ),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def embed_batch(client: OpenAI, texts: List[str], retries: int = 3) -> List[Optional[List[float]]]:
    """Embed a batch of texts with retry logic."""
    for attempt in range(retries):
        try:
            response = client.embeddings.create(
                model=EMBED_MODEL,
                input=texts,
                dimensions=EMBED_DIMENSIONS,
            )
            results = [None] * len(texts)
            for item in response.data:
                results[item.index] = item.embedding
            return results
        except Exception as e:
            if attempt < retries - 1:
                wait = 2 ** attempt
                print(f"\n  [WARNING] Embed attempt {attempt+1} failed: {e}. Retrying in {wait}s...")
                time.sleep(wait)
            else:
                print(f"\n  [ERROR] Embed failed after {retries} attempts: {e}")
                return [None] * len(texts)


# ---------------------------------------------------------------------------
# Opinion reader
# ---------------------------------------------------------------------------

def read_opinions(filepath: str, limit: Optional[int] = None) -> Iterator[dict]:
    """Yield opinion dicts from a gzipped line-delimited JSON file."""
    opener = gzip.open if filepath.endswith(".gz") else open
    count = 0
    with opener(filepath, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                opinion = json.loads(line)
            except json.JSONDecodeError:
                continue
            yield opinion
            count += 1
            if limit and count >= limit:
                break


def extract_opinion_data(opinion: dict) -> Optional[dict]:
    """Extract and normalize relevant fields from a CourtListener opinion dict."""
    # Filter by status
    status = opinion.get("status") or opinion.get("cluster", {}).get("precedential_status", "")
    if status and status.lower() not in ("published", "precedential"):
        return None

    court_id = opinion.get("court_id") or opinion.get("cluster", {}).get("court_id", "")
    if court_id not in ALLOWED_COURT_IDS:
        return None

    # Get text content — prefer plain_text, fall back to html_with_citations stripped
    plain_text = (
        opinion.get("plain_text")
        or opinion.get("text")
        or ""
    ).strip()

    if not plain_text:
        return None

    # Case metadata
    case_name = (
        opinion.get("case_name")
        or opinion.get("cluster", {}).get("case_name", "")
        or "Unknown"
    ).strip()

    # Citation: try cluster citations list
    citations = []
    cluster = opinion.get("cluster") or {}
    if isinstance(cluster.get("citations"), list):
        for c in cluster["citations"]:
            if isinstance(c, dict):
                reporter = c.get("reporter", "")
                volume = c.get("volume", "")
                page = c.get("page", "")
                if volume and reporter and page:
                    citations.append(f"{volume} {reporter} {page}")
            elif isinstance(c, str):
                citations.append(c)
    citation = citations[0] if citations else ""

    date_filed = opinion.get("date_filed") or cluster.get("date_filed") or None

    opinion_id = opinion.get("id") or opinion.get("opinion_id")
    if opinion_id is None:
        return None

    return {
        "opinion_id": int(opinion_id),
        "case_name": case_name,
        "citation": citation,
        "court_id": court_id,
        "date_filed": date_filed,
        "plain_text": plain_text,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Ingest CourtListener bulk opinions into PostgreSQL/pgvector"
    )
    parser.add_argument(
        "--file",
        required=True,
        help="Path to opinions JSON or JSON.gz file (line-delimited)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=256,
        help="Number of chunks per embedding + DB batch (default: 256)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of opinions to process (useful for testing)"
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="PostgreSQL connection URL (defaults to DATABASE_URL env var)"
    )
    parser.add_argument(
        "--openai-key",
        default=None,
        help="OpenAI API key (defaults to OPENAI_API_KEY env var)"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    db_url = args.db_url or os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: --db-url or DATABASE_URL env var required.")
        sys.exit(1)

    openai_key = args.openai_key or os.environ.get("OPENAI_API_KEY")
    if not openai_key:
        print("ERROR: --openai-key or OPENAI_API_KEY env var required.")
        sys.exit(1)

    print(f"Connecting to database...")
    conn = get_connection(db_url)
    ensure_schema(conn)

    client = OpenAI(api_key=openai_key)
    enc = tiktoken.get_encoding("cl100k_base")

    print(f"Reading opinions from: {args.file}")
    if args.limit:
        print(f"Limit: {args.limit} opinions")

    # Counters
    total_opinions = 0
    skipped_opinions = 0
    ingested_opinions = 0
    total_chunks = 0
    total_embedded = 0

    # Pending batch
    pending_chunks: List[dict] = []

    def flush_batch():
        nonlocal total_embedded
        if not pending_chunks:
            return

        texts = [c["content"] for c in pending_chunks]
        embeddings = embed_batch(client, texts)

        rows = []
        for chunk, emb in zip(pending_chunks, embeddings):
            if emb is None:
                continue
            # Format for pgvector: list -> '[x,y,z,...]'
            emb_str = "[" + ",".join(str(v) for v in emb) + "]"
            rows.append({**chunk, "embedding": emb_str})
            total_embedded += 1

        bulk_insert_chunks(conn, rows)
        pending_chunks.clear()

    with tqdm(desc="Opinions", unit="op") as pbar:
        for opinion in read_opinions(args.file, limit=args.limit):
            total_opinions += 1
            pbar.update(1)

            data = extract_opinion_data(opinion)
            if data is None:
                skipped_opinions += 1
                continue

            # Skip already-ingested
            if opinion_already_ingested(conn, data["opinion_id"]):
                skipped_opinions += 1
                continue

            chunks = chunk_text(data["plain_text"], enc)
            if not chunks:
                skipped_opinions += 1
                continue

            for idx, chunk_content in enumerate(chunks):
                pending_chunks.append({
                    "tenant_id": PUBLIC_TENANT_ID,
                    "opinion_id": data["opinion_id"],
                    "case_name": data["case_name"],
                    "citation": data["citation"],
                    "court_id": data["court_id"],
                    "date_filed": data["date_filed"],
                    "chunk_index": idx,
                    "content": chunk_content,
                })
                total_chunks += 1

            ingested_opinions += 1

            if len(pending_chunks) >= args.batch_size:
                flush_batch()
                pbar.set_postfix(
                    ingested=ingested_opinions,
                    chunks=total_chunks,
                    embedded=total_embedded,
                )

    # Final flush
    flush_batch()

    conn.close()

    print("\n--- Ingestion complete ---")
    print(f"  Total opinions read:    {total_opinions:,}")
    print(f"  Opinions skipped:       {skipped_opinions:,}")
    print(f"  Opinions ingested:      {ingested_opinions:,}")
    print(f"  Total chunks created:   {total_chunks:,}")
    print(f"  Total chunks embedded:  {total_embedded:,}")


if __name__ == "__main__":
    main()
