"""Platform operator reads/writes under the production NOBYPASSRLS role."""

import os
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.datastructures import Headers

from app.models.api_access_log import ApiAccessLog
from app.models.conversation import UsageRecord
from app.models.error_log import ErrorLog
from app.models.mcp_product import MCPProductKey, MCPUsageEvent
from app.models.tenant import TenantSettings
from app.routers import platform
from app.services.mcp_product import hash_key
from tests.platform_auth_helpers import platform_headers


def _platform_request(method: str, path: str):
    return SimpleNamespace(
        method=method,
        url=SimpleNamespace(path=path),
        headers=Headers(platform_headers()),
        state=SimpleNamespace(),
        client=SimpleNamespace(host="127.0.0.1"),
    )


@pytest.mark.asyncio
async def test_platform_routes_scope_each_tenant_under_runtime_rls(
    db_session, test_tenant, test_user
):
    url = os.getenv("RLS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("RLS_TEST_DATABASE_URL is required for runtime-role integration")

    key = MCPProductKey(
        tenant_id=test_tenant.id,
        name="RLS product key",
        key_hash=hash_key("clmcp_runtime_rls_test"),
        key_prefix="clmcp_runti",
        allowed_tools=["search_caselaw"],
        monthly_call_limit=25,
        created_by_user_id=test_user.id,
    )
    db_session.add_all(
        [
            UsageRecord(
                tenant_id=test_tenant.id,
                user_id=test_user.id,
                tokens_in=10,
                tokens_out=5,
                cost_usd=Decimal("0.125"),
            ),
            key,
            ErrorLog(
                tenant_id=test_tenant.id,
                user_id=test_user.id,
                error_type="runtime_rls_test",
                severity="warning",
                message="Scoped operator diagnostic",
            ),
            ErrorLog(
                tenant_id=None,
                user_id=None,
                error_type="runtime_rls_system_test",
                severity="error",
                message="System operator diagnostic",
            ),
            ApiAccessLog(
                tenant_id=test_tenant.id,
                user_id=test_user.id,
                endpoint="/api/runtime-rls-test",
                method="GET",
                status_code=200,
                latency_ms=12.5,
            ),
        ]
    )
    await db_session.flush()
    db_session.add(
        MCPUsageEvent(
            tenant_id=test_tenant.id,
            product_key_id=key.id,
            user_id=test_user.id,
            auth_type="product_key",
            transport="streamable_http",
            tool_name="search_caselaw",
            status_code=200,
            result_count=2,
        )
    )
    await db_session.commit()

    engine = create_async_engine(url, pool_pre_ping=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as runtime_db:
            update = await platform.update_tenant(
                str(test_tenant.id),
                platform.TenantUpdate(plan="intake-only"),
                _platform_request("PUT", f"/api/platform/tenants/{test_tenant.id}"),
                runtime_db,
            )
            assert update["status"] == "updated"

            tenant_list = await platform.list_tenants(
                _platform_request("GET", "/api/platform/tenants"),
                runtime_db,
                page=1,
                limit=50,
            )
            listed_tenant = next(
                item
                for item in tenant_list["tenants"]
                if item.id == str(test_tenant.id)
            )
            assert listed_tenant.user_count == 1
            assert listed_tenant.requests_30d == 1

            detail = await platform.get_tenant_detail(
                str(test_tenant.id),
                _platform_request("GET", f"/api/platform/tenants/{test_tenant.id}"),
                runtime_db,
            )
            assert detail["tenant"].user_count == 1
            assert detail["module_config"]["plan"] == "intake-only"
            assert detail["usage_30d"]["requests"] == 1

            usage = await platform.platform_usage(
                _platform_request("GET", "/api/platform/usage"), runtime_db
            )
            assert usage.total_users == 1
            assert usage.requests_30d == 1

            mcp = await platform.platform_mcp_overview(
                _platform_request("GET", "/api/platform/mcp"), runtime_db
            )
            assert mcp["overview"].active_keys == 1
            assert mcp["overview"].calls_30d == 1

            errors = await platform.list_platform_errors(
                _platform_request("GET", "/api/platform/logs"),
                runtime_db,
                page=1,
                limit=20,
                severity=None,
                error_type="runtime_rls_test",
                tenant_id=None,
                days=7,
                unresolved_only=False,
            )
            assert errors.total == 1
            assert errors.errors[0].message == "Scoped operator diagnostic"

            error_summary = await platform.platform_error_summary(
                _platform_request("GET", "/api/platform/logs/summary"),
                runtime_db,
                days=7,
            )
            assert error_summary.total_errors == 2
            assert error_summary.by_type == {
                "runtime_rls_test": 1,
                "runtime_rls_system_test": 1,
            }

            tenant_errors = await platform.tenant_error_logs(
                str(test_tenant.id),
                _platform_request("GET", f"/api/platform/logs/tenant/{test_tenant.id}"),
                runtime_db,
                page=1,
                limit=20,
                severity=None,
                error_type=None,
                days=7,
                unresolved_only=False,
            )
            assert tenant_errors.total == 1
            tenant_error_summary = await platform.tenant_error_summary(
                str(test_tenant.id),
                _platform_request(
                    "GET", f"/api/platform/logs/tenant/{test_tenant.id}/summary"
                ),
                runtime_db,
                days=7,
            )
            assert tenant_error_summary.total_errors == 1

            access = await platform.list_access_logs(
                _platform_request("GET", "/api/platform/access-logs"),
                runtime_db,
                page=1,
                limit=20,
                tenant_id=None,
                endpoint="runtime-rls-test",
                status_code=None,
                hours=24,
            )
            assert access.total == 1
            assert access.entries[0].endpoint == "/api/runtime-rls-test"

            access_summary = await platform.access_log_summary(
                _platform_request("GET", "/api/platform/access-logs/summary"),
                runtime_db,
                tenant_id=None,
                hours=24,
            )
            assert access_summary.total_requests == 1
            assert access_summary.by_status == {"200": 1}
            assert access_summary.avg_latency_ms == pytest.approx(12.5)

            bypass = await runtime_db.scalar(
                text("SELECT current_setting('app.rls_bypass', true)")
            )
            assert bypass in (None, "", "off")

        settings = await db_session.scalar(
            select(TenantSettings).where(TenantSettings.tenant_id == test_tenant.id)
        )
        assert settings is not None
        assert settings.custom_config["plan"] == "intake-only"
    finally:
        await engine.dispose()
