from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import re
import signal
import time
from datetime import datetime, timezone

from clarity_agent import __version__
from clarity_agent.api_client import SaaSClient
from clarity_agent.config import AgentConfig
from clarity_agent.db import FileLedger
from clarity_agent.heartbeat import HeartbeatService, host_info
from clarity_agent.schedule import due_for_scan
from clarity_agent.smb_auth import ShareCredential
from clarity_agent.smb_reader import SmbReader
from clarity_agent.smb_scanner import SmbScanner
from clarity_agent.task_worker import TaskWorker
from clarity_agent import updater
from clarity_agent.utils import parse_smb_path, setup_logging

logger = logging.getLogger("clarity_agent")


def _safe_request_error(exc: Exception) -> str:
    """Return bounded API validation detail without echoing request secrets."""
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        message = f"HTTP {exc.response.status_code}"
        try:
            detail = exc.response.json().get("detail")
        except (ValueError, TypeError):
            detail = None
        if isinstance(detail, str):
            detail_text = _safe_validation_message(detail)
        elif isinstance(detail, list):
            entries = []
            for item in detail[:5]:
                if not isinstance(item, dict):
                    continue
                loc = item.get("loc")
                msg = item.get("msg")
                field = next(
                    (
                        part
                        for part in reversed(loc or [])
                        if isinstance(part, str)
                        and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", part)
                    ),
                    "request",
                )
                safe_msg = _safe_validation_message(msg)
                if safe_msg:
                    entries.append(f"{field}: {safe_msg}")
            detail_text = "; ".join(entries)[:300]
        else:
            detail_text = ""
        if detail_text:
            message += f": {detail_text}"
        return message
    if isinstance(exc, httpx.RequestError):
        return "network request failed"
    # Unknown exceptions may interpolate a local path, identity, or library
    # object into their string form. The exception type is sufficient for the
    # portal status without copying arbitrary third-party detail.
    return type(exc).__name__


def _safe_validation_message(value: object) -> str:
    """Keep only harmless, bounded validation prose from a server response."""
    if not isinstance(value, str) or not value or len(value) > 200:
        return ""
    value = " ".join(value.split())
    # Do not copy request values (paths, URLs, credentials, or arbitrary input)
    # into a local log or scan-status field.
    if any(
        token in value for token in ("\\", "/", "://", "@", "input_value", "input=")
    ):
        return ""
    return value


# How often the daemon wakes to see which shares are due. Shares carry their
# own cron schedule; this is only the resolution at which those are honoured.
SCAN_TICK_SECONDS = 60
# How long a fetched share list (with its credentials) is reused before the
# agent asks the SaaS again.
SHARE_CACHE_TTL_SECONDS = 300
# Keep individual sync requests bounded so a large first scan does not exceed
# reverse-proxy/body limits or hold one HTTP request open for minutes.
SYNC_BATCH_SIZE = 100


def normalize_share(share: dict) -> dict:
    """Fill in ``server``/``share``/``root_path`` from the UNC path.

    The SaaS sends the parsed parts, but a share configured before this agent
    version — or one edited by hand — may only carry ``share_path``.
    """
    normalized = dict(share)
    normalized.setdefault("share_id", normalized.get("id", ""))
    share_path = normalized.get("share_path") or ""
    if not normalized.get("server") or not normalized.get("share"):
        if share_path:
            server, share_name, root = parse_smb_path(share_path)
            normalized["server"] = normalized.get("server") or server
            normalized["share"] = normalized.get("share") or share_name
            normalized.setdefault("root_path", root)
    if not share_path and normalized.get("server") and normalized.get("share"):
        normalized["share_path"] = f"\\\\{normalized['server']}\\{normalized['share']}"
    return normalized


async def _scan_share(
    share: dict,
    ledger: FileLedger,
    client: SaaSClient,
    smb_scanner: SmbScanner,
) -> dict:
    """Scan one share, report the outcome to the SaaS, and return it.

    The outcome is returned rather than only logged because an admin-triggered
    "Scan now" has to report the same result the share row shows; treating a
    non-raising failure as success made the console say "Scan finished" for a
    scan recorded as failed.
    """
    share = normalize_share(share)
    share_id = (
        share.get("share_id") or f"{share.get('server', '')}/{share.get('share', '')}"
    )
    share["share_id"] = share_id
    started_at = datetime.now(timezone.utc)

    file_exts = share.get("file_extensions")
    credential = ShareCredential.from_share(share)
    logger.info(
        "Scanning %s as %s",
        share.get("share_path") or share_id,
        credential.describe,
    )

    try:
        result = await smb_scanner.scan_share(share, file_extensions=file_exts)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        logger.error("Scan of %s failed: %s", share_id, message)
        await _report_scan(
            client, share_id, "failed", error=message, started_at=started_at
        )
        raise

    new_and_changed = result.new_files + result.changed_files
    synced_count = 0
    sync_error: str | None = None

    if new_and_changed or result.deleted_files:
        sync_files = [
            {
                "path": f["path"],
                "filename": f["filename"],
                "ext": f["ext"],
                "mime_type": f["mime_type"],
                "snippet": f.get("snippet", ""),
                "size_bytes": f.get("size_bytes", 0),
                "modified_time": f["modified_time"],
                "content_hash": f["content_hash"],
            }
            for f in new_and_changed
        ]
        try:
            for offset in range(0, len(sync_files) or 1, SYNC_BATCH_SIZE):
                batch = sync_files[offset : offset + SYNC_BATCH_SIZE]
                # Deletions are sent once, alongside the first batch. If there
                # are no files, still send a deletion-only request.
                batch_deletions = result.deleted_files if offset == 0 else []
                response = await client.sync(batch, batch_deletions, share_id)
                if not isinstance(response, dict):
                    raise RuntimeError("Sync returned an invalid response")
                response_errors = response.get("errors") or []
                error_paths = {
                    str(item.get("path"))
                    for item in response_errors
                    if isinstance(item, dict) and item.get("path")
                }
                has_unscoped_error = any(
                    not isinstance(item, dict) or not item.get("path")
                    for item in response_errors
                )
                ledger_batch = new_and_changed[offset : offset + SYNC_BATCH_SIZE]
                accepted_batch = [
                    finfo
                    for finfo in ledger_batch
                    if not has_unscoped_error and str(finfo["path"]) not in error_paths
                ]
                for finfo in accepted_batch:
                    finfo["share_id"] = share_id
                await ledger.upsert_files(accepted_batch)
                accepted_deletions = [
                    path
                    for path in batch_deletions
                    if not has_unscoped_error and str(path) not in error_paths
                ]
                await ledger.mark_deleted_paths(accepted_deletions)
                synced_count += len(accepted_batch)
                if response_errors:
                    details = "; ".join(
                        str(item.get("error", item))
                        if isinstance(item, dict)
                        else str(item)
                        for item in response_errors[:3]
                    )
                    sync_error = (
                        f"Sync rejected {len(response_errors)} item(s): {details}"
                    )
            logger.info(
                "Synced %d new/changed, %d deleted",
                synced_count,
                len(result.deleted_files),
            )
        except Exception as exc:
            sync_error = f"Sync failed: {_safe_request_error(exc)}"
            logger.error("%s", sync_error)
    else:
        logger.info("No changes detected for share %s", share_id)

    for err in result.errors:
        logger.error("Scan error: %s", err)

    indexed = synced_count + len(result.unchanged_files)
    error = sync_error or (result.errors[0] if result.errors else None)
    status = (
        "failed" if (error and not indexed) else ("partial" if error else "success")
    )
    await _report_scan(
        client,
        share_id,
        status,
        file_count=indexed,
        error=error,
        started_at=started_at,
    )
    return {
        "share_id": share_id,
        "status": status,
        "file_count": indexed,
        "synced": synced_count,
        "error": error,
    }


async def _report_scan(
    client: SaaSClient,
    share_id: str,
    status: str,
    file_count: int | None = None,
    error: str | None = None,
    started_at: datetime | None = None,
) -> None:
    """Best-effort scan status report; never let it break the scan loop."""
    try:
        await client.report_scan_status(
            share_id,
            status,
            file_count=file_count,
            error=error,
            started_at=started_at.isoformat() if started_at else None,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as exc:
        logger.warning("Could not report scan status for %s: %s", share_id, exc)


class ShareCache:
    """Keeps the last share list so tasks and scans agree on credentials.

    The scan loop ticks every minute to honour per-share schedules, which is
    far more often than share configuration changes, so the list is re-fetched
    on a TTL rather than on every tick.
    """

    def __init__(self, client: SaaSClient, ttl_seconds: int = SHARE_CACHE_TTL_SECONDS):
        self.client = client
        self.ttl_seconds = ttl_seconds
        self._shares: list[dict] = []
        self._fetched_at: float | None = None

    async def refresh(self) -> list[dict]:
        shares = await self.client.get_shares()
        self._shares = [normalize_share(s) for s in shares]
        self._fetched_at = time.monotonic()
        return self._shares

    def _is_stale(self) -> bool:
        return (
            self._fetched_at is None
            or time.monotonic() - self._fetched_at >= self.ttl_seconds
        )

    async def get(self, refresh_if_stale: bool = True) -> list[dict]:
        if refresh_if_stale and self._is_stale():
            try:
                await self.refresh()
            except Exception as exc:
                logger.error("Could not refresh shares: %s", exc)
        return self._shares


async def run_daemon(
    config: AgentConfig, stop_event: asyncio.Event | None = None
) -> None:
    setup_logging(config.log_level)
    logger.info("LawHand Agent v%s starting", __version__)

    ledger = FileLedger(config.ledger_path)
    await ledger.init()

    client = SaaSClient(config)
    smb_scanner = SmbScanner(config, ledger)
    reader = SmbReader()
    shares = ShareCache(client)
    # Scan times are kept in memory: an agent that restarts re-indexes its
    # shares once, which is the same behaviour as a fresh install.
    last_scans: dict[str, datetime] = {}

    async def scan_one(share: dict) -> dict:
        return await _scan_share(share, ledger, client, smb_scanner)

    async def update_agent(target_version: str, manifest_id: str) -> dict:
        info = await updater.check_async()
        if target_version != info.version:
            raise updater.UpdateError(
                "Requested update version is not the current official release"
            )
        if manifest_id != f"agent-v{info.version}":
            raise updater.UpdateError(
                "Requested update manifest identity does not match"
            )
        return await updater.apply_async(info)

    task_worker = TaskWorker(
        config,
        client,
        reader,
        share_provider=shares.get,
        scan_callback=scan_one,
        share_refresher=shares.refresh,
        update_callback=update_agent,
    )
    heartbeat = HeartbeatService(config, client)

    stop_event = stop_event or asyncio.Event()

    def _signal_handler(*_):
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except (NotImplementedError, ValueError):
            # Windows services and non-main threads have no signal handlers.
            pass

    async def scan_loop():
        # Ticking every minute is what lets a share keep its own schedule; the
        # tick itself only compares timestamps unless a share is actually due.
        while not stop_event.is_set():
            try:
                for share in await shares.get():
                    if stop_event.is_set():
                        break
                    if not share.get("is_enabled", True):
                        continue
                    share_id = share.get("share_id", "")
                    now = datetime.now(timezone.utc)
                    if not due_for_scan(
                        share,
                        last_scans.get(share_id),
                        now,
                        fallback_minutes=config.scan_interval_minutes,
                    ):
                        continue
                    last_scans[share_id] = now
                    await scan_one(share)
            except Exception as exc:
                logger.error("Scan cycle failed: %s", exc)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=SCAN_TICK_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def task_loop():
        while not stop_event.is_set():
            poll_result = -1
            try:
                poll_result = await task_worker.poll_and_execute()
            except Exception as exc:
                logger.error("Task poll failed: %s", exc)
            # Successful calls long-poll on the server, so reconnect
            # immediately and keep an outbound near-real-time channel. Only a
            # transport failure uses the configured retry backoff.
            if poll_result >= 0:
                continue
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=config.task_poll_interval_seconds
                )
            except asyncio.TimeoutError:
                pass

    async def heartbeat_loop():
        while not stop_event.is_set():
            try:
                await heartbeat.send()
            except Exception as exc:
                logger.error("Heartbeat failed: %s", exc)
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=config.heartbeat_interval_seconds
                )
            except asyncio.TimeoutError:
                pass

    logger.info("Entering main loop")
    await asyncio.gather(
        scan_loop(), task_loop(), heartbeat_loop(), return_exceptions=True
    )

    await ledger.close()
    await client.close()
    logger.info("Agent shut down")


async def _register_with_saas(
    config: AgentConfig,
    pairing_code: str,
    agent_info: dict,
) -> dict:
    """Register and close HTTP resources on the same event loop."""
    client = SaaSClient(config)
    try:
        return await client.register(
            pairing_code=pairing_code,
            agent_info=agent_info,
        )
    finally:
        await client.close()


def cmd_register(args) -> None:
    config = AgentConfig(saas_url=args.url or "https://getlawhand.com")
    setup_logging(config.log_level)
    info = host_info()

    try:
        result = asyncio.run(
            _register_with_saas(
                config,
                args.code,
                {
                    "agent_name": args.name or info["hostname"],
                    "agent_version": info["agent_version"],
                    "hostname": info["hostname"],
                    "os_info": info["os_info"],
                },
            )
        )
    except Exception as exc:
        # Never print the request body or command line, which may contain the
        # one-time pairing code. Keep the operator-facing failure actionable.
        import httpx

        if isinstance(exc, httpx.HTTPStatusError):
            message = f"SaaS rejected registration (HTTP {exc.response.status_code})"
            try:
                detail = exc.response.json().get("detail")
            except (ValueError, AttributeError):
                detail = None
            if (
                isinstance(detail, str)
                and 0 < len(detail) <= 200
                and args.code not in detail
                and not any(character in detail for character in "\r\n")
            ):
                message = f"{message}: {detail}"
        elif isinstance(exc, httpx.RequestError):
            message = "Could not reach the SaaS registration endpoint"
        elif isinstance(exc, ValueError) and str(exc).startswith("CLARITY_SAAS_URL"):
            message = str(exc)
        else:
            message = "Registration failed; check the agent log for details"
            logging.getLogger(__name__).error(
                "Registration failed (%s)", type(exc).__name__
            )
        raise SystemExit(message) from None

    config.api_key = result["api_key"]
    config.agent_id = result["agent_id"]

    # Local SMB credentials are optional: shares normally carry their own
    # credential from the admin console, and Kerberos/machine-account setups
    # need none at all.
    if args.smb_username:
        config.smb_username = args.smb_username
        config.smb_domain = args.smb_domain or ""
        config.smb_password = args.smb_password or getpass.getpass(
            "SMB password (leave blank to use the service account): "
        )

    config.save_config()
    print(f"Agent registered. ID: {config.agent_id}")
    print(f"Config saved to {config.config_path}")
    print("Add shares and credentials in Administration → File Shares.")


def cmd_start(args) -> None:
    config = AgentConfig.load()
    if not config.api_key or not config.agent_id:
        raise SystemExit(
            "Agent is not registered. Run: lawhand-agent register --code <pairing code>"
        )
    asyncio.run(run_daemon(config))


def cmd_scan(args) -> None:
    config = AgentConfig.load()
    setup_logging(config.log_level)

    async def _run():
        ledger = FileLedger(config.ledger_path)
        await ledger.init()
        smb_scanner = SmbScanner(config, ledger)
        client = SaaSClient(config)
        try:
            if args.share_path:
                server, share, root = parse_smb_path(args.share_path)
                share_config = {
                    "server": server,
                    "share": share,
                    "root_path": root,
                    "share_path": args.share_path,
                    "share_id": args.share_id or f"{server}/{share}",
                }
                await _scan_share(share_config, ledger, client, smb_scanner)
            else:
                # No path given: scan everything the SaaS has assigned to us,
                # using the credentials it delivers.
                for share in await client.get_shares():
                    await _scan_share(share, ledger, client, smb_scanner)
        finally:
            await ledger.close()
            await client.close()

    asyncio.run(_run())


def cmd_status(args) -> None:
    config = AgentConfig.load()
    print(f"Agent ID:      {config.agent_id or '(not registered)'}")
    print(f"SaaS URL:      {config.saas_url}")
    # Never echo any part of the key: this output is pasted into support
    # tickets and captured in service logs.
    print(f"API Key:       {'configured' if config.api_key else '(not set)'}")
    print(f"Config file:   {config.config_path}")
    print(f"Ledger:        {config.ledger_path}")
    print(f"Scan interval: {config.scan_interval_minutes} min (fallback)")
    print(f"Task poll:     {config.task_poll_interval_seconds} sec")

    if not (config.api_key and config.agent_id):
        return

    async def _shares():
        client = SaaSClient(config)
        try:
            return await client.get_shares()
        finally:
            await client.close()

    try:
        shares = asyncio.run(_shares())
    except Exception as exc:
        print(f"Shares:        (could not reach SaaS: {exc})")
        return

    print(f"Shares:        {len(shares)}")
    for share in shares:
        credential = ShareCredential.from_share(share, config)
        print(f"  - {share.get('share_path', '?')} [{credential.describe}]")


def cmd_service(args) -> None:
    from clarity_agent import service

    service.dispatch(args.action)


def cmd_update(args) -> None:
    """Check or apply the verified latest official agent release."""

    async def _run():
        info = await updater.check_async()
        if args.check:
            current = updater._version_tuple(__version__)
            available = updater._version_tuple(info.version) > current
            print(
                f"Current: {__version__}\nLatest:  {info.version}\nUpdate:  {'available' if available else 'not needed'}"
            )
            return
        print(await updater.apply_async(info))

    try:
        asyncio.run(_run())
    except updater.UpdateError as exc:
        raise SystemExit(f"Update failed: {exc}") from exc


def main():
    parser = argparse.ArgumentParser(
        prog="lawhand-agent", description="LawHand File Share Relay Agent"
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )

    sub = parser.add_subparsers(dest="command", required=True)

    reg = sub.add_parser("register", help="Register this agent with the SaaS")
    reg.add_argument(
        "--code", required=True, help="Pairing code from Administration → File Shares"
    )
    reg.add_argument(
        "--name", default="", help="Agent display name (defaults to hostname)"
    )
    reg.add_argument("--url", default="https://getlawhand.com", help="SaaS API URL")
    reg.add_argument(
        "--smb-username", default="", help="Optional fallback SMB username"
    )
    reg.add_argument(
        "--smb-password",
        default="",
        help="Optional fallback SMB password (prompted if omitted)",
    )
    reg.add_argument("--smb-domain", default="", help="Optional fallback SMB domain")
    reg.set_defaults(func=cmd_register)

    start = sub.add_parser("start", help="Run the agent in the foreground")
    start.set_defaults(func=cmd_start)

    scan = sub.add_parser("scan", help="Scan now (all assigned shares, or one path)")
    scan.add_argument(
        "--share-path", default="", help='UNC path (e.g. "\\\\server\\share")'
    )
    scan.add_argument("--share-id", default="", help="Share ID override")
    scan.set_defaults(func=cmd_scan)

    status = sub.add_parser("status", help="Show agent status and assigned shares")
    status.set_defaults(func=cmd_status)

    svc = sub.add_parser("service", help="Manage the background service")
    svc.add_argument(
        "action",
        choices=["install", "start", "stop", "restart", "remove", "run", "status"],
        help="Service action (Windows service / systemd unit)",
    )
    svc.set_defaults(func=cmd_service)

    update = sub.add_parser("update", help="Check or apply the official latest update")
    mode = update.add_mutually_exclusive_group()
    mode.add_argument(
        "--check", dest="check", action="store_true", help="Check without applying"
    )
    mode.add_argument(
        "--apply",
        dest="check",
        action="store_false",
        help="Download, verify, and apply",
    )
    update.set_defaults(func=cmd_update, check=True)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
