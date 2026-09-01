"""Fail-closed construction for the dedicated Studio render API and worker."""

from __future__ import annotations

import asyncio
import json
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from app.config import Settings, validate_studio_render_paths
from app.database import async_session_maker
from app.schemas.studio_render import (
    StudioRenderCapabilities,
    StudioRendererManifest,
)
from app.services.studio_object_storage import LocalStudioObjectStore
from app.services.studio_artifact_retention import StudioRenderMaintenance
from app.services.studio_render_worker import StudioRenderWorker
from app.services.studio_render_worker_loop import (
    PostgresStudioRenderWorkSource,
    StudioRenderWorkerLoop,
)
from app.services.studio_worker_isolation import (
    StudioIsolationProfile,
    StudioIsolationRegistry,
    StudioSandboxLimits,
    StudioTrustedProcessorAdapter,
)


class StudioRenderRuntimeError(RuntimeError):
    """Sanitized configuration failure; never include provider or path details."""


@dataclass(frozen=True)
class StudioRenderApiRuntime:
    object_store: LocalStudioObjectStore
    manifests: Mapping[tuple[str, str, int], StudioRendererManifest]
    capabilities: StudioRenderCapabilities


_ARTIFACT_KIND = {
    "studio_template_analysis": "analysis",
    "studio_template_ocr": "ocr",
    "studio_page_preview": "page_preview",
    "studio_test_render": "test_render",
}
def _json_object(raw: str, *, name: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise StudioRenderRuntimeError(f"Studio {name} configuration is invalid.") from exc
    if not isinstance(value, dict):
        raise StudioRenderRuntimeError(f"Studio {name} configuration is invalid.")
    return value


def load_studio_renderer_manifests(
    settings: Settings,
) -> tuple[
    dict[tuple[str, str, int], StudioRendererManifest],
    StudioRenderCapabilities,
]:
    try:
        document = json.loads(settings.TEMPLATE_STUDIO_RENDER_MANIFESTS_JSON)
        capabilities = StudioRenderCapabilities.model_validate(document)
    except Exception as exc:
        raise StudioRenderRuntimeError(
            "Studio manifest configuration is invalid."
        ) from exc
    return (
        {
            capability.key: capability.renderer_manifest
            for capability in capabilities.capabilities
        },
        capabilities,
    )


def build_studio_render_api_runtime(settings: Settings) -> StudioRenderApiRuntime:
    if not settings.TEMPLATE_STUDIO_RENDER_ENABLED:
        raise StudioRenderRuntimeError("Studio rendering is disabled.")
    try:
        storage_root, _ = validate_studio_render_paths(
            settings,
            require_workspace=settings.TEMPLATE_STUDIO_RENDER_WORKER_ENABLED,
        )
        store = LocalStudioObjectStore(
            storage_root,
            max_object_bytes=settings.TEMPLATE_STUDIO_RENDER_MAX_OBJECT_BYTES,
        )
        manifests, capabilities = load_studio_renderer_manifests(settings)
    except StudioRenderRuntimeError:
        raise
    except Exception as exc:
        raise StudioRenderRuntimeError(
            "Studio rendering is temporarily unavailable."
        ) from exc
    return StudioRenderApiRuntime(
        object_store=store,
        manifests=manifests,
        capabilities=capabilities,
    )


def _profile(raw: object) -> StudioIsolationProfile:
    if not isinstance(raw, dict):
        raise StudioRenderRuntimeError("Studio worker profile is invalid.")
    values = dict(raw)
    try:
        limits_raw = values.pop("limits", {})
        if not isinstance(limits_raw, dict):
            raise TypeError("invalid limits")
        for key in (
            "runtime_root",
            "launcher",
            "executable",
            "runtime_bundle_manifest",
            "font_pack",
            "rasterizer",
            "converter",
            "validator",
        ):
            values[key] = Path(values[key])
        values["fixed_arguments"] = tuple(values.get("fixed_arguments", ()))
        values["environment"] = dict(values.get("environment", {}))
        values["limits"] = StudioSandboxLimits(**limits_raw)
        return StudioIsolationProfile(**values)
    except Exception as exc:
        raise StudioRenderRuntimeError("Studio worker profile is invalid.") from exc


def _is_reparse_point(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    is_junction = getattr(path, "is_junction", None)
    return (
        path.is_symlink()
        or bool(is_junction and is_junction())
        or bool(reparse_flag and attributes & reparse_flag)
    )


def _assert_plain_ancestor_chain(path: Path) -> None:
    for ancestor in reversed((path, *path.parents)):
        if _is_reparse_point(ancestor):
            raise ValueError("unsafe workspace ancestor")


def _prepare_workspace(settings: Settings) -> Path:
    supplied_root = Path(settings.TEMPLATE_STUDIO_RENDER_WORKSPACE_DIR)
    try:
        _assert_plain_ancestor_chain(supplied_root)
        _, configured_root = validate_studio_render_paths(
            settings, require_workspace=True
        )
        if configured_root is None:
            raise ValueError("unsafe workspace")
        root = configured_root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        _assert_plain_ancestor_chain(supplied_root)
        _assert_plain_ancestor_chain(root)
        _, verified_root = validate_studio_render_paths(
            settings, require_workspace=True
        )
        if (
            verified_root != root
            or root.resolve(strict=True) != root
            or not root.is_dir()
        ):
            raise ValueError("unsafe workspace")
        for child in root.iterdir():
            if child.parent != root:
                raise ValueError("unsafe workspace child")
            child_is_junction = getattr(child, "is_junction", None)
            if bool(child_is_junction and child_is_junction()):
                child.rmdir()
            elif _is_reparse_point(child):
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    except Exception as exc:
        raise StudioRenderRuntimeError(
            "Studio render workspace is unavailable."
        ) from exc
    return root


def build_studio_render_worker_loop(settings: Settings) -> StudioRenderWorkerLoop:
    if not (
        settings.TEMPLATE_STUDIO_RENDER_ENABLED
        and settings.TEMPLATE_STUDIO_RENDER_WORKER_ENABLED
    ):
        raise StudioRenderRuntimeError("Studio render worker is disabled.")
    api_runtime = build_studio_render_api_runtime(settings)
    profile_document = _json_object(
        settings.TEMPLATE_STUDIO_RENDER_PROFILES_JSON, name="worker profile"
    )
    capability_by_configuration_key = {
        capability.configuration_key: capability
        for capability in api_runtime.capabilities.capabilities
    }
    if set(profile_document) != set(capability_by_configuration_key):
        raise StudioRenderRuntimeError("Studio worker profile configuration is incomplete.")

    profiles_by_capability = {
        capability.key: _profile(profile_document[configuration_key])
        for configuration_key, capability in capability_by_configuration_key.items()
    }
    unique_profiles: dict[str, StudioIsolationProfile] = {}
    for profile in profiles_by_capability.values():
        existing = unique_profiles.get(profile.profile_id)
        if existing is not None and existing != profile:
            raise StudioRenderRuntimeError("Studio worker profile identity is ambiguous.")
        unique_profiles[profile.profile_id] = profile
    try:
        workspace_root = _prepare_workspace(settings)
        registry = StudioIsolationRegistry(list(unique_profiles.values()))
        processors = {
            capability.key: StudioTrustedProcessorAdapter(
                registry,
                profiles_by_capability[capability.key].profile_id,
                workspace_root=workspace_root,
                artifact_kind=_ARTIFACT_KIND[capability.kind],
                media_type=capability.output_media_type,
                retention_class=(
                    "review"
                    if capability.kind == "studio_test_render"
                    else "ephemeral"
                ),
                max_source_bytes=settings.TEMPLATE_STUDIO_RENDER_MAX_OBJECT_BYTES,
                max_binding_bytes=settings.TEMPLATE_STUDIO_RENDER_MAX_OBJECT_BYTES,
            )
            for capability in api_runtime.capabilities.capabilities
        }
        if any(
            processors[capability.key].renderer_manifest.sha256
            != capability.renderer_manifest.sha256
            for capability in api_runtime.capabilities.capabilities
        ):
            raise StudioRenderRuntimeError(
                "Studio worker attestation does not match the API manifest."
            )
        worker = StudioRenderWorker(
            async_session_maker,
            object_store=api_runtime.object_store,
            processors=processors,
            lease_seconds=settings.TEMPLATE_STUDIO_RENDER_LEASE_SECONDS,
            heartbeat_seconds=settings.TEMPLATE_STUDIO_RENDER_HEARTBEAT_SECONDS,
            processor_timeout_seconds=(
                settings.TEMPLATE_STUDIO_RENDER_PROCESSOR_TIMEOUT_SECONDS
            ),
            artifact_ttl_seconds=settings.TEMPLATE_STUDIO_RENDER_ARTIFACT_TTL_SECONDS,
            metadata_ttl_seconds=(
                settings.TEMPLATE_STUDIO_RENDER_METADATA_TTL_SECONDS
            ),
            max_input_binding_bytes=(
                settings.TEMPLATE_STUDIO_RENDER_MAX_INPUT_BINDING_BYTES
            ),
            retained_artifact_limit=(
                settings.TEMPLATE_STUDIO_RENDER_RETAINED_ARTIFACT_LIMIT
            ),
            retained_byte_limit=settings.TEMPLATE_STUDIO_RENDER_RETAINED_BYTE_LIMIT,
            live_artifact_limit=settings.TEMPLATE_STUDIO_RENDER_LIVE_ARTIFACT_LIMIT,
            live_byte_limit=settings.TEMPLATE_STUDIO_RENDER_LIVE_BYTE_LIMIT,
        )
    except StudioRenderRuntimeError:
        raise
    except Exception as exc:
        raise StudioRenderRuntimeError(
            "Studio render worker is temporarily unavailable."
        ) from exc
    async def runtime_heartbeat(healthy: bool) -> None:
        await asyncio.to_thread(
            api_runtime.object_store.touch_worker_heartbeat,
            healthy=healthy,
        )

    return StudioRenderWorkerLoop(
        source=PostgresStudioRenderWorkSource(
            async_session_maker,
            tenant_scan_batch=settings.TEMPLATE_STUDIO_RENDER_TENANT_SCAN_BATCH,
        ),
        worker=worker,
        batch_size=settings.TEMPLATE_STUDIO_RENDER_BATCH_SIZE,
        concurrency=settings.TEMPLATE_STUDIO_RENDER_CONCURRENCY,
        idle_seconds=settings.TEMPLATE_STUDIO_RENDER_IDLE_SECONDS,
        maintenance=StudioRenderMaintenance(
            async_session_maker,
            object_store=api_runtime.object_store,
            tenant_batch_size=settings.TEMPLATE_STUDIO_RENDER_TENANT_SCAN_BATCH,
            artifact_ttl_seconds=(
                settings.TEMPLATE_STUDIO_RENDER_ARTIFACT_TTL_SECONDS
            ),
            metadata_ttl_seconds=(
                settings.TEMPLATE_STUDIO_RENDER_METADATA_TTL_SECONDS
            ),
        ),
        maintenance_interval_seconds=(
            settings.TEMPLATE_STUDIO_RENDER_MAINTENANCE_INTERVAL_SECONDS
        ),
        runtime_heartbeat=runtime_heartbeat,
    )


__all__ = [
    "StudioRenderApiRuntime",
    "StudioRenderRuntimeError",
    "build_studio_render_api_runtime",
    "build_studio_render_worker_loop",
    "load_studio_renderer_manifests",
]
