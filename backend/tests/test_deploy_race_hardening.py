"""Deploy race hardening observed during the workstream D stage deploys.

Two latent defects surfaced when the first post-B2 stage deploy recreated the
LiteLLM gateway, and when an hourly scheduled backup collided with a deploy:

1. The deploy's health-wait loop covered backend/scheduler/nginx but never the
   LiteLLM healthcheck, so the verification gates failed a recreated gateway
   still in ``health=starting``.
2. ``legalapp-backup.timer`` (hourly, ``RandomizedDelaySec=10m``) can hold
   restic's exclusive repository lock when a deploy's proven-backup step runs;
   restic failed fast (``waiting up to 0s``) and aborted the deploy before any
   mutation.

These pins keep the bounded waits in place.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _script(name: str) -> str:
    return (ROOT / "scripts" / name).read_text(encoding="utf-8")


def test_deploy_waits_for_recreated_gateway_health_before_gates():
    deploy = _script("deploy_prod.sh")

    wait = deploy.index("Waiting for the recreated LiteLLM gateway")
    # The wait is gated on this release having recreated the gateway, so the
    # common unchanged-gateway path skips it entirely.
    guard = deploy.rindex('[[ "$litellm_gateway_changed" == true ]]', 0, wait)
    assert guard < wait
    # It runs after the app-tier health loop and before the release
    # verification gates that require health=healthy.
    health_loop = deploy.index('"$backend_health" != healthy')
    gates = deploy.index("Running release verification gates")
    assert health_loop < wait < gates
    # Bounded window: 90 probes x 4s, with an early break on healthy/running.
    assert "seq 1 90" in deploy
    assert (
        '[[ "$litellm_health" == healthy || "$litellm_health" == running ]] && break'
        in deploy
    )
    # Fail closed: an unhealthy gateway dumps logs and exits before the gates.
    unhealthy = deploy.index("recreated LiteLLM gateway reported unhealthy")
    timeout = deploy.index(
        "recreated LiteLLM gateway did not become healthy within the bounded window"
    )
    assert wait < unhealthy < gates
    assert wait < timeout < gates
    assert deploy.count("exit 6") >= 3


def test_backup_retries_the_exclusive_restic_lock():
    backup = _script("backup_db.sh")

    # Only restic backup takes the exclusive repository lock; a collision with
    # the hourly timer must delay for a bounded window, never fail the backup.
    assert 'restic backup --retry-lock "${RESTIC_RETRY_LOCK:-10m}"' in backup
    assert backup.count("--retry-lock") == 1
    # Read-only commands must not inherit the wait.
    snapshots_line = next(
        line
        for line in backup.splitlines()
        if line.strip().startswith("restic snapshots")
    )
    assert "retry-lock" not in snapshots_line
