"""Fail-closed subprocess policy for hostile Studio document processing."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, TypeVar


_PROFILE_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,79}$")
_ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ThreadResult = TypeVar("_ThreadResult")


class StudioIsolationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StudioIsolationProfile:
    """Server-owned attestation for an approved sandbox launcher binary."""

    profile_id: str
    launcher: Path
    launcher_sha256: str
    executable: Path
    executable_sha256: str
    fixed_arguments: tuple[str, ...] = ()
    environment: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float = 60.0
    max_stdout_bytes: int = 1_048_576
    max_stderr_bytes: int = 65_536
    max_arguments: int = 32
    network_isolation_enforced: bool = False
    resource_limits_enforced: bool = False
    process_tree_enforced: bool = False


@dataclass(frozen=True)
class StudioIsolatedInvocation:
    profile_id: str
    arguments: tuple[str, ...] = ()


@dataclass(frozen=True)
class StudioIsolatedResult:
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True)
class StudioIsolatedProcessorOutput:
    content: bytes
    content_sha256: str
    media_type: str
    artifact_kind: str
    renderer_identity: str
    converter_identity: str
    validator_identity: str
    retention_class: str


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65_536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_regular_file(path: Path) -> bool:
    return path.is_absolute() and path.is_file() and not path.is_symlink()


async def _to_thread_to_completion(
    function: Callable[..., _ThreadResult], /, *args: Any, **kwargs: Any
) -> _ThreadResult:
    """Drain a worker thread before propagating any number of cancellations."""

    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    cancellation_requested = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancellation_requested = True
    if cancellation_requested:
        try:
            task.result()
        except BaseException:
            pass
        raise asyncio.CancelledError
    return task.result()


class StudioIsolationRegistry:
    """Immutable registry; callers select a profile but cannot choose a command."""

    __slots__ = ("_profiles", "_sealed")

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("Studio isolation registries are immutable")
        object.__setattr__(self, name, value)

    def __init__(self, profiles: list[StudioIsolationProfile]):
        object.__setattr__(self, "_sealed", False)
        registered: dict[str, StudioIsolationProfile] = {}
        for profile in profiles:
            self._validate_profile(profile)
            if profile.profile_id in registered:
                raise ValueError("duplicate Studio isolation profile")
            registered[profile.profile_id] = replace(
                profile,
                launcher=Path(profile.launcher),
                executable=Path(profile.executable),
                fixed_arguments=tuple(profile.fixed_arguments),
                environment=MappingProxyType(
                    {
                        str(key): str(value)
                        for key, value in profile.environment.items()
                    }
                ),
            )
        self._profiles = MappingProxyType(registered)
        object.__setattr__(self, "_sealed", True)

    @staticmethod
    def _validate_profile(profile: StudioIsolationProfile) -> None:
        if not _PROFILE_ID.fullmatch(profile.profile_id):
            raise ValueError("invalid Studio isolation profile id")
        if not all(
            (
                profile.network_isolation_enforced,
                profile.resource_limits_enforced,
                profile.process_tree_enforced,
            )
        ):
            raise StudioIsolationError(
                "isolation_unavailable",
                "Studio sandbox enforcement is unavailable.",
            )
        launcher = Path(profile.launcher)
        executable = Path(profile.executable)
        if not _safe_regular_file(launcher) or not _safe_regular_file(executable):
            raise StudioIsolationError(
                "isolation_unavailable", "Studio sandbox binary is unavailable."
            )
        if not _DIGEST.fullmatch(profile.launcher_sha256) or not _DIGEST.fullmatch(
            profile.executable_sha256
        ):
            raise ValueError("invalid sandbox binary digest")
        if _file_sha256(launcher) != profile.launcher_sha256:
            raise StudioIsolationError(
                "isolation_unavailable", "Studio sandbox attestation failed."
            )
        if _file_sha256(executable) != profile.executable_sha256:
            raise StudioIsolationError(
                "isolation_unavailable", "Studio processor attestation failed."
            )
        if not 0.1 <= profile.timeout_seconds <= 1800:
            raise ValueError("sandbox timeout must be between 0.1 and 1800 seconds")
        if not 1 <= profile.max_stdout_bytes <= 100 * 1024 * 1024:
            raise ValueError("invalid sandbox stdout limit")
        if not 1 <= profile.max_stderr_bytes <= 1024 * 1024:
            raise ValueError("invalid sandbox stderr limit")
        if not 0 <= profile.max_arguments <= 128:
            raise ValueError("invalid sandbox argument limit")
        StudioIsolationRegistry._validate_arguments(profile.fixed_arguments, 128)
        for key, value in profile.environment.items():
            if not _ENV_KEY.fullmatch(str(key)) or str(key) in {
                "PATH",
                "PYTHONPATH",
                "LD_PRELOAD",
                "DYLD_INSERT_LIBRARIES",
            }:
                raise ValueError("unsafe Studio sandbox environment key")
            if "\x00" in str(value) or len(str(value)) > 4096:
                raise ValueError("unsafe Studio sandbox environment value")

    @staticmethod
    def _validate_arguments(arguments: tuple[str, ...], limit: int) -> None:
        if len(arguments) > limit:
            raise StudioIsolationError(
                "invalid_invocation", "Studio processor arguments exceed their limit."
            )
        for value in arguments:
            if not isinstance(value, str) or "\x00" in value or len(value) > 4096:
                raise StudioIsolationError(
                    "invalid_invocation", "Studio processor arguments are invalid."
                )

    def resolve(self, invocation: StudioIsolatedInvocation) -> StudioIsolationProfile:
        profile = self._profiles.get(invocation.profile_id)
        if profile is None:
            raise StudioIsolationError(
                "isolation_unavailable", "Studio sandbox profile is unavailable."
            )
        self._validate_arguments(invocation.arguments, profile.max_arguments)
        # Re-attest on every invocation so an on-disk launcher replacement is
        # detected even after process startup.
        if (
            _file_sha256(Path(profile.launcher)) != profile.launcher_sha256
            or _file_sha256(Path(profile.executable)) != profile.executable_sha256
        ):
            raise StudioIsolationError(
                "isolation_unavailable", "Studio sandbox attestation failed."
            )
        return profile


class StudioTrustedProcessorAdapter:
    """Concrete processor whose execution path cannot be replaced by a subclass."""

    __slots__ = (
        "_sealed",
        "_isolation_registry",
        "isolation_policy_id",
        "workspace_root",
        "artifact_kind",
        "media_type",
        "renderer_identity",
        "converter_identity",
        "validator_identity",
        "retention_class",
        "max_source_bytes",
        "max_binding_bytes",
        "max_metadata_bytes",
    )
    isolation_enforced = True

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("Studio processor adapters are immutable")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        registry: StudioIsolationRegistry,
        profile_id: str,
        *,
        workspace_root: str | Path,
        artifact_kind: str = "test_render",
        media_type: str = "application/pdf",
        renderer_identity: str = "studio-renderer-boundary-v1",
        converter_identity: str = "studio-converter-boundary-v1",
        validator_identity: str = "studio-validator-v1",
        retention_class: str = "review",
        max_source_bytes: int = 100 * 1024 * 1024,
        max_binding_bytes: int = 100 * 1024 * 1024,
        max_metadata_bytes: int = 1024 * 1024,
    ):
        object.__setattr__(self, "_sealed", False)
        registry.resolve(StudioIsolatedInvocation(profile_id=profile_id))
        root = Path(workspace_root).absolute()
        if not root.is_dir() or root.is_symlink():
            raise StudioIsolationError(
                "invalid_workspace", "Studio processor workspace root is unavailable."
            )
        if artifact_kind not in {"analysis", "ocr", "page_preview", "test_render"}:
            raise ValueError("invalid Studio processor artifact kind")
        if retention_class not in {"ephemeral", "review", "evidence"}:
            raise ValueError("invalid Studio processor retention class")
        if not 1 <= max_source_bytes <= 100 * 1024 * 1024:
            raise ValueError("invalid Studio processor source limit")
        if not 1 <= max_binding_bytes <= 100 * 1024 * 1024:
            raise ValueError("invalid Studio processor binding limit")
        if not 1 <= max_metadata_bytes <= 4 * 1024 * 1024:
            raise ValueError("invalid Studio processor metadata limit")
        for identity in (
            renderer_identity,
            converter_identity,
            validator_identity,
        ):
            if not 1 <= len(identity) <= 200 or any(ord(char) < 32 for char in identity):
                raise ValueError("invalid Studio processor identity")
        self._isolation_registry = registry
        self.isolation_policy_id = profile_id
        self.workspace_root = root
        self.artifact_kind = artifact_kind
        self.media_type = media_type
        self.renderer_identity = renderer_identity
        self.converter_identity = converter_identity
        self.validator_identity = validator_identity
        self.retention_class = retention_class
        self.max_source_bytes = max_source_bytes
        self.max_binding_bytes = max_binding_bytes
        self.max_metadata_bytes = max_metadata_bytes
        object.__setattr__(self, "_sealed", True)

    async def run_isolated(
        self, *, arguments: tuple[str, ...], workspace: str | Path
    ) -> StudioIsolatedResult:
        return await run_isolated_process(
            self._isolation_registry,
            StudioIsolatedInvocation(
                profile_id=self.isolation_policy_id,
                arguments=arguments,
            ),
            workspace=workspace,
        )

    @staticmethod
    def _json_bytes(value: Mapping | dict, *, limit: int) -> bytes:
        try:
            encoded = json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise StudioIsolationError(
                "invalid_invocation", "Studio processor metadata is invalid."
            ) from exc
        if len(encoded) > limit:
            raise StudioIsolationError(
                "invalid_invocation", "Studio processor metadata exceeds its limit."
            )
        return encoded

    @staticmethod
    def _write_inputs(
        workspace: Path,
        *,
        source: bytes,
        snapshot: bytes,
        options: bytes,
        input_binding: bytes | None,
    ) -> tuple[str, ...]:
        (workspace / "source.bin").write_bytes(source)
        (workspace / "snapshot.json").write_bytes(snapshot)
        (workspace / "options.json").write_bytes(options)
        arguments = [
            "--source-file",
            "source.bin",
            "--snapshot-file",
            "snapshot.json",
            "--options-file",
            "options.json",
        ]
        if input_binding is not None:
            (workspace / "input-binding.bin").write_bytes(input_binding)
            arguments.extend(("--input-binding-file", "input-binding.bin"))
        return tuple(arguments)

    async def process(
        self,
        *,
        source: bytes,
        snapshot: dict,
        options: dict,
        input_binding: bytes | None,
    ) -> StudioIsolatedProcessorOutput:
        if not isinstance(source, bytes) or not 1 <= len(source) <= self.max_source_bytes:
            raise StudioIsolationError(
                "input_too_large", "Studio source exceeds its processing limit."
            )
        if input_binding is not None and (
            not isinstance(input_binding, bytes)
            or len(input_binding) > self.max_binding_bytes
        ):
            raise StudioIsolationError(
                "input_too_large", "Studio input binding exceeds its processing limit."
            )
        snapshot_bytes = self._json_bytes(snapshot, limit=self.max_metadata_bytes)
        options_bytes = self._json_bytes(options, limit=self.max_metadata_bytes)
        with tempfile.TemporaryDirectory(
            prefix="studio-render-", dir=self.workspace_root
        ) as temporary:
            workspace = Path(temporary)
            arguments = await _to_thread_to_completion(
                self._write_inputs,
                workspace,
                source=source,
                snapshot=snapshot_bytes,
                options=options_bytes,
                input_binding=input_binding,
            )
            result = await self.run_isolated(
                arguments=arguments,
                workspace=workspace,
            )
        if not result.stdout:
            raise StudioIsolationError(
                "processor_failed", "Studio processor produced no output."
            )
        return StudioIsolatedProcessorOutput(
            content=result.stdout,
            content_sha256=hashlib.sha256(result.stdout).hexdigest(),
            media_type=self.media_type,
            artifact_kind=self.artifact_kind,
            renderer_identity=self.renderer_identity,
            converter_identity=self.converter_identity,
            validator_identity=self.validator_identity,
            retention_class=self.retention_class,
        )

    async def terminate(self) -> None:
        # Cancelling process() propagates to run_isolated_process, which owns
        # process-tree termination before the workspace context can be cleaned.
        return None


def _minimal_environment(profile: StudioIsolationProfile, workspace: Path) -> dict[str, str]:
    environment = {
        "STUDIO_ISOLATED": "1",
        "TEMP": str(workspace),
        "TMP": str(workspace),
    }
    if os.name == "nt" and os.environ.get("SYSTEMROOT"):
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    environment.update({str(key): str(value) for key, value in profile.environment.items()})
    return environment


async def _read_bounded(
    stream: asyncio.StreamReader | None, *, limit: int
) -> bytes:
    if stream is None:
        return b""
    output = bytearray()
    while True:
        chunk = await stream.read(min(65_536, limit + 1 - len(output)))
        if not chunk:
            return bytes(output)
        output.extend(chunk)
        if len(output) > limit:
            raise StudioIsolationError(
                "processor_output_limit", "Studio processor output exceeded its limit."
            )


async def _kill_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.kill()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        pass


async def run_isolated_process(
    registry: StudioIsolationRegistry,
    invocation: StudioIsolatedInvocation,
    *,
    workspace: str | Path,
) -> StudioIsolatedResult:
    """Run a verified sandbox launcher without a shell or inherited secrets."""

    profile = registry.resolve(invocation)
    working = Path(workspace).absolute()
    if not working.is_dir() or working.is_symlink():
        raise StudioIsolationError(
            "invalid_workspace", "Studio processor workspace is unavailable."
        )
    argv = (
        str(profile.launcher),
        *profile.fixed_arguments,
        str(profile.executable),
        *invocation.arguments,
    )
    creation_options = {}
    if os.name == "nt":
        creation_options["creationflags"] = getattr(
            __import__("subprocess"), "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        creation_options["start_new_session"] = True
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(working),
        env=_minimal_environment(profile, working),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **creation_options,
    )
    stdout_task = asyncio.create_task(
        _read_bounded(process.stdout, limit=profile.max_stdout_bytes)
    )
    stderr_task = asyncio.create_task(
        _read_bounded(process.stderr, limit=profile.max_stderr_bytes)
    )
    wait_task = asyncio.create_task(process.wait())
    tasks = (stdout_task, stderr_task, wait_task)
    try:
        stdout, stderr, _ = await asyncio.wait_for(
            asyncio.gather(*tasks), timeout=profile.timeout_seconds
        )
    except TimeoutError as exc:
        await _kill_process(process)
        raise StudioIsolationError(
            "processor_timeout", "Studio processing exceeded its time limit."
        ) from exc
    except asyncio.CancelledError:
        await _kill_process(process)
        raise
    except Exception:
        await _kill_process(process)
        raise
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    if process.returncode != 0:
        raise StudioIsolationError(
            "processor_failed", "Studio processor did not complete successfully."
        )
    return StudioIsolatedResult(stdout=stdout, stderr=stderr)
