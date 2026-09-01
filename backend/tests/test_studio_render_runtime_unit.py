"""Database-free fail-closed construction checks for the Studio render runtime.

These cover the process-entry and dependency-wiring seams that the job and
route suites never reach: the worker entrypoint module, manifest/profile
parsing, and the guards that must refuse to build a worker before any
sandbox, workspace, or database resource is touched.
"""

import asyncio
import json
import signal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.studio_render_worker_main as worker_main
from app.schemas.studio_render import StudioRendererComponent, StudioRendererManifest
from app.services.studio_render_runtime import (
    StudioRenderRuntimeError,
    _json_object,
    _prepare_workspace,
    _profile,
    build_studio_render_api_runtime,
    build_studio_render_worker_loop,
    load_studio_renderer_manifests,
)


def _renderer_manifest_document() -> dict:
    def component(name: str, digest: str) -> dict:
        return StudioRendererComponent(
            name=name, version="1.0.0", content_sha256=digest * 64
        ).model_dump(mode="json")

    return StudioRendererManifest(
        isolation_policy_id="studio-test-v1",
        launcher_sha256="1" * 64,
        sandbox_policy_sha256="9" * 64,
        fixed_arguments_sha256="2" * 64,
        environment_sha256="3" * 64,
        runtime_bundle_sha256="0" * 64,
        font_pack_sha256="4" * 64,
        renderer=component("renderer", "5"),
        rasterizer=component("rasterizer", "6"),
        converter=component("converter", "7"),
        validator=component("validator", "8"),
    ).model_dump(mode="json")


_MEDIA_TYPE = {
    "studio_test_render": "application/pdf",
    "studio_page_preview": "image/png",
    "studio_template_analysis": "application/json",
}


def _capabilities_document(*kinds: str) -> dict:
    return {
        "contract_version": 1,
        "capabilities": [
            {
                "kind": kind,
                "source_format": "markdown",
                "render_options_contract_version": 1,
                "output_media_type": _MEDIA_TYPE[kind],
                "renderer_manifest": _renderer_manifest_document(),
            }
            for kind in (kinds or ("studio_test_render",))
        ],
    }


# An absolute root that is guaranteed absent on every platform, so profile
# construction is exercised without ever depending on a real runtime bundle.
_ABSENT_RUNTIME_ROOT = str(Path(Path.cwd().anchor) / "studio-runtime-absent")


def _profile_document(
    *,
    profile_id: str = "studio-render-v1",
    runtime_root: str = _ABSENT_RUNTIME_ROOT,
    **overrides,
) -> dict:
    root = Path(runtime_root)
    values = {
        "profile_id": profile_id,
        "runtime_root": str(root),
        "launcher": str(root / "launcher"),
        "launcher_sha256": "1" * 64,
        "executable": str(root / "renderer"),
        "executable_sha256": "5" * 64,
        "runtime_bundle_manifest": str(root / "bundle.json"),
        "runtime_bundle_sha256": "0" * 64,
        "font_pack": str(root / "fonts"),
        "font_pack_sha256": "4" * 64,
        "renderer_version": "1.0.0",
        "rasterizer": str(root / "rasterizer"),
        "rasterizer_version": "1.0.0",
        "rasterizer_sha256": "6" * 64,
        "converter": str(root / "converter"),
        "converter_version": "1.0.0",
        "converter_sha256": "7" * 64,
        "validator": str(root / "validator"),
        "validator_version": "1.0.0",
        "validator_sha256": "8" * 64,
    }
    values.update(overrides)
    return values


def _runtime_settings(tmp_path: Path, **overrides):
    values = {
        "TEMPLATE_STUDIO_RENDER_ENABLED": True,
        "TEMPLATE_STUDIO_RENDER_WORKER_ENABLED": True,
        "TEMPLATE_STUDIO_RENDER_STORAGE_DIR": str(tmp_path / "cas"),
        "TEMPLATE_STUDIO_RENDER_WORKSPACE_DIR": str(tmp_path / "workspace"),
        "UPLOAD_DIR": str(tmp_path / "uploads"),
        "TEMPLATE_STUDIO_RENDER_MAX_OBJECT_BYTES": 1024 * 1024,
        "TEMPLATE_STUDIO_RENDER_MANIFESTS_JSON": json.dumps(_capabilities_document()),
        "TEMPLATE_STUDIO_RENDER_PROFILES_JSON": json.dumps(
            {"studio_test_render:markdown:v1": _profile_document()}
        ),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


# --- configuration parsing -------------------------------------------------


def test_json_object_accepts_only_a_json_object():
    assert _json_object('{"a": 1}', name="worker profile") == {"a": 1}

    with pytest.raises(StudioRenderRuntimeError) as invalid:
        _json_object("{broken", name="worker profile")
    with pytest.raises(StudioRenderRuntimeError) as not_an_object:
        _json_object("[1, 2]", name="worker profile")

    # The message stays generic; a configuration body can carry deployment paths.
    for error in (invalid, not_an_object):
        assert str(error.value) == "Studio worker profile configuration is invalid."


def test_load_studio_renderer_manifests_indexes_capabilities_by_dispatch_key():
    settings = SimpleNamespace(
        TEMPLATE_STUDIO_RENDER_MANIFESTS_JSON=json.dumps(
            _capabilities_document("studio_test_render", "studio_page_preview")
        )
    )

    manifests, capabilities = load_studio_renderer_manifests(settings)

    assert set(manifests) == {
        ("studio_test_render", "markdown", 1),
        ("studio_page_preview", "markdown", 1),
    }
    assert [capability.key for capability in capabilities.capabilities] == [
        ("studio_test_render", "markdown", 1),
        ("studio_page_preview", "markdown", 1),
    ]
    assert manifests[("studio_test_render", "markdown", 1)].launcher_sha256 == "1" * 64


@pytest.mark.parametrize("raw", ["not-json", json.dumps({"contract_version": 1})])
def test_load_studio_renderer_manifests_rejects_invalid_documents(raw):
    settings = SimpleNamespace(TEMPLATE_STUDIO_RENDER_MANIFESTS_JSON=raw)

    with pytest.raises(StudioRenderRuntimeError) as error:
        load_studio_renderer_manifests(settings)
    assert str(error.value) == "Studio manifest configuration is invalid."


# --- API runtime construction ---------------------------------------------


def test_build_studio_render_api_runtime_refuses_when_rendering_is_disabled(tmp_path):
    settings = _runtime_settings(tmp_path, TEMPLATE_STUDIO_RENDER_ENABLED=False)

    with pytest.raises(StudioRenderRuntimeError) as error:
        build_studio_render_api_runtime(settings)
    assert str(error.value) == "Studio rendering is disabled."


def test_build_studio_render_api_runtime_binds_the_store_to_the_configured_root(
    tmp_path,
):
    settings = _runtime_settings(tmp_path)

    runtime = build_studio_render_api_runtime(settings)

    assert runtime.object_store.root == (tmp_path / "cas").absolute()
    assert runtime.object_store.max_object_bytes == 1024 * 1024
    assert set(runtime.manifests) == {("studio_test_render", "markdown", 1)}
    assert runtime.capabilities.contract_version == 1


def test_build_studio_render_api_runtime_reraises_sanitized_manifest_failures(tmp_path):
    settings = _runtime_settings(
        tmp_path, TEMPLATE_STUDIO_RENDER_MANIFESTS_JSON="not-json"
    )

    with pytest.raises(StudioRenderRuntimeError) as error:
        build_studio_render_api_runtime(settings)
    assert str(error.value) == "Studio manifest configuration is invalid."


def test_build_studio_render_api_runtime_sanitizes_unexpected_storage_failures(
    tmp_path,
):
    settings = _runtime_settings(tmp_path)

    with patch(
        "app.services.studio_render_runtime.LocalStudioObjectStore",
        side_effect=OSError(str(tmp_path / "cas")),
    ):
        with pytest.raises(StudioRenderRuntimeError) as error:
            build_studio_render_api_runtime(settings)

    # The raw OSError text carries the storage path, so it must not surface.
    assert str(error.value) == "Studio rendering is temporarily unavailable."
    assert str(tmp_path) not in str(error.value)


# --- isolation profile parsing --------------------------------------------


def test_profile_materializes_paths_limits_and_frozen_defaults():
    profile = _profile(
        _profile_document(
            fixed_arguments=["--no-network"],
            environment={"LANG": "C"},
            limits={"cpu_seconds": 5, "max_processes": 2},
        )
    )

    assert profile.profile_id == "studio-render-v1"
    assert profile.launcher == Path(_ABSENT_RUNTIME_ROOT) / "launcher"
    assert profile.font_pack == Path(_ABSENT_RUNTIME_ROOT) / "fonts"
    assert profile.fixed_arguments == ("--no-network",)
    assert profile.environment == {"LANG": "C"}
    assert profile.limits.cpu_seconds == 5
    assert profile.limits.max_processes == 2
    # An unspecified limit keeps the server-owned default rather than going open.
    assert profile.limits.max_open_files == 128
    assert profile.boundary_kind == "attested_supervisor_v1"


@pytest.mark.parametrize(
    "document",
    [
        pytest.param(["not", "a", "mapping"], id="not-a-mapping"),
        pytest.param(
            _profile_document(limits=["not", "a", "mapping"]), id="bad-limits"
        ),
        pytest.param(
            _profile_document(limits={"unknown_limit": 1}), id="unknown-limit"
        ),
        pytest.param(_profile_document(unexpected_field=1), id="unknown-field"),
    ],
)
def test_profile_rejects_anything_it_cannot_fully_attest(document):
    with pytest.raises(StudioRenderRuntimeError) as error:
        _profile(document)
    assert str(error.value) == "Studio worker profile is invalid."


def test_profile_rejects_a_document_missing_an_attested_path():
    document = _profile_document()
    del document["font_pack"]

    with pytest.raises(StudioRenderRuntimeError):
        _profile(document)


# --- worker loop construction ---------------------------------------------


@pytest.mark.parametrize(
    ("rendering", "worker"),
    [(False, False), (False, True), (True, False)],
)
def test_build_studio_render_worker_loop_requires_both_flags(
    tmp_path, rendering, worker
):
    settings = _runtime_settings(
        tmp_path,
        TEMPLATE_STUDIO_RENDER_ENABLED=rendering,
        TEMPLATE_STUDIO_RENDER_WORKER_ENABLED=worker,
    )

    with pytest.raises(StudioRenderRuntimeError) as error:
        build_studio_render_worker_loop(settings)
    assert str(error.value) == "Studio render worker is disabled."


@pytest.mark.parametrize(
    "profiles",
    [
        pytest.param({}, id="no-profiles"),
        pytest.param(
            {"studio_page_preview:markdown:v1": _profile_document()}, id="wrong-key"
        ),
        pytest.param(
            {
                "studio_test_render:markdown:v1": _profile_document(),
                "studio_page_preview:markdown:v1": _profile_document(),
            },
            id="extra-profile",
        ),
    ],
)
def test_build_studio_render_worker_loop_requires_an_exact_profile_set(
    tmp_path, profiles
):
    settings = _runtime_settings(
        tmp_path, TEMPLATE_STUDIO_RENDER_PROFILES_JSON=json.dumps(profiles)
    )

    with pytest.raises(StudioRenderRuntimeError) as error:
        build_studio_render_worker_loop(settings)
    assert str(error.value) == "Studio worker profile configuration is incomplete."


def test_build_studio_render_worker_loop_rejects_malformed_profile_configuration(
    tmp_path,
):
    settings = _runtime_settings(
        tmp_path, TEMPLATE_STUDIO_RENDER_PROFILES_JSON='["not", "an", "object"]'
    )

    with pytest.raises(StudioRenderRuntimeError) as error:
        build_studio_render_worker_loop(settings)
    assert str(error.value) == "Studio worker profile configuration is invalid."


def test_build_studio_render_worker_loop_rejects_an_ambiguous_profile_identity(
    tmp_path,
):
    """One profile_id must never describe two different attested boundaries."""

    settings = _runtime_settings(
        tmp_path,
        TEMPLATE_STUDIO_RENDER_MANIFESTS_JSON=json.dumps(
            _capabilities_document("studio_test_render", "studio_page_preview")
        ),
        TEMPLATE_STUDIO_RENDER_PROFILES_JSON=json.dumps(
            {
                "studio_test_render:markdown:v1": _profile_document(),
                "studio_page_preview:markdown:v1": _profile_document(
                    executable_sha256="b" * 64
                ),
            }
        ),
    )

    with pytest.raises(StudioRenderRuntimeError) as error:
        build_studio_render_worker_loop(settings)
    assert str(error.value) == "Studio worker profile identity is ambiguous."


def test_build_studio_render_worker_loop_accepts_one_identity_shared_by_capabilities(
    tmp_path,
):
    """An identical profile reused across capabilities is not ambiguous.

    Construction still fails afterwards, at workspace and attestation setup,
    so this asserts only that the identity guard let the configuration through.
    """

    settings = _runtime_settings(
        tmp_path,
        TEMPLATE_STUDIO_RENDER_MANIFESTS_JSON=json.dumps(
            _capabilities_document("studio_test_render", "studio_page_preview")
        ),
        TEMPLATE_STUDIO_RENDER_PROFILES_JSON=json.dumps(
            {
                "studio_test_render:markdown:v1": _profile_document(),
                "studio_page_preview:markdown:v1": _profile_document(),
            }
        ),
    )

    with pytest.raises(StudioRenderRuntimeError) as error:
        build_studio_render_worker_loop(settings)
    assert str(error.value) != "Studio worker profile identity is ambiguous."


# --- worker process entrypoint --------------------------------------------


def test_worker_entrypoint_runs_the_loop_until_a_signal_stops_it():
    observed = {}

    class _Loop:
        async def run_forever(self, stop):
            observed["stop_set"] = stop.is_set()
            stop.set()
            observed["stopped"] = stop.is_set()

    settings = object()
    with (
        patch.object(worker_main, "get_settings", return_value=settings) as settings_of,
        patch.object(
            worker_main, "build_studio_render_worker_loop", return_value=_Loop()
        ) as build,
    ):
        asyncio.run(worker_main.run())

    settings_of.assert_called_once_with()
    build.assert_called_once_with(settings)
    assert observed == {"stop_set": False, "stopped": True}


def test_worker_entrypoint_installs_available_termination_handlers():
    installed = []

    class _Loop:
        async def run_forever(self, stop):
            return None

    async def _drive():
        loop = asyncio.get_running_loop()
        with patch.object(
            loop,
            "add_signal_handler",
            side_effect=lambda value, handler: installed.append(value),
        ):
            await worker_main.run()

    with (
        patch.object(worker_main, "get_settings", return_value=object()),
        patch.object(
            worker_main, "build_studio_render_worker_loop", return_value=_Loop()
        ),
    ):
        asyncio.run(_drive())

    expected = [
        getattr(signal, name)
        for name in ("SIGINT", "SIGTERM")
        if getattr(signal, name, None) is not None
    ]
    assert installed == expected


def test_worker_entrypoint_tolerates_platforms_without_signal_handlers():
    """A Windows loop raises NotImplementedError; the worker must still start."""

    started = []

    class _Loop:
        async def run_forever(self, stop):
            started.append(True)

    async def _drive():
        loop = asyncio.get_running_loop()
        with patch.object(loop, "add_signal_handler", side_effect=NotImplementedError):
            await worker_main.run()

    with (
        patch.object(worker_main, "get_settings", return_value=object()),
        patch.object(
            worker_main, "build_studio_render_worker_loop", return_value=_Loop()
        ),
    ):
        asyncio.run(_drive())

    assert started == [True]


def test_worker_entrypoint_main_drives_the_async_runner():
    with patch.object(worker_main.asyncio, "run") as run:
        worker_main.main()

    run.assert_called_once()
    coroutine = run.call_args.args[0]
    assert coroutine.cr_code is worker_main.run.__code__
    coroutine.close()


# --- workspace preparation -------------------------------------------------


def _workspace_settings(tmp_path: Path, **overrides):
    values = {
        "TEMPLATE_STUDIO_RENDER_STORAGE_DIR": str(tmp_path / "cas"),
        "TEMPLATE_STUDIO_RENDER_WORKSPACE_DIR": str(tmp_path / "workspace"),
        "UPLOAD_DIR": str(tmp_path / "uploads"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_prepare_workspace_clears_every_kind_of_leftover(tmp_path):
    """A crashed render can leave files, trees, or stray entries behind."""

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "loose.bin").write_bytes(b"old output")
    nested = workspace / "job-1" / "deep"
    nested.mkdir(parents=True)
    (nested / "page.png").write_bytes(b"old page")
    (workspace / "empty-dir").mkdir()

    prepared = _prepare_workspace(_workspace_settings(tmp_path))

    assert prepared == workspace.resolve()
    assert list(prepared.iterdir()) == []


def test_prepare_workspace_creates_a_private_root_when_absent(tmp_path):
    prepared = _prepare_workspace(_workspace_settings(tmp_path))

    assert prepared.is_dir()
    assert list(prepared.iterdir()) == []


def test_prepare_workspace_sanitizes_an_unusable_root(tmp_path):
    """The raw path is never echoed back; it can name deployment layout."""

    blocker = tmp_path / "workspace"
    blocker.write_bytes(b"not a directory")

    with pytest.raises(StudioRenderRuntimeError) as error:
        _prepare_workspace(_workspace_settings(tmp_path))
    assert str(error.value) == "Studio render workspace is unavailable."
    assert str(tmp_path) not in str(error.value)
