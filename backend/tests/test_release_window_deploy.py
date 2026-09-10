"""Workstream D: the deploy publishes an advisory release-window banner.

``deploy_prod.sh`` writes ``release-window.json`` into the host-status dir
(mounted read-only into the backend) once preflight has passed, and removes it
on EVERY exit path: a dedicated EXIT trap while the marker lives, folded into
the scheduler cutover trap during cutover, and re-armed for the post-cutover
gates. A failed write warns and continues — an advisory banner must never
block a deploy.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _deploy_script() -> str:
    return (ROOT / "scripts" / "deploy_prod.sh").read_text(encoding="utf-8")


def test_marker_is_written_beside_host_status_after_preflight():
    deploy = _deploy_script()

    assert 'release_window_file="$host_status_dir/release-window.json"' in deploy
    # Written as JSON by python3 (safe quoting for the message text), carrying
    # only a window id and the fixed kindly worded message — no hostnames.
    assert '"window_id": window_id' in deploy
    assert '"started_at":' in deploy
    # After preflight (TLS check) so a failing preflight never warns users,
    # and before the databases come up, so users get the full deploy as lead
    # time.
    tls_check = deploy.index("nginx TLS certificate files are missing")
    marker_write = deploy.index("Release window marker published")
    databases_up = deploy.index("Bring up both private databases")
    assert tls_check < marker_write < databases_up


def test_marker_cleanup_is_armed_on_every_exit_path():
    deploy = _deploy_script()

    cleanup_def = deploy.index("release_window_cleanup()")
    first_trap = deploy.index("trap release_window_cleanup EXIT")
    assert cleanup_def < first_trap
    # The scheduler cutover trap (which replaces the EXIT trap mid-script)
    # also removes the marker, and the cleanup is re-armed right after the
    # cutover trap is retired so the post-cutover gates are covered too.
    scheduler_trap = deploy.index("restore_previous_scheduler_on_cutover_failure()")
    cutover_rm = deploy.index('rm -f -- "$release_window_file"', scheduler_trap)
    rearmed = deploy.index("trap release_window_cleanup EXIT", cutover_rm)
    cutover_retired = deploy.index("scheduler_cutover_complete=true\ntrap - EXIT")
    assert scheduler_trap < cutover_rm < rearmed
    assert cutover_retired < rearmed
    assert deploy.count('rm -f -- "$release_window_file"') >= 2


def test_marker_write_failure_warns_and_continues():
    deploy = _deploy_script()

    warn = deploy.index("release window marker could not be written")
    continuing = deploy.index("continuing without the advisory banner")
    assert warn < continuing
    # The warning must live on the if/else of the python3 write, so a failed
    # write cannot abort the deploy.
    write_if = deploy.index('if python3 - "$release_window_file"')
    assert write_if < warn
