"""
OHLC data source abstraction.

SOLID:
- SRP: a data source only knows how to fetch candles for one symbol/
       timeframe from one place.
- OCP: a new data source (a broker API, a paid vendor, ...) is a new
       OhlcDataSource implementation -- nothing else changes.
- LSP: any OhlcDataSource is interchangeable wherever the interface is used.
- DIP: NseTimeframeRouter (and everything above it) depends on this
       abstraction, never on nsepython/yfinance directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class OhlcDataSource(ABC):
    @abstractmethod
    def fetch(self, symbol: str, timeframe: str) -> pd.DataFrame:
        """Returns a DataFrame with columns [time, open, high, low, close,
        volume], time as tz-naive datetime, sorted oldest -> newest.
        Returns an empty DataFrame (never raises) if the timeframe isn't
        supported by this source, or the fetch failed."""
        ...
