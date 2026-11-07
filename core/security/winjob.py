"""Windows Job Object wrapper using ctypes.

Creates a job with extended limit information, exposes its handle and
information structures so callers and tests can query real OS state.
"""

import ctypes
import os
from dataclasses import dataclass

# Limit flags
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400

# Information class
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

# Process access
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_longlong),
        ("WriteTransferCount", ctypes.c_longlong),
        ("OtherTransferCount", ctypes.c_longlong),
    ]


class _JOB_OBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_longlong),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOB_OBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


# Alias with the Windows macro name.
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_STRUCT = JobObjectExtendedLimitInformation


@dataclass(frozen=True)
class JobLimits:
    memory_bytes: int = 256 * 1024 * 1024
    active_processes: int = 1
    die_on_unhandled_exception: bool = True


class JobObject:
    def __init__(self, limits: JobLimits) -> None:
        if os.name != "nt":
            raise OSError("Windows Job Objects require Windows")
        if not isinstance(limits, JobLimits):
            raise TypeError("limits must be JobLimits")
        self._kernel32 = ctypes.windll.kernel32
        self._kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        self._kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        self._kernel32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        self._kernel32.SetInformationJobObject.restype = ctypes.c_int
        self._kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        self._kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        self._kernel32.OpenProcess.restype = ctypes.c_void_p
        self._kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._kernel32.CloseHandle.restype = ctypes.c_int
        self._kernel32.QueryInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        self._kernel32.QueryInformationJobObject.restype = ctypes.c_int

        handle = self._kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.handle = handle

        info = JobObjectExtendedLimitInformation()
        info.BasicLimitInformation.ActiveProcessLimit = int(limits.active_processes)
        info.ProcessMemoryLimit = int(limits.memory_bytes)
        flags = (
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        )
        if limits.die_on_unhandled_exception:
            flags |= JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
        info.BasicLimitInformation.LimitFlags = int(flags)

        ok = self._kernel32.SetInformationJobObject(
            self.handle,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            err = ctypes.get_last_error()
            try:
                self._kernel32.CloseHandle(self.handle)
            except Exception:
                pass
            self.handle = 0
            raise ctypes.WinError(err)

    def assign(self, pid: int) -> None:
        if not self.handle:
            raise OSError("job closed")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise ValueError("pid must be a positive int")
        proc_handle = self._kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        if not proc_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            ok = self._kernel32.AssignProcessToJobObject(self.handle, proc_handle)
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self._kernel32.CloseHandle(proc_handle)

    def close(self) -> None:
        handle = getattr(self, "handle", 0)
        if handle:
            try:
                self._kernel32.CloseHandle(handle)
            finally:
                self.handle = 0
