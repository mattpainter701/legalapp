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
