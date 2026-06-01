"""Add public_chunks table for BGE-384 CourtListener embeddings (Jetson Phase 2).

Revision ID: 006
Revises: 005
Create Date: 2026-06-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # public_chunks stores CourtListener opinions with BGE-small 384-dim embeddings.
    # Populated offline by scripts/ingest_courtlistener.py and
    # scripts/jetson_embed_worker.py. The main `chunks` table uses OpenAI 1536-dim.
    op.create_table(
        "public_chunks",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("opinion_id", sa.String(255), nullable=True),
        sa.Column("case_name", sa.String(500), nullable=True),
        sa.Column("citation", sa.String(500), nullable=True),
        sa.Column("court", sa.String(255), nullable=True),
        sa.Column("decision_date", sa.Date, nullable=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=True),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index("ix_public_chunks_opinion_id", "public_chunks", ["opinion_id"])
    op.create_index("ix_public_chunks_court", "public_chunks", ["court"])

    # IVFFlat cosine index — add after initial bulk load (lists=100 needs ~3k rows).
    # Run manually after ingest: CREATE INDEX CONCURRENTLY ix_public_chunks_embedding
    #   ON public_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);


def downgrade() -> None:
    op.drop_index("ix_public_chunks_court", table_name="public_chunks")
    op.drop_index("ix_public_chunks_opinion_id", table_name="public_chunks")
    op.drop_table("public_chunks")
