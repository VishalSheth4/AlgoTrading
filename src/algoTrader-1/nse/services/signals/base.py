"""
NSE signal abstraction -- the extension point for future filters/
strategies beyond Supertrend flip.

SOLID:
- SRP: a NseSignalStrategy only detects its own signal type for one
       (symbol, timeframe) OHLC series; it doesn't know about NSE/yfinance
       data sourcing, caching, or how results get displayed.
- OCP: a new filter (e.g. RSI divergence, volume spike, a second
       Supertrend period, ...) is a new NseSignalStrategy subclass
       registered with NseSignalEngine -- nothing else changes.
- LSP: any NseSignalStrategy is interchangeable wherever the interface
       is used.
- ISP: one method, compute().
- DIP: NseSignalEngine (and the dashboard service) depend on this
       abstraction, never on a concrete strategy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


@dataclass
class NseSignal:
    symbol: str
    timeframe: str
    strategy: str      # e.g. "supertrend_flip" -- lets the UI group/filter by strategy later
    signal: str        # e.g. "buy" / "sell" -- generic label, not strategy-specific naming
    detected_at_unix: int
    price: float

    def row_key(self) -> tuple:
        """Identity for dedup: the same (symbol, timeframe, strategy)
        signal on the same closed candle should only ever appear as one
        row, no matter how many times it's recomputed."""
        return (self.symbol, self.timeframe, self.strategy, self.detected_at_unix)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "strategy": self.strategy,
            "signal": self.signal,
            "detected_at_unix": self.detected_at_unix,
            "price": self.price,
        }


class NseSignalStrategy(ABC):
    name: str

    @abstractmethod
    def compute(self, symbol: str, timeframe: str, df: pd.DataFrame) -> list[NseSignal]:
        """df has columns [time, open, high, low, close, volume], sorted
        oldest -> newest. Return zero or more freshly-detected signals
        (typically zero or one, for the most recent CLOSED candle)."""
        ...
