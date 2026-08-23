"""
Process-wide MT5 connection singleton.

SRP: this module's only job is "hand back one already-managed
MT5ConnectionManager" -- nothing else touches mt5.initialize()/shutdown()
directly. CandleService and PriceService both depend on this instead of
each opening their own connection, so a 1s price poll and a 15s chart
poll never fight over separate MT5 sessions.
"""

from __future__ import annotations

import threading

from .mt5_multi_timeframe_reader import MT5ConnectionManager

_connection: MT5ConnectionManager | None = None
_lock = threading.Lock()


def get_shared_connection() -> MT5ConnectionManager:
    global _connection
    if _connection is None:
        with _lock:
            if _connection is None:
                _connection = MT5ConnectionManager()
    return _connection
