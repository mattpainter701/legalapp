SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS courts (
    court_id text PRIMARY KEY,
    short_name text,
    full_name text NOT NULL,
    jurisdiction text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dockets (
    docket_id bigint PRIMARY KEY,
    court_id text REFERENCES courts(court_id),
    docket_number text,
    case_name text,
    date_filed date,
    date_terminated date,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS opinion_clusters (
    cluster_id bigint PRIMARY KEY,
    docket_id bigint REFERENCES dockets(docket_id),
    case_name text,
    date_filed date,
    precedential_status text,
    citations jsonb NOT NULL DEFAULT '[]'::jsonb,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS opinions (
    opinion_id bigint PRIMARY KEY,
    cluster_id bigint REFERENCES opinion_clusters(cluster_id),
    type text,
    author_id bigint,
    html_with_citations text,
    plain_text text,
    sha1 text,
    source_url text,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS opinion_citations (
    id bigserial PRIMARY KEY,
    citing_opinion_id bigint REFERENCES opinions(opinion_id),
    cited_opinion_id bigint,
    cited_cluster_id bigint,
    cited_reporter text,
    cited_volume text,
    cited_page text,
    depth integer NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS opinion_chunks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    opinion_id bigint NOT NULL REFERENCES opinions(opinion_id) ON DELETE CASCADE,
    cluster_id bigint REFERENCES opinion_clusters(cluster_id),
    court_id text REFERENCES courts(court_id),
    chunk_index integer NOT NULL,
    content text NOT NULL,
    fts tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    embedding vector(1024),
    embedding_model text NOT NULL DEFAULT 'mixedbread-ai/mxbai-embed-large-v1',
    embedding_version integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (opinion_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS ingest_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source text NOT NULL,
    snapshot_date date,
    status text NOT NULL DEFAULT 'running',
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    rows_processed bigint NOT NULL DEFAULT 0,
    chunks_created bigint NOT NULL DEFAULT 0,
    errors jsonb NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS embedding_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    worker_id integer NOT NULL,
    total_workers integer NOT NULL,
    model text NOT NULL DEFAULT 'mixedbread-ai/mxbai-embed-large-v1',
    dim integer NOT NULL DEFAULT 1024,
    status text NOT NULL DEFAULT 'running',
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    chunks_embedded bigint NOT NULL DEFAULT 0,
    errors bigint NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_opinion_chunks_court ON opinion_chunks(court_id);
CREATE INDEX IF NOT EXISTS ix_opinion_chunks_embedding_version ON opinion_chunks(embedding_version);
CREATE INDEX IF NOT EXISTS ix_opinion_chunks_fts ON opinion_chunks USING gin(fts);
CREATE INDEX IF NOT EXISTS ix_opinion_chunks_embedding_hnsw ON opinion_chunks USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_opinion_citations_citing ON opinion_citations(citing_opinion_id);
CREATE INDEX IF NOT EXISTS ix_opinion_citations_cited ON opinion_citations(cited_opinion_id);
"""
