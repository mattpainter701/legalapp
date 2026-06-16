"""Add clause-level chunk metadata and full-text search index.

Revision ID: 060_chunk_metadata_fts
Revises: 059_matter_correspondence
Create Date: 2026-06-16

- Adds section_path (legal document hierarchy) and clause_type
  (definition/obligation/remedy/governing_law/recital/general) columns
  to the chunks table so the retrieval layer can do clause-type-aware
  reranking and filtering.
- Adds a GIN-indexed tsvector column for PostgreSQL full-text search
  (BM25-like) to enable hybrid retrieval (dense embeddings + FTS).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "060"
down_revision: Union[str, None] = "059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Add clause metadata columns ──────────────────────────────────────
    op.add_column(
        "chunks",
        sa.Column(
            "section_path",
            sa.String(1000),
            nullable=True,
            comment="Document hierarchy path, e.g. Article I > Section 1.01 > (a)",
        ),
    )
    op.add_column(
        "chunks",
        sa.Column(
            "clause_type",
            sa.String(50),
            nullable=True,
            server_default=sa.text("'general'"),
            comment="definition | obligation | remedy | governing_law | recital | general",
        ),
    )

    # ── Full-text search (BM25-like) ─────────────────────────────────────
    # Add a generated tsvector column for PostgreSQL FTS
    op.execute(
        sa.text(
            """
            ALTER TABLE chunks
            ADD COLUMN fts tsvector
            GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED
            """
        )
    )
    op.create_index(
        "ix_chunks_fts",
        "chunks",
        ["fts"],
        postgresql_using="gin",
    )

    # ── Indexes for clause-type filtering ────────────────────────────────
    op.create_index("ix_chunks_clause_type", "chunks", ["clause_type"])
    op.create_index(
        "ix_chunks_document_clause",
        "chunks",
        ["document_id", "clause_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_chunks_document_clause")
    op.drop_index("ix_chunks_clause_type")
    op.drop_index("ix_chunks_fts", postgresql_using="gin")
    op.execute(sa.text("ALTER TABLE chunks DROP COLUMN IF EXISTS fts"))
    op.drop_column("chunks", "clause_type")
    op.drop_column("chunks", "section_path")
