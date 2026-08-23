"""
Applies a list of Strategy instances to a DataFrame in sequence.

Adding a new strategy means appending an instance to the list passed into
CandleService (see candle_service.py) -- this class never needs to change.
"""

from __future__ import annotations

import pandas as pd

from .base import Strategy
from .registry import StrategyToggleRegistry


class StrategyRunner:
    def __init__(self, strategies: list[Strategy], registry: StrategyToggleRegistry | None = None):
        self._strategies = strategies
        self._registry = registry

    def run(self, df: pd.DataFrame) -> pd.DataFrame:
        for strategy in self._strategies:
            if self._registry is not None and not self._registry.is_enabled(strategy.name):
                continue
            df = strategy.compute(df)
        return df
