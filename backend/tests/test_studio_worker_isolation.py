"""Hostile invocation and bounded-process checks for Studio isolation."""

import asyncio
import hashlib
import io
import json
import os
import sys
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pypdf import PdfWriter

from app.services.studio_worker_isolation import (
    StudioIsolatedInvocation,
    StudioIsolatedResult,
    StudioIsolationError,
    StudioIsolationProfile,
    StudioIsolationRegistry,
    StudioSandboxLimits,
    StudioTrustedProcessorAdapter,
    _terminate_process_tree,
    run_isolated_process,
    validate_studio_output,
)

def _profile(tmp_path, **updates):
    launcher = tmp_path / "sandbox-launcher.bin"
    executable = tmp_path / "renderer.bin"
    runtime_bundle = tmp_path / "runtime.bundle.manifest"
    font_pack = tmp_path / "fonts.bundle"
    rasterizer = tmp_path / "rasterizer.bin"
    converter = tmp_path / "converter.bin"
    validator = tmp_path / "validator.bin"
    launcher.write_bytes(b"verified sandbox")
    executable.write_bytes(b"renderer")
    runtime_bundle.write_bytes(b"runtime bundle v1")
    font_pack.write_bytes(b"fonts")
    rasterizer.write_bytes(b"rasterizer")
    converter.write_bytes(b"converter")
    validator.write_bytes(b"validator")
    values = {
        "profile_id": "studio-test-v1",
        "runtime_root": tmp_path.absolute(),
        "launcher": launcher.absolute(),
        "launcher_sha256": hashlib.sha256(launcher.read_bytes()).hexdigest(),
        "executable": executable.absolute(),
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "runtime_bundle_manifest": runtime_bundle.absolute(),
        "runtime_bundle_sha256": hashlib.sha256(
            runtime_bundle.read_bytes()
        ).hexdigest(),
        "font_pack": font_pack.absolute(),
        "font_pack_sha256": hashlib.sha256(font_pack.read_bytes()).hexdigest(),
        "renderer_version": "1.0.0",
        "rasterizer": rasterizer.absolute(),
        "rasterizer_version": "1.0.0",
        "rasterizer_sha256": hashlib.sha256(rasterizer.read_bytes()).hexdigest(),
        "converter": converter.absolute(),
        "converter_version": "1.0.0",
        "converter_sha256": hashlib.sha256(converter.read_bytes()).hexdigest(),
        "validator": validator.absolute(),
        "validator_version": "1.0.0",
        "validator_sha256": hashlib.sha256(validator.read_bytes()).hexdigest(),
        "timeout_seconds": 1,
        "max_stdout_bytes": 32,
        "max_stderr_bytes": 16,
    }
    values.update(updates)
    return StudioIsolationProfile(**values)


def _pdf_bytes():
    stream = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(stream)
    return stream.getvalue()


def _validator_report(
    content: bytes,
    *,
    artifact_kind="test_render",
    media_type="application/pdf",
    pages=None,
    document_page_count=None,
):
    pages = pages or [{"page_number": 1, "width_points": 612, "height_points": 792}]
    document_page_count = document_page_count or max(
        page["page_number"] for page in pages
    )
    return json.dumps(
        {
            "contract_version": 1,
            "artifact_sha256": hashlib.sha256(content).hexdigest(),
            "artifact_kind": artifact_kind,
            "media_type": media_type,
            "artifact_page_count": len(pages),
            "document_page_count": document_page_count,
            "pages": pages,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _actual_process_profile(tmp_path, renderer_source, **updates):
    profile = _profile(tmp_path)
    launcher = Path(profile.launcher)
    renderer = Path(profile.executable)
    launcher.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "arguments = sys.argv[1:]\n"
        "separator = arguments.index('--')\n"
        "target = arguments[separator + 1:]\n"
        "os.execv(target[0], target)\n",
        encoding="utf-8",
    )
    renderer.write_text(
        f"#!{sys.executable}\n{renderer_source}", encoding="utf-8"
    )
    launcher.chmod(0o700)
    renderer.chmod(0o700)
    return replace(
        profile,
        launcher_sha256=hashlib.sha256(launcher.read_bytes()).hexdigest(),
        executable_sha256=hashlib.sha256(renderer.read_bytes()).hexdigest(),
        **updates,
    )


def test_registry_fails_closed_without_code_owned_boundary(tmp_path):
    with pytest.raises(StudioIsolationError) as caught:
        StudioIsolationRegistry(
            [_profile(tmp_path, boundary_kind="caller_attested")]
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

    component_profile = _profile(tmp_path)
    component_registry = StudioIsolationRegistry([component_profile])
    Path(component_profile.validator).write_bytes(b"replaced validator")
    with pytest.raises(StudioIsolationError, match="attestation"):
        component_registry.resolve(
            StudioIsolatedInvocation(profile_id=component_profile.profile_id)
        )

    bundle_profile = _profile(tmp_path)
    bundle_registry = StudioIsolationRegistry([bundle_profile])
    Path(bundle_profile.runtime_bundle_manifest).write_bytes(b"replaced bundle")
    with pytest.raises(StudioIsolationError, match="attestation"):
        bundle_registry.resolve(
            StudioIsolatedInvocation(profile_id=bundle_profile.profile_id)
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
    manifest = registry.manifest(profile.profile_id)
    assert manifest.renderer.content_sha256 == profile.executable_sha256
    assert manifest.sha256
    changed_limits = StudioIsolationRegistry(
        [
            _profile(
                tmp_path,
                limits=StudioSandboxLimits(cpu_seconds=61),
            )
        ]
    ).manifest(profile.profile_id)
    assert changed_limits.sandbox_policy_sha256 != manifest.sandbox_policy_sha256
    assert changed_limits.sha256 != manifest.sha256
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

    async def isolated(
        _registry,
        invocation,
        *,
        workspace,
        stdout_limit=None,
        _component="renderer",
    ):
        working = Path(workspace)
        observed["workspace"] = working
        if _component == "validator":
            assert (working / "output.bin").read_bytes() == _pdf_bytes()
            assert stdout_limit == adapter.max_metadata_bytes
            return StudioIsolatedResult(
                stdout=_validator_report(_pdf_bytes()), stderr=b""
            )
        observed["arguments"] = invocation.arguments
        observed["stdout_limit"] = stdout_limit
        assert (working / "source.bin").read_bytes() == b"source"
        assert b"signed_url" in (working / "snapshot.json").read_bytes()
        assert (working / "options.json").is_file()
        return StudioIsolatedResult(stdout=_pdf_bytes(), stderr=b"")

    with patch(
        "app.services.studio_worker_isolation.run_isolated_process",
        isolated,
    ):
        output = await adapter.process(
            source=b"source",
            snapshot={"value": "$(signed_url)"},
            options={"flatten_pdf": False, "max_output_bytes": 4096},
            input_binding=None,
        )
    assert "signed_url" not in " ".join(observed["arguments"])
    assert observed["stdout_limit"] == 4096
    assert not observed["workspace"].exists()
    assert output.content == _pdf_bytes()
    assert output.content_sha256 == hashlib.sha256(_pdf_bytes()).hexdigest()
    assert output.artifact_page_count == 1
    assert output.document_page_count == 1
    assert output.geometry_manifest.sha256 == output.geometry_manifest_sha256
    assert output.runtime_manifest_sha256 == output.renderer_manifest.sha256


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
        self.pid = None

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
    assert "--deny-network" in args
    assert "--kill-process-tree" in args
    assert "--verify-executable-sha256" in args
    assert "--runtime-bundle-manifest" in args
    assert "--verify-runtime-bundle-sha256" in args
    for component in ("font-pack", "rasterizer", "converter", "validator"):
        assert f"--verify-{component}-sha256" in args
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


@pytest.mark.asyncio
async def test_request_output_limit_is_enforced_before_profile_limit(tmp_path):
    profile = _profile(tmp_path, max_stdout_bytes=32)
    process = _FakeProcess(stdout=b"12345", wait_forever=True)
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        with pytest.raises(StudioIsolationError) as caught:
            await run_isolated_process(
                StudioIsolationRegistry([profile]),
                StudioIsolatedInvocation(profile_id=profile.profile_id),
                workspace=tmp_path,
                stdout_limit=4,
            )
    assert caught.value.code == "processor_output_limit"
    assert process.killed is True


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group integration")
@pytest.mark.asyncio
async def test_real_supervised_process_overflow_is_terminated(tmp_path):
    profile = _actual_process_profile(
        tmp_path,
        "import os, signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "os.write(1, b'x' * 4096)\n"
        "time.sleep(30)\n",
        max_stdout_bytes=64,
        timeout_seconds=5,
    )
    with pytest.raises(StudioIsolationError) as caught:
        await run_isolated_process(
            StudioIsolationRegistry([profile]),
            StudioIsolatedInvocation(profile_id=profile.profile_id),
            workspace=tmp_path,
        )
    assert caught.value.code == "processor_output_limit"


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group integration")
@pytest.mark.asyncio
async def test_repeated_cancellation_kills_real_process_before_workspace_cleanup(
    tmp_path,
):
    heartbeat = tmp_path / "descendant-heartbeat.txt"
    child_pid = tmp_path / "descendant.pid"
    child_source = (
        "import signal, time\n"
        "from pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"path = Path({str(heartbeat)!r})\n"
        "while True:\n"
        "    path.write_text(str(time.time()), encoding='utf-8')\n"
        "    time.sleep(0.02)\n"
    )
    profile = _actual_process_profile(
        tmp_path,
        "import signal, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"child = subprocess.Popen([sys.executable, '-c', {child_source!r}])\n"
        f"Path({str(child_pid)!r}).write_text(str(child.pid), encoding='utf-8')\n"
        "time.sleep(30)\n",
        timeout_seconds=10,
        max_stdout_bytes=4096,
    )
    adapter = StudioTrustedProcessorAdapter(
        StudioIsolationRegistry([profile]),
        profile.profile_id,
        workspace_root=tmp_path,
    )
    task = asyncio.create_task(
        adapter.process(
            source=b"source",
            snapshot={},
            options={"max_output_bytes": 4096, "max_pages": 1},
            input_binding=None,
        )
    )
    for _ in range(100):
        if list(tmp_path.glob("studio-render-*")) and heartbeat.exists():
            break
        await asyncio.sleep(0.01)
    assert list(tmp_path.glob("studio-render-*"))
    assert heartbeat.exists() and child_pid.exists()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=8)
    assert not list(tmp_path.glob("studio-render-*"))
    stopped_at = heartbeat.read_text(encoding="utf-8")
    await asyncio.sleep(0.15)
    assert heartbeat.read_text(encoding="utf-8") == stopped_at


@pytest.mark.asyncio
async def test_posix_termination_targets_the_process_group():
    process = _FakeProcess(wait_forever=True)
    process.pid = 424242
    with patch(
        "app.services.studio_worker_isolation.os.name", "posix"
    ), patch(
        "app.services.studio_worker_isolation.os.killpg", create=True
    ) as killpg:
        killpg.side_effect = lambda _pid, sig: (
            setattr(process, "killed", True) if sig == 9 else None
        )
        await _terminate_process_tree(process)
    assert killpg.call_args_list[0].args == (process.pid, 15)
    assert killpg.call_args_list[1].args == (process.pid, 9)


def test_kind_specific_output_parsers_fail_closed():
    analysis_content = b'{"contract_version":1,"pages":[{"page_number":1}]}'
    analysis = validate_studio_output(
        _validator_report(
            analysis_content,
            artifact_kind="analysis",
            media_type="application/json",
            pages=[{"page_number": 1}],
        ),
        content=analysis_content,
        content_sha256=hashlib.sha256(analysis_content).hexdigest(),
        artifact_kind="analysis",
        media_type="application/json",
        max_pages=10,
    )
    assert analysis.artifact_page_count == 1
    pdf = validate_studio_output(
        _validator_report(_pdf_bytes()),
        content=_pdf_bytes(),
        content_sha256=hashlib.sha256(_pdf_bytes()).hexdigest(),
        artifact_kind="test_render",
        media_type="application/pdf",
        max_pages=10,
    )
    assert pdf.artifact_page_count == 1
    with pytest.raises(StudioIsolationError) as malformed:
        validate_studio_output(
            _validator_report(b"different"),
            content=b"%PDF-hostile without trailer",
            content_sha256=hashlib.sha256(
                b"%PDF-hostile without trailer"
            ).hexdigest(),
            artifact_kind="test_render",
            media_type="application/pdf",
            max_pages=10,
        )
    assert malformed.value.code == "validation_failed"

    incomplete_pdf = _validator_report(
        _pdf_bytes(), pages=[{"page_number": 1}]
    )
    with pytest.raises(StudioIsolationError) as missing_pdf_geometry:
        validate_studio_output(
            incomplete_pdf,
            content=_pdf_bytes(),
            content_sha256=hashlib.sha256(_pdf_bytes()).hexdigest(),
            artifact_kind="test_render",
            media_type="application/pdf",
            max_pages=10,
        )
    assert missing_pdf_geometry.value.code == "validation_failed"

    incomplete_preview = _validator_report(
        b"preview",
        artifact_kind="page_preview",
        media_type="image/png",
        pages=[{"page_number": 1, "width_px": 10, "height_px": 20}],
    )
    with pytest.raises(StudioIsolationError) as missing_preview_geometry:
        validate_studio_output(
            incomplete_preview,
            content=b"preview",
            content_sha256=hashlib.sha256(b"preview").hexdigest(),
            artifact_kind="page_preview",
            media_type="image/png",
            max_pages=10,
            page_number=1,
        )
    assert missing_preview_geometry.value.code == "validation_failed"


@pytest.mark.parametrize(
    "pages",
    [
        [
            {
                "page_number": 1,
                "width_points": 612,
                "height_points": 792,
                "width_px": 1,
            }
        ],
        [
            {"page_number": 2, "width_points": 612, "height_points": 792},
            {"page_number": 1, "width_points": 612, "height_points": 792},
        ],
        [{"page_number": 1, "width_points": float("nan"), "height_points": 792}],
        [{"page_number": 1, "width_points": float("inf"), "height_points": 792}],
        [{"page_number": 1, "width_points": 0, "height_points": 792}],
        [{"page_number": 1, "width_points": -1, "height_points": 792}],
        [{"page_number": 1, "width_points": 100_000_001, "height_points": 792}],
    ],
)
def test_validator_rejects_extra_misordered_and_unbounded_geometry(pages):
    content = _pdf_bytes()
    with pytest.raises(StudioIsolationError) as caught:
        validate_studio_output(
            _validator_report(content, pages=pages),
            content=content,
            content_sha256=hashlib.sha256(content).hexdigest(),
            artifact_kind="test_render",
            media_type="application/pdf",
            max_pages=10,
        )
    assert caught.value.code == "validation_failed"


def test_preview_report_geometry_must_match_png_bytes():
    from PIL import Image

    stream = io.BytesIO()
    Image.new("RGB", (20, 30), "white").save(
        stream, format="PNG", dpi=(150, 150)
    )
    content = stream.getvalue()
    report = _validator_report(
        content,
        artifact_kind="page_preview",
        media_type="image/png",
        pages=[
            {
                "page_number": 3,
                "width_px": 21,
                "height_px": 30,
                "dpi_x": 150,
                "dpi_y": 150,
            }
        ],
    )
    with pytest.raises(StudioIsolationError) as caught:
        validate_studio_output(
            report,
            content=content,
            content_sha256=hashlib.sha256(content).hexdigest(),
            artifact_kind="page_preview",
            media_type="image/png",
            max_pages=10,
            page_number=3,
        )
    assert caught.value.code == "validation_failed"


def test_png_decode_dimensions_dpi_and_mapping_are_authoritative():
    from PIL import Image

    stream = io.BytesIO()
    Image.new("RGB", (20, 30), "white").save(
        stream, format="PNG", dpi=(150, 150)
    )
    validated = validate_studio_output(
        _validator_report(
            stream.getvalue(),
            artifact_kind="page_preview",
            media_type="image/png",
            pages=[
                {
                    "page_number": 3,
                    "width_px": 20,
                    "height_px": 30,
                    "dpi_x": 150,
                    "dpi_y": 150,
                }
            ],
        ),
        content=stream.getvalue(),
        content_sha256=hashlib.sha256(stream.getvalue()).hexdigest(),
        artifact_kind="page_preview",
        media_type="image/png",
        max_pages=10,
        page_number=3,
    )
    assert validated.artifact_page_count == 1
    assert validated.document_page_count == 3
    assert len(validated.geometry_manifest_sha256) == 64
    assert validated.geometry_manifest.pages[0].coordinate_space == "pixels"


def test_validator_rejects_impossible_document_count_or_preview_page():
    content = _pdf_bytes()
    with pytest.raises(StudioIsolationError):
        validate_studio_output(
            _validator_report(content, document_page_count=2),
            content=content,
            content_sha256=hashlib.sha256(content).hexdigest(),
            artifact_kind="test_render",
            media_type="application/pdf",
            max_pages=10,
        )
    from PIL import Image

    stream = io.BytesIO()
    Image.new("RGB", (20, 30), "white").save(stream, format="PNG")
    preview = stream.getvalue()
    with pytest.raises(StudioIsolationError):
        validate_studio_output(
            _validator_report(
                preview,
                artifact_kind="page_preview",
                media_type="image/png",
                pages=[
                    {
                        "page_number": 2,
                        "width_px": 20,
                        "height_px": 30,
                        "dpi_x": 96,
                        "dpi_y": 96,
                    }
                ],
                document_page_count=2,
            ),
            content=preview,
            content_sha256=hashlib.sha256(preview).hexdigest(),
            artifact_kind="page_preview",
            media_type="image/png",
            max_pages=10,
            page_number=1,
        )


def test_docx_package_parser_and_page_bound():
    from docx import Document

    stream = io.BytesIO()
    document = Document()
    document.add_paragraph("bounded output")
    document.save(stream)
    validated = validate_studio_output(
        _validator_report(
            stream.getvalue(),
            media_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            pages=[
                {"page_number": 1, "width_points": 612, "height_points": 792},
                {"page_number": 2, "width_points": 612, "height_points": 792},
            ],
        ),
        content=stream.getvalue(),
        content_sha256=hashlib.sha256(stream.getvalue()).hexdigest(),
        artifact_kind="test_render",
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        max_pages=10,
    )
    assert validated.artifact_page_count == 2
    assert validated.document_page_count == 2
