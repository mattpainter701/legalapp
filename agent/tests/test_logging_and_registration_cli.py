import logging
from argparse import Namespace

import httpx
import pytest

from clarity_agent import __main__ as cli
from clarity_agent import config, utils


def test_windows_logging_is_rotating_and_idempotent(monkeypatch, tmp_path):
    handlers = logging.getLogger().handlers[:]
    try:
        root = logging.getLogger()
        root.handlers.clear()
        monkeypatch.setattr(utils, "_windows_file_logging_enabled", lambda: True)
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)

        utils.setup_logging()
        utils.setup_logging()

        matching = [
            h
            for h in logging.getLogger().handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(matching) == 1
        assert matching[0].baseFilename.endswith("logs\\agent.log") or matching[
            0
        ].baseFilename.endswith("logs/agent.log")
        matching[0].emit(logging.LogRecord("x", logging.INFO, "", 0, "ok", (), None))
        assert (
            (tmp_path / "logs" / "agent.log")
            .read_text(encoding="utf-8")
            .endswith("ok\n")
        )
    finally:
        for handler in logging.getLogger().handlers:
            handler.close()
        logging.getLogger().handlers[:] = handlers


def test_register_cli_hides_http_error_and_pairing_code(monkeypatch):
    request = httpx.Request("POST", "https://getlawhand.com/api/v1/smb/agents/register")
    response = httpx.Response(
        400, request=request, json={"detail": "Pairing code expired"}
    )

    async def fail(*_args, **_kwargs):
        raise httpx.HTTPStatusError("bad", request=request, response=response)

    monkeypatch.setattr(cli, "_register_with_saas", fail)
    # Registration initializes the real Windows service log before making a
    # request. Keep this unit test from ever attaching a handler to an
    # installed agent's ProgramData log on a developer workstation.
    monkeypatch.setattr(cli, "setup_logging", lambda _level: None)
    monkeypatch.setattr(
        cli,
        "host_info",
        lambda: {
            "hostname": "host",
            "agent_version": "0.15.1",
            "os_info": "test",
        },
    )
    args = Namespace(
        code="SECRET-CODE",
        name="",
        url=None,
        smb_username="",
        smb_password="",
        smb_domain="",
    )
    with pytest.raises(SystemExit) as exc:
        cli.cmd_register(args)
    assert str(exc.value) == (
        "SaaS rejected registration (HTTP 400): Pairing code expired"
    )
    assert "SECRET-CODE" not in str(exc.value)


@pytest.mark.parametrize("failure", [False, True])
def test_search_preflight_cli_reports_runtime_result(monkeypatch, capsys, failure):
    import sys
    from search_node import extraction, config

    monkeypatch.setattr(config.Settings, "from_env", lambda: object())

    class Extractor:
        def __init__(self, settings):
            pass

        def preflight(self):
            if failure:
                raise RuntimeError("missing isolated runtime")

    monkeypatch.setattr(extraction, "IsolatedExtractor", Extractor)
    monkeypatch.setattr(sys, "argv", ["lawhand-agent", "search-preflight"])
    if failure:
        with pytest.raises(SystemExit, match="missing isolated runtime"):
            cli.main()
    else:
        cli.main()
        assert "runtime and process containment are ready" in capsys.readouterr().out


def test_status_reports_opensearch_manifest_without_search(
    monkeypatch, capsys, tmp_path
):
    from clarity_agent.config import AgentConfig

    config = AgentConfig(
        search_node_enabled=True, ledger_path=str(tmp_path / "ledger.db")
    )
    monkeypatch.setattr(cli.AgentConfig, "load", lambda: config)

    async def stats(path):
        assert path.endswith("opensearch-manifest.db")
        return {
            "available": True,
            "statuses": {"error": {"files": 2}, "ready": {"files": 4}},
        }

    monkeypatch.setattr(cli, "read_index_stats", stats)
    cli.cmd_status(Namespace())
    output = capsys.readouterr().out
    assert "OpenSearch:" in output and "error: 2 file(s)" in output
    assert "search-preflight" in output
