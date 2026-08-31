import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_truststore_is_a_runtime_dependency():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert any(
        dependency.startswith("truststore>=")
        for dependency in pyproject["project"]["dependencies"]
    )


def test_truststore_is_explicitly_bundled_for_the_packaged_agent():
    spec = (ROOT / "packaging" / "lawhand-agent.spec").read_text(encoding="utf-8")

    assert '"truststore"' in spec
