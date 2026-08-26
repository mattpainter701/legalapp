import asyncio
import sys
import threading
import types

from clarity_agent import service


def _fake_windows_modules(monkeypatch):
    class Framework:
        def __init__(self, _args):
            self.reported = []

        def ReportServiceStatus(self, status):
            self.reported.append(status)

    class Service:
        SERVICE_STOP_PENDING = 3

    event = types.SimpleNamespace(
        CreateEvent=lambda *_args: object(),
        SetEvent=lambda *_args: None,
    )
    util = types.SimpleNamespace(ServiceFramework=Framework)
    monkeypatch.setitem(sys.modules, "win32event", event)
    monkeypatch.setitem(sys.modules, "win32service", Service)
    monkeypatch.setitem(sys.modules, "win32serviceutil", util)
    return service._windows_service_class()


def test_windows_service_latches_stop_before_async_loop(monkeypatch):
    service_class = _fake_windows_modules(monkeypatch)
    instance = service_class(["LawHandAgent"])

    instance.SvcStop()

    assert instance._stop_requested.is_set()
    assert instance.reported == [3]


def test_windows_service_forwards_stop_to_async_event(monkeypatch):
    service_class = _fake_windows_modules(monkeypatch)
    instance = service_class(["LawHandAgent"])
    loop = asyncio.new_event_loop()
    stop_event = asyncio.Event()
    instance._loop = loop
    instance._stop_event = stop_event

    instance.SvcStop()
    loop.run_until_complete(asyncio.sleep(0))

    assert instance._stop_requested.is_set()
    assert stop_event.is_set()
    loop.close()


def test_windows_service_starts_only_one_watchdog_for_concurrent_stops(monkeypatch):
    service_class = _fake_windows_modules(monkeypatch)
    instance = service_class(["LawHandAgent"])
    started = []
    caller_thread = threading.Thread

    class FakeThread:
        def __init__(self, **kwargs):
            self.target = kwargs["target"]

        def start(self):
            started.append(self.target)

    monkeypatch.setattr(service.threading, "Thread", FakeThread)
    callers = [caller_thread(target=instance.SvcStop) for _ in range(16)]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join()

    assert len(started) == 1
    assert instance._watchdog_started


def test_windows_service_watchdog_has_last_resort_exit(monkeypatch):
    service_class = _fake_windows_modules(monkeypatch)
    instance = service_class(["LawHandAgent"])
    exited = []
    monkeypatch.setattr(service.os, "_exit", lambda code: exited.append(code))
    monkeypatch.setattr(service, "SERVICE_STOP_GRACE_SECONDS", 0)
    monkeypatch.setattr(service, "SERVICE_STOP_HARD_DEADLINE_SECONDS", 0)

    instance._stop_watchdog()

    assert exited == [0]


def test_windows_service_watchdog_preserves_graceful_shutdown(monkeypatch):
    service_class = _fake_windows_modules(monkeypatch)
    instance = service_class(["LawHandAgent"])
    exited = threading.Event()
    monkeypatch.setattr(service.os, "_exit", lambda _code: exited.set())
    monkeypatch.setattr(service, "SERVICE_STOP_GRACE_SECONDS", 1)
    monkeypatch.setattr(service, "SERVICE_STOP_HARD_DEADLINE_SECONDS", 1)
    instance._shutdown_complete.set()

    instance._stop_watchdog()

    assert not exited.is_set()
