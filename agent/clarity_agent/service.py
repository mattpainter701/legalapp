"""Background service management for the file share agent.

The agent has to survive reboots on a machine nobody logs into, so it runs as a
Windows service or a systemd unit rather than a console process:

    lawhand-agent service install     # register (and enable at boot)
    lawhand-agent service start|stop|restart|status
    lawhand-agent service remove

``service run`` is the entry point the service manager itself calls; on Windows
it hands control to the SCM dispatcher, on Linux it is a plain foreground run
under systemd.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from pathlib import Path

SERVICE_NAME = "LawHandAgent"
SERVICE_DISPLAY_NAME = "LawHand File Share Agent"
SERVICE_DESCRIPTION = (
    "Indexes approved on-premise file shares and relays document text to "
    "LawHand on request."
)
# SCM/MSI commonly gives a service roughly 30 seconds to stop.  Allow normal
# async cleanup first, then terminate a wedged service before that deadline.
# The hard exit is deliberately Windows-service-only: a stuck SMB worker can
# keep asyncio's default executor alive even after its task is cancelled.
SERVICE_STOP_GRACE_SECONDS = 20
SERVICE_STOP_HARD_DEADLINE_SECONDS = 25
SYSTEMD_UNIT_PATH = Path("/etc/systemd/system/lawhand-agent.service")


class ServiceError(RuntimeError):
    """A service action failed in a way the operator needs to see."""


def _is_frozen() -> bool:
    """True when running from a PyInstaller-built binary."""
    return getattr(sys, "frozen", False)


def _launch_command() -> list[str]:
    """The command a service manager should run to start the agent."""
    if _is_frozen():
        return [sys.executable, "service", "run"]
    return [sys.executable, "-m", "clarity_agent", "service", "run"]


def dispatch(action: str) -> None:
    if sys.platform == "win32":
        _windows(action)
    else:
        _systemd(action)


def _run_daemon_foreground() -> None:
    from clarity_agent.__main__ import run_daemon
    from clarity_agent.config import AgentConfig

    asyncio.run(run_daemon(AgentConfig.load()))


# ── Windows ─────────────────────────────────────────────────────────────────


def _windows(action: str) -> None:
    try:
        import servicemanager
        import win32serviceutil
    except ImportError as exc:  # pragma: no cover - Windows-only path
        raise ServiceError(
            "Windows service support needs pywin32 "
            "(pip install pywin32). The packaged installer bundles it."
        ) from exc

    service_class = _windows_service_class()

    if action == "run":
        # Called by the SCM itself.
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(service_class)
        servicemanager.StartServiceCtrlDispatcher()
        return

    if action == "install":
        command = _launch_command()
        exe_name = command[0]
        exe_args = subprocess.list2cmdline(command[1:])
        win32serviceutil.InstallService(
            pythonClassString=f"{service_class.__module__}.{service_class.__name__}",
            serviceName=SERVICE_NAME,
            displayName=SERVICE_DISPLAY_NAME,
            description=SERVICE_DESCRIPTION,
            startType=_auto_start(),
            exeName=exe_name,
            exeArgs=exe_args,
        )
        print(
            f"Installed service {SERVICE_NAME}. Start it with: "
            f"lawhand-agent service start"
        )
        return

    if action == "remove":
        win32serviceutil.RemoveService(SERVICE_NAME)
        print(f"Removed service {SERVICE_NAME}")
        return

    if action == "start":
        win32serviceutil.StartService(SERVICE_NAME)
        print(f"Started {SERVICE_NAME}")
        return

    if action == "stop":
        win32serviceutil.StopService(SERVICE_NAME)
        print(f"Stopped {SERVICE_NAME}")
        return

    if action == "restart":
        win32serviceutil.RestartService(SERVICE_NAME)
        print(f"Restarted {SERVICE_NAME}")
        return

    if action == "status":
        import win32service

        states = {
            win32service.SERVICE_STOPPED: "stopped",
            win32service.SERVICE_START_PENDING: "starting",
            win32service.SERVICE_STOP_PENDING: "stopping",
            win32service.SERVICE_RUNNING: "running",
        }
        status = win32serviceutil.QueryServiceStatus(SERVICE_NAME)[1]
        print(f"{SERVICE_NAME}: {states.get(status, status)}")
        return

    raise ServiceError(f"Unknown service action: {action}")


def _auto_start():  # pragma: no cover - Windows-only
    import win32service

    return win32service.SERVICE_AUTO_START


def _windows_service_class():  # pragma: no cover - Windows-only
    import win32event
    import win32service
    import win32serviceutil

    class LawHandAgentService(win32serviceutil.ServiceFramework):
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = SERVICE_DISPLAY_NAME
        _svc_description_ = SERVICE_DESCRIPTION

        def __init__(self, args):
            super().__init__(args)
            self._wait_stop = win32event.CreateEvent(None, 0, 0, None)
            # SvcStop runs on a pywin32 control-handler thread and may arrive
            # before SvcDoRun has created its asyncio loop.  Keep the request
            # in a thread-safe latch so that an early stop cannot be lost.
            self._stop_requested = threading.Event()
            self._shutdown_complete = threading.Event()
            self._watchdog_lock = threading.Lock()
            self._watchdog_started = False
            self._loop = None
            self._stop_event = None

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self._stop_requested.set()
            # SCM may dispatch repeated stop controls concurrently. Serialize
            # the check-and-start so only one watchdog can ever be created.
            with self._watchdog_lock:
                if not self._watchdog_started:
                    self._watchdog_started = True
                    threading.Thread(
                        target=self._stop_watchdog,
                        name="lawhand-service-stop-watchdog",
                        daemon=True,
                    ).start()
            if self._loop is not None and self._stop_event is not None:
                self._loop.call_soon_threadsafe(self._stop_event.set)
            win32event.SetEvent(self._wait_stop)

        def _stop_watchdog(self):
            # Graceful cancellation and resource cleanup remain the normal
            # path.  os._exit is only the last resort because Python cannot
            # interrupt a blocking SMB call running in a worker thread. Exit
            # successfully: WiX configures SCM failure actions to restart the
            # agent, and a nonzero intentional-stop exit would recreate the
            # service overlap this watchdog is preventing.
            if self._shutdown_complete.wait(SERVICE_STOP_GRACE_SECONDS):
                return
            if self._shutdown_complete.wait(
                SERVICE_STOP_HARD_DEADLINE_SECONDS - SERVICE_STOP_GRACE_SECONDS
            ):
                return
            os._exit(0)

        def SvcDoRun(self):
            from clarity_agent.__main__ import run_daemon
            from clarity_agent.config import AgentConfig

            async def _main():
                self._loop = asyncio.get_running_loop()
                self._stop_event = asyncio.Event()
                if self._stop_requested.is_set():
                    self._stop_event.set()
                await run_daemon(AgentConfig.load(), stop_event=self._stop_event)

            try:
                asyncio.run(_main())
            finally:
                self._shutdown_complete.set()

    return LawHandAgentService


# ── systemd ─────────────────────────────────────────────────────────────────


SYSTEMD_UNIT = """[Unit]
Description={description}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=30
User={user}
Environment=CLARITY_CONFIG_DIR={config_dir}
# The agent only ever reads file shares; it needs no privileges of its own.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
"""


def _systemctl(*args: str) -> None:
    result = subprocess.run(["systemctl", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise ServiceError(
            (result.stderr or result.stdout or "systemctl failed").strip()
        )
    if result.stdout.strip():
        print(result.stdout.strip())


def _systemd(action: str) -> None:
    if action == "run":
        _run_daemon_foreground()
        return

    if action == "install":
        from clarity_agent.config import CONFIG_DIR

        if os.geteuid() != 0:
            raise ServiceError("Installing the service requires root (use sudo)")
        unit = SYSTEMD_UNIT.format(
            description=SERVICE_DESCRIPTION,
            exec_start=subprocess.list2cmdline(_launch_command()),
            user=os.environ.get("LAWHAND_AGENT_USER", "root"),
            config_dir=CONFIG_DIR,
        )
        SYSTEMD_UNIT_PATH.write_text(unit)
        _systemctl("daemon-reload")
        _systemctl("enable", "lawhand-agent.service")
        print(
            f"Installed {SYSTEMD_UNIT_PATH}. Start it with: "
            f"sudo lawhand-agent service start"
        )
        return

    if action == "remove":
        if os.geteuid() != 0:
            raise ServiceError("Removing the service requires root (use sudo)")
        subprocess.run(["systemctl", "stop", "lawhand-agent.service"], check=False)
        subprocess.run(["systemctl", "disable", "lawhand-agent.service"], check=False)
        SYSTEMD_UNIT_PATH.unlink(missing_ok=True)
        _systemctl("daemon-reload")
        print("Removed lawhand-agent.service")
        return

    if action in ("start", "stop", "restart"):
        _systemctl(action, "lawhand-agent.service")
        print(f"{action}ed lawhand-agent.service")
        return

    if action == "status":
        result = subprocess.run(
            ["systemctl", "is-active", "lawhand-agent.service"],
            capture_output=True,
            text=True,
        )
        print(f"lawhand-agent.service: {result.stdout.strip() or 'unknown'}")
        return

    raise ServiceError(f"Unknown service action: {action}")
