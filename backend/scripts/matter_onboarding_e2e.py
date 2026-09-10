"""Disposable browser acceptance host. Never imported by production app startup.

Uses real routes, auth, PostgreSQL, local file storage and the intake worker.
Only outbound client delivery is replaced with a private local test mailbox.
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from types import SimpleNamespace

from scripts.seed_e2e import _require_disposable_database

_require_disposable_database()
if os.getenv("E2E_ONBOARDING_ACCEPTANCE") != "true":
    raise RuntimeError("Explicit onboarding acceptance mode is required")

from app.main import app  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.services import matter_intake  # noqa: E402

mailbox = Path(get_settings().UPLOAD_DIR) / "onboarding-mailbox.jsonl"


async def capture_email(*args, **kwargs):
    if kwargs["to"] != ["jane.onboarding@example.com"]:
        raise RuntimeError("Acceptance delivery only permits the synthetic client")
    mailbox.parent.mkdir(parents=True, exist_ok=True)
    with mailbox.open("a", encoding="utf-8") as output:
        output.write(
            json.dumps({"subject": kwargs["subject"], "body": kwargs["text_body"]})
            + "\n"
        )
    return SimpleNamespace(
        delivery_certainty="confirmed_sent", provider="acceptance-mailbox"
    )


async def deny_sms(*args, **kwargs):
    raise RuntimeError("Browser acceptance does not contact an SMS provider")


matter_intake.send_client_email = capture_email
matter_intake.send_sms = deny_sms
original_lifespan = app.router.lifespan_context


async def worker():
    while True:
        await matter_intake.tick()
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(application):
    async with original_lifespan(application):
        task = asyncio.create_task(worker())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app.router.lifespan_context = lifespan
