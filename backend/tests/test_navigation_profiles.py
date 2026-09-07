import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from app.schemas.auth import UserProfileUpdate
from app.routers.roles import RoleIn
from app.services.navigation import resolve_navigation


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profiles,preferences,expected_paths,landing",
    [
        ([None], {}, None, "/matters"),
        ([], {}, None, "/matters"),
        ([["/intake", "/tasks"], None], {}, ["/intake", "/tasks"], "/tasks"),
        ([["/tasks"], ["/intake", "/tasks"]], {}, ["/tasks", "/intake"], "/tasks"),
        ([[]], {}, [], "/profile"),
        ([["/invoices"]], {}, ["/invoices"], "/profile"),
        (
            [["/intake", "/tasks"]],
            {"hidden": ["/tasks"]},
            ["/intake", "/tasks"],
            "/intake",
        ),
        ([["/tasks"]], {"hidden": ["/tasks"]}, ["/tasks"], "/profile"),
        (
            [["/intake", "/tasks"]],
            {"order": ["/invoices", "/intake", "/intake"]},
            ["/intake", "/tasks"],
            "/intake",
        ),
        ([None], {"order": ["/intake"]}, None, "/intake"),
    ],
)
async def test_navigation_resolution(profiles, preferences, expected_paths, landing):
    result = Mock()
    result.scalars.return_value.all.return_value = profiles
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    user = SimpleNamespace(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), navigation_preferences=preferences
    )
    paths, saved, route = await resolve_navigation(
        db, user, ["matters", "intake", "tasks"], "/matters"
    )
    assert (paths, saved, route) == (expected_paths, preferences, landing)
    query = str(db.execute.call_args.args[0])
    assert "roles.tenant_id =" in query and "user_roles.tenant_id =" in query


def test_navigation_input_validation():
    role = RoleIn(name="Receptionist", navigation_paths=["/tasks", "/tasks"])
    assert role.navigation_paths == ["/tasks"]
    assert RoleIn(name="Existing").navigation_paths is None
    data = UserProfileUpdate(
        navigation_preferences={"hidden": ["/tasks", "/tasks"], "order": ["/intake"]}
    )
    assert data.model_dump()["navigation_preferences"] == {
        "hidden": ["/tasks"],
        "order": ["/intake"],
    }
    for path in ["/admin", "/onboarding", "/unknown"]:
        with pytest.raises(ValidationError):
            RoleIn(name="Bad", navigation_paths=[path])
        with pytest.raises(ValidationError):
            UserProfileUpdate(navigation_preferences={"hidden": [path]})
    with pytest.raises(ValidationError):
        UserProfileUpdate(navigation_preferences={"role": "admin"})
    with pytest.raises(ValidationError):
        UserProfileUpdate(navigation_paths=["/tasks"])
    with pytest.raises(ValidationError):
        UserProfileUpdate(navigation_preferences={"order": ["/tasks"] * 51})
