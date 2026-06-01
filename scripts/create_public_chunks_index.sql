-- Build after the Jetson has populated public_chunks.embedding.
-- Must run outside a transaction because CONCURRENTLY cannot run in a transaction block.

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_public_chunks_embedding
ON public_chunks USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

ANALYZE public_chunks;
