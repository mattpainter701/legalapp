from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from clarity_agent import __version__
from clarity_agent.api_client import SaaSClient
from clarity_agent.config import AgentConfig
from clarity_agent.db import FileLedger
from clarity_agent.heartbeat import HeartbeatService
from clarity_agent.smb_reader import SmbReader
from clarity_agent.smb_scanner import SmbScanner
from clarity_agent.task_worker import TaskWorker
from clarity_agent.utils import parse_smb_path, setup_logging

logger = logging.getLogger("clarity_agent")


async def _scan_share(share: dict, ledger: FileLedger, client: SaaSClient, smb_scanner: SmbScanner) -> None:
    share_id = share.get("share_id", f"{share.get('server', '')}/{share.get('share', '')}")
    share["share_id"] = share_id

    file_exts = share.get("file_extensions")
    logger.info("Scanning share \\\\%s\\%s", share.get("server", "?"), share.get("share", "?"))
    result = await smb_scanner.scan_share(share, file_extensions=file_exts)

    new_and_changed = result.new_files + result.changed_files
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
            logger.info("Synced %d new/changed, %d deleted", len(sync_files), len(result.deleted_files))
        except Exception as exc:
            logger.error("Sync failed: %s", exc)
    else:
        logger.info("No changes detected for share %s", share_id)

    if result.errors:
        for err in result.errors:
            logger.error("Scan error: %s", err)


async def run_daemon(config: AgentConfig) -> None:
    setup_logging(config.log_level)
    logger.info("WellPled Agent v%s starting", __version__)

    ledger = FileLedger(config.ledger_path)
    await ledger.init()

    client = SaaSClient(config)
    smb_scanner = SmbScanner(config, ledger)
    reader = SmbReader()
    task_worker = TaskWorker(config, client, reader)
    heartbeat = HeartbeatService(config, client)

    stop_event = asyncio.Event()

    def _signal_handler(*_):
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    async def scan_loop():
        while not stop_event.is_set():
            try:
                shares = await client.get_shares()
                for share in shares:
                    await _scan_share(share, ledger, client, smb_scanner)
            except Exception as exc:
                logger.error("Scan cycle failed: %s", exc)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=config.scan_interval_minutes * 60)
            except asyncio.TimeoutError:
                pass

    async def task_loop():
        while not stop_event.is_set():
            try:
                await task_worker.poll_and_execute()
            except Exception as exc:
                logger.error("Task poll failed: %s", exc)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=config.task_poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def heartbeat_loop():
        while not stop_event.is_set():
            try:
                await heartbeat.send()
            except Exception as exc:
                logger.error("Heartbeat failed: %s", exc)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=config.heartbeat_interval_seconds)
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

    result = asyncio.run(client.register(
        pairing_code=args.code,
        agent_info={"name": args.name},
    ))

    config.api_key = result["api_key"]
    config.agent_id = result["agent_id"]
    if hasattr(args, "smb_username") and args.smb_username:
        config.smb_username = args.smb_username
    if hasattr(args, "smb_password") and args.smb_password:
        config.smb_password = args.smb_password
    if hasattr(args, "smb_domain") and args.smb_domain:
        config.smb_domain = args.smb_domain

    config.save_config()
    print(f"Agent registered. ID: {config.agent_id}")
    print(f"Config saved to {AgentConfig.__module__}")
    asyncio.run(client.close())


def cmd_start(args) -> None:
    config = AgentConfig.load()
    asyncio.run(run_daemon(config))


def cmd_scan(args) -> None:
    config = AgentConfig.load()
    setup_logging(config.log_level)

    server, share, rel_path = parse_smb_path(args.share_path)
    share_config = {"server": server, "share": share, "share_id": args.share_id or f"{server}/{share}"}

    async def _run():
        ledger = FileLedger(config.ledger_path)
        await ledger.init()
        smb_scanner = SmbScanner(config, ledger)
        client = SaaSClient(config)
        await _scan_share(share_config, ledger, client, smb_scanner)
        await ledger.close()
        await client.close()

    asyncio.run(_run())


def cmd_status(args) -> None:
    config = AgentConfig.load()
    print(f"Agent ID:   {config.agent_id or '(not registered)'}")
    print(f"SaaS URL:    {config.saas_url}")
    print(f"API Key:     {config.api_key[:8]}..." if config.api_key else "API Key:     (not set)")
    print(f"Ledger:      {config.ledger_path}")
    print(f"Scan interval: {config.scan_interval_minutes} min")
    print(f"Task poll:   {config.task_poll_interval_seconds} sec")


def main():
    parser = argparse.ArgumentParser(prog="clarity-agent", description="WellPled SMB Relay Agent")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    reg = sub.add_parser("register", help="Register agent with SaaS")
    reg.add_argument("--code", required=True, help="Pairing code from SaaS")
    reg.add_argument("--name", default="Unnamed Agent", help="Agent display name")
    reg.add_argument("--url", default="https://legalapp.perevagagroup.com", help="SaaS API URL")
    reg.add_argument("--smb-username", default="", help="SMB username")
    reg.add_argument("--smb-password", default="", help="SMB password")
    reg.add_argument("--smb-domain", default="", help="SMB domain")
    reg.set_defaults(func=cmd_register)

    start = sub.add_parser("start", help="Start agent daemon")
    start.set_defaults(func=cmd_start)

    scan = sub.add_parser("scan", help="One-time scan of a share")
    scan.add_argument("--share-path", required=True, help='UNC path (e.g. "\\\\server\\share")')
    scan.add_argument("--share-id", default="", help="Share ID override")
    scan.set_defaults(func=cmd_scan)

    status = sub.add_parser("status", help="Show agent status")
    status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
