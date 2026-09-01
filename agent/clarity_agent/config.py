from __future__ import annotations

import logging
import os
import subprocess
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
    "local_index_path": "CLARITY_LOCAL_INDEX_PATH",
    "local_index_enabled": "CLARITY_LOCAL_INDEX_ENABLED",
    "local_index_max_file_mb": "CLARITY_LOCAL_INDEX_MAX_FILE_MB",
    "local_index_workers": "CLARITY_LOCAL_INDEX_WORKERS",
    "search_node_enabled": "LAWHAND_SEARCH_NODE_ENABLED",
    "search_control_path": "LAWHAND_SEARCH_CONTROL_PATH",
    "opensearch_url": "LAWHAND_OPENSEARCH_URL",
    "opensearch_index_prefix": "LAWHAND_OPENSEARCH_INDEX_PREFIX",
    "opensearch_ca_path": "LAWHAND_OPENSEARCH_CA_PATH",
    "opensearch_username": "LAWHAND_OPENSEARCH_USERNAME",
    "opensearch_password": "LAWHAND_OPENSEARCH_PASSWORD",
    "search_gateway_host": "LAWHAND_SEARCH_GATEWAY_HOST",
    "search_gateway_port": "LAWHAND_SEARCH_GATEWAY_PORT",
    "search_gateway_token": "LAWHAND_SEARCH_GATEWAY_TOKEN",
    "search_max_results": "LAWHAND_SEARCH_MAX_RESULTS",
    "search_max_bulk_documents": "LAWHAND_SEARCH_MAX_BULK_DOCUMENTS",
    "search_max_bulk_mb": "LAWHAND_SEARCH_MAX_BULK_MB",
}

_INT_FIELDS = (
    "scan_interval_minutes",
    "task_poll_interval_seconds",
    "heartbeat_interval_seconds",
    "local_index_max_file_mb",
    "local_index_workers",
    "search_gateway_port",
    "search_max_results",
    "search_max_bulk_documents",
    "search_max_bulk_mb",
)

_BOOL_FIELDS = ("local_index_enabled", "search_node_enabled")


def _restrict(path: Path, *, required: bool = False) -> None:
    """Restrict a path, optionally failing when protection cannot be applied."""
    try:
        if os.name == "nt":
            # chmod is largely ignored by Windows.  Do not leave the API key
            # readable through the inherited ProgramData\Everyone ACL. The
            # data directory receives explicit SYSTEM/Administrators/service
            # account ACEs from the MSI. Directories have inheritance removed;
            # files reset to inherit that protected directory ACL. This keeps
            # the service-account ACE instead of stripping it from each file.
            # ``icacls`` is built into Windows and list arguments avoid shell
            # interpolation of paths.
            if path.is_dir():
                identity_result = subprocess.run(
                    ["whoami"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                identity = identity_result.stdout.strip()
                if identity_result.returncode != 0 or not identity:
                    message = (
                        "could not resolve the current Windows identity; "
                        "ACL inheritance was not changed"
                    )
                    if required:
                        raise PermissionError(f"{message}: {path}")
                    _logger.warning("%s on %s", message, path)
                    return
                # Preserve any explicit MSI service-account ACE while adding
                # language-neutral SYSTEM/Administrators SIDs and the current
                # identity. This also makes a source-created per-user config
                # directory usable after inherited broad ACLs are removed.
                command = [
                    "icacls",
                    str(path),
                    "/inheritance:r",
                    "/grant:r",
                    "*S-1-5-18:(OI)(CI)F",
                    "*S-1-5-32-544:(OI)(CI)F",
                    f"{identity}:(OI)(CI)F",
                ]
            else:
                # PermissionEx entries on the MSI data folder are inheritable;
                # resetting a child picks up SYSTEM, Administrators, and a
                # custom SERVICE_ACCOUNT without guessing that account here.
                command = ["icacls", str(path), "/reset"]
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                detail = result.stderr.strip() or f"icacls exited {result.returncode}"
                if required:
                    raise PermissionError(
                        f"could not restrict permissions on {path}: {detail}"
                    )
                _logger.warning(
                    "Could not restrict permissions on %s: %s", path, detail
                )
        else:
            # Directories need execute permission for traversal; applying a
            # file's 0600 mode to CONFIG_DIR would make the service unable to
            # open its own key/config on Linux.
            path.chmod(
                (stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
                if path.is_dir()
                else (stat.S_IRUSR | stat.S_IWUSR)
            )
    except PermissionError:
        if required:
            raise
        _logger.warning("Could not restrict permissions on %s", path)
    except OSError as exc:  # pragma: no cover - depends on the filesystem
        if required:
            raise PermissionError(
                f"could not restrict permissions on {path}: {exc}"
            ) from exc
        _logger.warning("Could not restrict permissions on %s: %s", path, exc)


def _load_or_create_key() -> bytes:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _restrict(CONFIG_DIR)
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
    local_index_path: str = ""
    # Explicit opt-in: an upgrade must never start a multi-terabyte crawl or
    # create a new derived-content store without an operator's decision.
    local_index_enabled: bool = False
    local_index_max_file_mb: int = 25
    # One worker is deliberately conservative for an HDD-backed SMB source.
    # Operators may raise this only after measuring queue and disk contention.
    local_index_workers: int = 1
    # Production Search Node. Kept independently default-off so an agent
    # upgrade never starts OpenSearch or creates a derived corpus implicitly.
    search_node_enabled: bool = False
    search_control_path: str = ""
    opensearch_url: str = "http://127.0.0.1:9200"
    opensearch_index_prefix: str = "lawhand-firm-memory"
    opensearch_ca_path: str = ""
    opensearch_username: str = ""
    opensearch_password: str = ""
    search_gateway_host: str = "127.0.0.1"
    search_gateway_port: int = 8765
    search_gateway_token: str = ""
    search_max_results: int = 100
    search_max_bulk_documents: int = 500
    search_max_bulk_mb: int = 8
    _encrypted_smb_password: str = field(default="", repr=False)
    _encrypted_opensearch_password: str = field(default="", repr=False)
    _encrypted_search_gateway_token: str = field(default="", repr=False)

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
                    if k in {
                        "smb_password",
                        "opensearch_password",
                        "search_gateway_token",
                    }:
                        setattr(cfg, f"_encrypted_{k}", v)
                        try:
                            setattr(cfg, k, _decrypt(v))
                        except Exception:
                            # Preserve the legacy SMB fallback for old plaintext
                            # configs. New Search Node secrets fail closed when
                            # the machine-local key cannot decrypt them.
                            setattr(cfg, k, v if k == "smb_password" else "")
                    elif hasattr(cfg, k):
                        setattr(cfg, k, v)
        for field_name, env_var in _ENV_MAP.items():
            val = os.environ.get(env_var)
            if val is not None:
                if field_name in _INT_FIELDS:
                    val = int(val)
                elif field_name in _BOOL_FIELDS:
                    val = val.strip().lower() not in {"0", "false", "no", "off"}
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
        _restrict(CONFIG_DIR)
        enc_pw = _encrypt(self.smb_password) if self.smb_password else ""
        enc_opensearch_pw = (
            _encrypt(self.opensearch_password) if self.opensearch_password else ""
        )
        enc_gateway_token = (
            _encrypt(self.search_gateway_token) if self.search_gateway_token else ""
        )
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
                "local_index_path": self.local_index_path,
                "local_index_enabled": self.local_index_enabled,
                "local_index_max_file_mb": self.local_index_max_file_mb,
                "local_index_workers": self.local_index_workers,
                "search_node_enabled": self.search_node_enabled,
                "search_control_path": self.search_control_path,
                "opensearch_url": self.opensearch_url,
                "opensearch_index_prefix": self.opensearch_index_prefix,
                "opensearch_ca_path": self.opensearch_ca_path,
                "opensearch_username": self.opensearch_username,
                "opensearch_password": enc_opensearch_pw,
                "search_gateway_host": self.search_gateway_host,
                "search_gateway_port": self.search_gateway_port,
                "search_gateway_token": enc_gateway_token,
                "search_max_results": self.search_max_results,
                "search_max_bulk_documents": self.search_max_bulk_documents,
                "search_max_bulk_mb": self.search_max_bulk_mb,
            }
        }
        temp_path = CONFIG_FILE.with_suffix(".tmp")
        if tomli_w is None:
            _logger.warning(
                "tomli_w not installed — saving config as JSON fallback. "
                "Install tomli_w for proper TOML support: pip install tomli_w"
            )
            import json

            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
        else:
            # Write-and-replace prevents a power loss during pairing from
            # leaving a truncated config which makes the service fail at boot.
            with open(temp_path, "wb") as f:
                tomli_w.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
        os.replace(temp_path, CONFIG_FILE)
        _restrict(CONFIG_FILE)
