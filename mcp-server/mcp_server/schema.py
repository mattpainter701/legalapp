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
    CHECK (event_status IN ('accepted', 'duplicate', 'skipped', 'retryable_failure', 'quarantined', 'failed', 'dead_letter'))
);
CREATE TABLE IF NOT EXISTS authority_harvest_checkpoints (
    source_key text NOT NULL REFERENCES legal_sources(source_key) ON DELETE RESTRICT,
    partition_key text NOT NULL,
    corpus_version text NOT NULL REFERENCES authority_corpus_versions(version),
    cursor_url text,
    cursor_hash text,
    status text NOT NULL DEFAULT 'active',
    retry_count integer NOT NULL DEFAULT 0,
    next_retry_at timestamptz,
    dead_letter_at timestamptz,
    last_successful_harvest_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_key, partition_key, corpus_version)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_authority_harvest_identity_v2
    ON authority_harvest_events(corpus_version, source_key, partition_key,
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

CREATE TABLE IF NOT EXISTS authority_operator_assertions (
    nonce text PRIMARY KEY,
    credential_id text NOT NULL,
    actor text NOT NULL,
    scope text NOT NULL,
    method text NOT NULL,
    path text NOT NULL,
    body_sha256 text NOT NULL,
    issued_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_authority_operator_assertions_expiry
    ON authority_operator_assertions(expires_at);

CREATE OR REPLACE FUNCTION reject_authority_evidence_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'authority evidence is append-only';
END;
$$;
DROP TRIGGER IF EXISTS authority_audits_append_only ON authority_audits;
CREATE TRIGGER authority_audits_append_only
    BEFORE UPDATE OR DELETE ON authority_audits
    FOR EACH ROW EXECUTE FUNCTION reject_authority_evidence_mutation();
DROP TRIGGER IF EXISTS authority_harvest_events_append_only ON authority_harvest_events;
CREATE TRIGGER authority_harvest_events_append_only
    BEFORE UPDATE OR DELETE ON authority_harvest_events
    FOR EACH ROW EXECUTE FUNCTION reject_authority_evidence_mutation();

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
    source_release text NOT NULL DEFAULT 'legacy',
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
    PRIMARY KEY (source_key, partition_key, source_release),
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
    corpus_version text REFERENCES authority_corpus_versions(version),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_key, external_id, corpus_version)
);

ALTER TABLE legal_documents DROP CONSTRAINT IF EXISTS legal_documents_source_key_external_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS ux_legal_documents_authority_identity
    ON legal_documents(source_key, external_id, corpus_version);

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
-- Preserve the currently served corpus during upgrade; do not leave legacy
-- rows invisible merely because version columns were introduced later.
UPDATE legal_documents
SET corpus_version = (SELECT version FROM authority_corpus_versions WHERE status='promoted' ORDER BY promoted_at DESC NULLS LAST LIMIT 1)
WHERE corpus_version IS NULL
  AND EXISTS (SELECT 1 FROM authority_corpus_versions WHERE status='promoted');
UPDATE legal_document_chunks c
SET corpus_version = d.corpus_version
FROM legal_documents d
WHERE c.document_id = d.id AND c.corpus_version IS NULL;
UPDATE opinion_clusters
SET corpus_version = (SELECT version FROM authority_corpus_versions WHERE status='promoted' ORDER BY promoted_at DESC NULLS LAST LIMIT 1)
WHERE corpus_version IS NULL
  AND EXISTS (SELECT 1 FROM authority_corpus_versions WHERE status='promoted');
UPDATE opinion_chunks c
SET corpus_version = cl.corpus_version
FROM opinion_clusters cl
WHERE c.cluster_id = cl.cluster_id AND c.corpus_version IS NULL;
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
    temperature_c numeric,
    capacity_evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status IN ('queued', 'leased', 'complete', 'retryable', 'dead_letter'))
);
-- Keep upgrades safe for installations that created this table before the
-- hardware-evidence fields were introduced.
ALTER TABLE authority_embedding_shards
    ADD COLUMN IF NOT EXISTS temperature_c numeric;
ALTER TABLE authority_embedding_shards
    ADD COLUMN IF NOT EXISTS capacity_evidence jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS authority_case_clusters (
    corpus_version text NOT NULL REFERENCES authority_corpus_versions(version),
    cluster_id bigint NOT NULL,
    docket_id bigint,
    case_name text,
    date_filed date,
    citations jsonb NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (corpus_version, cluster_id)
);
CREATE TABLE IF NOT EXISTS authority_case_chunks (
    corpus_version text NOT NULL REFERENCES authority_corpus_versions(version),
    chunk_id uuid NOT NULL DEFAULT gen_random_uuid(),
    opinion_id bigint NOT NULL,
    cluster_id bigint NOT NULL,
    court_id text,
    chunk_index integer NOT NULL,
    content text NOT NULL,
    fts tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    embedding vector(1024),
    embedding_model text,
    embedding_version text,
    PRIMARY KEY (corpus_version, opinion_id, chunk_index)
);
CREATE TABLE IF NOT EXISTS authority_case_opinions (
    corpus_version text NOT NULL REFERENCES authority_corpus_versions(version),
    opinion_id bigint NOT NULL,
    cluster_id bigint NOT NULL,
    source_url text,
    plain_text text,
    PRIMARY KEY (corpus_version, opinion_id)
);
ALTER TABLE authority_case_opinions
    ADD COLUMN IF NOT EXISTS cluster_id bigint;
UPDATE authority_case_opinions ao
SET cluster_id = o.cluster_id
FROM opinions o
WHERE ao.cluster_id IS NULL AND ao.opinion_id = o.opinion_id;
UPDATE authority_case_opinions ao
SET cluster_id = chunks.cluster_id
FROM (
    SELECT DISTINCT ON (corpus_version, opinion_id)
           corpus_version, opinion_id, cluster_id
    FROM authority_case_chunks
    ORDER BY corpus_version, opinion_id
) chunks
WHERE ao.cluster_id IS NULL
  AND ao.corpus_version = chunks.corpus_version
  AND ao.opinion_id = chunks.opinion_id;
ALTER TABLE authority_case_opinions
    ALTER COLUMN cluster_id SET NOT NULL;
CREATE TABLE IF NOT EXISTS authority_case_citations (
    citation_id uuid NOT NULL DEFAULT gen_random_uuid(),
    corpus_version text NOT NULL REFERENCES authority_corpus_versions(version),
    citing_opinion_id bigint NOT NULL,
    cited_opinion_id bigint,
    cited_cluster_id bigint,
    cited_reporter text,
    cited_volume text,
    cited_page text,
    depth integer NOT NULL DEFAULT 0,
    PRIMARY KEY (citation_id)
);

-- Upgrade the original two-column ledger without losing its legacy evidence.
-- Version is part of identity so side-by-side releases cannot overwrite one
-- another's counts or freshness checkpoint.
ALTER TABLE corpus_coverage_ledger ADD COLUMN IF NOT EXISTS source_release text;
UPDATE corpus_coverage_ledger SET source_release = 'legacy'
 WHERE source_release IS NULL;
ALTER TABLE corpus_coverage_ledger ALTER COLUMN source_release SET DEFAULT 'legacy';
ALTER TABLE corpus_coverage_ledger ALTER COLUMN source_release SET NOT NULL;
DO $$
DECLARE current_primary_key text;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO current_primary_key
    FROM pg_constraint
    WHERE conrelid = 'corpus_coverage_ledger'::regclass
      AND contype = 'p';
    IF current_primary_key IS DISTINCT FROM
       'PRIMARY KEY (source_key, partition_key, source_release)' THEN
        ALTER TABLE corpus_coverage_ledger DROP CONSTRAINT IF EXISTS corpus_coverage_ledger_pkey;
        ALTER TABLE corpus_coverage_ledger
            ADD CONSTRAINT corpus_coverage_ledger_pkey
            PRIMARY KEY (source_key, partition_key, source_release);
    END IF;
END $$;
ALTER TABLE authority_case_citations ADD COLUMN IF NOT EXISTS citation_id uuid DEFAULT gen_random_uuid();
CREATE UNIQUE INDEX IF NOT EXISTS ux_authority_case_citation_identity
    ON authority_case_citations(
      corpus_version, citing_opinion_id,
      cited_opinion_id, cited_cluster_id, cited_reporter,
      cited_volume, cited_page
    ) NULLS NOT DISTINCT;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conrelid='authority_case_citations'::regclass AND c.contype='p' AND pg_get_constraintdef(c.oid) <> 'PRIMARY KEY (citation_id)') THEN
        ALTER TABLE authority_case_citations DROP CONSTRAINT authority_case_citations_pkey;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='authority_case_citations'::regclass AND conname='authority_case_citations_pkey') THEN
        ALTER TABLE authority_case_citations ADD CONSTRAINT authority_case_citations_pkey PRIMARY KEY (citation_id);
    END IF;
END $$;

-- Snapshot rows are mutable only while their release is being assembled.  A
-- promoted/retired/rolled-back release must remain an immutable explanation
-- of what was searchable at that point in time.
CREATE OR REPLACE FUNCTION reject_authority_snapshot_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE release_status text;
BEGIN
    IF TG_OP = 'INSERT' AND current_setting('authority.snapshot_backfill', true) = 'on' THEN
        RETURN NEW;
    END IF;
    SELECT status INTO release_status
    FROM authority_corpus_versions
    WHERE version = COALESCE(OLD.corpus_version, NEW.corpus_version);
    IF release_status IS DISTINCT FROM 'staged'
       AND release_status IS DISTINCT FROM 'canary' THEN
        RAISE EXCEPTION 'authority snapshot % rows are immutable after staging', release_status;
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE OR REPLACE FUNCTION install_authority_snapshot_guard(table_name text) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    EXECUTE format('DROP TRIGGER IF EXISTS authority_snapshot_immutable ON %I', table_name);
    EXECUTE format('CREATE TRIGGER authority_snapshot_immutable
                    BEFORE INSERT OR UPDATE OR DELETE ON %I
                    FOR EACH ROW EXECUTE FUNCTION reject_authority_snapshot_mutation()', table_name);
END $$;
SELECT install_authority_snapshot_guard('authority_case_clusters');
SELECT install_authority_snapshot_guard('authority_case_opinions');
SELECT install_authority_snapshot_guard('authority_case_chunks');
SELECT install_authority_snapshot_guard('authority_case_citations');
DROP FUNCTION install_authority_snapshot_guard(text);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_authority_case_opinions_cluster') THEN
        ALTER TABLE authority_case_opinions ADD CONSTRAINT fk_authority_case_opinions_cluster
          FOREIGN KEY (corpus_version, cluster_id)
          REFERENCES authority_case_clusters(corpus_version, cluster_id) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_authority_case_chunks_opinion') THEN
        ALTER TABLE authority_case_chunks ADD CONSTRAINT fk_authority_case_chunks_opinion
          FOREIGN KEY (corpus_version, opinion_id)
          REFERENCES authority_case_opinions(corpus_version, opinion_id) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_authority_case_chunks_cluster') THEN
        ALTER TABLE authority_case_chunks ADD CONSTRAINT fk_authority_case_chunks_cluster
          FOREIGN KEY (corpus_version, cluster_id)
          REFERENCES authority_case_clusters(corpus_version, cluster_id) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_authority_case_citations_citing') THEN
        ALTER TABLE authority_case_citations ADD CONSTRAINT fk_authority_case_citations_citing
          FOREIGN KEY (corpus_version, citing_opinion_id)
          REFERENCES authority_case_opinions(corpus_version, opinion_id) NOT VALID;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_opinion_chunks_court ON opinion_chunks(court_id);
CREATE INDEX IF NOT EXISTS ix_opinion_chunks_embedding_version ON opinion_chunks(embedding_version);
CREATE INDEX IF NOT EXISTS ix_opinions_source_modified_at ON opinions(source_modified_at);
CREATE INDEX IF NOT EXISTS ix_source_sync_states_status ON source_sync_states(status, updated_at);
CREATE INDEX IF NOT EXISTS ix_corpus_coverage_ledger_state ON corpus_coverage_ledger(acquisition_state, updated_at);
CREATE INDEX IF NOT EXISTS ix_legal_sources_priority ON legal_sources(enabled, priority, source_key);
CREATE INDEX IF NOT EXISTS ix_authority_harvest_events_source ON authority_harvest_events(source_key, partition_key, observed_at);
CREATE INDEX IF NOT EXISTS ix_authority_audits_version ON authority_audits(corpus_version, sampled_at);
CREATE UNIQUE INDEX IF NOT EXISTS ux_one_promoted_authority_version
    ON authority_corpus_versions ((status)) WHERE status = 'promoted';
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
