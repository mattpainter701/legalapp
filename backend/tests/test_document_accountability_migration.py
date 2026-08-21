from pathlib import Path


def test_integrity_migration_enforces_rls_and_append_only_events():
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "116_cloud_document_accountability.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "116_cloud_doc_accountability"' in migration
    assert "ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in migration
    assert "document_integrity_events_append_only" in migration
    assert "BEFORE UPDATE OR DELETE ON document_integrity_events" in migration
