from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_desktop_cuda_supervisor_defaults_to_full_single_worker_coverage():
    supervisor = (ROOT / "scripts" / "run-local-direct-cuda-embedding.ps1").read_text()
    worker = (ROOT / "scripts" / "direct_cuda_embed_worker.py").read_text()

    assert "[int]$WorkerId = 0" in supervisor
    assert "[int]$TotalWorkers = 1" in supervisor
    assert "embedding-runtime" in supervisor
    assert "main-latest" not in supervisor
    assert r'$GitExe = "$env:ProgramFiles\Git\cmd\git.exe"' in supervisor
    assert "branch --show-current | Out-String" in supervisor
    assert "printenv POSTGRES_PASSWORD" in supervisor
    assert "--db-url" not in supervisor
    assert 'default=0' in worker
    assert 'default=1' in worker
    assert "mixedbread-ai/mxbai-embed-large-v1" in worker


def test_query_embedding_supervisor_is_private_and_restarting():
    supervisor = (
        ROOT / "deploy" / "jetson" / "lawhand-query-embedding-supervisor.sh"
    ).read_text()
    runbook = (ROOT / "docs" / "EMBEDDING_PIPELINE_OPERATIONS.md").read_text()
    systemd_unit = (
        ROOT / "deploy" / "jetson" / "lawhand-query-embedding@.service"
    ).read_text()

    assert "QUERY_EMBEDDING_BIND:-127.0.0.1" in supervisor
    assert "mcp_server.embedding_service:app" in supervisor
    assert "while true" in supervisor
    assert "--host ${QUERY_EMBEDDING_BIND}" in systemd_unit
    assert "--host 0.0.0.0" not in systemd_unit
    assert "IONOS never connects to PostgreSQL" in runbook
    assert "QUERY_EMBEDDING_BIND=127.0.0.1" in runbook
    assert "Docker bridge gateway at port 18031" in runbook
