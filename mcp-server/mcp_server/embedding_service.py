from __future__ import annotations

from functools import lru_cache

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .jetson_worker import load_model
from .worker_config import DEFAULT_MODEL

app = FastAPI(title="LawHand CourtListener Query Embeddings", version="0.1.0")


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=32)
    model: str = DEFAULT_MODEL
    batch_size: int = Field(default=8, ge=1, le=32)


@lru_cache(maxsize=2)
def _model(model_name: str):
    return load_model(model_name)


@app.get("/health")
def health():
    return {"status": "ok", "model": DEFAULT_MODEL}


@app.post("/embed")
def embed(body: EmbedRequest):
    model = _model(body.model)
    vectors = model.encode(
        body.texts,
        batch_size=body.batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return {
        "model": body.model,
        "dim": 1024,
        "embeddings": [vector.tolist() for vector in vectors],
    }
