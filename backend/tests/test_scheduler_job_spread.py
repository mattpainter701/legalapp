"""Interval jobs must not all fire in the same second.

APScheduler anchors every interval job to scheduler start. On 2026-09-11 the
hourly and fifteen-minute jobs fired together at xx:15:25 every hour and
exhausted the scheduler's connection pool, taking the one-minute heartbeat
down with them. Long-period jobs now carry jitter; short-cadence jobs do not.
"""

from datetime import timedelta

from apscheduler.triggers.interval import IntervalTrigger

from app.services import scheduler as scheduler_module

HERD_OBSERVED_IN_PRODUCTION = {
    "task-reminder",
    "esign-reminder",
    "demo-session-purge",
    "zoom-phone-reconciliation",
    "teams-voice-reconciliation",
    "background-ai-reconcile",
    "cloud-sync",
}
PERIOD_KEYS = ("weeks", "days", "hours", "minutes", "seconds")


def _registered_interval_jobs(monkeypatch):
    registered = []

    class _FakeScheduler:
        running = False

        def add_job(self, _func, *args, **kwargs):
            registered.append((args, kwargs))

        def start(self):
            pass

    monkeypatch.setattr(scheduler_module.settings, "CLOUD_SEARCH_ENABLED", True)
    instance = scheduler_module.LegalScheduler()
    monkeypatch.setattr(instance, "scheduler", _FakeScheduler())
    instance.start()
    return [
        (kwargs["id"], kwargs)
        for args, kwargs in registered
        if args and args[0] == "interval"
    ]


def _period(kwargs) -> timedelta:
    return timedelta(**{key: kwargs[key] for key in PERIOD_KEYS if key in kwargs})


def test_long_period_interval_jobs_are_jittered(monkeypatch):
    jobs = _registered_interval_jobs(monkeypatch)
    long_period = {
        job_id: kwargs
        for job_id, kwargs in jobs
        if _period(kwargs) >= timedelta(minutes=15)
    }

    assert HERD_OBSERVED_IN_PRODUCTION <= set(long_period)
    for job_id, kwargs in long_period.items():
        assert (
            kwargs.get("jitter") == scheduler_module.INTERVAL_JOB_JITTER_SECONDS
        ), job_id


def test_short_cadence_interval_jobs_keep_their_exact_cadence(monkeypatch):
    jobs = _registered_interval_jobs(monkeypatch)
    short_cadence = {
        job_id: kwargs
        for job_id, kwargs in jobs
        if _period(kwargs) < timedelta(minutes=15)
    }

    assert "scheduler-heartbeat" in short_cadence
    assert "durable-job-worker" in short_cadence
    for job_id, kwargs in short_cadence.items():
        assert "jitter" not in kwargs, job_id


def test_pinned_apscheduler_accepts_the_jitter_argument(monkeypatch):
    jittered = [
        (job_id, kwargs)
        for job_id, kwargs in _registered_interval_jobs(monkeypatch)
        if "jitter" in kwargs
    ]

    assert jittered
    for job_id, kwargs in jittered:
        trigger = IntervalTrigger(
            jitter=kwargs["jitter"],
            **{key: kwargs[key] for key in PERIOD_KEYS if key in kwargs},
        )
        assert trigger.jitter == scheduler_module.INTERVAL_JOB_JITTER_SECONDS, job_id
