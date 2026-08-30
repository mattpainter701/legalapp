SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO schema_migrations (version) VALUES ('authority-control-plane-v2')
ON CONFLICT (version) DO NOTHING;

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

ALTER TABLE opinions ADD COLUMN IF NOT EXISTS source_created_at timestamptz;
ALTER TABLE opinions ADD COLUMN IF NOT EXISTS source_modified_at timestamptz;
ALTER TABLE opinions ADD COLUMN IF NOT EXISTS content_hash text;
ALTER TABLE opinions ADD COLUMN IF NOT EXISTS last_synced_at timestamptz;
ALTER TABLE opinions ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb;

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

CREATE TABLE IF NOT EXISTS legal_sources (
    source_key text PRIMARY KEY,
    display_name text,
    description text,
    publisher text NOT NULL,
    source_type text NOT NULL,
    jurisdiction text,
    court_id text,
    canonical_url text NOT NULL,
    authority_tier text NOT NULL DEFAULT 'secondary',
    official_status text NOT NULL DEFAULT 'aggregator',
    ingestion_mode text NOT NULL DEFAULT 'manual',
    storage_policy text NOT NULL DEFAULT 'metadata_only',
    access_type text NOT NULL DEFAULT 'public_web',
    license_status text NOT NULL DEFAULT 'review_required',
    terms_url text,
    sync_frequency text,
    data_format text,
    corpus_table text,
    enabled boolean NOT NULL DEFAULT false,
    priority integer NOT NULL DEFAULT 100,
    coverage_start date,
    coverage_end date,
    coverage_kind text NOT NULL DEFAULT 'bounded',
    last_attempted_at timestamptz,
    last_successful_sync_at timestamptz,
    item_count bigint NOT NULL DEFAULT 0,
    chunk_count bigint NOT NULL DEFAULT 0,
    embedded_chunk_count bigint NOT NULL DEFAULT 0,
    parser_version text,
    embedding_model text,
    embedding_version integer,
    current_error text,
    licensing_notes text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Reviewed provenance is a control-plane contract, not an inference from a
-- public URL.  These fields are deliberately nullable for old seeded rows;
-- such rows remain claim-limited until an operator review fills them in.
ALTER TABLE legal_sources ADD COLUMN IF NOT EXISTS rights_decision text NOT NULL DEFAULT 'pending_review';
ALTER TABLE legal_sources ADD COLUMN IF NOT EXISTS source_tier text;
ALTER TABLE legal_sources ADD COLUMN IF NOT EXISTS geographic_scope jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE legal_sources ADD COLUMN IF NOT EXISTS temporal_scope jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE legal_sources ADD COLUMN IF NOT EXISTS expected_cadence text;
ALTER TABLE legal_sources ADD COLUMN IF NOT EXISTS completeness_caveats text;
ALTER TABLE legal_sources ADD COLUMN IF NOT EXISTS claim_safe_wording text;
ALTER TABLE legal_sources ADD COLUMN IF NOT EXISTS reviewed_at timestamptz;
ALTER TABLE legal_sources ADD COLUMN IF NOT EXISTS reviewed_by text;
ALTER TABLE legal_sources ADD COLUMN IF NOT EXISTS review_reason text;

CREATE TABLE IF NOT EXISTS authority_corpus_versions (
    version text PRIMARY KEY,
    status text NOT NULL DEFAULT 'staged',
    manifest_hash text NOT NULL,
    as_of timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    promoted_at timestamptz,
    rolled_back_at timestamptz,
    rollback_of text REFERENCES authority_corpus_versions(version),
    reason text,
    embedding_model text,
    embedding_version text,
    embedding_dimension integer,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (status IN ('staged', 'canary', 'promoted', 'rolled_back', 'retired'))
);

CREATE TABLE IF NOT EXISTS authority_harvest_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_key text NOT NULL REFERENCES legal_sources(source_key) ON DELETE RESTRICT,
    partition_key text NOT NULL,
    corpus_version text REFERENCES authority_corpus_versions(version),
    external_id text,
    content_hash text,
    cursor_before text,
    cursor_after text,
    event_status text NOT NULL,
    retry_count integer NOT NULL DEFAULT 0,
    quarantine_reason text,
    citation text,
    court text,
    effective_date date,
    observed_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (event_status IN ('accepted', 'duplicate', 'skipped', 'retryable_failure', 'quarantined', 'failed'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_authority_harvest_identity_v2
    ON authority_harvest_events(source_key, partition_key,
       COALESCE(external_id, ''), COALESCE(content_hash, ''), event_status);

CREATE TABLE IF NOT EXISTS authority_audits (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    corpus_version text NOT NULL REFERENCES authority_corpus_versions(version),
    audit_kind text NOT NULL,
    methodology text NOT NULL,
    thresholds jsonb NOT NULL DEFAULT '{}'::jsonb,
    result jsonb NOT NULL DEFAULT '{}'::jsonb,
    passed boolean NOT NULL,
    sampled_at timestamptz NOT NULL DEFAULT now(),
    auditor text NOT NULL,
    immutable_hash text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (audit_kind IN ('completeness', 'freshness', 'isolation', 'release'))
);

ALTER TABLE legal_sources ADD COLUMN IF NOT EXISTS display_name text;
ALTER TABLE legal_sources ADD COLUMN IF NOT EXISTS description text;
ALTER TABLE legal_sources ADD COLUMN IF NOT EXISTS authority_tier text NOT NULL DEFAULT 'secondary';
ALTER TABLE legal_sources ADD COLUMN IF NOT EXISTS official_status text NOT NULL DEFAULT 'aggregator';
ALTER TABLE legal_sources ADD COLUMN IF NOT EXISTS ingestion_mode text NOT NULL DEFAULT 'manual';
ALTER TABLE legal_sources ADD COLUMN IF NOT EXISTS storage_policy text NOT NULL DEFAULT 'metadata_only';
ALTER TABLE legal_sources ADD COLUMN IF NOT EXISTS access_type text NOT NULL DEFAULT 'public_web';
ALTER TABLE legal_sources ADD COLUMN IF NOT EXISTS license_status text NOT NULL DEFAULT 'review_required';
ALTER TABLE legal_sources ADD COLUMN IF NOT EXISTS terms_url text;
ALTER TABLE legal_sources ADD COLUMN IF NOT EXISTS sync_frequency text;
ALTER TABLE legal_sources ADD COLUMN IF NOT EXISTS data_format text;
ALTER TABLE legal_sources ADD COLUMN IF NOT EXISTS corpus_table text;
ALTER TABLE legal_sources ADD COLUMN IF NOT EXISTS enabled boolean NOT NULL DEFAULT false;
ALTER TABLE legal_sources ADD COLUMN IF NOT EXISTS priority integer NOT NULL DEFAULT 100;

CREATE TABLE IF NOT EXISTS source_sync_states (
    source_key text NOT NULL REFERENCES legal_sources(source_key) ON DELETE CASCADE,
    partition_key text NOT NULL,
    checkpoint_at timestamptz,
    cursor_url text,
    status text NOT NULL DEFAULT 'idle',
    last_attempted_at timestamptz,
    last_successful_sync_at timestamptz,
    rows_processed bigint NOT NULL DEFAULT 0,
    chunks_created bigint NOT NULL DEFAULT 0,
    last_error text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_key, partition_key)
);

-- Declares what a source partition is expected to contain, separately from the
-- observed sync cursor. This lets operators distinguish "complete" from a
-- successful-but-partial sync and report known coverage gaps later.
CREATE TABLE IF NOT EXISTS corpus_coverage_ledger (
    source_key text NOT NULL,
    partition_key text NOT NULL,
    expected_coverage jsonb NOT NULL DEFAULT '{}'::jsonb,
    expected_item_count bigint,
    acquisition_state text NOT NULL DEFAULT 'not_started',
    snapshot_date date,
    source_release text,
    rows_loaded bigint NOT NULL DEFAULT 0,
    chunks_loaded bigint NOT NULL DEFAULT 0,
    vectors_loaded bigint NOT NULL DEFAULT 0,
    bytes_loaded bigint NOT NULL DEFAULT 0,
    first_document_date date,
    last_document_date date,
    upstream_modified_at timestamptz,
    last_checked_at timestamptz,
    stale_after timestamptz,
    gap_reason text,
    owner text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_key, partition_key),
    CHECK (acquisition_state IN ('not_started', 'staged', 'loading', 'indexed',
        'complete', 'partial', 'blocked', 'retired'))
);

CREATE TABLE IF NOT EXISTS legal_documents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_key text NOT NULL REFERENCES legal_sources(source_key) ON DELETE RESTRICT,
    external_id text NOT NULL,
    document_type text NOT NULL,
    title text NOT NULL,
    citation text,
    jurisdiction text,
    authority_tier text NOT NULL,
    document_status text NOT NULL DEFAULT 'current',
    publication_date date,
    effective_date date,
    termination_date date,
    canonical_url text NOT NULL,
    source_modified_at timestamptz,
    retrieved_at timestamptz NOT NULL DEFAULT now(),
    content_hash text,
    raw_media_type text,
    raw_storage_uri text,
    parser_version text,
    text_content text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_key, external_id)
);

CREATE TABLE IF NOT EXISTS legal_document_chunks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES legal_documents(id) ON DELETE CASCADE,
    chunk_index integer NOT NULL,
    heading_path jsonb NOT NULL DEFAULT '[]'::jsonb,
    content text NOT NULL,
    content_hash text NOT NULL,
    token_count integer,
    fts tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    embedding vector(1024),
    embedding_model text NOT NULL DEFAULT 'mixedbread-ai/mxbai-embed-large-v1',
    embedding_version integer NOT NULL DEFAULT 0,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index)
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

ALTER TABLE legal_documents ADD COLUMN IF NOT EXISTS corpus_version text REFERENCES authority_corpus_versions(version);
ALTER TABLE legal_document_chunks ADD COLUMN IF NOT EXISTS corpus_version text REFERENCES authority_corpus_versions(version);
ALTER TABLE opinion_clusters ADD COLUMN IF NOT EXISTS corpus_version text REFERENCES authority_corpus_versions(version);
ALTER TABLE opinion_chunks ADD COLUMN IF NOT EXISTS corpus_version text REFERENCES authority_corpus_versions(version);
ALTER TABLE source_sync_states ADD COLUMN IF NOT EXISTS retry_count integer NOT NULL DEFAULT 0;
ALTER TABLE source_sync_states ADD COLUMN IF NOT EXISTS dead_letter_count integer NOT NULL DEFAULT 0;
ALTER TABLE source_sync_states ADD COLUMN IF NOT EXISTS lag_seconds integer;
ALTER TABLE source_sync_states ADD COLUMN IF NOT EXISTS next_retry_at timestamptz;
ALTER TABLE source_sync_states ADD COLUMN IF NOT EXISTS last_cursor_hash text;

CREATE TABLE IF NOT EXISTS authority_embedding_shards (
    shard_key text PRIMARY KEY,
    corpus_version text NOT NULL REFERENCES authority_corpus_versions(version),
    corpus_table text NOT NULL,
    model text NOT NULL,
    model_version text NOT NULL,
    dimension integer NOT NULL,
    status text NOT NULL DEFAULT 'queued',
    lease_owner text,
    lease_expires_at timestamptz,
    heartbeat_at timestamptz,
    attempts integer NOT NULL DEFAULT 0,
    dead_letter_reason text,
    throughput_per_minute numeric,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status IN ('queued', 'leased', 'complete', 'retryable', 'dead_letter'))
);

CREATE INDEX IF NOT EXISTS ix_opinion_chunks_court ON opinion_chunks(court_id);
CREATE INDEX IF NOT EXISTS ix_opinion_chunks_embedding_version ON opinion_chunks(embedding_version);
CREATE INDEX IF NOT EXISTS ix_opinions_source_modified_at ON opinions(source_modified_at);
CREATE INDEX IF NOT EXISTS ix_source_sync_states_status ON source_sync_states(status, updated_at);
CREATE INDEX IF NOT EXISTS ix_corpus_coverage_ledger_state ON corpus_coverage_ledger(acquisition_state, updated_at);
CREATE INDEX IF NOT EXISTS ix_legal_sources_priority ON legal_sources(enabled, priority, source_key);
CREATE INDEX IF NOT EXISTS ix_authority_harvest_events_source ON authority_harvest_events(source_key, partition_key, observed_at);
CREATE INDEX IF NOT EXISTS ix_authority_audits_version ON authority_audits(corpus_version, sampled_at);
CREATE INDEX IF NOT EXISTS ix_legal_documents_source ON legal_documents(source_key, document_type);
CREATE INDEX IF NOT EXISTS ix_legal_documents_citation ON legal_documents(citation) WHERE citation IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_legal_documents_effective ON legal_documents(jurisdiction, effective_date);
CREATE INDEX IF NOT EXISTS ix_legal_document_chunks_fts ON legal_document_chunks USING gin(fts);
CREATE INDEX IF NOT EXISTS ix_legal_document_chunks_embedding_hnsw ON legal_document_chunks USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_legal_documents_corpus_version ON legal_documents(corpus_version);
CREATE INDEX IF NOT EXISTS ix_legal_document_chunks_corpus_version ON legal_document_chunks(corpus_version);
CREATE INDEX IF NOT EXISTS ix_opinion_chunks_fts ON opinion_chunks USING gin(fts);
CREATE INDEX IF NOT EXISTS ix_opinion_chunks_embedding_hnsw ON opinion_chunks USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_opinion_citations_citing ON opinion_citations(citing_opinion_id);
CREATE INDEX IF NOT EXISTS ix_opinion_citations_cited ON opinion_citations(cited_opinion_id);
CREATE INDEX IF NOT EXISTS ix_opinion_citations_edge ON opinion_citations(citing_opinion_id, cited_opinion_id);
CREATE INDEX IF NOT EXISTS ix_opinion_citations_reporter ON opinion_citations(cited_cluster_id, cited_reporter, cited_volume, cited_page);
"""
