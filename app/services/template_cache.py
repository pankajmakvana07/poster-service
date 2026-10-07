import os
import re
import shutil
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional, Protocol, Tuple

from app.config import Settings

_KEY_RE = re.compile(r"^[0-9a-f]{64}$")


class TemplateCache(Protocol):
    def get(self, key: str) -> Optional[bytes]: ...

    def put(self, key: str, data: bytes) -> None: ...

    def clear(self) -> None: ...


class NullCache:
    def get(self, key: str) -> Optional[bytes]:
        return None

    def put(self, key: str, data: bytes) -> None:
        return None

    def clear(self) -> None:
        return None


class MemoryTemplateCache:
    """Bounded in-memory TTL cache for template image bytes."""

    def __init__(self, ttl_seconds: int, max_entries: int, max_bytes: int):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._items: "OrderedDict[str, Tuple[float, bytes]]" = OrderedDict()
        self._total = 0
        self._lock = threading.Lock()

    def _evict(self, now: float) -> None:
        for key in [k for k, (exp, _) in self._items.items() if exp <= now]:
            self._total -= len(self._items.pop(key)[1])
        while self._items and (len(self._items) > self.max_entries or self._total > self.max_bytes):
            _, (_, data) = self._items.popitem(last=False)
            self._total -= len(data)

    def get(self, key: str) -> Optional[bytes]:
        now = time.time()
        with self._lock:
            self._evict(now)
            item = self._items.get(key)
            if item is None:
                return None
            self._items.move_to_end(key)
            return item[1]

    def put(self, key: str, data: bytes) -> None:
        now = time.time()
        with self._lock:
            if key in self._items:
                self._total -= len(self._items.pop(key)[1])
            self._items[key] = (now + self.ttl, data)
            self._total += len(data)
            self._evict(now)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._total = 0


class DiskTemplateCache:
    """Temporary on-disk TTL cache shared by all worker processes; entries expire automatically."""

    def __init__(self, directory: Path, ttl_seconds: int, max_entries: int):
        self.directory = Path(directory)
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass
        self.purge()

    def _path(self, key: str) -> Optional[Path]:
        return self.directory / f"{key}.bin" if _KEY_RE.match(key) else None

    def purge(self) -> None:
        now = time.time()
        entries = []
        for path in self.directory.glob("*.bin"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if now - mtime > self.ttl:
                path.unlink(missing_ok=True)
            else:
                entries.append((mtime, path))
        for path in self.directory.glob("*.tmp"):
            try:
                if now - path.stat().st_mtime > 300:
                    path.unlink(missing_ok=True)
            except OSError:
                continue
        entries.sort()
        for _, path in entries[: max(0, len(entries) - self.max_entries)]:
            path.unlink(missing_ok=True)

    def get(self, key: str) -> Optional[bytes]:
        path = self._path(key)
        if path is None:
            return None
        try:
            if time.time() - path.stat().st_mtime > self.ttl:
                path.unlink(missing_ok=True)
                return None
            return path.read_bytes()
        except OSError:
            return None

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        if path is None:
            return
        temp = self.directory / f"{key}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            temp.write_bytes(data)
            os.replace(temp, path)
        except OSError:
            temp.unlink(missing_ok=True)
            return
        self.purge()

    def clear(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True, exist_ok=True)


def build_cache(settings: Settings) -> TemplateCache:
    backend = settings.template_cache_backend.lower()
    if backend == "memory":
        return MemoryTemplateCache(
            settings.template_cache_ttl_seconds,
            settings.template_cache_max_entries,
            settings.template_cache_max_memory_mb * 1024 * 1024,
        )
    if backend == "disk":
        return DiskTemplateCache(
            settings.cache_directory, settings.template_cache_ttl_seconds, settings.template_cache_max_entries
        )
    return NullCache()
