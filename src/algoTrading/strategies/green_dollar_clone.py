"""
"greenDollar" -- a BULL-ONLY variant of GreenDollarStrategy for backtesting.

Mirrors algoTrader's core/services/strategies/green_dollar_clone.py: keeps
only the source Mark-2 Pine script's `Bullvolumecheck` $ marker (bullish
candle + volume spike above both the 6-bar max and 12-bar average + 5 quiet
bars before it + price not overextended above the EMA) -- GreenDollarStrategy
already computes exactly this for its LONG (`signal == 1`) case. The
mirrored SHORT side (`Bearishvolumecheck`) is deliberately dropped.

Registered as its own STRATEGY_MAP entry ("greenDollar", separate from
"green_dollar") so it gets its own RR-matrix row and its own checkbox in
the Backtest UI, and every trade it produces is tagged with this name.
"""

from __future__ import annotations

import numpy as np

from algoTrading.strategies.green_dollar import GreenDollarStrategy


class GreenDollarCloneStrategy(GreenDollarStrategy):
    def generate_signals(self, df):
        df = super().generate_signals(df)
        short_mask = df['signal'] == -1
        df.loc[short_mask, 'signal'] = 0
        df.loc[short_mask, 'sl']     = np.nan
        df.loc[short_mask, 'tp']     = np.nan
        df.loc[short_mask, 'lot']    = 0.0
        return df
