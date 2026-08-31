"""Database-free worker facade safety checks."""

import asyncio
import hashlib
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.studio_object_storage import LocalStudioObjectStore
from app.services.studio_render_jobs import StudioRenderServiceError
from app.services.studio_render_worker import StudioRenderWorker, _isolation_failure
from app.services.studio_worker_isolation import (
    StudioIsolationProfile,
    StudioIsolationRegistry,
    StudioTrustedProcessorAdapter,
)


def _registry(tmp_path):
    launcher = Path(tmp_path) / "trusted-launcher.bin"
    executable = Path(tmp_path) / "renderer.bin"
    font_pack = Path(tmp_path) / "fonts.bundle"
    rasterizer = Path(tmp_path) / "rasterizer.bin"
    converter = Path(tmp_path) / "converter.bin"
    validator = Path(tmp_path) / "validator.bin"
    launcher.write_bytes(b"trusted launcher")
    executable.write_bytes(b"renderer")
    font_pack.write_bytes(b"fonts")
    rasterizer.write_bytes(b"rasterizer")
    converter.write_bytes(b"converter")
    validator.write_bytes(b"validator")
    profile = StudioIsolationProfile(
        profile_id="studio-test-v1",
        runtime_root=Path(tmp_path).absolute(),
        launcher=launcher.absolute(),
        launcher_sha256=hashlib.sha256(launcher.read_bytes()).hexdigest(),
        executable=executable.absolute(),
        executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        font_pack=font_pack.absolute(),
        font_pack_sha256=hashlib.sha256(font_pack.read_bytes()).hexdigest(),
        renderer_version="1.0.0",
        rasterizer=rasterizer.absolute(),
        rasterizer_version="1.0.0",
        rasterizer_sha256=hashlib.sha256(rasterizer.read_bytes()).hexdigest(),
        converter=converter.absolute(),
        converter_version="1.0.0",
        converter_sha256=hashlib.sha256(converter.read_bytes()).hexdigest(),
        validator=validator.absolute(),
        validator_version="1.0.0",
        validator_sha256=hashlib.sha256(validator.read_bytes()).hexdigest(),
    )
    return StudioIsolationRegistry([profile])


def _processor(tmp_path):
    return StudioTrustedProcessorAdapter(
        _registry(tmp_path),
        "studio-test-v1",
        workspace_root=tmp_path,
    )


class _BypassProcessor(StudioTrustedProcessorAdapter):
    async def process(self, **_kwargs):
        raise AssertionError("in-process execution must never be accepted")


def _worker(tmp_path, processor):
    return StudioRenderWorker(
        object(),
        object_store=LocalStudioObjectStore(tmp_path, max_object_bytes=1024),
        processors={"studio_test_render": processor},
        lease_seconds=30,
        heartbeat_seconds=5,
        processor_timeout_seconds=5,
        artifact_ttl_seconds=300,
    )


def test_worker_rejects_self_declared_or_missing_isolation(tmp_path):
    unsafe = SimpleNamespace(
        isolation_policy_id="self-declared",
        runtime_manifest_sha256="a" * 64,
    )
    with pytest.raises(ValueError, match="attested runtime"):
        _worker(tmp_path, unsafe)

    missing = SimpleNamespace(
        isolation_policy_id="",
        runtime_manifest_sha256="a" * 64,
    )
    with pytest.raises(ValueError, match="attested runtime"):
        _worker(tmp_path, missing)
    with pytest.raises(ValueError, match="attested runtime"):
        _worker(
            tmp_path,
            _BypassProcessor(
                _registry(tmp_path),
                "studio-test-v1",
                workspace_root=tmp_path,
            ),
        )
    trusted = _processor(tmp_path)
    worker = _worker(tmp_path, trusted)
    assert worker.processors
    with pytest.raises(TypeError):
        worker.processors["studio_test_render"] = unsafe
    with pytest.raises(AttributeError, match="immutable"):
        trusted.workspace_root = tmp_path / "provider-controlled"


@pytest.mark.asyncio
async def test_termination_is_bounded_and_calls_adapter_kill(tmp_path):
    processor = SimpleNamespace(terminate=AsyncMock())
    processing = asyncio.create_task(asyncio.Event().wait())
    await asyncio.sleep(0)
    await StudioRenderWorker._terminate_processor(processor, processing)
    processor.terminate.assert_awaited_once()
    assert processing.done()


class _SessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_args):
        return False


class _SessionFactory:
    def __call__(self):
        return _SessionContext()


@pytest.mark.asyncio
async def test_heartbeat_database_error_marks_lease_lost(tmp_path):
    processor = _processor(tmp_path)
    worker = _worker(tmp_path, processor)
    worker.session_factory = _SessionFactory()
    worker.heartbeat_seconds = 0.01
    stop = asyncio.Event()
    lost = asyncio.Event()
    lease = SimpleNamespace(tenant_id=uuid.uuid4())
    with patch(
        "app.services.studio_render_worker.set_tenant_context",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    ):
        await asyncio.wait_for(worker._heartbeat(lease, stop, lost), timeout=1)
    assert lost.is_set()


@pytest.mark.asyncio
async def test_preprocessor_phase_is_cancelled_when_lease_is_lost(tmp_path):
    worker = _worker(tmp_path, _processor(tmp_path))
    worker.processor_timeout_seconds = 5
    lost = asyncio.Event()
    cancelled = asyncio.Event()

    async def hanging_resolver():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    task = asyncio.create_task(
        worker._await_lease_bound_phase(hanging_resolver(), lost)
    )
    await asyncio.sleep(0)
    lost.set()
    with pytest.raises(StudioRenderServiceError) as caught:
        await asyncio.wait_for(task, timeout=1)
    assert caught.value.code == "cancelled"
    assert cancelled.is_set()


def test_isolation_failures_are_sanitized_by_cause():
    assert _isolation_failure("hostile_input") == ("hostile_input", False)
    assert _isolation_failure("processor_timeout") == ("processor_timeout", True)
    assert _isolation_failure("processor_output_limit") == (
        "output_too_large",
        False,
    )
    assert _isolation_failure("validation_failed") == (
        "validation_failed",
        False,
    )
    assert _isolation_failure("isolation_unavailable") == (
        "processor_unavailable",
        True,
    )
    assert _isolation_failure("processor_failed") == (
        "processor_unavailable",
        True,
    )
