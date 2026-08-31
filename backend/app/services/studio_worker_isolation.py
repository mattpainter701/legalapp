"""Fail-closed subprocess policy for hostile Studio document processing."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, TypeVar

from app.schemas.studio_render import (
    StudioRendererComponent,
    StudioRendererManifest,
    canonical_json_sha256,
)


_PROFILE_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,79}$")
_ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ThreadResult = TypeVar("_ThreadResult")


class StudioIsolationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StudioSandboxLimits:
    """Limits interpreted by the attested supervisor, never by a renderer."""

    cpu_seconds: int = 60
    max_memory_bytes: int = 512 * 1024 * 1024
    max_processes: int = 16
    max_open_files: int = 128


@dataclass(frozen=True)
class StudioIsolationProfile:
    """Server-owned attestation for an approved immutable runtime boundary."""

    profile_id: str
    runtime_root: Path
    launcher: Path
    launcher_sha256: str
    executable: Path
    executable_sha256: str
    font_pack: Path
    font_pack_sha256: str
    renderer_version: str
    rasterizer: Path
    rasterizer_version: str
    rasterizer_sha256: str
    converter: Path
    converter_version: str
    converter_sha256: str
    validator: Path
    validator_version: str
    validator_sha256: str
    boundary_kind: str = "attested_supervisor_v1"
    fixed_arguments: tuple[str, ...] = ()
    environment: Mapping[str, str] = field(default_factory=dict)
    limits: StudioSandboxLimits = field(default_factory=StudioSandboxLimits)
    timeout_seconds: float = 60.0
    max_stdout_bytes: int = 1_048_576
    max_stderr_bytes: int = 65_536
    max_arguments: int = 32


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
    renderer_manifest: StudioRendererManifest
    runtime_manifest_sha256: str
    page_count: int
    mapping_manifest_sha256: str
    retention_class: str


@dataclass(frozen=True)
class StudioValidatedOutput:
    page_count: int
    mapping_manifest_sha256: str


def validate_studio_output(
    validator_report: bytes,
    *,
    content: bytes,
    content_sha256: str,
    artifact_kind: str,
    media_type: str,
    max_pages: int,
    page_number: int | None = None,
) -> StudioValidatedOutput:
    """Verify only a bounded report emitted by the attested validator process."""

    try:
        if (
            not isinstance(validator_report, bytes)
            or not validator_report
            or len(validator_report) > 1024 * 1024
            or not isinstance(content, bytes)
            or not content
            or _DIGEST.fullmatch(content_sha256) is None
            or hashlib.sha256(content).hexdigest() != content_sha256
            or not 1 <= max_pages <= 1_000
        ):
            raise ValueError("invalid validator boundary")
        decoded = json.loads(validator_report.decode("utf-8"))
        if not isinstance(decoded, dict) or set(decoded) != {
            "contract_version",
            "artifact_sha256",
            "artifact_kind",
            "media_type",
            "page_count",
            "pages",
        }:
            raise ValueError("invalid validator contract")
        pages = decoded["pages"]
        page_count = decoded["page_count"]
        if (
            decoded["contract_version"] != 1
            or decoded["artifact_sha256"] != content_sha256
            or decoded["artifact_kind"] != artifact_kind
            or decoded["media_type"] != media_type
            or isinstance(page_count, bool)
            or not isinstance(page_count, int)
            or not 1 <= page_count <= max_pages
            or not isinstance(pages, list)
            or len(pages) != page_count
        ):
            raise ValueError("validator evidence mismatch")
        required_page_keys = {"page_number"}
        if artifact_kind == "page_preview":
            required_page_keys.update(
                {"width_px", "height_px", "dpi_x", "dpi_y"}
            )
        elif media_type in {
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }:
            required_page_keys.update({"width_points", "height_points"})
        canonical_pages: list[dict[str, int | float]] = []
        for index, page in enumerate(pages, start=1):
            if not isinstance(page, dict) or set(page) != required_page_keys:
                raise ValueError("invalid validator page evidence")
            expected_page = page_number if artifact_kind == "page_preview" else index
            if (
                isinstance(page["page_number"], bool)
                or not isinstance(page["page_number"], int)
                or page["page_number"] != expected_page
            ):
                raise ValueError("invalid validator page ordering")
            canonical_page: dict[str, int | float] = {}
            for key, value in page.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError("invalid validator page value")
                if key in {"page_number", "width_px", "height_px"} and not isinstance(
                    value, int
                ):
                    raise ValueError("invalid validator page value")
                numeric = float(value)
                if not math.isfinite(numeric) or numeric <= 0 or numeric > 100_000_000:
                    raise ValueError("invalid validator page bound")
                canonical_page[key] = value
            canonical_pages.append(canonical_page)
        if artifact_kind == "page_preview" and (
            page_number is None or page_count != 1
        ):
            raise ValueError("invalid preview evidence")
        if artifact_kind == "page_preview":
            if (
                len(content) < 24
                or content[:8] != b"\x89PNG\r\n\x1a\n"
                or content[12:16] != b"IHDR"
            ):
                raise ValueError("invalid preview bytes")
            width_px = int.from_bytes(content[16:20], "big")
            height_px = int.from_bytes(content[20:24], "big")
            if (
                canonical_pages[0]["width_px"] != width_px
                or canonical_pages[0]["height_px"] != height_px
            ):
                raise ValueError("preview geometry mismatch")
    except Exception as exc:
        raise StudioIsolationError(
            "validation_failed", "Studio validator evidence is invalid."
        ) from exc
    return StudioValidatedOutput(
        page_count=page_count,
        mapping_manifest_sha256=canonical_json_sha256(
            {"contract_version": 1, "pages": canonical_pages}
        ),
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65_536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _safe_runtime_file(runtime_root: Path, path: Path) -> bool:
    """Reject link/junction indirection anywhere inside the attested runtime."""

    if not runtime_root.is_absolute() or not path.is_absolute():
        return False
    try:
        relative = path.relative_to(runtime_root)
    except ValueError:
        return False
    current = runtime_root
    if not current.is_dir() or _is_link_like(current):
        return False
    for component in relative.parts:
        current = current / component
        if _is_link_like(current):
            return False
    return current.is_file()


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
                runtime_root=Path(profile.runtime_root),
                launcher=Path(profile.launcher),
                executable=Path(profile.executable),
                font_pack=Path(profile.font_pack),
                rasterizer=Path(profile.rasterizer),
                converter=Path(profile.converter),
                validator=Path(profile.validator),
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
        if profile.boundary_kind != "attested_supervisor_v1":
            raise StudioIsolationError(
                "isolation_unavailable",
                "Studio sandbox enforcement is unavailable.",
            )
        runtime_root = Path(profile.runtime_root)
        launcher = Path(profile.launcher)
        executable = Path(profile.executable)
        component_paths = (
            Path(profile.font_pack),
            Path(profile.rasterizer),
            Path(profile.converter),
            Path(profile.validator),
        )
        if (
            not runtime_root.is_absolute()
            or not runtime_root.is_dir()
            or _is_link_like(runtime_root)
            or not _safe_runtime_file(runtime_root, launcher)
            or not _safe_runtime_file(runtime_root, executable)
            or not all(
                _safe_runtime_file(runtime_root, path)
                for path in component_paths
            )
        ):
            raise StudioIsolationError(
                "isolation_unavailable", "Studio sandbox binary is unavailable."
            )
        try:
            launcher.relative_to(runtime_root)
            executable.relative_to(runtime_root)
            for path in component_paths:
                path.relative_to(runtime_root)
        except ValueError as exc:
            raise StudioIsolationError(
                "isolation_unavailable",
                "Studio runtime is outside its immutable root.",
            ) from exc
        digests = (
            profile.launcher_sha256,
            profile.executable_sha256,
            profile.font_pack_sha256,
            profile.rasterizer_sha256,
            profile.converter_sha256,
            profile.validator_sha256,
        )
        if not all(_DIGEST.fullmatch(value) for value in digests):
            raise ValueError("invalid sandbox binary digest")
        if _file_sha256(launcher) != profile.launcher_sha256:
            raise StudioIsolationError(
                "isolation_unavailable", "Studio sandbox attestation failed."
            )
        if _file_sha256(executable) != profile.executable_sha256:
            raise StudioIsolationError(
                "isolation_unavailable", "Studio processor attestation failed."
            )
        expected_components = (
            (Path(profile.font_pack), profile.font_pack_sha256),
            (Path(profile.rasterizer), profile.rasterizer_sha256),
            (Path(profile.converter), profile.converter_sha256),
            (Path(profile.validator), profile.validator_sha256),
        )
        if any(_file_sha256(path) != digest for path, digest in expected_components):
            raise StudioIsolationError(
                "isolation_unavailable", "Studio runtime attestation failed."
            )
        if not 0.1 <= profile.timeout_seconds <= 1800:
            raise ValueError("sandbox timeout must be between 0.1 and 1800 seconds")
        if not 1 <= profile.max_stdout_bytes <= 100 * 1024 * 1024:
            raise ValueError("invalid sandbox stdout limit")
        if not 1 <= profile.max_stderr_bytes <= 1024 * 1024:
            raise ValueError("invalid sandbox stderr limit")
        if not 0 <= profile.max_arguments <= 128:
            raise ValueError("invalid sandbox argument limit")
        limits = profile.limits
        if not 1 <= limits.cpu_seconds <= 1800:
            raise ValueError("invalid sandbox CPU limit")
        if not 16 * 1024 * 1024 <= limits.max_memory_bytes <= 8 * 1024**3:
            raise ValueError("invalid sandbox memory limit")
        if not 1 <= limits.max_processes <= 128:
            raise ValueError("invalid sandbox process limit")
        if not 16 <= limits.max_open_files <= 4096:
            raise ValueError("invalid sandbox open-file limit")
        for version in (
            profile.renderer_version,
            profile.rasterizer_version,
            profile.converter_version,
            profile.validator_version,
        ):
            if not re.fullmatch(r"[A-Za-z0-9_.+-]{1,80}", version):
                raise ValueError("invalid Studio processor version")
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
        try:
            attested = (
                Path(profile.runtime_root).is_dir()
                and not _is_link_like(Path(profile.runtime_root))
                and _safe_runtime_file(
                    Path(profile.runtime_root), Path(profile.launcher)
                )
                and _safe_runtime_file(
                    Path(profile.runtime_root), Path(profile.executable)
                )
                and _safe_runtime_file(
                    Path(profile.runtime_root), Path(profile.font_pack)
                )
                and _safe_runtime_file(
                    Path(profile.runtime_root), Path(profile.rasterizer)
                )
                and _safe_runtime_file(
                    Path(profile.runtime_root), Path(profile.converter)
                )
                and _safe_runtime_file(
                    Path(profile.runtime_root), Path(profile.validator)
                )
                and _file_sha256(Path(profile.launcher)) == profile.launcher_sha256
                and _file_sha256(Path(profile.executable))
                == profile.executable_sha256
                and _file_sha256(Path(profile.font_pack))
                == profile.font_pack_sha256
                and _file_sha256(Path(profile.rasterizer))
                == profile.rasterizer_sha256
                and _file_sha256(Path(profile.converter))
                == profile.converter_sha256
                and _file_sha256(Path(profile.validator))
                == profile.validator_sha256
            )
        except OSError:
            attested = False
        if not attested:
            raise StudioIsolationError(
                "isolation_unavailable", "Studio sandbox attestation failed."
            )
        return profile

    def manifest(self, profile_id: str) -> StudioRendererManifest:
        profile = self.resolve(StudioIsolatedInvocation(profile_id=profile_id))
        return StudioRendererManifest(
            isolation_policy_id=profile.profile_id,
            boundary_kind="attested_supervisor_v1",
            launcher_sha256=profile.launcher_sha256,
            sandbox_policy_sha256=canonical_json_sha256(
                {
                    "boundary_kind": profile.boundary_kind,
                    "cpu_seconds": profile.limits.cpu_seconds,
                    "max_memory_bytes": profile.limits.max_memory_bytes,
                    "max_processes": profile.limits.max_processes,
                    "max_open_files": profile.limits.max_open_files,
                    "timeout_seconds": profile.timeout_seconds,
                    "max_stdout_bytes": profile.max_stdout_bytes,
                    "max_stderr_bytes": profile.max_stderr_bytes,
                    "max_arguments": profile.max_arguments,
                    "network": "deny",
                    "process_tree": "kill",
                }
            ),
            fixed_arguments_sha256=canonical_json_sha256(
                list(profile.fixed_arguments)
            ),
            environment_sha256=canonical_json_sha256(dict(profile.environment)),
            font_pack_sha256=profile.font_pack_sha256,
            renderer=StudioRendererComponent(
                name="studio-renderer",
                version=profile.renderer_version,
                content_sha256=profile.executable_sha256,
            ),
            rasterizer=StudioRendererComponent(
                name="studio-rasterizer",
                version=profile.rasterizer_version,
                content_sha256=profile.rasterizer_sha256,
            ),
            converter=StudioRendererComponent(
                name="studio-converter",
                version=profile.converter_version,
                content_sha256=profile.converter_sha256,
            ),
            validator=StudioRendererComponent(
                name="studio-validator",
                version=profile.validator_version,
                content_sha256=profile.validator_sha256,
            ),
        )


class StudioTrustedProcessorAdapter:
    """Concrete processor whose execution path cannot be replaced by a subclass."""

    __slots__ = (
        "_sealed",
        "_isolation_registry",
        "isolation_policy_id",
        "workspace_root",
        "artifact_kind",
        "media_type",
        "renderer_manifest",
        "runtime_manifest_sha256",
        "retention_class",
        "max_source_bytes",
        "max_binding_bytes",
        "max_metadata_bytes",
    )
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
        retention_class: str = "review",
        max_source_bytes: int = 100 * 1024 * 1024,
        max_binding_bytes: int = 100 * 1024 * 1024,
        max_metadata_bytes: int = 1024 * 1024,
    ):
        object.__setattr__(self, "_sealed", False)
        registry.resolve(StudioIsolatedInvocation(profile_id=profile_id))
        manifest = registry.manifest(profile_id)
        root = Path(workspace_root).absolute()
        if not root.is_dir() or _is_link_like(root):
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
        self._isolation_registry = registry
        self.isolation_policy_id = profile_id
        self.workspace_root = root
        self.artifact_kind = artifact_kind
        self.media_type = media_type
        self.renderer_manifest = manifest
        self.runtime_manifest_sha256 = manifest.sha256
        self.retention_class = retention_class
        self.max_source_bytes = max_source_bytes
        self.max_binding_bytes = max_binding_bytes
        self.max_metadata_bytes = max_metadata_bytes
        object.__setattr__(self, "_sealed", True)

    async def run_isolated(
        self,
        *,
        arguments: tuple[str, ...],
        workspace: str | Path,
        stdout_limit: int,
    ) -> StudioIsolatedResult:
        return await run_isolated_process(
            self._isolation_registry,
            StudioIsolatedInvocation(
                profile_id=self.isolation_policy_id,
                arguments=arguments,
            ),
            workspace=workspace,
            stdout_limit=stdout_limit,
        )

    async def run_isolated_validator(
        self,
        *,
        arguments: tuple[str, ...],
        workspace: str | Path,
    ) -> StudioIsolatedResult:
        return await run_isolated_validator(
            self._isolation_registry,
            StudioIsolatedInvocation(
                profile_id=self.isolation_policy_id,
                arguments=arguments,
            ),
            workspace=workspace,
            stdout_limit=self.max_metadata_bytes,
        )

    def attest_runtime(self) -> StudioRendererManifest:
        manifest = self._isolation_registry.manifest(self.isolation_policy_id)
        if manifest.sha256 != self.runtime_manifest_sha256:
            raise StudioIsolationError(
                "isolation_unavailable", "Studio runtime attestation changed."
            )
        return manifest

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
        requested_output_limit = options.get("max_output_bytes", 25 * 1024 * 1024)
        if (
            isinstance(requested_output_limit, bool)
            or not isinstance(requested_output_limit, int)
            or not 1 <= requested_output_limit <= 100 * 1024 * 1024
        ):
            raise StudioIsolationError(
                "invalid_invocation", "Studio output limit is invalid."
            )
        max_pages = options.get("max_pages", 250)
        page_number = options.get("page_number")
        if (
            isinstance(max_pages, bool)
            or not isinstance(max_pages, int)
            or not 1 <= max_pages <= 1_000
        ):
            raise StudioIsolationError(
                "invalid_invocation", "Studio page limit is invalid."
            )
        self.attest_runtime()
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
                stdout_limit=requested_output_limit,
            )
            if not result.stdout:
                raise StudioIsolationError(
                    "processor_failed", "Studio processor produced no output."
                )
            content_sha256 = hashlib.sha256(result.stdout).hexdigest()
            await _to_thread_to_completion(
                (workspace / "output.bin").write_bytes,
                result.stdout,
            )
            validator_arguments = (
                "--studio-validate-output",
                "--input-file",
                "output.bin",
                "--artifact-kind",
                self.artifact_kind,
                "--media-type",
                self.media_type,
                "--expected-sha256",
                content_sha256,
                "--max-pages",
                str(max_pages),
            )
            if page_number is not None:
                validator_arguments += ("--page-number", str(page_number))
            validator_result = await self.run_isolated_validator(
                arguments=validator_arguments,
                workspace=workspace,
            )
            validation = validate_studio_output(
                validator_result.stdout,
                content=result.stdout,
                content_sha256=content_sha256,
                artifact_kind=self.artifact_kind,
                media_type=self.media_type,
                max_pages=max_pages,
                page_number=page_number,
            )
        return StudioIsolatedProcessorOutput(
            content=result.stdout,
            content_sha256=content_sha256,
            media_type=self.media_type,
            artifact_kind=self.artifact_kind,
            renderer_manifest=self.renderer_manifest,
            runtime_manifest_sha256=self.runtime_manifest_sha256,
            page_count=validation.page_count,
            mapping_manifest_sha256=validation.mapping_manifest_sha256,
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


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    """Terminate the OS process tree, with the attested supervisor as backstop."""

    pid = getattr(process, "pid", None)
    if process.returncode is not None:
        return
    if os.name == "nt" and pid:
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        taskkill = Path(system_root) / "System32" / "taskkill.exe"
        try:
            killer = await asyncio.create_subprocess_exec(
                str(taskkill),
                "/PID",
                str(pid),
                "/T",
                "/F",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), timeout=5)
        except Exception:
            process.kill()
    elif pid:
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError:
            process.kill()
        try:
            await asyncio.wait_for(process.wait(), timeout=0.5)
            return
        except TimeoutError:
            try:
                # SIGKILL is POSIX signal 9. Windows' signal module omits the
                # symbolic attribute, which matters only to synthetic tests
                # that exercise this otherwise unreachable POSIX branch.
                os.killpg(pid, getattr(signal, "SIGKILL", 9))
            except ProcessLookupError:
                pass
            except OSError:
                process.kill()
    else:
        process.kill()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        if process.returncode is None:
            process.kill()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError as exc:
                raise StudioIsolationError(
                    "isolation_unavailable",
                    "Studio process tree could not be terminated.",
                ) from exc


async def _terminate_process_tree_to_completion(
    process: asyncio.subprocess.Process,
) -> None:
    """Drain process-tree termination before honoring repeated cancellation."""

    task = asyncio.create_task(_terminate_process_tree(process))
    cancellation_requested = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancellation_requested = True
    task.result()
    if cancellation_requested:
        raise asyncio.CancelledError


def _supervisor_argv(
    profile: StudioIsolationProfile,
    invocation: StudioIsolatedInvocation,
    workspace: Path,
    *,
    component: str = "renderer",
) -> tuple[str, ...]:
    """Build the only accepted policy boundary; callers cannot remove controls."""

    if component == "renderer":
        executable = profile.executable
        executable_sha256 = profile.executable_sha256
        fixed_arguments = profile.fixed_arguments
    elif component == "validator":
        executable = profile.validator
        executable_sha256 = profile.validator_sha256
        fixed_arguments = ()
    else:
        raise StudioIsolationError(
            "invalid_invocation", "Studio runtime component is invalid."
        )
    limits = profile.limits
    return (
        str(profile.launcher),
        "--studio-policy",
        "attested-supervisor-v1",
        "--deny-network",
        "--kill-process-tree",
        "--studio-component",
        component,
        "--workspace",
        str(workspace),
        "--cpu-seconds",
        str(limits.cpu_seconds),
        "--max-memory-bytes",
        str(limits.max_memory_bytes),
        "--max-processes",
        str(limits.max_processes),
        "--max-open-files",
        str(limits.max_open_files),
        "--verify-executable-sha256",
        executable_sha256,
        "--font-pack",
        str(profile.font_pack),
        "--verify-font-pack-sha256",
        profile.font_pack_sha256,
        "--rasterizer",
        str(profile.rasterizer),
        "--verify-rasterizer-sha256",
        profile.rasterizer_sha256,
        "--converter",
        str(profile.converter),
        "--verify-converter-sha256",
        profile.converter_sha256,
        "--validator",
        str(profile.validator),
        "--verify-validator-sha256",
        profile.validator_sha256,
        "--",
        str(executable),
        *fixed_arguments,
        *invocation.arguments,
    )


async def run_isolated_process(
    registry: StudioIsolationRegistry,
    invocation: StudioIsolatedInvocation,
    *,
    workspace: str | Path,
    stdout_limit: int | None = None,
    _component: str = "renderer",
) -> StudioIsolatedResult:
    """Run a verified sandbox launcher without a shell or inherited secrets."""

    profile = registry.resolve(invocation)
    working = Path(workspace).absolute()
    if not working.is_dir() or _is_link_like(working):
        raise StudioIsolationError(
            "invalid_workspace", "Studio processor workspace is unavailable."
        )
    if stdout_limit is not None and (
        isinstance(stdout_limit, bool)
        or not isinstance(stdout_limit, int)
        or stdout_limit < 1
    ):
        raise StudioIsolationError(
            "invalid_invocation", "Studio output limit is invalid."
        )
    effective_stdout_limit = min(
        profile.max_stdout_bytes,
        stdout_limit if stdout_limit is not None else profile.max_stdout_bytes,
    )
    argv = _supervisor_argv(
        profile,
        invocation,
        working,
        component=_component,
    )
    creation_options = {}
    if os.name == "nt":
        creation_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
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
        _read_bounded(process.stdout, limit=effective_stdout_limit)
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
        await _terminate_process_tree_to_completion(process)
        raise StudioIsolationError(
            "processor_timeout", "Studio processing exceeded its time limit."
        ) from exc
    except asyncio.CancelledError:
        await _terminate_process_tree_to_completion(process)
        raise
    except Exception:
        await _terminate_process_tree_to_completion(process)
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


async def run_isolated_validator(
    registry: StudioIsolationRegistry,
    invocation: StudioIsolatedInvocation,
    *,
    workspace: str | Path,
    stdout_limit: int,
) -> StudioIsolatedResult:
    """Run the separately attested validator under the same sandbox boundary."""

    return await run_isolated_process(
        registry,
        invocation,
        workspace=workspace,
        stdout_limit=stdout_limit,
        _component="validator",
    )
