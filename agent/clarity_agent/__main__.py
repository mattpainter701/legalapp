from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import signal
from datetime import datetime, timezone

from clarity_agent import __version__
from clarity_agent.api_client import SaaSClient
from clarity_agent.config import AgentConfig
from clarity_agent.db import FileLedger
from clarity_agent.heartbeat import HeartbeatService, host_info
from clarity_agent.smb_auth import ShareCredential
from clarity_agent.smb_reader import SmbReader
from clarity_agent.smb_scanner import SmbScanner
from clarity_agent.task_worker import TaskWorker
from clarity_agent.utils import parse_smb_path, setup_logging

logger = logging.getLogger("clarity_agent")


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
) -> None:
    """Scan one share and report the outcome back to the SaaS."""
    share = normalize_share(share)
    share_id = share.get("share_id") or f"{share.get('server', '')}/{share.get('share', '')}"
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
        await _report_scan(client, share_id, "failed", error=message, started_at=started_at)
        raise

    new_and_changed = result.new_files + result.changed_files
    synced_count = 0
    sync_error: str | None = None

    if new_and_changed or result.deleted_files:
        for finfo in new_and_changed:
            finfo["share_id"] = share_id
            await ledger.upsert_file(finfo)

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
            await client.sync(sync_files, result.deleted_files, share_id)
            synced_count = len(sync_files)
            logger.info(
                "Synced %d new/changed, %d deleted", synced_count, len(result.deleted_files)
            )
        except Exception as exc:
            sync_error = f"Sync failed: {exc}"
            logger.error("%s", sync_error)
    else:
        logger.info("No changes detected for share %s", share_id)

    for err in result.errors:
        logger.error("Scan error: %s", err)

    indexed = len(new_and_changed) + len(result.unchanged_files)
    error = sync_error or (result.errors[0] if result.errors else None)
    status = "failed" if (error and not indexed) else ("partial" if error else "success")
    await _report_scan(
        client,
        share_id,
        status,
        file_count=indexed,
        error=error,
        started_at=started_at,
    )


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
    """Keeps the last share list so tasks and scans agree on credentials."""

    def __init__(self, client: SaaSClient):
        self.client = client
        self._shares: list[dict] = []

    async def refresh(self) -> list[dict]:
        shares = await self.client.get_shares()
        self._shares = [normalize_share(s) for s in shares]
        return self._shares

    async def get(self) -> list[dict]:
        if not self._shares:
            try:
                await self.refresh()
            except Exception as exc:
                logger.error("Could not refresh shares: %s", exc)
        return self._shares


async def run_daemon(config: AgentConfig, stop_event: asyncio.Event | None = None) -> None:
    setup_logging(config.log_level)
    logger.info("LawHand Agent v%s starting", __version__)

    ledger = FileLedger(config.ledger_path)
    await ledger.init()

    client = SaaSClient(config)
    smb_scanner = SmbScanner(config, ledger)
    reader = SmbReader()
    shares = ShareCache(client)

    async def scan_one(share: dict) -> None:
        await _scan_share(share, ledger, client, smb_scanner)

    task_worker = TaskWorker(
        config,
        client,
        reader,
        share_provider=shares.get,
        scan_callback=scan_one,
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
        while not stop_event.is_set():
            try:
                for share in await shares.refresh():
                    if not share.get("is_enabled", True):
                        continue
                    await scan_one(share)
            except Exception as exc:
                logger.error("Scan cycle failed: %s", exc)
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=config.scan_interval_minutes * 60
                )
            except asyncio.TimeoutError:
                pass

    async def task_loop():
        while not stop_event.is_set():
            try:
                await task_worker.poll_and_execute()
            except Exception as exc:
                logger.error("Task poll failed: %s", exc)
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
    await asyncio.gather(scan_loop(), task_loop(), heartbeat_loop(), return_exceptions=True)

    await ledger.close()
    await client.close()
    logger.info("Agent shut down")


def cmd_register(args) -> None:
    config = AgentConfig(saas_url=args.url)
    client = SaaSClient(config)
    info = host_info()

    result = asyncio.run(
        client.register(
            pairing_code=args.code,
            agent_info={
                "agent_name": args.name or info["hostname"],
                "agent_version": info["agent_version"],
                "hostname": info["hostname"],
                "os_info": info["os_info"],
            },
        )
    )

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
    asyncio.run(client.close())
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
    print(f"API Key:       {config.api_key[:8] + '...' if config.api_key else '(not set)'}")
    print(f"Config file:   {config.config_path}")
    print(f"Ledger:        {config.ledger_path}")
    print(f"Scan interval: {config.scan_interval_minutes} min")
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


def main():
    parser = argparse.ArgumentParser(
        prog="lawhand-agent", description="LawHand File Share Relay Agent"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    reg = sub.add_parser("register", help="Register this agent with the SaaS")
    reg.add_argument("--code", required=True, help="Pairing code from Administration → File Shares")
    reg.add_argument("--name", default="", help="Agent display name (defaults to hostname)")
    reg.add_argument("--url", default="https://getlawhand.com", help="SaaS API URL")
    reg.add_argument("--smb-username", default="", help="Optional fallback SMB username")
    reg.add_argument("--smb-password", default="", help="Optional fallback SMB password (prompted if omitted)")
    reg.add_argument("--smb-domain", default="", help="Optional fallback SMB domain")
    reg.set_defaults(func=cmd_register)

    start = sub.add_parser("start", help="Run the agent in the foreground")
    start.set_defaults(func=cmd_start)

    scan = sub.add_parser("scan", help="Scan now (all assigned shares, or one path)")
    scan.add_argument("--share-path", default="", help='UNC path (e.g. "\\\\server\\share")')
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

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
