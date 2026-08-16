from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


class JetsonDispatchError(RuntimeError):
    """A dispatch failure whose message is safe to write to shared logs."""


_URL_CREDENTIALS = re.compile(
    r"(?P<prefix>\b(?:postgres(?:ql)?|https?|ssh)://[^\s:/@]+:)[^\s@]+(?P<suffix>@)",
    re.IGNORECASE,
)
_DB_URL_ARGUMENT = re.compile(r"(?P<prefix>--db-url(?:=|\s+))(?P<value>\S+)", re.IGNORECASE)
_PASSWORD_ASSIGNMENT = re.compile(
    r"(?P<prefix>\b(?:password|passwd|pwd|sshpass)\s*[=:]\s*)(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)


def redact_secrets(value: object) -> str:
    """Return a log-safe representation of a command or exception message."""

    text = str(value)
    text = _DB_URL_ARGUMENT.sub(r"\g<prefix><redacted>", text)
    text = _URL_CREDENTIALS.sub(r"\g<prefix>***\g<suffix>", text)
    return _PASSWORD_ASSIGNMENT.sub(r"\g<prefix><redacted>", text)


@dataclass(frozen=True)
class JetsonTarget:
    env_index: int
    worker_id: int
    host: str
    user: str


def jetson_hosts_from_env(explicit_hosts: str = "") -> list[str]:
    if explicit_hosts.strip():
        return explicit_hosts.split()

    hosts: list[str] = []
    for index in range(10):
        value = (
            os.environ.get(f"JETSON_{index}_HOST")
            or os.environ.get(f"JETSON{index}_HOST")
            or os.environ.get(f"JETSON_{index}")
            or os.environ.get(f"JETSON{index}")
        )
        if value:
            hosts.append(value)
    return hosts


def jetson_user_from_env(index: int, default_user: str) -> str:
    return (
        os.environ.get(f"JETSON_{index}_USER")
        or os.environ.get(f"JETSON{index}_USER")
        or default_user
    )


def jetson_password_from_env(index: int) -> str | None:
    return os.environ.get(f"JETSON_{index}_PASSWORD") or os.environ.get(f"JETSON{index}_PASSWORD")


def jetson_target_specs_from_env(explicit_hosts: str, default_user: str) -> list[JetsonTarget]:
    if explicit_hosts.strip():
        hosts = explicit_hosts.split()
        return [
            JetsonTarget(
                env_index=index,
                worker_id=index,
                host=host,
                user=jetson_user_from_env(index, default_user),
            )
            for index, host in enumerate(hosts)
        ]

    targets: list[JetsonTarget] = []
    for env_index in range(10):
        value = (
            os.environ.get(f"JETSON_{env_index}_HOST")
            or os.environ.get(f"JETSON{env_index}_HOST")
            or os.environ.get(f"JETSON_{env_index}")
            or os.environ.get(f"JETSON{env_index}")
        )
        if value:
            targets.append(
                JetsonTarget(
                    env_index=env_index,
                    worker_id=len(targets),
                    host=value,
                    user=jetson_user_from_env(env_index, default_user),
                )
            )
    return targets


def build_worker_command(
    *,
    script_dir: str,
    db_url: str,
    worker_id: int,
    total_workers: int,
    batch_size: int,
    background: bool = True,
) -> str:
    prefix = "nohup " if background else "PYTHONUNBUFFERED=1 "
    suffix = " &" if background else ""
    return (
        f"mkdir -p ~/clarity-legal-logs && cd {shlex.quote(script_dir)} && {prefix}python3 jetson_embed_worker.py "
        f"--model mxbai --dim 1024 --worker-id {worker_id} --total-workers {total_workers} "
        f"--batch-size {batch_size} --db-url {shlex.quote(db_url)} --loop "
        f">> ~/clarity-legal-logs/courtlistener_worker_{worker_id}.log 2>&1{suffix}"
    )


def tunneled_db_url(db_url: str, remote_port: int) -> str:
    parts = urlsplit(db_url)
    if "@" in parts.netloc:
        auth, _hostport = parts.netloc.rsplit("@", 1)
        netloc = f"{auth}@127.0.0.1:{remote_port}"
    else:
        netloc = f"127.0.0.1:{remote_port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def db_tunnel_endpoint(db_url: str) -> tuple[str, int]:
    parts = urlsplit(db_url)
    if not parts.hostname:
        raise ValueError("db_url must include a hostname for reverse tunnel mode")
    return parts.hostname, parts.port or 5432


def run_ssh_command(
    target: JetsonTarget,
    command: str,
    *,
    reverse_tunnel: str | None = None,
    foreground: bool = False,
) -> subprocess.Popen | None:
    ssh_command = [
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
    ]
    if reverse_tunnel:
        ssh_command.extend(["-o", "ExitOnForwardFailure=yes", "-R", reverse_tunnel])
    ssh_command.append(f"{target.user}@{target.host}")
    ssh_command.append(command)
    password = jetson_password_from_env(target.env_index)
    env = os.environ.copy()
    if password:
        env["SSHPASS"] = password
        ssh_command = ["sshpass", "-e", *ssh_command]
    try:
        if foreground:
            return subprocess.Popen(ssh_command, env=env)
        subprocess.run(ssh_command, check=True, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        return_code = getattr(exc, "returncode", None)
        detail = f"exit_status={return_code}" if return_code is not None else f"error_type={type(exc).__name__}"
        raise JetsonDispatchError(
            f"Jetson SSH dispatch failed host={target.host} worker={target.worker_id} {detail}"
        ) from None
    return None


def dispatch_targets(
    targets: list[JetsonTarget],
    script_dir: str,
    db_url: str,
    batch_size: int,
    *,
    reverse_tunnel: bool = False,
    tunnel_remote_port_base: int = 15434,
) -> None:
    total = len(targets)
    if total == 0:
        raise ValueError("At least one Jetson host is required")
    processes: list[tuple[JetsonTarget, subprocess.Popen]] = []
    tunnel_host, tunnel_port = db_tunnel_endpoint(db_url) if reverse_tunnel else ("", 0)
    for target in targets:
        worker_db_url = (
            tunneled_db_url(db_url, tunnel_remote_port_base + target.worker_id)
            if reverse_tunnel
            else db_url
        )
        command = build_worker_command(
            script_dir=script_dir,
            db_url=worker_db_url,
            worker_id=target.worker_id,
            total_workers=total,
            batch_size=batch_size,
            background=not reverse_tunnel,
        )
        process = run_ssh_command(
            target,
            command,
            reverse_tunnel=(
                f"127.0.0.1:{tunnel_remote_port_base + target.worker_id}:{tunnel_host}:{tunnel_port}"
                if reverse_tunnel
                else None
            ),
            foreground=reverse_tunnel,
        )
        if process:
            processes.append((target, process))
    for target, process in processes:
        code = process.wait()
        if code != 0:
            raise JetsonDispatchError(
                f"Jetson worker session failed host={target.host} "
                f"worker={target.worker_id} exit_status={code}"
            )


def dispatch(hosts: list[str], user: str, script_dir: str, db_url: str, batch_size: int) -> None:
    targets = [
        JetsonTarget(
            env_index=worker_id,
            worker_id=worker_id,
            host=host,
            user=jetson_user_from_env(worker_id, user),
        )
        for worker_id, host in enumerate(hosts)
    ]
    dispatch_targets(targets, script_dir, db_url, batch_size)


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch CourtListener embedding workers on Jetsons")
    parser.add_argument("--hosts", default=os.environ.get("JETSON_HOSTS", ""))
    parser.add_argument("--user", default=os.environ.get("JETSON_USER", "jetson"))
    parser.add_argument("--script-dir", default=os.environ.get("JETSON_SCRIPT_DIR", "/home/jetson/legalapp/scripts"))
    parser.add_argument("--db-url", default=os.environ.get("VECTORDB_URL", ""))
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("BATCH_SIZE", "32")))
    parser.add_argument(
        "--reverse-tunnel",
        action="store_true",
        default=os.environ.get("JETSON_DB_REVERSE_TUNNEL", "").lower() in {"1", "true", "yes"},
    )
    parser.add_argument(
        "--tunnel-remote-port-base",
        type=int,
        default=int(os.environ.get("JETSON_DB_TUNNEL_REMOTE_PORT_BASE", "15434")),
    )
    args = parser.parse_args()
    if not args.db_url:
        raise SystemExit("VECTORDB_URL or --db-url is required")
    dispatch_targets(
        jetson_target_specs_from_env(args.hosts, args.user),
        args.script_dir,
        args.db_url,
        args.batch_size,
        reverse_tunnel=args.reverse_tunnel,
        tunnel_remote_port_base=args.tunnel_remote_port_base,
    )


if __name__ == "__main__":
    main()
