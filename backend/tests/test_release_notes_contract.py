import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


release_docs = _load_script(
    "generate_release_notes", ROOT / "scripts" / "generate_release_notes.py"
)
merge_policy = _load_script(
    "verify_merge_policy", ROOT / "scripts" / "verify_merge_policy.py"
)


def test_committed_customer_notes_match_catalog_and_readme_links():
    catalog = release_docs.load_and_validate_catalog()

    assert release_docs.OUTPUT_PATH.read_text(encoding="utf-8") == (
        release_docs.render_release_notes(catalog)
    )
    assert release_docs.validate_repository_links() == []


def test_catalog_validation_rejects_non_plain_highlight(tmp_path):
    catalog = release_docs.load_and_validate_catalog()
    catalog["releases"][0]["highlights"][0]["description"] = (
        "Read https://example.com/internal-details"
    )
    catalog_path = tmp_path / "release_notes.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

    try:
        release_docs.load_and_validate_catalog(catalog_path)
    except ValueError as exc:
        assert "plain language" in str(exc)
    else:
        raise AssertionError("catalog validation accepted a link in a public bullet")


def test_release_note_attestation_requires_all_three_artifacts(tmp_path):
    event = {
        "pull_request": {
            "body": "\n".join(
                (
                    "- [x] Documentation updated",
                    "- [x] Customer release notes updated",
                    "- [x] Security and privacy impact reviewed",
                )
            )
        }
    }
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")

    errors = merge_policy.check_pr_template(
        str(event_path), {"backend/app/release_notes.json"}
    )

    assert len(errors) == 1
    assert "CHANGELOG.md" in errors[0]
    assert "RELEASE_NOTES.md" in errors[0]


def test_no_release_note_attestation_accepts_internal_change(tmp_path):
    event = {
        "pull_request": {
            "body": "\n".join(
                (
                    "- [x] No documentation impact",
                    "- [x] No customer-facing release note",
                    "- [x] Security and privacy impact reviewed",
                )
            )
        }
    }
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")

    assert (
        merge_policy.check_pr_template(str(event_path), {"backend/app/jobs.py"}) == []
    )


def _mcp_pr_event(tmp_path, *mcp_lines):
    event = {
        "pull_request": {
            "body": "\n".join(
                (
                    "- [x] Documentation updated",
                    "- [x] No customer-facing release note",
                    "- [x] Security and privacy impact reviewed",
                    *mcp_lines,
                )
            )
        }
    }
    event_path = tmp_path / "mcp-event.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    return event_path


def test_mcp_change_requires_documentation_handoff(tmp_path):
    event_path = _mcp_pr_event(tmp_path)

    errors = merge_policy.check_pr_template(
        str(event_path), {"backend/app/routers/mcp.py"}
    )

    assert any("exactly one MCP documentation option" in error for error in errors)
    assert any("name the MCP area" in error for error in errors)
    assert any("wiki handoff note" in error for error in errors)


def test_mcp_documentation_attestation_requires_canonical_doc_change(tmp_path):
    event_path = _mcp_pr_event(
        tmp_path,
        "- [x] MCP documentation updated",
        "- MCP area: workspace protocol",
        "- Wiki handoff note: Updated the workspace protocol contract.",
    )

    errors = merge_policy.check_pr_template(
        str(event_path), {"backend/app/routers/mcp.py"}
    )

    assert any("no canonical MCP documentation file changed" in error for error in errors)


def test_mcp_documentation_handoff_accepts_canonical_doc_update(tmp_path):
    event_path = _mcp_pr_event(
        tmp_path,
        "- [x] MCP documentation updated",
        "- MCP area: workspace protocol",
        "- Wiki handoff note: See docs/mcp/README.md and the adapter update.",
    )

    assert (
        merge_policy.check_pr_template(
            str(event_path),
            {"backend/app/routers/mcp.py", "docs/mcp/README.md"},
        )
        == []
    )


def test_mcp_documentation_not_needed_requires_explanation(tmp_path):
    event_path = _mcp_pr_event(
        tmp_path,
        "- [x] MCP documentation not needed",
        "- MCP area: research tests",
        "- Wiki handoff note: none",
    )

    errors = merge_policy.check_pr_template(
        str(event_path), {"mcp-server/tests/test_contracts.py"}
    )

    assert any("wiki handoff note" in error for error in errors)


def test_mcp_documentation_not_needed_accepts_reasoned_handoff(tmp_path):
    event_path = _mcp_pr_event(
        tmp_path,
        "- [x] MCP documentation not needed",
        "- MCP area: research tests",
        "- Wiki handoff note: Test-only refactor with no contract or operations change.",
    )

    assert (
        merge_policy.check_pr_template(
            str(event_path), {"mcp-server/tests/test_contracts.py"}
        )
        == []
    )


def test_mcp_documentation_index_is_an_mcp_surface():
    assert merge_policy.is_mcp_surface_file("docs/mcp/README.md")
    assert merge_policy.is_mcp_documentation_file("docs/mcp/README.md")


def test_mcp_canonical_and_reference_doc_boundaries():
    assert merge_policy.is_mcp_documentation_file("docs/ARCHITECTURE.md")
    assert merge_policy.is_mcp_documentation_file(
        "docs/credential_security_operations.md"
    )
    assert not merge_policy.is_mcp_documentation_file("docs/legal_rag.md")
    assert merge_policy.is_mcp_surface_file("docs/legal_rag.md")


def test_mcp_boundary_and_migration_paths_are_surfaces():
    for path in (
        "backend/app/main.py",
        "nginx/nginx.conf",
        "backend/app/middleware/tenant.py",
        "backend/app/services/platform_auth.py",
        "backend/app/services/mcp_transport_security.py",
        "backend/app/services/workspace_mcp_oauth.py",
        "backend/app/models/generated_artifact.py",
        "backend/app/routers/chat_artifacts.py",
        "backend/migrations/versions/2026_add_mcp_delivery.py",
        "backend/migrations/versions/2026_add_tenant_rls_grant_task.py",
    ):
        assert merge_policy.is_mcp_surface_file(path), path


def test_mcp_boundary_tokens_do_not_match_unrelated_paths():
    assert not merge_policy.is_mcp_surface_file("production_tasks.md")
    assert not merge_policy.is_mcp_surface_file(
        "frontend/src/components/review-panel.md"
    )


def test_mcp_checkbox_does_not_accept_trailing_commentary(tmp_path):
    event_path = _mcp_pr_event(
        tmp_path,
        "- [x] MCP documentation updated (see discussion)",
        "- MCP area: workspace protocol",
        "- Wiki handoff note: Updated the workspace protocol contract.",
    )

    errors = merge_policy.check_pr_template(
        str(event_path), {"backend/app/routers/mcp.py", "docs/mcp/README.md"}
    )

    assert any("exactly one MCP documentation option" in error for error in errors)
