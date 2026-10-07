"""A small thread-safe cache. Tools run on worker threads, so shared lookups (the
calendar list, the address book) need a lock, and concurrent callers of the same
key should wait for one load rather than each starting their own."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Hashable
from typing import Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    def __init__(self, ttl: float | None):
        """ttl: seconds an entry stays fresh, or None to keep it for the life of the process."""
        self.ttl = ttl
        self._entries: dict[Hashable, tuple[float, T]] = {}
        self._lock = threading.Lock()
        self._key_locks: dict[Hashable, threading.Lock] = {}
        # Bumped by invalidate(), so a load that started before it can't store stale data after it.
        self._generation: dict[Hashable, int] = {}

    def get(self, key: Hashable, load: Callable[[], T], refresh: bool = False) -> T:
        with self._lock:
            key_lock = self._key_locks.setdefault(key, threading.Lock())
        with key_lock:  # one load per key at a time; other keys aren't blocked
            with self._lock:
                entry = self._entries.get(key)
                generation = self._generation.get(key, 0)
            if entry and not refresh and (self.ttl is None or time.monotonic() - entry[0] < self.ttl):
                return entry[1]
            value = load()
            with self._lock:
                if self._generation.get(key, 0) == generation:
                    self._entries[key] = (time.monotonic(), value)
            return value

    def invalidate(self, key: Hashable | None = None) -> None:
        with self._lock:
            keys = list(self._entries) + list(self._generation) if key is None else [key]
            for k in keys:
                self._entries.pop(k, None)
                self._generation[k] = self._generation.get(k, 0) + 1
