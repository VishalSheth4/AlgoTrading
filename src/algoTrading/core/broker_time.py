"""
MT5 broker/server time is NOT necessarily UTC -- brokers commonly run
their server clock on EET/EEST (UTC+2/UTC+3) or another arbitrary offset.
fetch_mt5.py previously saved candle timestamps as if they were already
UTC, which silently shifted every backtested candle's time by whatever the
broker's offset happens to be -- e.g. a signal algoTrader's live dashboard
correctly times at 00:03 UTC would land in a backtest's trade_data.csv
timestamped 03:03, making the two impossible to line up when debugging
"why didn't the backtest pick up this trade".

Mirrors algoTrader/core/services/broker_time.py's detection logic exactly
(compare a live tick's timestamp against real UTC "now"), kept as a
separate copy rather than a cross-import so algoTrading stays runnable
standalone, independent of algoTrader.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

REFRESH_SECONDS = 3600  # re-detect hourly in case of a broker DST shift


class BrokerTimeOffset:
    def __init__(self, mt5_module, symbol: str = "XAUUSD"):
        self._mt5 = mt5_module
        self._symbol = symbol
        self._offset = timedelta(0)
        self._detected_at: float = 0.0
        self._lock = threading.Lock()

    def get_offset(self) -> timedelta:
        with self._lock:
            now = time.time()
            if self._detected_at > 0 and (now - self._detected_at) < REFRESH_SECONDS:
                return self._offset

            try:
                tick = self._mt5.symbol_info_tick(self._symbol)
                if tick is not None and tick.time:
                    server_naive = datetime.fromtimestamp(tick.time, tz=timezone.utc).replace(tzinfo=None)
                    real_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
                    delta_minutes = (server_naive - real_utc_naive).total_seconds() / 60
                    # Broker offsets are always whole/half/quarter hours;
                    # rounding absorbs the few seconds of request latency
                    # in this comparison.
                    rounded_minutes = round(delta_minutes / 15) * 15
                    self._offset = timedelta(minutes=rounded_minutes)
                    self._detected_at = now
            except Exception:
                pass  # keep the last known offset (defaults to 0) on failure

            return self._offset


_instance: BrokerTimeOffset | None = None
_instance_lock = threading.Lock()


def get_broker_time_offset(mt5_module=None, symbol: str = "XAUUSD") -> BrokerTimeOffset:
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                if mt5_module is None:
                    import MetaTrader5 as mt5_default
                    mt5_module = mt5_default
                _instance = BrokerTimeOffset(mt5_module, symbol)
    return _instance
