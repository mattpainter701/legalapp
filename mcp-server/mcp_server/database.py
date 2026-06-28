from __future__ import annotations

import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras


def normalize_db_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )


def db_url_from_env() -> str:
    url = os.environ.get("VECTORDB_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("VECTORDB_URL or DATABASE_URL is required")
    return normalize_db_url(url)


@contextmanager
def connect(db_url: str | None = None):
    conn = psycopg2.connect(normalize_db_url(db_url) if db_url else db_url_from_env())
    try:
        yield conn
    finally:
        conn.close()


def dict_rows(cursor):
    columns = [desc.name for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]
