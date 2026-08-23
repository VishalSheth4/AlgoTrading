"""
Dispatches to the right OhlcDataSource per timeframe -- yfinance for
intraday (15m/1h/4h), nsepython for Daily/Weekly (NSE's own real history).
Itself an OhlcDataSource, so it composes transparently with
CachedOhlcDataSource and anything else built against the abstraction.
"""

from __future__ import annotations

import pandas as pd

from .base import OhlcDataSource


class RoutedOhlcDataSource(OhlcDataSource):
    def __init__(self, sources_by_timeframe: dict[str, OhlcDataSource]):
        self._sources_by_timeframe = sources_by_timeframe

    def fetch(self, symbol: str, timeframe: str) -> pd.DataFrame:
        source = self._sources_by_timeframe.get(timeframe)
        if source is None:
            return pd.DataFrame()
        return source.fetch(symbol, timeframe)
