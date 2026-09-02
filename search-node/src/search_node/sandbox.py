"""Platform containment for the parser and OCR children.

Both worker pools hand untrusted customer documents to a short-lived child that
may itself spawn a vendor process (Tika's JVM, Poppler, Tesseract). Killing only
the direct child leaves that grandchild running, so containment has to be a
whole-tree construct on both supported platforms: a session plus rlimits on
POSIX, a job object on Windows.
"""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Callable
from typing import Self

from .config import Settings

# winnt.h. Only the flags this module sets are named.
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
_JOBOBJECT_EXTENDED_LIMIT_INFORMATION = 9
# CPU limits are expressed in 100-nanosecond units.
_HUNDRED_NS_PER_SECOND = 10_000_000


def _windows_structures():
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # Declaring these is not optional: the default int return truncates a 64-bit
    # HANDLE, and every later call would then operate on a bogus job.
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return ctypes, kernel32, JOBOBJECT_EXTENDED_LIMIT_INFORMATION


def rlimit_plan(settings: Settings, *, jvm_expected: bool) -> list[tuple[int, tuple[int, int]]]:
    """The rlimits the parser child gets, as data.

    Kept separate from applying them so tests can assert the plan without
    running ``setsid``/``prctl`` against the test runner itself.
    """
    import resource

    limits = settings.limits
    memory = limits.memory_bytes
    plan: list[tuple[int, tuple[int, int]]] = []
    if jvm_expected:
        # A JVM reserves far more virtual address space than it ever commits, so
        # any RLIMIT_AS we would be willing to grant makes `java` fail to start
        # rather than bounding it. Bound the data segment instead and let the
        # wall-clock kill and the operator's cgroup own the true ceiling.
        # Without a configured Tika jar the child is pure Python and RLIMIT_AS
        # remains the tighter, more honest bound.
        plan.append((resource.RLIMIT_DATA, (memory, memory)))
    else:
        plan.append((resource.RLIMIT_AS, (memory, memory)))
    plan.append((resource.RLIMIT_CPU, (limits.wall_seconds, limits.wall_seconds + 1)))
    plan.append((resource.RLIMIT_FSIZE, (limits.temp_bytes, limits.temp_bytes)))
    # A JVM opens far more descriptors than the Python parser.
    plan.append((resource.RLIMIT_NOFILE, (512 if jvm_expected else 64,) * 2))
    # Deliberately no RLIMIT_NPROC. The kernel counts it per real UID rather
    # than per descendant tree, so it neither bounds our subtree nor fails
    # safely: any other process owned by the service account consumes the same
    # budget, and the parser then dies with EAGAIN on a fork it was entitled to
    # make. Bound process count where it can actually be scoped to the tree —
    # the job object on Windows, and the operator's cgroup (pids.max) on Linux.
    return plan


def _posix_limits(settings: Settings, *, jvm_expected: bool) -> Callable[[], None] | None:
    if os.name != "posix":
        return None

    def apply() -> None:
        import ctypes
        import resource

        for which, pair in rlimit_plan(settings, jvm_expected=jvm_expected):
            resource.setrlimit(which, pair)
        os.setsid()
        # PR_SET_NO_NEW_PRIVS. Container policy separately drops all capabilities.
        ctypes.CDLL(None).prctl(38, 1, 0, 0, 0)

    return apply


class ProcessContainer:
    """Bounds one child process and every process it spawns.

    POSIX places the child in its own session so the whole group can be signalled
    and applies rlimits before exec. Windows assigns it to a job object carrying
    memory, active-process, and CPU-time limits, which terminates the tree when
    the job is terminated or its last handle closes.
    """

    def __init__(self, settings: Settings, *, jvm_expected: bool = False):
        self.settings = settings
        self.jvm_expected = jvm_expected
        self._job = None
        self._ctypes = None
        self._kernel32 = None

    def __enter__(self) -> Self:
        if os.name == "nt":
            self._open_job()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _open_job(self) -> None:
        try:
            ctypes, kernel32, extended_type = _windows_structures()
            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                return
            limits = self.settings.limits
            information = extended_type()
            basic = information.BasicLimitInformation
            basic.LimitFlags = (
                _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                | _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
                | _JOB_OBJECT_LIMIT_PROCESS_MEMORY
                | _JOB_OBJECT_LIMIT_JOB_MEMORY
                | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                | _JOB_OBJECT_LIMIT_PROCESS_TIME
            )
            # A JVM needs room for its own helper processes; the Python-only
            # parser never legitimately forks.
            basic.ActiveProcessLimit = 64 if self.jvm_expected else 4
            basic.PerProcessUserTimeLimit = limits.wall_seconds * _HUNDRED_NS_PER_SECOND
            information.BasicLimitInformation = basic
            # Windows caps committed memory here rather than reserved address
            # space, so this stays correct for the JVM as well.
            information.ProcessMemoryLimit = limits.memory_bytes
            information.JobMemoryLimit = limits.memory_bytes * 2
            if not kernel32.SetInformationJobObject(
                job,
                _JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(information),
                ctypes.sizeof(information),
            ):
                kernel32.CloseHandle(job)
                return
            self._ctypes = ctypes
            self._kernel32 = kernel32
            self._job = job
        except (OSError, AttributeError, ValueError):
            # Containment is unavailable on this host; the caller still applies
            # its wall-clock kill. Never fail an extraction over this.
            self._job = None

    @property
    def contained(self) -> bool:
        """True when the whole spawned tree is genuinely bounded."""
        return self._job is not None if os.name == "nt" else os.name == "posix"

    def popen_kwargs(self) -> dict:
        kwargs: dict = {"preexec_fn": _posix_limits(self.settings, jvm_expected=self.jvm_expected)}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        return kwargs

    def adopt(self, proc: subprocess.Popen) -> None:
        """Assign a started child to the job before it can spawn anything.

        Callers must not have written the child's stdin yet: the parser and OCR
        children block on their first read, which is what keeps this assignment
        ahead of any grandchild.
        """
        if self._job is None or self._kernel32 is None:
            return
        self._kernel32.AssignProcessToJobObject(self._job, int(proc._handle))

    def terminate(self, proc: subprocess.Popen) -> None:
        """Kill the child and every process it spawned."""
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        else:
            if self._job is not None and self._kernel32 is not None:
                self._kernel32.TerminateJobObject(self._job, 1)
            else:
                proc.kill()
        proc.communicate()

    def close(self) -> None:
        if self._job is not None and self._kernel32 is not None:
            # KILL_ON_JOB_CLOSE reaps any straggler the vendor process left
            # behind once the last handle goes away.
            self._kernel32.CloseHandle(self._job)
        self._job = None
        self._ctypes = None
        self._kernel32 = None
