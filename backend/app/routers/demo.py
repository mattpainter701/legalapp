"""Public, access-code guarded provisioning for disposable sales demos."""

from __future__ import annotations

import secrets
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.models.demo_session import DemoSession
from app.models.plugin import TenantPluginEntitlement
from app.models.tenant import Tenant, TenantSettings
from app.models.user import User
from app.routers.auth import (
    _create_refresh_token,
    _issue_access_token,
    _set_auth_cookies,
)
from app.schemas.demo import DemoSessionRequest, DemoSessionResponse
from app.services.demo_clone import DemoFixtureError, clone_demo_fixture
from app.services.operator_audit import record_operator_audit
from app.services.plugins.manifest import valid_plugin_names
from app.services.rbac_service import provision_tenant_rbac

router = APIRouter(prefix="/demo", tags=["demo"])
settings = get_settings()
_DEMO_DOMAIN_SUFFIX = ".demo.invalid"
_PROVISION_LOCK = 7_341_991_204


def _remove_target_files(tenant_id: uuid.UUID) -> None:
    upload_root = Path(settings.UPLOAD_DIR).resolve()
    target_root = (upload_root / str(tenant_id)).resolve()
    if target_root.is_relative_to(upload_root) and target_root.exists():
        shutil.rmtree(target_root)


def _require_demo_enabled() -> None:
    if not (
        settings.DEMO_MODE_ENABLED
        and settings.DEMO_ACCESS_CODE
        and settings.DEMO_FIXTURE_TENANT_DOMAIN
    ):
        raise HTTPException(status_code=404, detail="Not found")


def _validate_fixture_tenant(tenant: Tenant) -> None:
    if tenant.billing_tier == "demo" or tenant.domain.endswith(_DEMO_DOMAIN_SUFFIX):
        raise DemoFixtureError("A disposable demo tenant cannot be used as the fixture")
    sensitive = (
        tenant.stripe_customer_id,
        tenant.stripe_subscription_id,
        tenant.api_key,
        tenant.api_key_hash,
        tenant.api_key_prefix,
        tenant.cloud_root_folder,
        tenant.service_account_email,
        tenant.stripe_subscription_status != "none",
        tenant.mcp_entitlement_status != "disabled",
        tenant.mcp_billing_status != "disabled",
    )
    if any(value for value in sensitive):
        raise DemoFixtureError(
            "Fixture tenant contains live billing or integration state"
        )


@router.post("/session", response_model=DemoSessionResponse, status_code=201)
async def create_demo_session(
    body: DemoSessionRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    _require_demo_enabled()
    if not secrets.compare_digest(body.access_code, settings.DEMO_ACCESS_CODE):
        raise HTTPException(status_code=401, detail="Invalid demo access code")

    now = datetime.now(timezone.utc)
    target_tenant_id = uuid.uuid4()
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()
    expires_at = now + timedelta(hours=settings.DEMO_SESSION_TTL_HOURS)
    try:
        # One transaction serializes the cap check and all provisioning work.
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": _PROVISION_LOCK}
        )
        fixture = await db.scalar(
            select(Tenant).where(
                Tenant.domain == settings.DEMO_FIXTURE_TENANT_DOMAIN,
                Tenant.is_active.is_(True),
            )
        )
        if fixture is None:
            raise HTTPException(status_code=503, detail="Demo fixture is unavailable")
        _validate_fixture_tenant(fixture)

        active_count = await db.scalar(
            select(func.count(Tenant.id)).where(
                Tenant.billing_tier == "demo",
                Tenant.is_active.is_(True),
                Tenant.expires_at > now,
                Tenant.domain.endswith(_DEMO_DOMAIN_SUFFIX),
            )
        )
        if int(active_count or 0) >= settings.DEMO_MAX_ACTIVE:
            raise HTTPException(
                status_code=503, detail="All demo workspaces are in use"
            )

        tenant = Tenant(
            id=target_tenant_id,
            name="LawHand Demo Workspace",
            domain=f"demo-{target_tenant_id.hex[:16]}{_DEMO_DOMAIN_SUFFIX}",
            company_name="LawHand Demo Firm",
            billing_tier="demo",
            is_active=True,
            expires_at=expires_at,
            onboarding_completed=True,
            onboarding_step=4,
            rag_corpus_revision=fixture.rag_corpus_revision,
        )
        db.add(tenant)
        await db.flush()
        await set_tenant_context(db, str(target_tenant_id))

        user = User(
            id=user_id,
            tenant_id=target_tenant_id,
            email=str(body.email).lower().strip(),
            full_name=body.full_name.strip(),
            role="admin",
            oauth_provider="demo",
            oauth_subject=str(session_id),
            is_active=True,
            license_active=True,
            premium_ai_enabled=False,
            privacy_mode=True,
        )
        demo_session = DemoSession(
            id=session_id,
            tenant_id=target_tenant_id,
            fixture_tenant_id=fixture.id,
            fixture_version=fixture.updated_at.astimezone(timezone.utc).isoformat(),
            prospect_name=body.full_name.strip(),
            prospect_email=str(body.email).lower().strip(),
            status="provisioning",
            quota=settings.DEMO_MESSAGE_QUOTA,
            expires_at=expires_at,
        )
        db.add_all(
            [
                user,
                demo_session,
                TenantSettings(
                    tenant_id=target_tenant_id,
                    enable_pii_detection=True,
                    enable_auto_memory=False,
                    use_customer_llm=False,
                    primary_cloud_provider=None,
                    custom_config={"plan": "demo"},
                ),
                *[
                    TenantPluginEntitlement(
                        tenant_id=target_tenant_id,
                        plugin_name=plugin_name,
                        status="included",
                        source="live-demo",
                        expires_at=expires_at,
                    )
                    for plugin_name in valid_plugin_names()
                ],
            ]
        )
        await db.flush()

        clone_counts = await clone_demo_fixture(
            db,
            fixture_tenant_id=fixture.id,
            target_tenant_id=target_tenant_id,
            target_user_id=user_id,
        )
        await provision_tenant_rbac(db, target_tenant_id, user_id)
        demo_session.status = "active"
        await record_operator_audit(
            db,
            request,
            action="demo.session.created",
            actor_type="demo_access_code",
            resource_type="demo_session",
            resource_id=str(session_id),
            metadata={
                "tenant_id": str(target_tenant_id),
                "fixture_version": demo_session.fixture_version,
                "cloned_rows": sum(clone_counts.values()),
            },
        )
        await db.commit()
    except HTTPException:
        await db.rollback()
        _remove_target_files(target_tenant_id)
        raise
    except DemoFixtureError as exc:
        await db.rollback()
        _remove_target_files(target_tenant_id)
        raise HTTPException(
            status_code=503, detail="Demo fixture failed validation"
        ) from exc
    except Exception:
        await db.rollback()
        _remove_target_files(target_tenant_id)
        raise

    access_token = await _issue_access_token(db, user, tenant)
    refresh_token = await _create_refresh_token(request, user)
    _set_auth_cookies(response, access_token, refresh_token)
    return DemoSessionResponse(
        user_id=str(user_id),
        tenant_id=str(target_tenant_id),
        session_id=str(session_id),
        expires_at=expires_at,
        quota=settings.DEMO_MESSAGE_QUOTA,
        used=0,
    )
