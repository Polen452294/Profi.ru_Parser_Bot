from __future__ import annotations

import os
from pathlib import Path


class AlreadyRunningError(RuntimeError):
    pass


class SingleInstanceLock:
    """Межпроцессная блокировка одного экземпляра сервиса."""

    def __init__(self, path: Path):
        self.path = path
        self._file = None

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file = self.path.open("a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt

                file.seek(0, os.SEEK_END)
                if file.tell() == 0:
                    file.write(" ")
                    file.flush()
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            file.close()
            raise AlreadyRunningError(
                "Другой экземпляр парсера уже запущен"
            ) from exc

        file.seek(0)
        file.truncate()
        file.write(str(os.getpid()))
        file.flush()
        self.path.chmod(0o600)
        self._file = file

    def release(self) -> None:
        if self._file is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None


class InterProcessFileLock:
    """Короткая блокировка файла между парсером и процессом уведомлений."""

    def __init__(self, path: Path):
        self.path = path
        self._file = None

    def __enter__(self) -> "InterProcessFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file = self.path.open("a+", encoding="utf-8")
        if os.name == "nt":
            import msvcrt

            file.seek(0, os.SEEK_END)
            if file.tell() == 0:
                file.write(" ")
                file.flush()
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(file.fileno(), fcntl.LOCK_EX)
        self._file = file
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._file is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None
