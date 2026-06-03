# CourtListener Jetson Pipeline

This pipeline uses one NVIDIA Jetson on the same network as PostgreSQL. The Jetson downloads CourtListener bulk data to local NVMe, inserts unembedded chunks into `public_chunks`, then generates BGE-small embeddings directly against PostgreSQL.

## 1. Prerequisites

- Alembic migration `006_public_chunks` has run on the target database.
- PostgreSQL has `pgvector` installed.
- Jetson can reach the PostgreSQL host and port.
- Jetson has Python dependencies installed:

```bash
pip install psycopg2-binary sentence-transformers tqdm python-dotenv torch tiktoken
```

## 2. Download CourtListener Data

Store bulk opinion files on the Jetson NVMe, for example:

```bash
mkdir -p /data/courtlistener
# Download opinion JSON or JSON.gz files from https://www.courtlistener.com/api/bulk-info/
```

## 3. Extract And Chunk Opinions

The ingest script does not create schema and does not embed. It inserts `public_chunks` rows with `embedding = NULL`.

```bash
export DATABASE_URL='postgresql://user:pass@postgres-host:5432/clarity'
python scripts/ingest_courtlistener.py \
  --file /data/courtlistener/opinions.json.gz \
  --batch-size 1000
```

For a smoke test:

```bash
python scripts/ingest_courtlistener.py \
  --file /data/courtlistener/opinions.json.gz \
  --batch-size 100 \
  --limit 1000
```

## 4. Embed On The Jetson

Run one worker for the single-Jetson setup:

```bash
python scripts/jetson_embed_worker.py \
  --worker-id 0 \
  --total-workers 1 \
  --batch-size 64 \
  --db-url "$DATABASE_URL" \
  --loop
```

Or launch remotely from the app checkout:

```bash
export JETSON_HOST=jetson.local
export JETSON_USER=jetson
export DATABASE_URL='postgresql://user:pass@postgres-host:5432/clarity'
bash scripts/trigger_jetson_workers.sh
```

## 5. Build The Vector Index

After embeddings are mostly complete, run:

```bash
psql "$DATABASE_URL" -f scripts/create_public_chunks_index.sql
```

## 6. Verify Counts

```sql
SELECT COUNT(*) AS total_chunks FROM public_chunks;
SELECT COUNT(*) AS embedded_chunks FROM public_chunks WHERE embedding IS NOT NULL;
SELECT COUNT(*) AS pending_chunks FROM public_chunks WHERE embedding IS NULL;
```

## 7. Production RAG Behavior

Private tenant documents continue to use OpenAI-compatible 1536-dimensional embeddings in `chunks`. Public CourtListener search uses BGE-small 384-dimensional query embeddings against `public_chunks` when `sentence-transformers` is available in the backend runtime. If it is not installed, private RAG still works and public RAG is skipped.
