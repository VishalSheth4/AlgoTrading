"""
Generic OHLCV resampler -- used to build 4H bars from 1H data (Yahoo
Finance has no native "4h" interval) and Weekly bars from Daily data.
"""

from __future__ import annotations

import pandas as pd


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if df.empty:
        return df

    indexed = df.set_index("time")
    agg = indexed.resample(rule, label="right", closed="right").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    })
    agg = agg.dropna(subset=["open"])
    return agg.reset_index()
