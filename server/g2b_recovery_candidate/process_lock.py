"""Unmodified process-lock class extracted from the existing collector."""
import pathlib
import os
import json
import datetime as dt
from zoneinfo import ZoneInfo
KST = ZoneInfo("Asia/Seoul")

class LocalDbProcessLock:
    """Cross-platform non-blocking process lock for one local SQLite database.

    The small lock file may remain on disk after exit; the operating-system lock,
    not file existence, decides ownership. This means a crash cannot leave a stale
    lock that permanently blocks the next collector run.
    """

    def __init__(self, db_path):
        path = pathlib.Path(str(db_path)).expanduser().resolve()
        self.path = pathlib.Path(str(path) + ".collector.lock")
        self._handle = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            handle.close()
            return False

        try:
            metadata = {
                "pid": os.getpid(),
                "locked_at_kst": dt.datetime.now(KST).isoformat(timespec="seconds"),
            }
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps(metadata, ensure_ascii=False).encode("utf-8"))
            handle.flush()
            handle.seek(0)
        except Exception:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
            raise
        self._handle = handle
        return True

    def release(self):
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("LOCAL_COLLECTOR_ALREADY_RUNNING")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()

