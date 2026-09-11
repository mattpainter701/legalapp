"""Unit tests for the self-serve trial helpers (no database required)."""

from datetime import timedelta

from app.config import get_settings
from app.services.trials import (
    config_marks_trial,
    new_trial_window,
    trial_config,
    trial_period_days,
)


def test_config_marks_trial_only_for_explicit_true():
    assert config_marks_trial({"trial": True}) is True
    assert config_marks_trial({"trial": False}) is False
    assert config_marks_trial({"plan": "full-platform"}) is False
    assert config_marks_trial({}) is False
    assert config_marks_trial(None) is False
    assert config_marks_trial("not-a-dict") is False


def test_trial_period_days_defaults_to_thirty(monkeypatch):
    monkeypatch.setattr(get_settings(), "SIGNUP_TRIAL_DAYS", 30)
    assert trial_period_days() == 30


def test_trial_period_days_floors_at_one(monkeypatch):
    monkeypatch.setattr(get_settings(), "SIGNUP_TRIAL_DAYS", 0)
    assert trial_period_days() == 1
    monkeypatch.setattr(get_settings(), "SIGNUP_TRIAL_DAYS", -5)
    assert trial_period_days() == 1


def test_new_trial_window_uses_configured_days(monkeypatch):
    monkeypatch.setattr(get_settings(), "SIGNUP_TRIAL_DAYS", 7)
    start, end = new_trial_window()
    assert end - start == timedelta(days=7)


def test_trial_config_marks_and_records_window():
    start, end = new_trial_window()
    config = trial_config(start, end)
    assert config_marks_trial(config) is True
    assert config["trial_started_at"] == start.isoformat()
    assert config["trial_ends_at"] == end.isoformat()
