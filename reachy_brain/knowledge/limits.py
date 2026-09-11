"""Apply only inside a disposable parser child, before importing document libraries."""

import sys

_job_handles = []  # Windows owns these until this short-lived child exits.


def limit_parser_memory(limit=512 * 1024 * 1024):
    if type(limit) is not int or not 64 * 1024**2 <= limit <= 1024**3:
        raise ValueError("invalid_parser_memory_limit")
    if sys.platform != "win32":
        import resource

        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        bounds = [limit, *(n for n in (soft, hard) if n != resource.RLIM_INFINITY)]
        effective = min(bounds)
        resource.setrlimit(resource.RLIMIT_AS, (effective, effective))
        return {"kind": "address_space", "bytes": effective}

    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_longlong),
            ("job_time", ctypes.c_longlong),
            ("flags", wintypes.DWORD),
            ("minimum_working_set", ctypes.c_size_t),
            ("maximum_working_set", ctypes.c_size_t),
            ("active_processes", wintypes.DWORD),
            ("affinity", ctypes.c_size_t),
            ("priority", wintypes.DWORD),
            ("scheduling", wintypes.DWORD),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("basic", BasicLimits),
            ("io", ctypes.c_ulonglong * 6),
            ("process_memory", ctypes.c_size_t),
            ("job_memory", ctypes.c_size_t),
            ("peak_process_memory", ctypes.c_size_t),
            ("peak_job_memory", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise OSError("parser_memory_limit_unavailable")
    try:
        information = ExtendedLimits()
        information.basic.flags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
        information.process_memory = limit
        if not kernel.SetInformationJobObject(
            job, 9, ctypes.byref(information), ctypes.sizeof(information)
        ):
            raise OSError("parser_memory_limit_unavailable")
        if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
            raise OSError("parser_memory_limit_unavailable")
    except BaseException:
        kernel.CloseHandle(job)
        raise
    _job_handles.append(job)
    return {"kind": "committed_memory", "bytes": limit}
