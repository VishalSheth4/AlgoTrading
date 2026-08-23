"""
Trade execution abstraction.

SOLID:
- SRP: a TradeExecutor only knows how to place ONE trade for ONE signal.
- OCP: a new execution path (paper trading, a different broker, ...) is a
       new TradeExecutor implementation -- nothing else changes.
- DIP: AutoTradingService and ClosedCandleTradeGate depend on this
       abstraction, never on MetaTrader5 directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class TradeSignal:
    timeframe: str
    direction: str  # "buy" | "sell"
    candle_time_unix: int  # identifies the CLOSED candle that triggered this -- used for dedup
    price_hint: float
    source: str = "unknown"  # originating strategy/column name, e.g. "green_dollar_bear"
    # Per-strategy SL/TP, expressed as a PRICE DISTANCE (not an absolute
    # price) from wherever the order actually fills -- computed by
    # AutoTradingService from the strategy's own {rule.column}_sl/_tp price
    # columns at signal time (risk = |signal-time entry - signal-time SL|),
    # then re-applied relative to the live fill price rather than reusing
    # the stale historical price, since the live tick can have moved since
    # the candle closed. None means the strategy didn't provide one --
    # TradeExecutor falls back to its own fixed default distance.
    sl_distance: float | None = None
    tp_distance: float | None = None


class TradeExecutor(ABC):
    @abstractmethod
    def execute(self, signal: TradeSignal) -> dict:
        """Place the trade. Returns a result dict, e.g.
        {"success": bool, "message": str, "order_id": int | None}."""
        ...
