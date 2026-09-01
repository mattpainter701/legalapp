"""Construction bounds for the Studio render worker.

The worker facade's runtime behaviour is covered in
``test_studio_render_worker_unit.py``; this file covers only the numeric
limits it refuses at construction.

The worker holds a fenced lease over tenant data while driving an isolated
processor, so it refuses at construction any limit it could not enforce later:
a lease it cannot heartbeat inside, a retention window that would strand
metadata, or a processor without an attested runtime.
"""

import hashlib
from pathlib import Path

import pytest

from app.services.studio_render_worker import StudioRenderWorker
from app.services.studio_worker_isolation import (
    StudioIsolationProfile,
    StudioIsolationRegistry,
    StudioTrustedProcessorAdapter,
)

_BUNDLE_FILES = (
    "launcher",
    "renderer",
    "bundle.json",
    "fonts.dat",
    "rasterizer",
    "converter",
    "validator",
)


def _bundle(tmp_path: Path) -> Path:
    root = tmp_path / "runtime"
    root.mkdir(parents=True, exist_ok=True)
    for name in _BUNDLE_FILES:
        (root / name).write_bytes(f"studio-{name}-payload".encode("utf-8"))
    return root


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def processor(tmp_path):
    """A genuinely attested adapter — the worker rejects any other type."""

    root = _bundle(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = StudioIsolationRegistry(
        [
            StudioIsolationProfile(
                profile_id="studio-render-v1",
                runtime_root=root,
                launcher=root / "launcher",
                launcher_sha256=_digest(root / "launcher"),
                executable=root / "renderer",
                executable_sha256=_digest(root / "renderer"),
                runtime_bundle_manifest=root / "bundle.json",
                runtime_bundle_sha256=_digest(root / "bundle.json"),
                font_pack=root / "fonts.dat",
                font_pack_sha256=_digest(root / "fonts.dat"),
                renderer_version="1.0.0",
                rasterizer=root / "rasterizer",
                rasterizer_version="1.0.0",
                rasterizer_sha256=_digest(root / "rasterizer"),
                converter=root / "converter",
                converter_version="1.0.0",
                converter_sha256=_digest(root / "converter"),
                validator=root / "validator",
                validator_version="1.0.0",
                validator_sha256=_digest(root / "validator"),
            )
        ]
    )
    return StudioTrustedProcessorAdapter(
        registry, "studio-render-v1", workspace_root=workspace
    )


def _worker(**overrides):
    values = {
        "object_store": object(),
        "processors": {},
    }
    values.update(overrides)
    return StudioRenderWorker(object(), **values)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("lease_seconds", 29, "lease_seconds"),
        ("lease_seconds", 3601, "lease_seconds"),
        ("heartbeat_seconds", 4, "heartbeat_seconds"),
        ("heartbeat_seconds", 450, "heartbeat_seconds"),
        ("processor_timeout_seconds", 4, "processor timeout"),
        ("processor_timeout_seconds", 1801, "processor timeout"),
        ("artifact_ttl_seconds", 299, "artifact TTL"),
        ("artifact_ttl_seconds", 604_801, "artifact TTL"),
        ("metadata_ttl_seconds", 86_399, "metadata TTL"),
        ("metadata_ttl_seconds", 31_536_001, "metadata TTL"),
        ("max_input_binding_bytes", 0, "input binding limit"),
        ("max_input_binding_bytes", 100 * 1024 * 1024 + 1, "input binding limit"),
        ("retained_artifact_limit", 0, "retained artifact limit"),
        ("retained_artifact_limit", 100_001, "retained artifact limit"),
        ("retained_byte_limit", 0, "retained byte limit"),
        ("retained_byte_limit", 10 * 1024**4 + 1, "retained byte limit"),
    ],
)
def test_worker_refuses_a_bound_it_could_not_enforce(field, value, expected):
    with pytest.raises(ValueError, match=expected):
        _worker(**{field: value})


def test_worker_requires_a_heartbeat_inside_half_the_lease():
    """A heartbeat at or past half the lease cannot renew before expiry."""

    with pytest.raises(ValueError, match="below half the lease"):
        _worker(lease_seconds=60, heartbeat_seconds=30)


def test_worker_requires_metadata_to_outlive_artifact_bytes():
    with pytest.raises(ValueError, match="metadata TTL must outlive"):
        _worker(artifact_ttl_seconds=604_800, metadata_ttl_seconds=604_800)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("live_artifact_limit", 1, "live artifact limit"),
        ("live_artifact_limit", 100_001, "live artifact limit"),
        ("live_byte_limit", 1, "live byte limit"),
        ("live_byte_limit", 10 * 1024**4 + 1, "live byte limit"),
    ],
)
def test_worker_requires_live_limits_to_cover_retained_limits(field, value, expected):
    with pytest.raises(ValueError, match=expected):
        _worker(retained_artifact_limit=500, retained_byte_limit=1024, **{field: value})


@pytest.mark.parametrize(
    "processors",
    [
        pytest.param({"studio_test_render": object()}, id="unattested-object"),
        pytest.param(
            {"studio_test_render": type("Fake", (), {})()}, id="look-alike-class"
        ),
    ],
)
def test_worker_refuses_a_processor_without_an_attested_runtime(processors):
    with pytest.raises(ValueError, match="attested runtime"):
        _worker(processors=processors)


def test_worker_accepts_an_attested_processor_keyed_by_dispatch_tuple(processor):
    key = ("studio_test_render", "markdown", 1)

    worker = _worker(processors={key: processor})

    assert worker._processors[key] is processor


def test_worker_expands_a_kind_key_across_every_source_format(processor):
    worker = _worker(processors={"studio_test_render": processor})

    assert set(worker._processors) == {
        ("studio_test_render", source_format, 1)
        for source_format in ("markdown", "docx", "pdf")
    }


def test_worker_ignores_a_key_that_is_not_a_dispatch_target(processor):
    worker = _worker(processors={"not_a_studio_kind": processor})

    assert dict(worker._processors) == {}
