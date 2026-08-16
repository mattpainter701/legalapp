import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server import dispatcher, embedding_scheduler  # noqa: E402
from mcp_server.dispatcher import (  # noqa: E402
    JetsonDispatchError,
    JetsonTarget,
    dispatch_targets,
    redact_secrets,
    run_ssh_command,
)
from mcp_server.embedding_scheduler import (  # noqa: E402
    SchedulerConfig,
    retry_delay_seconds,
    run_scheduler,
    unembedded_chunk_count,
)


def test_redact_secrets_masks_database_urls_and_password_assignments():
    message = (
        "command --db-url postgresql://courtlistener:supersecret@db:5432/courtlistener "
        "password=another-secret"
    )

    redacted = redact_secrets(message)

    assert "supersecret" not in redacted
    assert "another-secret" not in redacted
    assert "--db-url <redacted>" in redacted
    assert "password=<redacted>" in redacted


def test_run_ssh_command_raises_log_safe_error(monkeypatch):
    target = JetsonTarget(env_index=0, worker_id=0, host="jetson.test", user="worker")
    remote_command = (
        "python3 worker.py --db-url "
        "postgresql://courtlistener:supersecret@db:5432/courtlistener"
    )

    captured = []

    def fail(command, **_kwargs):
        captured.append(command)
        raise subprocess.CalledProcessError(255, command)

    monkeypatch.setattr(subprocess, "run", fail)

    with pytest.raises(JetsonDispatchError) as raised:
        run_ssh_command(target, remote_command)

    assert "supersecret" not in str(raised.value)
    assert "jetson.test" in str(raised.value)
    assert "exit_status=255" in str(raised.value)
    assert "ConnectTimeout=15" in captured[0]
    assert "ServerAliveInterval=15" in captured[0]
    assert "ServerAliveCountMax=3" in captured[0]


def test_foreground_worker_failure_does_not_echo_process_arguments(monkeypatch):
    class FailedProcess:
        args = [
            "ssh",
            "worker@jetson.test",
            "--db-url postgresql://courtlistener:supersecret@db/courtlistener",
        ]

        def wait(self):
            return 255

    monkeypatch.setattr(dispatcher, "run_ssh_command", lambda *_args, **_kwargs: FailedProcess())

    with pytest.raises(JetsonDispatchError) as raised:
        dispatch_targets(
            [JetsonTarget(env_index=0, worker_id=0, host="jetson.test", user="worker")],
            "/data/legalapp-embeddings/scripts",
            "postgresql://courtlistener:supersecret@db:5432/courtlistener",
            8,
            reverse_tunnel=True,
        )

    assert "supersecret" not in str(raised.value)
    assert "exit_status=255" in str(raised.value)


def test_reverse_tunnel_fails_fast_when_remote_forward_cannot_open(monkeypatch):
    captured = []

    class Process:
        pass

    def fake_popen(command, **_kwargs):
        captured.append(command)
        return Process()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    process = run_ssh_command(
        JetsonTarget(env_index=0, worker_id=0, host="jetson.test", user="worker"),
        "python3 worker.py",
        reverse_tunnel="127.0.0.1:15434:db:5432",
        foreground=True,
    )

    assert isinstance(process, Process)
    assert "ExitOnForwardFailure=yes" in captured[0]


@pytest.mark.parametrize(
    ("failures", "expected"),
    [(0, 0), (1, 60), (2, 120), (3, 240), (4, 300), (8, 300), (100_000, 300)],
)
def test_retry_delay_is_exponential_and_capped(failures, expected):
    assert retry_delay_seconds(failures, 60, 300) == expected


def test_scheduler_log_redacts_database_password_and_uses_retry_delay(monkeypatch, capsys):
    secret = "scheduler-secret"

    def fail_connect(_db_url):
        raise RuntimeError(
            f"connection failed postgresql://courtlistener:{secret}@db:5432/courtlistener"
        )

    delays = []

    def stop_after_delay(seconds):
        delays.append(seconds)
        raise StopIteration

    monkeypatch.setattr(embedding_scheduler, "connect", fail_connect)
    monkeypatch.setattr(embedding_scheduler.time, "sleep", stop_after_delay)

    with pytest.raises(StopIteration):
        run_scheduler(
            SchedulerConfig(
                db_url=f"postgresql://courtlistener:{secret}@db:5432/courtlistener",
                retry_initial_seconds=7,
                retry_max_seconds=30,
            )
        )

    output = capsys.readouterr().out
    assert secret not in output
    assert "retry_in_seconds=7" in output
    assert delays == [7]


def test_scheduler_counts_only_retrievable_authority_chunks():
    class Cursor:
        def __init__(self):
            self.sql = ""

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql):
            self.sql = sql

        def fetchone(self):
            return [0]

    class Connection:
        def __init__(self):
            self.cursor_obj = Cursor()

        def cursor(self):
            return self.cursor_obj

    connection = Connection()

    assert unembedded_chunk_count(connection) == 0
    assert (
        "JOIN legal_documents d ON d.id = c.document_id" in connection.cursor_obj.sql
    )
    assert (
        "JOIN legal_sources s ON s.source_key = d.source_key"
        in connection.cursor_obj.sql
    )
    assert "d.document_status = 'current'" in connection.cursor_obj.sql
    assert "s.enabled IS TRUE" in connection.cursor_obj.sql


def test_jetson_systemd_unit_restarts_query_embedding_service():
    unit = (
        Path(__file__).resolve().parents[2]
        / "deploy"
        / "jetson"
        / "lawhand-query-embedding@.service"
    )
    contents = unit.read_text()

    assert "Restart=always" in contents
    assert "RestartSec=10s" in contents
    assert "mcp_server.embedding_service:app" in contents
