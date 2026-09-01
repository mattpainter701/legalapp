"""Attestation and bound checks for the Studio worker isolation boundary.

Every guard here is fail-closed: an isolation profile is only usable when the
on-disk runtime still matches the digests the server attested, and a processor
adapter refuses limits it cannot enforce. These run without a database or a
real sandbox by building a genuine runtime bundle on a temporary path.
"""

import hashlib
import json
import math
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.studio_worker_isolation import (
    StudioIsolatedInvocation,
    StudioIsolationError,
    StudioIsolationProfile,
    StudioIsolationRegistry,
    StudioSandboxLimits,
    StudioTrustedProcessorAdapter,
    validate_studio_output,
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
    """Write a real runtime bundle so attestation digests are genuine."""

    root = tmp_path / "runtime"
    root.mkdir(parents=True, exist_ok=True)
    for name in _BUNDLE_FILES:
        (root / name).write_bytes(f"studio-{name}-payload".encode("utf-8"))
    return root


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _profile(root: Path, **overrides) -> StudioIsolationProfile:
    values = {
        "profile_id": "studio-render-v1",
        "runtime_root": root,
        "launcher": root / "launcher",
        "launcher_sha256": _digest(root / "launcher"),
        "executable": root / "renderer",
        "executable_sha256": _digest(root / "renderer"),
        "runtime_bundle_manifest": root / "bundle.json",
        "runtime_bundle_sha256": _digest(root / "bundle.json"),
        "font_pack": root / "fonts.dat",
        "font_pack_sha256": _digest(root / "fonts.dat"),
        "renderer_version": "1.0.0",
        "rasterizer": root / "rasterizer",
        "rasterizer_version": "1.0.0",
        "rasterizer_sha256": _digest(root / "rasterizer"),
        "converter": root / "converter",
        "converter_version": "1.0.0",
        "converter_sha256": _digest(root / "converter"),
        "validator": root / "validator",
        "validator_version": "1.0.0",
        "validator_sha256": _digest(root / "validator"),
    }
    values.update(overrides)
    return StudioIsolationProfile(**values)


@pytest.fixture
def runtime_root(tmp_path):
    return _bundle(tmp_path)


# --- registry attestation --------------------------------------------------


def test_registry_accepts_a_bundle_whose_digests_still_match(runtime_root):
    registry = StudioIsolationRegistry([_profile(runtime_root)])

    resolved = registry.resolve(StudioIsolatedInvocation(profile_id="studio-render-v1"))

    assert resolved.profile_id == "studio-render-v1"
    assert resolved.runtime_root == runtime_root
    assert registry.manifest("studio-render-v1").isolation_policy_id == (
        "studio-render-v1"
    )


def test_registry_rejects_two_profiles_sharing_one_identity(runtime_root):
    with pytest.raises(ValueError, match="duplicate"):
        StudioIsolationRegistry([_profile(runtime_root), _profile(runtime_root)])


@pytest.mark.parametrize(
    "profile_id",
    ["", "ab", "Studio-Render", "9studio", "studio render", "x" * 81],
)
def test_registry_rejects_a_malformed_profile_id(runtime_root, profile_id):
    with pytest.raises(ValueError, match="profile id"):
        StudioIsolationRegistry([_profile(runtime_root, profile_id=profile_id)])


def test_registry_rejects_a_boundary_it_cannot_enforce(runtime_root):
    with pytest.raises(StudioIsolationError) as error:
        StudioIsolationRegistry(
            [_profile(runtime_root, boundary_kind="best_effort_v0")]
        )
    assert error.value.code == "isolation_unavailable"


def test_registry_rejects_a_runtime_component_outside_the_attested_root(
    runtime_root, tmp_path
):
    stray = tmp_path / "stray-validator"
    stray.write_bytes(b"stray")

    with pytest.raises(StudioIsolationError) as error:
        StudioIsolationRegistry(
            [_profile(runtime_root, validator=stray, validator_sha256=_digest(stray))]
        )
    assert error.value.code == "isolation_unavailable"


def test_registry_rejects_a_missing_runtime_component(runtime_root):
    profile = _profile(runtime_root)
    (runtime_root / "converter").unlink()

    with pytest.raises(StudioIsolationError) as error:
        StudioIsolationRegistry([profile])
    assert error.value.code == "isolation_unavailable"


def test_registry_rejects_a_digest_that_is_not_a_sha256(runtime_root):
    with pytest.raises(ValueError, match="binary digest"):
        StudioIsolationRegistry([_profile(runtime_root, launcher_sha256="z" * 64)])


@pytest.mark.parametrize(
    ("field", "expected_message"),
    [
        ("launcher_sha256", "sandbox attestation failed"),
        ("executable_sha256", "processor attestation failed"),
        ("runtime_bundle_sha256", "runtime attestation failed"),
        ("font_pack_sha256", "runtime attestation failed"),
        ("rasterizer_sha256", "runtime attestation failed"),
        ("converter_sha256", "runtime attestation failed"),
        ("validator_sha256", "runtime attestation failed"),
    ],
)
def test_registry_rejects_a_component_whose_content_moved(
    runtime_root, field, expected_message
):
    """A swapped binary must be caught even though the path is still valid."""

    with pytest.raises(StudioIsolationError) as error:
        StudioIsolationRegistry([_profile(runtime_root, **{field: "b" * 64})])
    assert expected_message in str(error.value).lower()
    assert error.value.code == "isolation_unavailable"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeout_seconds", 0.05),
        ("timeout_seconds", 1801),
        ("max_stdout_bytes", 0),
        ("max_stdout_bytes", 100 * 1024 * 1024 + 1),
        ("max_stderr_bytes", 0),
        ("max_stderr_bytes", 1024 * 1024 + 1),
        ("max_arguments", -1),
        ("max_arguments", 129),
    ],
)
def test_registry_rejects_out_of_range_process_bounds(runtime_root, field, value):
    with pytest.raises(ValueError, match="sandbox"):
        StudioIsolationRegistry([_profile(runtime_root, **{field: value})])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cpu_seconds", 0),
        ("cpu_seconds", 1801),
        ("max_memory_bytes", 15 * 1024 * 1024),
        ("max_memory_bytes", 9 * 1024**3),
        ("max_processes", 0),
        ("max_processes", 129),
        ("max_open_files", 15),
        ("max_open_files", 4097),
    ],
)
def test_registry_rejects_out_of_range_sandbox_limits(runtime_root, field, value):
    limits = StudioSandboxLimits(**{field: value})

    with pytest.raises(ValueError, match="sandbox"):
        StudioIsolationRegistry([_profile(runtime_root, limits=limits)])


@pytest.mark.parametrize("version", ["", "1.0.0 beta", "v1/2", "x" * 81, "présent"])
def test_registry_rejects_an_unprintable_processor_version(runtime_root, version):
    with pytest.raises(ValueError, match="processor version"):
        StudioIsolationRegistry([_profile(runtime_root, renderer_version=version)])


@pytest.mark.parametrize(
    "key",
    ["PATH", "PYTHONPATH", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "lower", "1BAD"],
)
def test_registry_rejects_an_unsafe_sandbox_environment_key(runtime_root, key):
    with pytest.raises(ValueError, match="environment key"):
        StudioIsolationRegistry([_profile(runtime_root, environment={key: "value"})])


@pytest.mark.parametrize("value", ["has\x00null", "x" * 4097])
def test_registry_rejects_an_unsafe_sandbox_environment_value(runtime_root, value):
    with pytest.raises(ValueError, match="environment value"):
        StudioIsolationRegistry([_profile(runtime_root, environment={"LANG": value})])


# --- invocation-time re-attestation ---------------------------------------


def test_resolve_refuses_an_unregistered_profile(runtime_root):
    registry = StudioIsolationRegistry([_profile(runtime_root)])

    with pytest.raises(StudioIsolationError) as error:
        registry.resolve(StudioIsolatedInvocation(profile_id="studio-other-v1"))
    assert error.value.code == "isolation_unavailable"


def test_resolve_refuses_more_arguments_than_the_profile_allows(runtime_root):
    registry = StudioIsolationRegistry([_profile(runtime_root, max_arguments=2)])

    with pytest.raises(StudioIsolationError) as error:
        registry.resolve(
            StudioIsolatedInvocation(
                profile_id="studio-render-v1", arguments=("a", "b", "c")
            )
        )
    assert error.value.code == "invalid_invocation"


@pytest.mark.parametrize("argument", ["has\x00null", "x" * 4097, 7])
def test_resolve_refuses_a_malformed_argument(runtime_root, argument):
    registry = StudioIsolationRegistry([_profile(runtime_root)])

    with pytest.raises(StudioIsolationError) as error:
        registry.resolve(
            StudioIsolatedInvocation(
                profile_id="studio-render-v1", arguments=(argument,)
            )
        )
    assert error.value.code == "invalid_invocation"


def test_resolve_detects_a_launcher_replaced_after_registration(runtime_root):
    """Registration attested the bundle; resolve must re-check every call."""

    registry = StudioIsolationRegistry([_profile(runtime_root)])
    (runtime_root / "launcher").write_bytes(b"replaced-after-startup")

    with pytest.raises(StudioIsolationError) as error:
        registry.resolve(StudioIsolatedInvocation(profile_id="studio-render-v1"))
    assert error.value.code == "isolation_unavailable"


def test_resolve_fails_closed_when_the_runtime_cannot_be_read(runtime_root):
    registry = StudioIsolationRegistry([_profile(runtime_root)])

    with patch(
        "app.services.studio_worker_isolation._file_sha256",
        side_effect=OSError("device is unavailable"),
    ):
        with pytest.raises(StudioIsolationError) as error:
            registry.resolve(StudioIsolatedInvocation(profile_id="studio-render-v1"))
    assert error.value.code == "isolation_unavailable"


# --- processor adapter -----------------------------------------------------


def _adapter(runtime_root: Path, workspace: Path, **overrides):
    registry = StudioIsolationRegistry([_profile(runtime_root)])
    return StudioTrustedProcessorAdapter(
        registry, "studio-render-v1", workspace_root=workspace, **overrides
    )


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / "workspace"
    path.mkdir()
    return path


def test_adapter_binds_the_attested_manifest_to_the_processor(runtime_root, workspace):
    adapter = _adapter(runtime_root, workspace, artifact_kind="test_render")

    assert adapter.isolation_policy_id == "studio-render-v1"
    assert adapter.workspace_root == workspace.absolute()
    assert adapter.runtime_manifest_sha256 == adapter.renderer_manifest.sha256
    assert adapter.attest_runtime().sha256 == adapter.runtime_manifest_sha256


def test_adapter_is_immutable_after_construction(runtime_root, workspace):
    adapter = _adapter(runtime_root, workspace)

    with pytest.raises(AttributeError, match="immutable"):
        adapter.artifact_kind = "analysis"


def test_adapter_requires_an_existing_workspace_root(runtime_root, tmp_path):
    with pytest.raises(StudioIsolationError) as error:
        _adapter(runtime_root, tmp_path / "absent-workspace")
    assert error.value.code == "invalid_workspace"


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"artifact_kind": "screenshot"}, "artifact kind"),
        ({"retention_class": "forever"}, "retention class"),
        ({"max_source_bytes": 0}, "source limit"),
        ({"max_source_bytes": 100 * 1024 * 1024 + 1}, "source limit"),
        ({"max_binding_bytes": 0}, "binding limit"),
        ({"max_binding_bytes": 100 * 1024 * 1024 + 1}, "binding limit"),
        ({"max_metadata_bytes": 0}, "metadata limit"),
        ({"max_metadata_bytes": 4 * 1024 * 1024 + 1}, "metadata limit"),
    ],
)
def test_adapter_rejects_limits_it_cannot_enforce(
    runtime_root, workspace, kwargs, expected
):
    with pytest.raises(ValueError, match=expected):
        _adapter(runtime_root, workspace, **kwargs)


def test_adapter_reports_a_runtime_that_changed_under_it(runtime_root, workspace):
    adapter = _adapter(runtime_root, workspace)
    other = adapter.renderer_manifest.model_copy(update={"launcher_sha256": "c" * 64})

    # The registry seals its own attributes, so patch the class it resolves on.
    with patch.object(StudioIsolationRegistry, "manifest", return_value=other):
        with pytest.raises(StudioIsolationError) as error:
            adapter.attest_runtime()
    assert error.value.code == "isolation_unavailable"


@pytest.mark.parametrize(
    "value",
    [
        pytest.param({"nan": math.nan}, id="not-a-number"),
        pytest.param({"set": {1, 2}}, id="unserializable"),
    ],
)
def test_adapter_refuses_metadata_it_cannot_canonicalize(value):
    with pytest.raises(StudioIsolationError) as error:
        StudioTrustedProcessorAdapter._json_bytes(value, limit=1024)
    assert error.value.code == "invalid_invocation"


def test_adapter_refuses_metadata_beyond_its_limit():
    with pytest.raises(StudioIsolationError) as error:
        StudioTrustedProcessorAdapter._json_bytes({"k": "v" * 100}, limit=16)
    assert error.value.code == "invalid_invocation"


def test_adapter_canonicalizes_metadata_deterministically():
    encoded = StudioTrustedProcessorAdapter._json_bytes({"b": 2, "a": 1}, limit=1024)

    assert encoded == b'{"a":1,"b":2}'


# --- validator evidence ----------------------------------------------------


def _report(content: bytes, **overrides) -> bytes:
    document = {
        "contract_version": 1,
        "artifact_sha256": hashlib.sha256(content).hexdigest(),
        "artifact_kind": "test_render",
        "media_type": "application/pdf",
        "artifact_page_count": 1,
        "document_page_count": 1,
        "pages": [{"page_number": 1, "width_points": 612, "height_points": 792}],
    }
    document.update(overrides)
    return json.dumps(document).encode("utf-8")


def _validate(content: bytes, report: bytes, **kwargs):
    options = {
        "content": content,
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "artifact_kind": "test_render",
        "media_type": "application/pdf",
        "max_pages": 10,
    }
    options.update(kwargs)
    return validate_studio_output(report, **options)


def test_validator_evidence_produces_a_canonical_geometry_manifest():
    content = b"%PDF-1.7 body"

    validated = _validate(content, _report(content))

    assert validated.artifact_page_count == 1
    assert validated.document_page_count == 1
    assert validated.geometry_manifest_sha256 == validated.geometry_manifest.sha256
    assert validated.geometry_manifest.pages[0].coordinate_space == "points"


@pytest.mark.parametrize(
    ("content", "report", "max_pages"),
    [
        pytest.param(b"body", b"", 10, id="empty-report"),
        pytest.param(b"body", b"x" * (1024 * 1024 + 1), 10, id="oversized-report"),
        pytest.param(b"", b"{}", 10, id="empty-content"),
        pytest.param(b"body", b"{}", 0, id="zero-max-pages"),
        pytest.param(b"body", b"{}", 1001, id="excessive-max-pages"),
    ],
)
def test_validator_rejects_evidence_outside_its_boundary(content, report, max_pages):
    with pytest.raises(StudioIsolationError) as error:
        _validate(content, report, max_pages=max_pages)
    assert error.value.code == "validation_failed"


def test_validator_rejects_a_digest_that_does_not_describe_the_content():
    content = b"%PDF-1.7 body"

    with pytest.raises(StudioIsolationError) as error:
        _validate(content, _report(content), content_sha256="d" * 64)
    assert error.value.code == "validation_failed"


def test_validator_rejects_a_report_with_unexpected_keys():
    content = b"%PDF-1.7 body"
    document = json.loads(_report(content))
    document["extra_field"] = 1

    with pytest.raises(StudioIsolationError) as error:
        _validate(content, json.dumps(document).encode("utf-8"))
    assert error.value.code == "validation_failed"


@pytest.mark.parametrize(
    "page",
    [
        pytest.param(
            {"page_number": 1, "width_points": "612", "height_points": 792},
            id="string-dimension",
        ),
        pytest.param(
            {"page_number": 1, "width_points": True, "height_points": 792},
            id="boolean-dimension",
        ),
        pytest.param(
            {"page_number": 1, "width_points": 0, "height_points": 792},
            id="non-positive-dimension",
        ),
        pytest.param(
            {"page_number": 1, "width_points": 1e9, "height_points": 792},
            id="unbounded-dimension",
        ),
    ],
)
def test_validator_rejects_a_page_value_it_cannot_trust(page):
    content = b"%PDF-1.7 body"

    with pytest.raises(StudioIsolationError) as error:
        _validate(content, _report(content, pages=[page]))
    assert error.value.code == "validation_failed"


def _png(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + (0).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
    )


def _preview_report(content: bytes, *, page_number: int = 2, **overrides) -> bytes:
    document = {
        "contract_version": 1,
        "artifact_sha256": hashlib.sha256(content).hexdigest(),
        "artifact_kind": "page_preview",
        "media_type": "image/png",
        "artifact_page_count": 1,
        "document_page_count": 5,
        "pages": [
            {
                "page_number": page_number,
                "width_px": 800,
                "height_px": 600,
                "dpi_x": 96,
                "dpi_y": 96,
            }
        ],
    }
    document.update(overrides)
    return json.dumps(document).encode("utf-8")


def _validate_preview(content: bytes, report: bytes, **kwargs):
    options = {
        "content": content,
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "artifact_kind": "page_preview",
        "media_type": "image/png",
        "max_pages": 10,
        "page_number": 2,
    }
    options.update(kwargs)
    return validate_studio_output(report, **options)


def test_preview_evidence_matches_the_rendered_png_header():
    content = _png(800, 600)

    validated = _validate_preview(content, _preview_report(content))

    assert validated.artifact_page_count == 1
    assert validated.document_page_count == 5
    assert validated.geometry_manifest.pages[0].coordinate_space == "pixels"


def test_preview_evidence_requires_a_requested_page_number():
    content = _png(800, 600)

    with pytest.raises(StudioIsolationError) as error:
        _validate_preview(
            content, _preview_report(content, page_number=1), page_number=None
        )
    assert error.value.code == "validation_failed"


def test_preview_page_cannot_exceed_the_source_document():
    content = _png(800, 600)
    report = _preview_report(content, page_number=9, document_page_count=5)

    with pytest.raises(StudioIsolationError) as error:
        _validate_preview(content, report, page_number=9)
    assert error.value.code == "validation_failed"


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4, id="truncated"),
        pytest.param(b"GIF89a" + b"\x00" * 32, id="not-a-png"),
        pytest.param(
            b"\x89PNG\r\n\x1a\n" + (0).to_bytes(4, "big") + b"BAAD" + b"\x00" * 8,
            id="missing-ihdr",
        ),
    ],
)
def test_preview_rejects_bytes_that_are_not_the_attested_image(content):
    with pytest.raises(StudioIsolationError) as error:
        _validate_preview(content, _preview_report(content))
    assert error.value.code == "validation_failed"


def test_preview_rejects_geometry_that_contradicts_the_png_header():
    content = _png(800, 600)
    report = _preview_report(content)
    document = json.loads(report)
    document["pages"][0]["width_px"] = 1024

    with pytest.raises(StudioIsolationError) as error:
        _validate_preview(content, json.dumps(document).encode("utf-8"))
    assert error.value.code == "validation_failed"
