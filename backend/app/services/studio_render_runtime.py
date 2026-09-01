"""Fail-closed construction for the dedicated Studio render API and worker."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from app.config import Settings
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
        store = LocalStudioObjectStore(
            Path(settings.TEMPLATE_STUDIO_RENDER_STORAGE_DIR),
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
        registry = StudioIsolationRegistry(list(unique_profiles.values()))
        processors = {
            capability.key: StudioTrustedProcessorAdapter(
                registry,
                profiles_by_capability[capability.key].profile_id,
                workspace_root=Path(settings.TEMPLATE_STUDIO_RENDER_WORKSPACE_DIR),
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
        )
    except StudioRenderRuntimeError:
        raise
    except Exception as exc:
        raise StudioRenderRuntimeError(
            "Studio render worker is temporarily unavailable."
        ) from exc
    return StudioRenderWorkerLoop(
        source=PostgresStudioRenderWorkSource(async_session_maker),
        worker=worker,
        batch_size=settings.TEMPLATE_STUDIO_RENDER_BATCH_SIZE,
        concurrency=settings.TEMPLATE_STUDIO_RENDER_CONCURRENCY,
        idle_seconds=settings.TEMPLATE_STUDIO_RENDER_IDLE_SECONDS,
        maintenance=StudioRenderMaintenance(
            async_session_maker,
            object_store=api_runtime.object_store,
        ),
        maintenance_interval_seconds=(
            settings.TEMPLATE_STUDIO_RENDER_MAINTENANCE_INTERVAL_SECONDS
        ),
    )


__all__ = [
    "StudioRenderApiRuntime",
    "StudioRenderRuntimeError",
    "build_studio_render_api_runtime",
    "build_studio_render_worker_loop",
    "load_studio_renderer_manifests",
]
