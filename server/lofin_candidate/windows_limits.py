"""Windows process-memory measurement and OS-enforced child-job memory ceiling."""
import ctypes as C
import os
from ctypes import wintypes as W


class Counters(C.Structure):
    _fields_=[('cb',W.DWORD),('PageFaultCount',W.DWORD)]+[(n,C.c_size_t) for n in (
        'PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage',
        'QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage','PrivateUsage')]


def process_memory(handle=None):
    if os.name!='nt':
        raise RuntimeError('WINDOWS_MEMORY_MEASUREMENT_REQUIRED')
    kernel=C.WinDLL('kernel32',use_last_error=True)
    kernel.GetCurrentProcess.restype=W.HANDLE
    kernel.K32GetProcessMemoryInfo.argtypes=[W.HANDLE,C.POINTER(Counters),W.DWORD]
    kernel.K32GetProcessMemoryInfo.restype=W.BOOL
    value=Counters()
    value.cb=C.sizeof(value)
    if not kernel.K32GetProcessMemoryInfo(handle or kernel.GetCurrentProcess(),C.byref(value),value.cb):
        raise C.WinError(C.get_last_error())
    return {'private_bytes':value.PrivateUsage,'working_set_bytes':value.WorkingSetSize,
            'peak_working_set_bytes':value.PeakWorkingSetSize}


class BasicLimit(C.Structure):
    _fields_=[('PerProcessUserTimeLimit',C.c_longlong),('PerJobUserTimeLimit',C.c_longlong),
              ('LimitFlags',W.DWORD),('MinimumWorkingSetSize',C.c_size_t),('MaximumWorkingSetSize',C.c_size_t),
              ('ActiveProcessLimit',W.DWORD),('Affinity',C.c_size_t),('PriorityClass',W.DWORD),('SchedulingClass',W.DWORD)]


class IoCounters(C.Structure):
    _fields_=[(n,C.c_ulonglong) for n in ('ReadOperationCount','WriteOperationCount','OtherOperationCount',
                                       'ReadTransferCount','WriteTransferCount','OtherTransferCount')]


class ExtendedLimit(C.Structure):
    _fields_=[('BasicLimitInformation',BasicLimit),('IoInfo',IoCounters),('ProcessMemoryLimit',C.c_size_t),
              ('JobMemoryLimit',C.c_size_t),('PeakProcessMemoryUsed',C.c_size_t),('PeakJobMemoryUsed',C.c_size_t)]


class WindowsJob:
    def __init__(self,process,memory_mb=256):
        if os.name!='nt' or not 32<=memory_mb<=256:
            raise RuntimeError('INVALID_WINDOWS_JOB_LIMIT')
        kernel=self.kernel=C.WinDLL('kernel32',use_last_error=True)
        kernel.CreateJobObjectW.argtypes=[C.c_void_p,W.LPCWSTR]
        kernel.CreateJobObjectW.restype=W.HANDLE
        kernel.SetInformationJobObject.argtypes=[W.HANDLE,C.c_int,C.c_void_p,W.DWORD]
        kernel.SetInformationJobObject.restype=W.BOOL
        kernel.AssignProcessToJobObject.argtypes=[W.HANDLE,W.HANDLE]
        kernel.AssignProcessToJobObject.restype=W.BOOL
        kernel.CloseHandle.argtypes=[W.HANDLE]
        kernel.CloseHandle.restype=W.BOOL
        self.handle=kernel.CreateJobObjectW(None,None)
        if not self.handle:
            raise C.WinError(C.get_last_error())
        limits=ExtendedLimit()
        # PROCESS_MEMORY | KILL_ON_JOB_CLOSE; no breakaway permitted.
        limits.BasicLimitInformation.LimitFlags=0x100|0x2000
        limits.ProcessMemoryLimit=int(memory_mb*1024*1024)
        if (not kernel.SetInformationJobObject(self.handle,9,C.byref(limits),C.sizeof(limits))
                or not kernel.AssignProcessToJobObject(self.handle,W.HANDLE(int(process._handle)))):
            error=C.get_last_error()
            self.close()
            raise C.WinError(error)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle=None

    def __enter__(self):
        return self

    def __exit__(self,*args):
        self.close()
