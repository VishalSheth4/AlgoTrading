"""
"greenDollar" -- a BULL-ONLY variant of GreenDollarStrategy.

Keeps exactly the source Mark-2 Pine script's `Bullvolumecheck` $ marker:

    Bullvolumecheck = (open < close and volume > maxx and volume > avgg
                        and volume[5] < avgg and volume[4] < avgg
                        and volume[3] < avgg and volume[2] < avgg
                        and volume[1] < avgg and alertIsAboveEma)

    plotshape(Bullvolumecheck ? TrendLine + ta.atr(8) : na, text = '💲',
              style = shape.labeldown, location = location.belowbar,
              color = color.green, ...)

That's already exactly what GreenDollarStrategy.compute() computes for its
`_bull` column/price (bullish candle + volume spike above both the 6-bar
max and 12-bar average + 5 quiet bars before it + price not overextended
above the EMA, marked at the ATR-trailing TrendLine + 8-period ATR) -- the
Pine script's `Bearishvolumecheck` (the mirrored SHORT-side $ marker) is
deliberately NOT ported here. So this class computes the identical parent
signal, then drops the bear side entirely: registered under its own name
so it gets its own StrategyToggleRegistry entry (enable/disable, alerts,
auto-trading) and its own RR-matrix row, independent of "green_dollar"
(which still fires both bull and bear).
"""

from __future__ import annotations

import pandas as pd

from .green_dollar import GreenDollarStrategy


class GreenDollarCloneStrategy(GreenDollarStrategy):
    name = "greenDollar"

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = super().compute(df)
        # Bull-only: never fires a bearish signal, no matter what the
        # parent's Bearishvolumecheck-equivalent logic found.
        bear_col = f"{self.name}_bear"
        df[bear_col] = False if len(df) else pd.Series(dtype="bool")
        return df
