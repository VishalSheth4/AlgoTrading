"""
Supertrend flip signal for NSE stocks.

Reuses the SAME SupertrendIndicator/TrendFlipDetector already built and
verified for the XAUUSD dashboard (core app) -- Supertrend math only
needs OHLC data, so there's no reason to duplicate it here. This does
NOT touch or import anything XAUUSD-specific (CandleService, the MT5
connection, etc.), only the pure-computation indicator classes.

STRICT RULE (same principle applied to XAUUSD's auto-trading and Green
Dollar signals): a flip is only ever confirmed from the last FULLY
CLOSED candle. The most recent bar in an intraday (15m/1h) fetch may
still be forming while the market is open, so flip detection runs on
`df` with that last row dropped, never on the raw last row directly.
"""

from __future__ import annotations

import pandas as pd

from core.services.indicators.flip_detector import TrendFlipDetector
from core.services.indicators.supertrend import SupertrendIndicator

from .base import NseSignal, NseSignalStrategy


class SupertrendFlipSignalStrategy(NseSignalStrategy):
    name = "supertrend_flip"

    def __init__(self, period: int = 10, multiplier: float = 3.0):
        self._period = period
        self._indicator = SupertrendIndicator(period, multiplier)
        self._flip_detector = TrendFlipDetector()

    def compute(self, symbol: str, timeframe: str, df: pd.DataFrame) -> list[NseSignal]:
        if df.empty or len(df) < self._period + 5:
            return []

        enriched = self._indicator.compute(df)

        # Drop the last row before flip-checking -- it may still be a
        # forming candle if the market is currently open.
        closed_enriched = enriched.iloc[:-1]
        if len(closed_enriched) < 2:
            return []

        flip_direction = self._flip_detector.detect(closed_enriched)
        if not flip_direction:
            return []

        closed_row = closed_enriched.iloc[-1]
        signal = "buy" if flip_direction == "bullish" else "sell"

        return [NseSignal(
            symbol=symbol,
            timeframe=timeframe,
            strategy=self.name,
            signal=signal,
            detected_at_unix=int(pd.Timestamp(closed_row["time"]).timestamp()),
            price=float(closed_row["close"]),
        )]
