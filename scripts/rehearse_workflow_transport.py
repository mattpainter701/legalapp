"""Official MCP client against the real ASGI adapter and migrated fixture data."""

import time
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient
from jose import jwt
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette
from starlette.routing import Route


async def replay_through_mcp(sessions, *, tenant_id, user_id, grant_id, plan):
    from app.services import workspace_mcp_protocol as protocol
    now=int(time.time())
    signing_key="isolated-workflow-rehearsal-key-" + "x"*32
    issuer="http://localhost:8000"
    scopes="matters:read tasks:read tasks:propose documents:read documents:propose"
    app=Starlette(routes=[Route(protocol.MCP_ENDPOINT_PATH,protocol.workspace_protocol_endpoint,methods=["GET","POST","DELETE"])])
    app.state.redis=None
    app.state.jti_blacklist={}
    with patch.object(protocol,"async_session_maker",sessions), \
         patch.object(protocol.settings,"WORKSPACE_MCP_ENABLED",True), \
         patch.object(protocol.settings,"WORKSPACE_MCP_ISSUER",issuer), \
         patch.object(protocol.settings,"WORKSPACE_MCP_TOKEN_SIGNING_KEY",signing_key), \
         patch.object(protocol.settings,"DEV_MODE",True), \
         patch.object(protocol,"enforce_workspace_request_limit",AsyncMock()), \
         patch.object(protocol,"enforce_workspace_grant_call_budget",AsyncMock()):
        token=jwt.encode({"iss":issuer,"aud":protocol.settings.WORKSPACE_MCP_AUDIENCE,
            "sub":str(user_id),"tenant_id":str(tenant_id),"type":"workspace_mcp",
            "token_use":"access","client_id":"runtime-rehearsal","grant_id":str(grant_id),
            "jti":"runtime-rehearsal","scope":scopes,"iat":now,"exp":now+300},
            signing_key,algorithm=protocol.settings.ALGORITHM)
        with patch.object(protocol.workspace_protocol_session_manager,"security_settings",protocol._transport_security()):
            async with protocol.workspace_protocol_session_manager.run():
                async with AsyncClient(transport=ASGITransport(app=app),base_url=issuer,
                    headers={"Authorization":f"Bearer {token}"}) as http_client:
                    async with streamable_http_client(f"{issuer}{protocol.MCP_ENDPOINT_PATH}",http_client=http_client) as (read,write,_):
                        async with ClientSession(read,write) as client:
                            await client.initialize()
                            proposed=await client.call_tool("propose_workflow_run",plan.model_dump(mode="json"))
                            assert not proposed.isError,proposed
                            result=proposed.structuredContent
                            readback=await client.call_tool("get_workflow_run",{"run_id":result["run_id"]})
                            assert not readback.isError,readback
                            assert readback.structuredContent["plan_sha256"]==result["plan_sha256"]
                            return result
