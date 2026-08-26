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

    assert models["clarity-standard"] == ("openai/nemotron-3-ultra-free")
    assert models["clarity-standard-zen-nemotron"] == ("openai/nemotron-3-ultra-free")
    assert models["clarity-standard-deepseek-flash-free"] == (
        "openai/deepseek-v4-flash-free"
    )

    assert "clarity-standard-openrouter-gemma" not in models
    assert "clarity-standard-openrouter-qwen" not in models
    assert "clarity-premium-openrouter-qwen" not in models
    assert "clarity-premium-zen-flash" not in models

    assert config["router_settings"]["fallbacks"] == [
        {"clarity-standard": ["clarity-standard-deepseek-flash-free"]},
        {"clarity-premium": ["clarity-standard"]},
        {"clarity-background": ["clarity-background-zen"]},
    ]


def test_background_route_is_luna_responses_only_and_never_falls_to_premium():
    config = yaml.safe_load((ROOT / "litellm_config.yaml").read_text())
    entries = {entry["model_name"]: entry for entry in config["model_list"]}

    assert entries["clarity-background"]["litellm_params"] == {
        "model": "openai/gpt-5.6-luna",
        "api_base": "https://opencode.ai/zen/go/v1",
        "api_key": "os.environ/OPENCODE_GO_API_KEY",
        "timeout": 30,
    }
    assert entries["clarity-background-zen"]["litellm_params"]["api_base"] == (
        "https://opencode.ai/zen/v1"
    )
    assert config["router_settings"]["routing_strategy"] == "simple-shuffle"
    background_fallbacks = next(
        item["clarity-background"]
        for item in config["router_settings"]["fallbacks"]
        if "clarity-background" in item
    )
    assert background_fallbacks == ["clarity-background-zen"]
    assert "clarity-premium" not in background_fallbacks
