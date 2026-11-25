"""In-memory rate limiting, concurrency control, caching, deduplication, and privacy hashing."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from collections import deque
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


def hash_identifier(value: str, salt: str) -> str:
    """Return a truncated salted HMAC-SHA256 digest for privacy-safe logging."""
    digest = hmac.new(
        salt.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:12]


class SlidingWindowRateLimiter:
    """In-memory sliding-window rate limiter keyed by hashed identifier."""

    def __init__(self, max_requests: int, window_seconds: int = 3600) -> None:
        self.max_requests = max(1, max_requests)
        self.window_seconds = max(1, window_seconds)
        self._buckets: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def allow(self, key: str) -> tuple[bool, int]:
        """Check and record a request. Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        async with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = deque()
                self._buckets[key] = bucket

            while bucket and bucket[0] <= cutoff:
                bucket.popleft()

            if len(bucket) >= self.max_requests:
                retry_after = max(1, int(bucket[0] + self.window_seconds - now))
                return False, retry_after

            bucket.append(now)
            # Prune completely empty buckets occasionally
            if len(self._buckets) > 2048:
                empty_keys = [k for k, v in self._buckets.items() if not v]
                for k in empty_keys:
                    self._buckets.pop(k, None)
            return True, 0


@dataclass
class _CacheEntry(Generic[T]):
    value: T
    created_at: float
    expires_at: float


class TTLCache(Generic[T]):
    """Simple async-safe in-memory TTL cache with bounded capacity."""

    def __init__(self, ttl_seconds: int, max_items: int = 512) -> None:
        self.ttl_seconds = max(0, ttl_seconds)
        self.max_items = max(16, max_items)
        self._store: dict[str, _CacheEntry[T]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> tuple[T | None, int]:
        """Return (value, age_seconds) if present and unexpired, else (None, 0)."""
        if self.ttl_seconds <= 0:
            return None, 0
        now = time.monotonic()
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None, 0
            if now >= entry.expires_at:
                self._store.pop(key, None)
                return None, 0
            age = max(0, int(now - entry.created_at))
            return entry.value, age

    async def set(self, key: str, value: T, ttl_override: int | None = None) -> None:
        ttl = self.ttl_seconds if ttl_override is None else max(0, ttl_override)
        if ttl <= 0:
            return
        now = time.monotonic()
        async with self._lock:
            self._prune_expired(now)
            if len(self._store) >= self.max_items and key not in self._store:
                oldest_key = min(self._store, key=lambda k: self._store[k].created_at)
                self._store.pop(oldest_key, None)
            self._store[key] = _CacheEntry(
                value=value,
                created_at=now,
                expires_at=now + ttl,
            )

    async def mark_if_new(self, key: str, ttl_override: int | None = None) -> bool:
        """Atomically record key if absent/expired. Returns True if newly marked, False if duplicate."""
        ttl = self.ttl_seconds if ttl_override is None else max(1, ttl_override)
        now = time.monotonic()
        async with self._lock:
            self._prune_expired(now)
            existing = self._store.get(key)
            if existing is not None and now < existing.expires_at:
                return False
            if len(self._store) >= self.max_items:
                oldest_key = min(self._store, key=lambda k: self._store[k].created_at)
                self._store.pop(oldest_key, None)
            self._store[key] = _CacheEntry(
                value=True,  # type: ignore[arg-type]
                created_at=now,
                expires_at=now + ttl,
            )
            return True

    def _prune_expired(self, now: float) -> None:
        expired = [k for k, entry in self._store.items() if now >= entry.expires_at]
        for k in expired:
            self._store.pop(k, None)


class RuntimeLimits:
    """Container for shared rate limiters, semaphore, caches, and user preferences."""

    def __init__(
        self,
        *,
        rate_limit_per_hour: int,
        max_concurrent_analyses: int,
        cache_ttl_seconds: int,
    ) -> None:
        self.web_rate_limiter = SlidingWindowRateLimiter(
            max_requests=rate_limit_per_hour,
            window_seconds=3600,
        )
        self.whatsapp_rate_limiter = SlidingWindowRateLimiter(
            max_requests=rate_limit_per_hour,
            window_seconds=3600,
        )
        self.analysis_semaphore = asyncio.Semaphore(max_concurrent_analyses)
        self.result_cache: TTLCache[dict] = TTLCache(
            ttl_seconds=cache_ttl_seconds,
            max_items=256,
        )
        self.webhook_dedupe: TTLCache[bool] = TTLCache(
            ttl_seconds=86400,  # 24 hours
            max_items=2048,
        )
        self.whatsapp_last_report: TTLCache[dict] = TTLCache(
            ttl_seconds=1800,  # 30 minutes
            max_items=512,
        )
        self.whatsapp_user_lang: TTLCache[str] = TTLCache(
            ttl_seconds=86400 * 7,
            max_items=1024,
        )
