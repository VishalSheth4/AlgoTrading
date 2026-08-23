"""
Indicator abstraction.

SOLID:
- ISP: one method, nothing consumers don't need.
- OCP: new indicators (RSI, EMA, ...) are added by implementing Indicator,
       never by modifying CandleService or existing indicators.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Indicator(ABC):
    @abstractmethod
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return a new DataFrame with indicator column(s) appended.
        Input df must have open/high/low/close columns, sorted oldest -> newest."""
        ...
