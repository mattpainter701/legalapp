from __future__ import annotations

import os
import tomllib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.fernet import Fernet

try:
    import tomli_w
except ImportError:
    tomli_w = None

_logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".clarity-agent"
CONFIG_FILE = CONFIG_DIR / "config.toml"
KEY_FILE = CONFIG_DIR / ".key"

_ENV_MAP = {
    "saas_url": "CLARITY_SAAS_URL",
    "api_key": "CLARITY_API_KEY",
    "agent_id": "CLARITY_AGENT_ID",
    "smb_username": "CLARITY_SMB_USERNAME",
    "smb_password": "CLARITY_SMB_PASSWORD",
    "smb_domain": "CLARITY_SMB_DOMAIN",
    "ledger_path": "CLARITY_LEDGER_PATH",
    "log_level": "CLARITY_LOG_LEVEL",
    "scan_interval_minutes": "CLARITY_SCAN_INTERVAL",
    "task_poll_interval_seconds": "CLARITY_TASK_POLL_INTERVAL",
    "heartbeat_interval_seconds": "CLARITY_HEARTBEAT_INTERVAL",
}


def _load_or_create_key() -> bytes:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if KEY_FILE.exists():
        return KEY_FILE.read_bytes()
    key = Fernet.generate_key()
    KEY_FILE.write_bytes(key)
    return key


def _encrypt(plaintext: str) -> str:
    return Fernet(_load_or_create_key()).encrypt(plaintext.encode()).decode()


def _decrypt(ciphertext: str) -> str:
    return Fernet(_load_or_create_key()).decrypt(ciphertext.encode()).decode()


@dataclass
class AgentConfig:
    saas_url: str = "https://getlawhand.com"
    api_key: str = ""
    agent_id: str = ""
    smb_username: str = ""
    smb_password: str = ""
    smb_domain: str = ""
    ledger_path: str = str(CONFIG_DIR / "ledger.db")
    log_level: str = "INFO"
    scan_interval_minutes: int = 360
    task_poll_interval_seconds: int = 30
    heartbeat_interval_seconds: int = 300
    _encrypted_smb_password: str = field(default="", repr=False)

    @classmethod
    def load(cls) -> AgentConfig:
        cfg = cls()
        if CONFIG_FILE.exists():
            data = cls._read_config_file()
            if data:
                for k, v in data.get("agent", {}).items():
                    if k == "smb_password":
                        cfg._encrypted_smb_password = v
                        try:
                            cfg.smb_password = _decrypt(v)
                        except Exception:
                            cfg.smb_password = v
                    elif hasattr(cfg, k):
                        setattr(cfg, k, v)
        for field_name, env_var in _ENV_MAP.items():
            val = os.environ.get(env_var)
            if val is not None:
                if field_name in ("scan_interval_minutes", "task_poll_interval_seconds", "heartbeat_interval_seconds"):
                    val = int(val)
                setattr(cfg, field_name, val)
        return cfg

    @staticmethod
    def _read_config_file() -> dict | None:
        """Read config file, supporting both TOML and JSON fallback format."""
        try:
            with open(CONFIG_FILE, "rb") as f:
                return tomllib.load(f)
        except Exception:
            try:
                import json
                with open(CONFIG_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                return None

    def save_config(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        enc_pw = _encrypt(self.smb_password) if self.smb_password else ""
        data = {
            "agent": {
                "saas_url": self.saas_url,
                "api_key": self.api_key,
                "agent_id": self.agent_id,
                "smb_username": self.smb_username,
                "smb_password": enc_pw,
                "smb_domain": self.smb_domain,
                "ledger_path": self.ledger_path,
                "log_level": self.log_level,
                "scan_interval_minutes": self.scan_interval_minutes,
                "task_poll_interval_seconds": self.task_poll_interval_seconds,
                "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
            }
        }
        if tomli_w is None:
            _logger.warning(
                "tomli_w not installed — saving config as JSON fallback. "
                "Install tomli_w for proper TOML support: pip install tomli_w"
            )
            import json
            with open(CONFIG_FILE, "w") as f:
                json.dump(data, f, indent=2)
        else:
            with open(CONFIG_FILE, "wb") as f:
                tomli_w.dump(data, f)
