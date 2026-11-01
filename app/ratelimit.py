"""Pembatas percobaan gagal sederhana (in-memory, per proses).

Cukup untuk 1 worker gunicorn seperti konfigurasi sekarang. Jika nanti worker
ditambah atau ada beberapa container, pindahkan ke penyimpanan bersama (DB/Redis).
"""
import threading
import time
from collections import deque


class FailureLimiter:
    def __init__(self, max_fails, window_seconds, max_keys=10_000):
        self.max_fails = max_fails
        self.window = window_seconds
        self.max_keys = max_keys
        self._hits = {}
        self._lock = threading.Lock()

    def _prune(self, key, now):
        q = self._hits.get(key)
        if q is None:
            return None
        while q and now - q[0] > self.window:
            q.popleft()
        if not q:
            del self._hits[key]
            return None
        return q

    def retry_after(self, key):
        """Detik tersisa jika key sedang diblokir, 0 jika boleh mencoba."""
        now = time.monotonic()
        with self._lock:
            q = self._prune(key, now)
            if q is None or len(q) < self.max_fails:
                return 0
            return max(1, int(self.window - (now - q[0])) + 1)

    def record(self, key):
        now = time.monotonic()
        with self._lock:
            if len(self._hits) >= self.max_keys:  # cegah memori membengkak
                for k in list(self._hits):
                    self._prune(k, now)
            self._hits.setdefault(key, deque()).append(now)

    def reset(self, key):
        with self._lock:
            self._hits.pop(key, None)
