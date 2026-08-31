"""Hostile invocation and bounded-process checks for Studio isolation."""

import asyncio
import hashlib
import threading
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.services.studio_worker_isolation import (
    StudioIsolatedInvocation,
    StudioIsolatedResult,
    StudioIsolationError,
    StudioIsolationProfile,
    StudioIsolationRegistry,
    StudioTrustedProcessorAdapter,
    run_isolated_process,
)

def _profile(tmp_path, **updates):
    launcher = tmp_path / "sandbox-launcher.bin"
    executable = tmp_path / "renderer.bin"
    launcher.write_bytes(b"verified sandbox")
    executable.write_bytes(b"renderer")
    values = {
        "profile_id": "studio-test-v1",
        "launcher": launcher.absolute(),
        "launcher_sha256": hashlib.sha256(launcher.read_bytes()).hexdigest(),
        "executable": executable.absolute(),
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "network_isolation_enforced": True,
        "resource_limits_enforced": True,
        "process_tree_enforced": True,
        "timeout_seconds": 1,
        "max_stdout_bytes": 32,
        "max_stderr_bytes": 16,
    }
    values.update(updates)
    return StudioIsolationProfile(**values)


def test_registry_fails_closed_without_all_enforcement(tmp_path):
    with pytest.raises(StudioIsolationError) as caught:
        StudioIsolationRegistry(
            [_profile(tmp_path, network_isolation_enforced=False)]
        )
    assert caught.value.code == "isolation_unavailable"


def test_launcher_is_re_attested_and_caller_cannot_choose_executable(tmp_path):
    profile = _profile(tmp_path)
    registry = StudioIsolationRegistry([profile])
    invocation = StudioIsolatedInvocation(
        profile_id=profile.profile_id,
        arguments=("; rm -rf /", "$(provider_secret)", "--literal"),
    )
    assert registry.resolve(invocation).executable == profile.executable
    Path(profile.launcher).write_bytes(b"replaced")
    with pytest.raises(StudioIsolationError, match="attestation"):
        registry.resolve(invocation)

    executable_profile = _profile(tmp_path)
    executable_registry = StudioIsolationRegistry([executable_profile])
    Path(executable_profile.executable).write_bytes(b"replaced renderer")
    with pytest.raises(StudioIsolationError, match="attestation"):
        executable_registry.resolve(
            StudioIsolatedInvocation(profile_id=executable_profile.profile_id)
        )


def test_registry_owns_immutable_environment_copy(tmp_path):
    supplied_environment = {"SAFE_SETTING": "fixed"}
    supplied_arguments = ["--fixed"]
    profile = _profile(
        tmp_path,
        environment=supplied_environment,
        fixed_arguments=supplied_arguments,
    )
    registry = StudioIsolationRegistry([profile])
    supplied_environment["PATH"] = "C:/provider-secret"
    supplied_arguments.append("$(signed_url)")
    resolved = registry.resolve(
        StudioIsolatedInvocation(profile_id=profile.profile_id)
    )
    assert dict(resolved.environment) == {"SAFE_SETTING": "fixed"}
    assert resolved.fixed_arguments == ("--fixed",)
    with pytest.raises(AttributeError, match="immutable"):
        registry._profiles = {}


@pytest.mark.asyncio
async def test_concrete_adapter_stages_inputs_and_owns_isolated_execution(tmp_path):
    profile = _profile(tmp_path)
    registry = StudioIsolationRegistry([profile])
    adapter = StudioTrustedProcessorAdapter(
        registry,
        profile.profile_id,
        workspace_root=tmp_path,
    )
    observed = {}

    async def isolated(_registry, invocation, *, workspace):
        working = Path(workspace)
        observed["workspace"] = working
        observed["arguments"] = invocation.arguments
        assert (working / "source.bin").read_bytes() == b"source"
        assert b"signed_url" in (working / "snapshot.json").read_bytes()
        assert (working / "options.json").is_file()
        return StudioIsolatedResult(stdout=b"rendered", stderr=b"")

    with patch(
        "app.services.studio_worker_isolation.run_isolated_process",
        isolated,
    ):
        output = await adapter.process(
            source=b"source",
            snapshot={"value": "$(signed_url)"},
            options={"flatten_pdf": False},
            input_binding=None,
        )
    assert "signed_url" not in " ".join(observed["arguments"])
    assert not observed["workspace"].exists()
    assert output.content == b"rendered"
    assert output.content_sha256 == hashlib.sha256(b"rendered").hexdigest()


@pytest.mark.asyncio
async def test_cancelled_staging_drains_writer_before_workspace_cleanup(tmp_path):
    profile = _profile(tmp_path)
    adapter = StudioTrustedProcessorAdapter(
        StudioIsolationRegistry([profile]),
        profile.profile_id,
        workspace_root=tmp_path,
    )
    started = threading.Event()
    release = threading.Event()
    observed = {}

    def blocking_write(workspace, **_inputs):
        observed["workspace"] = Path(workspace)
        started.set()
        release.wait(timeout=2)
        (Path(workspace) / "source.bin").write_bytes(b"sensitive")
        return ("--source-file", "source.bin")

    with patch.object(
        StudioTrustedProcessorAdapter,
        "_write_inputs",
        staticmethod(blocking_write),
    ):
        task = asyncio.create_task(
            adapter.process(
                source=b"sensitive",
                snapshot={},
                options={},
                input_binding=None,
            )
        )
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not observed["workspace"].exists()
    assert not list(tmp_path.glob("studio-render-*"))


class _FakeProcess:
    def __init__(self, *, stdout=b"ok", stderr=b"", returncode=0, wait_forever=False):
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        self.stdout.feed_eof()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.returncode = None if wait_forever else returncode
        self._result = returncode
        self._wait_forever = wait_forever
        self.killed = False

    async def wait(self):
        if self._wait_forever and not self.killed:
            await asyncio.Event().wait()
        self.returncode = self._result
        return self.returncode

    def kill(self):
        self.killed = True
        self._wait_forever = False


@pytest.mark.asyncio
async def test_no_shell_minimal_environment_and_literal_hostile_arguments(tmp_path):
    profile = _profile(tmp_path)
    registry = StudioIsolationRegistry([profile])
    process = _FakeProcess()
    create = AsyncMock(return_value=process)
    hostile = "$(signed_url); provider_id=C:/private"
    with patch("asyncio.create_subprocess_exec", create):
        result = await run_isolated_process(
            registry,
            StudioIsolatedInvocation(profile_id=profile.profile_id, arguments=(hostile,)),
            workspace=tmp_path,
        )
    assert result.stdout == b"ok"
    args = create.await_args.args
    kwargs = create.await_args.kwargs
    assert args[-1] == hostile
    assert "shell" not in kwargs
    assert "PATH" not in kwargs["env"]
    assert "provider" not in str(kwargs["env"]).lower()


@pytest.mark.asyncio
async def test_streaming_output_limit_kills_process(tmp_path):
    profile = _profile(tmp_path, max_stdout_bytes=4)
    registry = StudioIsolationRegistry([profile])
    process = _FakeProcess(stdout=b"12345", wait_forever=True)
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        with pytest.raises(StudioIsolationError) as caught:
            await run_isolated_process(
                registry,
                StudioIsolatedInvocation(profile_id=profile.profile_id),
                workspace=tmp_path,
            )
    assert caught.value.code == "processor_output_limit"
    assert process.killed is True


@pytest.mark.asyncio
async def test_timeout_kills_supervised_process_tree(tmp_path):
    profile = _profile(tmp_path, timeout_seconds=0.1)
    registry = StudioIsolationRegistry([profile])
    process = _FakeProcess(wait_forever=True)
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        with pytest.raises(StudioIsolationError) as caught:
            await run_isolated_process(
                registry,
                StudioIsolatedInvocation(profile_id=profile.profile_id),
                workspace=tmp_path,
            )
    assert caught.value.code == "processor_timeout"
    assert process.killed is True
