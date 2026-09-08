import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock

from app.routers.cloud_admin import _CloudSearchTestRequest, cloud_search_test
from app.services import retrieval_planner as planner_module
from app.services.llm_routing import LLMRoute
from app.services.retrieval_planner import RetrievalPlanner


class _PlannerLLM:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = []

    async def complete(self, **_kwargs):
        import json

        self.calls.append(_kwargs)
        return json.dumps(self.payload), {}, {}


def _standard_route() -> LLMRoute:
    return LLMRoute(
        requested_route="standard",
        resolved_route="profile-standard",
        gateway_alias="standard-model",
    )


@pytest.fixture
def admitted_route(monkeypatch):
    async def allow(*_args, **_kwargs):
        return True

    monkeypatch.setattr(planner_module, "route_matter_context_allowed", allow)
    return _standard_route()


@pytest.mark.asyncio
async def test_planner_normalizes_and_deduplicates_keywords(admitted_route):
    planner = RetrievalPlanner(
        _PlannerLLM(
            {
                "should_search": True,
                "sources": ["outlook"],
                "keywords": ["  notice ", "NOTICE", " jurisdiction "],
            }
        )
    )

    plan = await planner.plan(
        "find the notice",
        db=object(),
        tenant_id="tenant",
        active_providers=["microsoft"],
        planning_route=admitted_route,
    )

    assert plan["keywords"] == ["notice", "jurisdiction"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "keywords",
    [None, [], [""], ["  "], ["*"], ["**", "?"], [" ", "*"], "notice", ["notice", 3]],
)
async def test_planner_rejects_keyword_lists_that_could_become_wildcard_search(
    keywords,
    admitted_route,
):
    planner = RetrievalPlanner(
        _PlannerLLM(
            {"should_search": True, "sources": ["outlook"], "keywords": keywords}
        )
    )

    assert (
        await planner.plan(
            "find a file",
            db=object(),
            tenant_id="tenant",
            active_providers=["microsoft"],
            planning_route=admitted_route,
        )
        is None
    )


@pytest.mark.asyncio
async def test_planner_rejects_people_only_output_until_people_filter_is_applied(
    admitted_route,
):
    planner = RetrievalPlanner(
        _PlannerLLM(
            {
                "should_search": True,
                "sources": ["outlook"],
                "people": ["client@example.com"],
            }
        )
    )
    assert (
        await planner.plan(
            "find messages from the client",
            db=object(),
            tenant_id="tenant",
            active_providers=["microsoft"],
            planning_route=admitted_route,
        )
        is None
    )


@pytest.mark.asyncio
async def test_planner_does_not_send_question_when_route_is_blocked(monkeypatch):
    llm = _PlannerLLM(
        {"should_search": True, "sources": ["outlook"], "keywords": ["notice"]}
    )
    planner = RetrievalPlanner(llm)

    async def deny(*_args, **_kwargs):
        return False

    monkeypatch.setattr(planner_module, "route_matter_context_allowed", deny)
    result = await planner.plan(
        "private matter question",
        db=object(),
        tenant_id="tenant",
        active_providers=["microsoft"],
        planning_route=_standard_route(),
    )

    assert result is None
    assert llm.calls == []


@pytest.mark.asyncio
async def test_planner_uses_selected_premium_route(monkeypatch):
    llm = _PlannerLLM(
        {"should_search": True, "sources": ["outlook"], "keywords": ["notice"]}
    )
    planner = RetrievalPlanner(llm)
    premium = LLMRoute(
        requested_route="premium",
        resolved_route="profile-premium",
        gateway_alias="premium-model",
    )

    async def allow(*_args, **_kwargs):
        return True

    monkeypatch.setattr(planner_module, "route_matter_context_allowed", allow)
    result = await planner.plan(
        "private matter question",
        db=object(),
        tenant_id="tenant",
        active_providers=["microsoft"],
        planning_route=premium,
    )

    assert result["keywords"] == ["notice"]
    assert llm.calls[0]["provider"] == premium.provider
    assert llm.calls[0]["model"] == premium.model
    assert llm.calls[0]["use_premium"] is True
    assert llm.calls[0]["max_output_tokens"] == 512


@pytest.mark.asyncio
async def test_standalone_planner_never_uses_unadmitted_implicit_model(monkeypatch):
    llm = _PlannerLLM({"should_search": False})
    planner = RetrievalPlanner(llm)
    resolve = AsyncMock(return_value=_standard_route())
    admit = AsyncMock(return_value=False)
    monkeypatch.setattr(planner_module, "resolve_llm_route", resolve)
    monkeypatch.setattr(planner_module, "route_matter_context_allowed", admit)

    assert await planner.plan("private question") is None
    resolve.assert_not_awaited()
    assert (
        await planner.plan("private question", db=object(), tenant_id="tenant") is None
    )
    resolve.assert_awaited_once()
    admit.assert_awaited_once()
    assert llm.calls == []


@pytest.mark.asyncio
async def test_planner_admission_error_sends_no_private_context(monkeypatch):
    llm = _PlannerLLM({"should_search": False})
    monkeypatch.setattr(
        planner_module,
        "route_matter_context_allowed",
        AsyncMock(side_effect=RuntimeError("policy unavailable")),
    )
    assert (
        await RetrievalPlanner(llm).plan(
            "private question",
            db=object(),
            tenant_id="tenant",
            planning_route=_standard_route(),
        )
        is None
    )
    assert llm.calls == []


@pytest.mark.asyncio
async def test_manual_cloud_search_rejects_blank_query_after_auth_before_search(
    monkeypatch,
):
    authenticate = AsyncMock()
    monkeypatch.setattr("app.routers.cloud_admin._require_admin", authenticate)
    with pytest.raises(HTTPException, match="query must not be blank"):
        await cloud_search_test(_CloudSearchTestRequest(query=" \t"), None, None)
    authenticate.assert_awaited_once()
