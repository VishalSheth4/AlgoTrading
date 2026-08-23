"""
Strategy abstraction.

Distinct from indicators/base.py's Indicator: an Indicator draws a value
(Supertrend line, engulfing flag) derived directly from OHLC. A Strategy
is a trade-signal generator -- it may combine several indicators/filters
(volume, EMA, a trailing trend line, ...) to decide "is this candle worth
flagging". Keeping them separate interfaces means a future backtest-only
strategy (e.g. one that also needs SL/TP sizing) can grow its own contract
without disturbing the chart-overlay Indicator pipeline.

SOLID:
- SRP: a Strategy only decides its own signal; it doesn't know about MT5,
       Django, or how its output gets displayed.
- OCP: adding a new strategy means writing a new Strategy subclass and
       registering it with StrategyRunner -- runner.py never changes.
- LSP: any Strategy is interchangeable wherever the interface is expected.
- ISP: one method, compute().
- DIP: StrategyRunner (and CandleService) depend on this abstraction, not
       on any concrete strategy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    name: str

    @abstractmethod
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return a new DataFrame with this strategy's signal column(s)
        appended. Input df must have open/high/low/close/tick_volume,
        sorted oldest -> newest."""
        ...
