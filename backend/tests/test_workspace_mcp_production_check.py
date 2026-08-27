from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_workspace_mcp_production_check_accepts_all_published_scopes() -> None:
    production_check = (ROOT / "scripts" / "production_check.sh").read_text(
        encoding="utf-8"
    )

    expected_workspace_scopes = {
        "communications:propose",
        "contacts:read",
        "documents:propose",
        "documents:read",
        "matters:read",
        "offline_access",
        "tasks:propose",
        "tasks:read",
        "templates:read",
    }

    for scope in expected_workspace_scopes:
        assert f'"{scope}",' in production_check
