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


def test_litellm_config_disables_raw_message_callbacks_by_default():
    config = yaml.safe_load((ROOT / "litellm_config.yaml").read_text())
    settings = config["litellm_settings"]

    assert settings["turn_off_message_logging"] is True
    assert "success_callback" not in settings
    assert "failure_callback" not in settings


def test_standard_chat_route_uses_fast_primary_and_live_fallbacks():
    config = yaml.safe_load((ROOT / "litellm_config.yaml").read_text())
    models = {
        entry["model_name"]: entry["litellm_params"]["model"]
        for entry in config["model_list"]
    }

    assert models["clarity-standard"] == (
        "openrouter/google/gemma-4-31b-it:free"
    )
    assert models["clarity-standard-zen-nemotron"] == (
        "openai/nemotron-3-ultra-free"
    )
    assert models["clarity-standard-deepseek-flash-free"] == (
        "openai/deepseek-v4-flash-free"
    )

    assert "clarity-standard-openrouter-gemma" not in models
    assert "clarity-standard-openrouter-qwen" not in models
    assert "clarity-premium-openrouter-qwen" not in models
    assert "clarity-premium-zen-flash" not in models

    assert "fallbacks" not in config["router_settings"]
