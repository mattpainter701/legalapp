from datetime import date, time

from app.routers.tasks import _task_update_values
from app.schemas.task import TaskUpdate


def test_task_update_values_preserves_explicit_nullable_clears():
    payload = TaskUpdate(description=None, due_date=None, due_time=None)
    assert _task_update_values(payload) == {
        "description": None,
        "due_date": None,
        "due_time": None,
        "acknowledge_prior_delivery_risk": False,
    }


def test_task_update_values_omission_keeps_existing_values_untouched():
    payload = TaskUpdate(title="Revised", due_date=date(2026, 9, 8), due_time=time(9))
    assert _task_update_values(payload) == {
        "title": "Revised",
        "due_date": date(2026, 9, 8),
        "due_time": time(9),
        "acknowledge_prior_delivery_risk": False,
    }
