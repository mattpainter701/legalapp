from __future__ import annotations

import logging
import os
import stat
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.fernet import Fernet

try:
    import tomli_w
except ImportError:  # pragma: no cover - optional at runtime, present in builds
    tomli_w = None

_logger = logging.getLogger(__name__)

LEGACY_CONFIG_DIR = Path.home() / ".clarity-agent"


def _default_config_dir() -> Path:
    """Where the agent keeps its config, key and ledger.

    A service runs as LocalSystem (Windows) or a system user (Linux), which has
    no useful home directory, so the machine-wide location is the default and
    the old per-user directory is honoured when it already exists.
    """
    override = os.environ.get("CLARITY_CONFIG_DIR")
    if override:
        return Path(override)
    if LEGACY_CONFIG_DIR.exists():
        return LEGACY_CONFIG_DIR
    if sys.platform == "win32":
        base = os.environ.get("ProgramData") or os.environ.get("ALLUSERSPROFILE")
        if base:
            return Path(base) / "LawHand" / "Agent"
        return LEGACY_CONFIG_DIR
    system_dir = Path("/etc/lawhand-agent")
    if system_dir.exists():
        return system_dir
    return LEGACY_CONFIG_DIR


CONFIG_DIR = _default_config_dir()
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

_INT_FIELDS = (
    "scan_interval_minutes",
    "task_poll_interval_seconds",
    "heartbeat_interval_seconds",
)


def _restrict(path: Path) -> None:
    """Make a file readable only by its owner where the OS supports it."""
    try:
        if os.name != "nt":
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:  # pragma: no cover - depends on the filesystem
        _logger.warning("Could not restrict permissions on %s: %s", path, exc)


def _load_or_create_key() -> bytes:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if KEY_FILE.exists():
        return KEY_FILE.read_bytes()
    key = Fernet.generate_key()
    KEY_FILE.write_bytes(key)
    _restrict(KEY_FILE)
    return key


def _encrypt(plaintext: str) -> str:
    return Fernet(_load_or_create_key()).encrypt(plaintext.encode()).decode()


def _decrypt(ciphertext: str) -> str:
    return Fernet(_load_or_create_key()).decrypt(ciphertext.encode()).decode()


@dataclass
class AgentConfig:
    """Local agent settings.

    The SMB fields here are only a *fallback*: shares normally carry their own
    credential from the tenant's credential vault, delivered per poll and kept
    in memory. They exist for air-gapped setups and for pre-vault installs.
    """

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

    @property
    def config_path(self) -> Path:
        return CONFIG_FILE

    @property
    def config_dir(self) -> Path:
        return CONFIG_DIR

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
                if field_name in _INT_FIELDS:
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
        _restrict(CONFIG_FILE)
