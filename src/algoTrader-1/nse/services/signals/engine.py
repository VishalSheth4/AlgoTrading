"""
Orchestrates a list of NseSignalStrategy instances against one
(symbol, timeframe) OHLC series.

Adding filter #2, #3, ... means writing a new NseSignalStrategy subclass
and appending an instance to the list passed into NseSignalEngine --
this class never changes (Open/Closed Principle). Same pattern as
StrategyRunner/SMCEngine on the XAUUSD side.
"""

from __future__ import annotations

import pandas as pd

from .base import NseSignal, NseSignalStrategy


class NseSignalEngine:
    def __init__(self, strategies: list[NseSignalStrategy]):
        self._strategies = strategies

    def compute_for(self, symbol: str, timeframe: str, df: pd.DataFrame) -> list[NseSignal]:
        signals: list[NseSignal] = []
        for strategy in self._strategies:
            signals.extend(strategy.compute(symbol, timeframe, df))
        return signals
