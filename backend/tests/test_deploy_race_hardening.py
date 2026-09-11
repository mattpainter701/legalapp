"""Deploy race hardening observed during the workstream D stage deploys.

Two latent defects surfaced when the first post-B2 stage deploy recreated the
LiteLLM gateway, and when an hourly scheduled backup collided with a deploy:

1. The deploy's health-wait loop covered backend/scheduler/nginx but never the
   LiteLLM healthcheck, so the verification gates failed a recreated gateway
   still in ``health=starting``.
2. ``legalapp-backup.timer`` (hourly, ``RandomizedDelaySec=10m``) runs
   ``restic check``, which holds the *exclusive* repository lock for minutes.
   A deploy's proven-backup step started inside that window; its first repo
   command -- a read-only ``restic snapshots`` -- failed fast (``waiting up to
   0s``) and aborted the deploy before any mutation. The first fix retried
   only ``restic backup``, which takes a shared lock and was never the command
   that collided.

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


def test_backup_retries_every_restic_repository_lock():
    backup = _script("backup_db.sh")

    # Every repository command takes a lock, and the timer's ``check`` holds
    # the exclusive one -- which blocks read-only ``snapshots`` too. Each
    # command must wait for a bounded window rather than fail the backup.
    definition = backup.index('restic_lock_wait="${RESTIC_RETRY_LOCK:-10m}"')
    commands = [
        line.strip()
        for line in backup.splitlines()
        if line.strip().startswith("restic ")
    ]
    assert [line.split()[1] for line in commands] == [
        "snapshots",
        "backup",
        "check",
        "snapshots",
    ]
    for line in commands:
        assert '--retry-lock "$restic_lock_wait"' in line, line
    assert definition < backup.index(commands[0])
