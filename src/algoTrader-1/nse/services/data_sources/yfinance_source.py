"""
Intraday (15m/1h/4h) OHLC via yfinance.

Chosen for intraday data specifically because NSE's free public site
(what nsepython scrapes) does not expose intraday candle history for
individual equities at all -- only end-of-day data. Yahoo Finance has no
native "4h" interval, so 4h bars are built by resampling 1h data.

NSE tickers on Yahoo Finance use a ".NS" suffix (e.g. "RELIANCE.NS").
"""

from __future__ import annotations

import pandas as pd

from ..resample import resample_ohlc
from .base import OhlcDataSource

# yfinance's own limits: intraday intervals under 1d only return the last
# ~60 days of history.
_INTERVAL_BY_TIMEFRAME = {"15m": "15m", "1h": "60m"}
_PERIOD_BY_TIMEFRAME = {"15m": "60d", "1h": "60d"}


class YFinanceDataSource(OhlcDataSource):
    def __init__(self, yf_module=None):
        if yf_module is None:
            import yfinance as yf_default
            yf_module = yf_default
        self._yf = yf_module

    def fetch(self, symbol: str, timeframe: str) -> pd.DataFrame:
        if timeframe == "4h":
            hourly = self.fetch(symbol, "1h")
            return resample_ohlc(hourly, "4h")

        interval = _INTERVAL_BY_TIMEFRAME.get(timeframe)
        period = _PERIOD_BY_TIMEFRAME.get(timeframe)
        if interval is None:
            return pd.DataFrame()

        try:
            ticker = self._yf.Ticker(f"{symbol}.NS")
            raw = ticker.history(period=period, interval=interval)
        except Exception:
            return pd.DataFrame()

        if raw is None or raw.empty:
            return pd.DataFrame()

        raw = raw.reset_index()
        time_col = "Datetime" if "Datetime" in raw.columns else "Date"

        return pd.DataFrame({
            "time": pd.to_datetime(raw[time_col]).dt.tz_localize(None),
            "open": raw["Open"].astype(float),
            "high": raw["High"].astype(float),
            "low": raw["Low"].astype(float),
            "close": raw["Close"].astype(float),
            "volume": raw["Volume"].astype(float),
        })
