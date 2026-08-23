"""
TTL cache wrapping any OhlcDataSource, keyed by (symbol, timeframe).

This is the actual rate-limit safeguard: the dashboard's 1-minute refresh
cadence is a UI/polling concern, not a "hit the network every minute"
concern. A candle on a given timeframe cannot possibly change more often
than its own bar interval, so each timeframe gets a TTL close to its bar
length (with a floor, so 15m/1h/4h don't all collapse to the same
aggressive cadence). NIFTY 50 (50 symbols) x 5 timeframes refetched at
these TTLs stays comfortably under NSE's ~30-60 requests/minute limit --
uncached, it would be 250 requests every single poll.
"""

from __future__ import annotations

import threading
import time

import pandas as pd

from .base import OhlcDataSource

DEFAULT_TTL_SECONDS_BY_TIMEFRAME = {
    "15m": 5 * 60,
    "1h": 10 * 60,
    "4h": 20 * 60,
    "1d": 30 * 60,
    "1w": 60 * 60,
}


class CachedOhlcDataSource(OhlcDataSource):
    def __init__(self, inner: OhlcDataSource, ttl_seconds_by_timeframe: dict[str, int] | None = None):
        self._inner = inner
        self._ttl = ttl_seconds_by_timeframe or DEFAULT_TTL_SECONDS_BY_TIMEFRAME
        self._cache: dict[tuple[str, str], pd.DataFrame] = {}
        self._cached_at: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def fetch(self, symbol: str, timeframe: str) -> pd.DataFrame:
        key = (symbol, timeframe)
        ttl = self._ttl.get(timeframe, 300)

        with self._lock:
            cached = self._cache.get(key)
            cached_at = self._cached_at.get(key, 0.0)
            if cached is not None and (time.time() - cached_at) < ttl:
                return cached

        fresh = self._inner.fetch(symbol, timeframe)

        with self._lock:
            if not fresh.empty:
                self._cache[key] = fresh
                self._cached_at[key] = time.time()
            elif key in self._cache:
                # Fetch failed/empty (rate-limited, network hiccup, ...) --
                # keep serving the last good data rather than blanking the
                # row out.
                return self._cache[key]

        return fresh
