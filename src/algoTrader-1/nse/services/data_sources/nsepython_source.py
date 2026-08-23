"""
Daily/Weekly OHLC via nsepython (NSE's own historical data -- full real
history, unlike the intraday timeframes which NSE's free site doesn't
expose at all).

NOTE: nsepython scrapes NSE's public website (fetches the homepage first
for session cookies, then queries its JSON endpoints, per NSE's normal
anti-bot flow). NSE is known to rate-limit and IP-block aggressively --
if this returns empty repeatedly, you're likely being throttled/blocked;
back off the poll frequency (see nse_dashboard_service.py's per-timeframe
cache TTLs) before assuming a code bug.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from ..resample import resample_ohlc
from .base import OhlcDataSource

# Trading-day lookback long enough for weekly resampling and Supertrend's
# ATR warm-up to have enough history.
LOOKBACK_DAYS = 400


class NsepythonDataSource(OhlcDataSource):
    def __init__(self, nsepython_module=None):
        if nsepython_module is None:
            import nsepython as nsepython_default
            nsepython_module = nsepython_default
        self._nsepython = nsepython_module

    def fetch(self, symbol: str, timeframe: str) -> pd.DataFrame:
        if timeframe not in ("1d", "1w"):
            return pd.DataFrame()

        end = datetime.now()
        start = end - timedelta(days=LOOKBACK_DAYS)

        try:
            raw = self._nsepython.equity_history(
                symbol, "EQ", start.strftime("%d-%m-%Y"), end.strftime("%d-%m-%Y")
            )
        except Exception:
            return pd.DataFrame()

        if raw is None or raw.empty or "CH_TIMESTAMP" not in raw.columns:
            return pd.DataFrame()

        daily = pd.DataFrame({
            "time": pd.to_datetime(raw["CH_TIMESTAMP"]),
            "open": raw["CH_OPENING_PRICE"].astype(float),
            "high": raw["CH_TRADE_HIGH_PRICE"].astype(float),
            "low": raw["CH_TRADE_LOW_PRICE"].astype(float),
            "close": raw["CH_CLOSING_PRICE"].astype(float),
            "volume": raw["CH_TOT_TRADED_QTY"].astype(float),
        }).sort_values("time").reset_index(drop=True)

        if timeframe == "1w":
            return resample_ohlc(daily, "1W")
        return daily
