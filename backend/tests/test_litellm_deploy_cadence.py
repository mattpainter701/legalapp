"""Workstream B2: the gateway deploy decision lives in deploy_prod.sh.

The gateway is rebuilt/recreated only when its content-hash tag
(``legalapp-litellm:src-<hash>``) is absent from the host or no litellm
container is running; otherwise the release skips the gateway build, tags the
existing src image with the release commit, and excludes litellm and its
one-shot migrators from the force-recreate set.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _deploy_script() -> str:
    return (ROOT / "scripts" / "deploy_prod.sh").read_text(encoding="utf-8")


def test_gateway_decision_uses_content_hash_tag_and_running_container_guard():
    deploy = _deploy_script()

    assert 'litellm_src_hash="$(python3 scripts/litellm_src_hash.py)"' in deploy
    assert 'litellm_src_tag="legalapp-litellm:src-${litellm_src_hash}"' in deploy
    assert 'docker image inspect "$litellm_src_tag"' in deploy
    # The unchanged condition requires BOTH the src-tagged image AND a
    # running litellm container (the image can exist while the container is
    # missing after an incident).
    unchanged = deploy.index("litellm_gateway_changed=false")
    guard = deploy.index("ps -q litellm")
    assert guard < unchanged
    inspect_index = deploy.index('docker image inspect "$litellm_src_tag"')
    assert inspect_index < unchanged
    # The decision is made near the top, before the build section.
    build_section = deploy.index("==> Building application images sequentially")
    assert unchanged < build_section


def test_gateway_unchanged_path_skips_build_retags_and_names_no_migrators():
    deploy = _deploy_script()

    assert (
        'docker image tag "$litellm_src_tag" "legalapp-litellm:${APP_COMMIT}"' in deploy
    )
    assert (
        '[[ "$service" == "litellm" && "$litellm_gateway_changed" != true ]]' in deploy
    )
    # The force-recreate command expands a guarded migrator list, so an
    # unchanged release names NO gateway migrators on that line.
    recreate_line_end = deploy.index("\n", deploy.index("--force-recreate --no-deps"))
    recreate_line = deploy[
        deploy.index("--force-recreate --no-deps") : recreate_line_end
    ]
    assert "litellm-migrator" not in recreate_line
    assert "litellm-schema-migrator" not in recreate_line
    assert '${gateway_migrator_services[@]+"${gateway_migrator_services[@]}"}' in deploy
    # The migrator names only appear in the changed-guard assignment.
    migrator_assignment = deploy.index(
        "gateway_migrator_services=(litellm-migrator litellm-schema-migrator)"
    )
    changed_guard = deploy.rindex(
        'if [[ "$litellm_gateway_changed" == true ]]; then', 0, migrator_assignment
    )
    assert changed_guard < migrator_assignment
    # The changed gate is also what excludes litellm from the recreate set.
    recreate_filter = deploy.index(
        '[[ "$service" == "litellm" && "$litellm_gateway_changed" != true ]]'
    )
    recreate_command = deploy.index("--force-recreate --no-deps")
    assert recreate_filter < recreate_command


def test_gateway_changed_path_creates_src_and_rollback_tags_and_ledger_row():
    deploy = _deploy_script()

    ledger = 'litellm_gateway_ledger="$release_state_dir/litellm-gateway.tsv"'
    assert ledger in deploy
    assert "recorded_at\\tsrc_hash\\timage_id\\timage_tag" in deploy
    assert "clarity-legal/litellm:gateway-src-${litellm_src_hash}" in deploy
    assert (
        'docker image tag "legalapp-litellm:${APP_COMMIT}" "$litellm_src_tag"' in deploy
    )
    assert "docker image inspect -f '{{.Id}}'" in deploy
    assert "litellm-gateway.tsv" in deploy
    # Tagging and the ledger row happen only after the build loop succeeded.
    build_loop = deploy.index(
        "for service in backend scheduler migrator frontend office-addin nginx litellm; do"
    )
    assert build_loop < deploy.index(
        "clarity-legal/litellm:gateway-src-${litellm_src_hash}"
    )
    assert build_loop < deploy.index(ledger)


def test_gateway_decision_precedes_build_and_recreate_sections():
    deploy = _deploy_script()

    decision = deploy.index("litellm_gateway_changed=false")
    build_loop = deploy.index("==> Building application images sequentially")
    recreate_command = deploy.index("--force-recreate --no-deps")
    assert decision < build_loop < recreate_command
