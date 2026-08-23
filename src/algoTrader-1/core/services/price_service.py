"""
Lightweight live tick price for the header (polled every 1s).

Kept separate from CandleService on purpose (SRP + performance): a
symbol_info_tick() call is cheap, so the header can refresh at 1Hz
without paying the cost of the 6-timeframe candle+indicator pipeline.
Both services share one MT5 connection via mt5_connection.get_shared_connection().
"""

from __future__ import annotations

import os
import threading

import MetaTrader5 as mt5

from .mt5_connection import get_shared_connection

DEFAULT_SYMBOL = os.environ.get("MT5_SYMBOL", "XAUUSD")


class PriceService:
    def __init__(self, symbol: str = DEFAULT_SYMBOL, mt5_module=mt5):
        self._symbol = symbol
        self._mt5 = mt5_module
        self._connection = get_shared_connection()
        self._lock = threading.Lock()

    def get_price(self) -> dict | None:
        with self._lock:
            if not self._connection.is_connected and not self._connection.connect():
                return None
            tick = self._mt5.symbol_info_tick(self._symbol)

        if tick is None:
            return None

        return {
            "symbol": self._symbol,
            "bid": tick.bid,
            "ask": tick.ask,
            "last": tick.last,
            "time": tick.time,
        }

    @property
    def symbol(self) -> str:
        return self._symbol


_service: PriceService | None = None
_service_lock = threading.Lock()


def get_price_service() -> PriceService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = PriceService()
    return _service
