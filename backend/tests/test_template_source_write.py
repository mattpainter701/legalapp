from pathlib import Path
import uuid

import pytest

from app.routers import document_templates as router


@pytest.mark.asyncio
@pytest.mark.parametrize("uuid_tenant", [True, False])
async def test_source_write_is_exclusive_and_preserves_existing_evidence(
    tmp_path, monkeypatch, uuid_tenant
):
    monkeypatch.setattr(router.settings, "UPLOAD_DIR", str(tmp_path))
    tenant_id = uuid.uuid4()
    params = dict(
        tenant_id=tenant_id if uuid_tenant else str(tenant_id),
        template_id=uuid.uuid4(),
        filename="original.docx",
        content=b"original upload",
    )
    path = await router._persist_template_source(**params)
    with pytest.raises(FileExistsError):
        await router._persist_template_source(**{**params, "content": b"replacement"})
    assert Path(path).read_bytes() == b"original upload"


@pytest.mark.asyncio
async def test_source_write_failure_removes_partial_file(tmp_path, monkeypatch):
    monkeypatch.setattr(router.settings, "UPLOAD_DIR", str(tmp_path))
    normal_open = Path.open

    class FailingWrite:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.handle.close()

        def write(self, content):
            self.handle.write(content[:3])
            raise OSError("synthetic disk full")

    def open_file(path, mode="r", *args, **kwargs):
        handle = normal_open(path, mode, *args, **kwargs)
        return FailingWrite(handle) if mode == "xb" else handle

    monkeypatch.setattr(Path, "open", open_file)
    with pytest.raises(OSError, match="synthetic disk full"):
        await router._persist_template_source(
            tenant_id=str(uuid.uuid4()),
            template_id=uuid.uuid4(),
            filename="derived.docx",
            content=b"partial document",
        )
    assert list(tmp_path.rglob("derived.docx")) == []
