import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server.jetson_worker import (
    OllamaEmbeddingModel,
    embed_batch,
    measured_host_telemetry,
    update_sql,
)


class Response:
    def __init__(self, vectors):
        self.vectors = vectors

    def raise_for_status(self):
        return None

    def json(self):
        return {"embeddings": self.vectors}


class Client:
    def __init__(self, vectors):
        self.vectors = vectors
        self.request = None

    def post(self, path, json):
        self.request = (path, json)
        return Response(self.vectors)


def test_ollama_model_matches_worker_interface_and_dimension():
    model = OllamaEmbeddingModel.__new__(OllamaEmbeddingModel)
    model.model_name = "mxbai-embed-large"
    model.client = Client([[0.0] * 1024])

    vectors = embed_batch(model, ["Ohio probate"], 32)

    assert len(vectors) == 1
    assert len(vectors[0]) == 1024
    assert model.client.request[0] == "/api/embed"
    assert model.client.request[1]["input"][0].startswith(
        "Represent this sentence for searching relevant passages: "
    )


def test_ollama_model_rejects_wrong_dimension():
    model = OllamaEmbeddingModel.__new__(OllamaEmbeddingModel)
    model.model_name = "mxbai-embed-large"
    model.client = Client([[0.0] * 768])

    with pytest.raises(RuntimeError, match="1024-dimensional"):
        model.encode(["text"])


def test_snapshot_worker_uses_snapshot_identity_and_measured_telemetry():
    sql = update_sql("authority_case_chunks", "v2")
    assert "WHERE chunk_id = %s" in sql
    assert "WHERE id = %s" not in sql
    telemetry = measured_host_telemetry()
    assert telemetry["observed_at"].endswith("Z")
    assert "capacity" in telemetry
    assert telemetry["temperature_source"] in {"psutil", "unavailable"}
