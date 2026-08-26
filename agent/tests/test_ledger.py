import pytest

from clarity_agent.db import FileLedger


@pytest.mark.asyncio
async def test_upsert_files_writes_a_batch(tmp_path):
    ledger = FileLedger(str(tmp_path / "ledger.db"))
    await ledger.init()
    try:
        files = [
            {
                "path": r"\\FS\Legal\a.txt",
                "share_id": "share-1",
                "filename": "a.txt",
                "ext": ".txt",
                "content_hash": "a",
                "is_deleted": False,
            },
            {
                "path": r"\\FS\Legal\b.txt",
                "share_id": "share-1",
                "filename": "b.txt",
                "ext": ".txt",
                "content_hash": "b",
                "is_deleted": False,
            },
        ]
        await ledger.upsert_files(files)
        assert (await ledger.get_file(files[0]["path"]))["content_hash"] == "a"
        assert await ledger.get_all_paths("share-1") == {
            files[0]["path"],
            files[1]["path"],
        }
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_directory_sentinels_are_not_candidates_for_deletion(tmp_path):
    ledger = FileLedger(str(tmp_path / "ledger.db"))
    await ledger.init()
    try:
        await ledger.upsert_files(
            [
                {
                    "path": r"\\FS\Legal\unchanged.txt",
                    "share_id": "share-1",
                    "filename": "unchanged.txt",
                    "ext": ".txt",
                    "content_hash": "hash",
                    "is_deleted": False,
                },
                {
                    "path": r"\\FS\Legal",
                    "share_id": "share-1",
                    "filename": "Legal",
                    "dir_mtime": "2026-08-25T00:00:00+00:00",
                    "is_deleted": False,
                },
            ]
        )

        assert await ledger.get_all_paths("share-1") == {r"\\FS\Legal\unchanged.txt"}
        await ledger.cleanup_deleted("share-1", set())
        assert (await ledger.get_file(r"\\FS\Legal"))["is_deleted"] is False
    finally:
        await ledger.close()
