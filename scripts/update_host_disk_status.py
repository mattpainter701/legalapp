#!/usr/bin/env python3
"""Write an aggregate, non-sensitive status for every production filesystem."""

from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
STATUS_FILENAME = "disk-status.json"


def _read_env_file(path: Path) -> tuple[dict[str, str], set[str]]:
    values: dict[str, str] = {}
    keys: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "a").isalnum() or not key[0].isalpha():
            raise ValueError("production environment contains an invalid key")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if "\n" in value or "\r" in value:
            raise ValueError(f"{key} must be single-line")
        values[key] = value
        keys.add(key)
    return values, keys


def _absolute_nonroot(value: str, label: str) -> Path:
    path = Path(value)
    if (
        not path.is_absolute()
        or path == Path(path.anchor)
        or "\n" in value
        or "\r" in value
    ):
        raise ValueError(f"{label} must be an absolute non-root single-line path")
    return path


def _reject_symlink_chain(path: Path, label: str) -> None:
    current = path
    while True:
        # is_symlink() remains true for dangling links; exists() does not.
        if current.is_symlink():
            raise ValueError(
                f"{label} may not be a symlink or contain symlinked parents"
            )
        if current.parent == current:
            return
        current = current.parent


def _compose_model(
    env_file: Path, compose_files: list[Path], env_keys: set[str]
) -> dict[str, Any]:
    command = ["docker", "compose", "--env-file", str(env_file)]
    for compose_file in compose_files:
        command.extend(("-f", str(compose_file)))
    command.extend(("config", "--format", "json"))
    clean_env = os.environ.copy()
    # Compose gives inherited variables precedence over --env-file. The host
    # timer must inspect the deployed file, never shell/process overrides.
    for key in env_keys:
        clean_env.pop(key, None)
    result = subprocess.run(
        command,
        env=clean_env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("resolved production Compose model is unavailable")
    model = json.loads(result.stdout)
    if not isinstance(model.get("services"), dict):
        raise ValueError("resolved production Compose model has no services")
    return model


def _bind_sources(model: dict[str, Any]) -> list[Path]:
    sources: list[Path] = []
    for service in model["services"].values():
        if not isinstance(service, dict):
            raise ValueError("resolved production Compose service is invalid")
        volumes = service.get("volumes") or []
        if not isinstance(volumes, list):
            raise ValueError("resolved production Compose volumes are invalid")
        for volume in volumes:
            if not isinstance(volume, dict):
                raise ValueError("resolved production Compose volume is invalid")
            if volume.get("type") != "bind":
                continue
            source = volume.get("source")
            if not isinstance(source, str):
                raise ValueError("resolved production bind source is invalid")
            sources.append(_absolute_nonroot(source, "resolved Compose bind source"))
    return sources


def _docker_root(env_keys: set[str]) -> Path:
    clean_env = os.environ.copy()
    for key in env_keys:
        clean_env.pop(key, None)
    result = subprocess.run(
        ["docker", "info", "--format", "{{.DockerRootDir}}"],
        env=clean_env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("DockerRootDir is unavailable")
    return _absolute_nonroot(result.stdout.strip(), "DockerRootDir")


def build_disk_status(
    paths: list[Path],
    threshold_percent: int,
    *,
    checked_at_epoch: int | None = None,
    stat_fn=None,
    statvfs_fn=None,
) -> dict[str, int | str]:
    if not 1 <= threshold_percent <= 100:
        raise ValueError("DISK_MAX_PERCENT must be an integer from 1 to 100")
    seen_devices: set[int] = set()
    max_used_percent = 0
    errors = 0
    stat_fn = os.stat if stat_fn is None else stat_fn
    statvfs_fn = getattr(os, "statvfs", None) if statvfs_fn is None else statvfs_fn
    for path in paths:
        try:
            if not path.exists():
                raise FileNotFoundError
            if statvfs_fn is None:
                raise OSError("statvfs is unavailable")
            identity = stat_fn(path).st_dev
            if identity in seen_devices:
                continue
            seen_devices.add(identity)
            usage = statvfs_fn(path)
            total = usage.f_blocks * usage.f_frsize
            available = usage.f_bavail * usage.f_frsize
            if total <= 0 or available < 0 or available > total:
                raise ValueError
            used_percent = math.ceil((total - available) * 100 / total)
            max_used_percent = max(max_used_percent, used_percent)
        except (OSError, ValueError):
            errors += 1

    if errors:
        status_value = "unavailable"
    elif max_used_percent >= threshold_percent:
        status_value = "full"
    else:
        status_value = "ok"
    return {
        "schema_version": SCHEMA_VERSION,
        "checked_at_epoch": int(
            time.time() if checked_at_epoch is None else checked_at_epoch
        ),
        "status": status_value,
        "filesystems_checked": len(seen_devices),
        "paths_checked": len(paths),
        "max_used_percent": max_used_percent,
        "threshold_percent": threshold_percent,
        "errors_count": errors,
    }


def _write_status(status_dir: Path, payload: dict[str, Any]) -> Path:
    _reject_symlink_chain(status_dir, "HOST_STATUS_HOST_DIR")
    status_dir.mkdir(parents=True, exist_ok=True)
    _reject_symlink_chain(status_dir, "HOST_STATUS_HOST_DIR")
    if not status_dir.is_dir():
        raise ValueError("HOST_STATUS_HOST_DIR must be a directory")
    os.chmod(status_dir, 0o755)
    output = status_dir / STATUS_FILENAME
    if output.is_symlink() or (output.exists() and not output.is_file()):
        raise ValueError("host disk status output must be a regular non-symlink file")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{STATUS_FILENAME}.", dir=status_dir
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=os.environ.get("ENV_FILE", ".env"))
    parser.add_argument(
        "--compose-files",
        default=os.environ.get(
            "COMPOSE_FILES",
            os.environ.get("COMPOSE_FILE", "docker-compose.hypervisor.yml"),
        ),
    )
    args = parser.parse_args()

    env_file_input = Path(args.env_file).expanduser()
    if env_file_input.is_symlink() or not env_file_input.is_file():
        raise SystemExit("production environment must be a regular non-symlink file")
    env_file = env_file_input.resolve()
    values, env_keys = _read_env_file(env_file)
    root = Path(__file__).resolve().parents[1]
    compose_tokens = shlex.split(args.compose_files, posix=True)
    if not compose_tokens:
        raise SystemExit("no production Compose files configured")
    compose_files = []
    for token in compose_tokens:
        candidate = Path(token) if Path(token).is_absolute() else root / token
        if candidate.is_symlink() or not candidate.is_file():
            raise SystemExit(
                "production Compose files must be regular non-symlink files"
            )
        compose_files.append(candidate.resolve())

    status_dir = _absolute_nonroot(
        values.get("HOST_STATUS_HOST_DIR", ""), "HOST_STATUS_HOST_DIR"
    )
    threshold_raw = values.get("DISK_MAX_PERCENT", "85")
    if not threshold_raw.isdigit():
        raise SystemExit("DISK_MAX_PERCENT must be an integer from 1 to 100")
    threshold = int(threshold_raw)
    base_paths = [
        _absolute_nonroot(values.get("UPLOADS_HOST_DIR", ""), "UPLOADS_HOST_DIR"),
        _absolute_nonroot(values.get("DISK_PATH", "/"), "DISK_PATH")
        if values.get("DISK_PATH", "/") != "/"
        else Path("/"),
        root / "backups",
        status_dir,
    ]

    try:
        model = _compose_model(env_file, compose_files, env_keys)
        paths = [*base_paths, _docker_root(env_keys), *_bind_sources(model)]
        payload = build_disk_status(paths, threshold)
    except Exception:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "checked_at_epoch": int(time.time()),
            "status": "unavailable",
            "filesystems_checked": 0,
            "paths_checked": len(base_paths),
            "max_used_percent": 0,
            "threshold_percent": threshold,
            "errors_count": 1,
        }
    output = _write_status(status_dir, payload)
    print(
        f"host disk status={payload['status']} filesystems={payload['filesystems_checked']} "
        f"max_used={payload['max_used_percent']}% artifact={output.name}"
    )
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
