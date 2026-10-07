"""TTLCache: expiry, refresh, and one load per key under concurrent use."""

import threading
import time

from icloud_mail_mcp.cache import TTLCache


def test_ttl_and_refresh(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    cache = TTLCache(ttl=10)
    loads = []
    load = lambda: loads.append(1) or len(loads)  # noqa: E731
    assert cache.get("k", load) == 1
    assert cache.get("k", load) == 1  # fresh
    now[0] += 11
    assert cache.get("k", load) == 2  # expired
    assert cache.get("k", load, refresh=True) == 3
    cache.invalidate("k")
    assert cache.get("k", load) == 4


def test_concurrent_callers_share_one_load():
    cache = TTLCache(ttl=None)
    started = threading.Event()
    loads = []

    def slow_load():
        loads.append(1)
        started.set()
        time.sleep(0.2)
        return "value"

    results = []
    threads = [threading.Thread(target=lambda: results.append(cache.get("k", slow_load))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert loads == [1] and results == ["value"] * 8


def test_other_keys_are_not_blocked():
    cache = TTLCache(ttl=None)
    release = threading.Event()
    threading.Thread(target=lambda: cache.get("slow", lambda: release.wait(5) or "slow"), daemon=True).start()
    time.sleep(0.05)
    started = time.monotonic()
    assert cache.get("fast", lambda: "fast") == "fast"
    assert time.monotonic() - started < 1
    release.set()


def test_invalidate_during_a_load_discards_its_result():
    cache = TTLCache(ttl=None)
    loading, finish = threading.Event(), threading.Event()

    def old_load():
        loading.set()
        finish.wait(5)
        return "before the change"

    t = threading.Thread(target=lambda: cache.get("k", old_load))
    t.start()
    loading.wait(5)
    cache.invalidate("k")  # e.g. create_contact just added someone
    finish.set()
    t.join()
    assert cache.get("k", lambda: "after the change") == "after the change"
