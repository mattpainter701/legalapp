"""Runtime-role proof for PDF preview evidence FORCE RLS."""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import set_tenant_context
from app.models.document_template import DocumentTemplate
from app.models.document_template_preview import DocumentTemplatePreview
from app.models.tenant import Tenant


_ROLE = "pdf_preview_rls_probe"
_PASSWORD = "pdf_preview_rls_probe_password"


def _preview(*, tenant_id, template_id, marker: str) -> DocumentTemplatePreview:
    now = datetime.now(timezone.utc)
    return DocumentTemplatePreview(
        tenant_id=tenant_id,
        template_id=template_id,
        purpose="draft",
        contract_sha256=marker * 64,
        values_hmac_sha256="b" * 64,
        output_sha256="c" * 64,
        renderer_version="rls-test",
        flatten_pdf=True,
        reviewed_field_count=0,
        nonblank_field_count=0,
        reviewed_field_names=[],
        expires_at=now + timedelta(hours=1),
    )


@pytest.mark.asyncio
async def test_document_template_preview_force_rls_is_fail_closed_and_tenant_scoped(
    db_session, test_tenant
):
    database_url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://test:test@localhost:5432/legalapp_test",
    )
    tenant_b = Tenant(
        id=uuid.uuid4(),
        name="Preview RLS Tenant B",
        domain=f"preview-rls-{uuid.uuid4().hex}@example.test",
        is_active=True,
    )
    template_a = DocumentTemplate(
        tenant_id=test_tenant.id,
        title="Tenant A preview template",
        body="",
        format="pdf",
    )
    template_b = DocumentTemplate(
        tenant_id=tenant_b.id,
        title="Tenant B preview template",
        body="",
        format="pdf",
    )
    db_session.add_all([tenant_b, template_a, template_b])
    await db_session.flush()
    preview_a = _preview(
        tenant_id=test_tenant.id, template_id=template_a.id, marker="a"
    )
    preview_b = _preview(tenant_id=tenant_b.id, template_id=template_b.id, marker="f")
    db_session.add_all([preview_a, preview_b])
    await db_session.commit()

    admin_engine = create_async_engine(database_url, pool_pre_ping=True)
    role_url = (
        make_url(database_url)
        .set(username=_ROLE, password=_PASSWORD)
        .render_as_string(hide_password=False)
    )
    runtime_engine = None
    try:
        async with admin_engine.begin() as connection:
            await connection.execute(
                text(
                    f"""
                    DO $$ BEGIN
                      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{_ROLE}') THEN
                        CREATE ROLE {_ROLE} LOGIN PASSWORD '{_PASSWORD}'
                          NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
                      END IF;
                    END $$;
                    """
                )
            )
            await connection.execute(
                text(
                    f"ALTER ROLE {_ROLE} LOGIN PASSWORD '{_PASSWORD}' "
                    "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
                )
            )
            await connection.execute(
                text("ALTER TABLE document_template_previews ENABLE ROW LEVEL SECURITY")
            )
            await connection.execute(
                text("ALTER TABLE document_template_previews FORCE ROW LEVEL SECURITY")
            )
            await connection.execute(
                text(
                    "DROP POLICY IF EXISTS document_template_previews_tenant_isolation "
                    "ON document_template_previews"
                )
            )
            await connection.execute(
                text(
                    """
                    CREATE POLICY document_template_previews_tenant_isolation
                    ON document_template_previews
                    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
                    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
                    """
                )
            )
            await connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {_ROLE}"))
            await connection.execute(
                text(f"GRANT SELECT, INSERT ON document_template_previews TO {_ROLE}")
            )

        runtime_engine = create_async_engine(role_url, pool_pre_ping=True)
        maker = async_sessionmaker(runtime_engine, expire_on_commit=False)
        async with maker() as runtime_db:
            role_attributes = (
                await runtime_db.execute(
                    text(
                        "SELECT rolsuper, rolbypassrls FROM pg_roles "
                        "WHERE rolname = current_user"
                    )
                )
            ).one()
            assert role_attributes == (False, False)
            rls_flags = (
                await runtime_db.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE oid = 'document_template_previews'::regclass"
                    )
                )
            ).one()
            assert rls_flags == (True, True)

            # Missing tenant context is fail-closed.
            assert (
                await runtime_db.scalar(
                    select(func.count()).select_from(DocumentTemplatePreview)
                )
                == 0
            )

            await set_tenant_context(runtime_db, str(test_tenant.id))
            visible_ids = set(
                await runtime_db.scalars(select(DocumentTemplatePreview.id))
            )
            assert visible_ids == {preview_a.id}
            assert preview_b.id not in visible_ids

            with pytest.raises(DBAPIError):
                await runtime_db.execute(
                    text(
                        """
                        INSERT INTO document_template_previews (
                          tenant_id, template_id, purpose, contract_sha256,
                          values_hmac_sha256, output_sha256, renderer_version,
                          flatten_pdf, reviewed_field_count, nonblank_field_count,
                          reviewed_field_names, expires_at
                        ) VALUES (
                          :tenant_id, :template_id, 'draft', :contract,
                          :values, :output, 'rls-test', true, 0, 0,
                          '[]'::json, now() + interval '1 hour'
                        )
                        """
                    ),
                    {
                        "tenant_id": tenant_b.id,
                        "template_id": template_b.id,
                        "contract": "1" * 64,
                        "values": "2" * 64,
                        "output": "3" * 64,
                    },
                )
            await runtime_db.rollback()

            await set_tenant_context(runtime_db, str(test_tenant.id))
            await runtime_db.execute(
                text(
                    """
                    INSERT INTO document_template_previews (
                      tenant_id, template_id, purpose, contract_sha256,
                      values_hmac_sha256, output_sha256, renderer_version,
                      flatten_pdf, reviewed_field_count, nonblank_field_count,
                      reviewed_field_names, expires_at
                    ) VALUES (
                      :tenant_id, :template_id, 'draft', :contract,
                      :values, :output, 'rls-test', true, 0, 0,
                      '[]'::json, now() + interval '1 hour'
                    )
                    """
                ),
                {
                    "tenant_id": test_tenant.id,
                    "template_id": template_a.id,
                    "contract": "4" * 64,
                    "values": "5" * 64,
                    "output": "6" * 64,
                },
            )
            await runtime_db.commit()
            await set_tenant_context(runtime_db, str(test_tenant.id))
            assert (
                await runtime_db.scalar(
                    select(func.count()).select_from(DocumentTemplatePreview)
                )
                == 2
            )
    finally:
        if runtime_engine is not None:
            await runtime_engine.dispose()
        async with admin_engine.begin() as connection:
            role_exists = await connection.scalar(
                text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
                {"role": _ROLE},
            )
            if role_exists:
                await connection.execute(text(f"DROP OWNED BY {_ROLE}"))
                await connection.execute(text(f"DROP ROLE IF EXISTS {_ROLE}"))
        await admin_engine.dispose()
