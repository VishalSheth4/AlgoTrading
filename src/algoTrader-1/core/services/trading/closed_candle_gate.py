"""
Ensures a trade signal is only acted on ONCE per (timeframe, direction,
closed candle) combination -- prevents re-firing the same trade on every
poll cycle while that candle remains the latest closed one.

This does NOT decide whether a candle has closed -- that determination is
the caller's responsibility (see AutoTradingService, which structurally
never passes a still-forming candle in here). This class only stops
duplicate execution for the SAME already-confirmed signal.
"""

from __future__ import annotations

from .base import TradeExecutor, TradeSignal


class ClosedCandleTradeGate:
    def __init__(self, executor: TradeExecutor):
        self._executor = executor
        self._last_acted: dict[str, int] = {}

    def maybe_trade(self, signal: TradeSignal) -> dict | None:
        # Includes `source` so two different strategies firing the same
        # direction on the same timeframe (e.g. Green Dollar sell and this
        # new Engulfing+Consolidation sell, both on H1) dedup independently
        # instead of one masking the other.
        key = f"{signal.timeframe}:{signal.direction}:{signal.source}"
        if self._last_acted.get(key) == signal.candle_time_unix:
            return None  # already acted on this exact closed candle
        self._last_acted[key] = signal.candle_time_unix
        return self._executor.execute(signal)
