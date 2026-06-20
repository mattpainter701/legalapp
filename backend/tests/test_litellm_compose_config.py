from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def _litellm_env(compose_file: str) -> dict:
    data = yaml.safe_load((ROOT / compose_file).read_text())
    env = data["services"]["litellm"]["environment"]
    if isinstance(env, list):
        return dict(item.split("=", 1) for item in env)
    return env


def test_litellm_compose_enables_db_backed_model_updates():
    for compose_file in (
        "docker-compose.yml",
        "docker-compose.hypervisor.yml",
        "docker-compose.prod.yml",
    ):
        assert _litellm_env(compose_file)["STORE_MODEL_IN_DB"] == "True"
