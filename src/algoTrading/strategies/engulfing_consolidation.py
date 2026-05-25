"""
EngulfingConsolidation  (SHORT only)
=====================================
Takes a SELL when:
  1. Supertrend filter passes (see below), AND
  2. Bearish engulfing candle with upper wick longer than lower wick

BEARISH ENGULFING + UPPER WICK condition:
  • Previous candle was bullish         (prev close > prev open)
  • Current candle is bearish           (curr open  > curr close)
  • Current opens  ≥ previous close    (opens at / above prev close)
  • Previous open  ≥ current close     (fully engulfs prev body)
  • Current body   >  previous body    (stronger move)
  • Upper wick     >  lower wick × wick_ratio
      upper_wick = curr high  − curr open   (rejection from top)
      lower_wick = curr close − curr low    (tail from bottom)
    → wick_ratio = 1.0 means upper wick just needs to be longer
    → increase wick_ratio (e.g. 1.5) for a stronger rejection signal

SUPERTREND filter:
  GREEN (trend = +1) → any signal allowed
  RED   (trend = -1) → only the first red_window candles after the flip

ENTRY / SL / TP:
  Entry  : close of signal candle
  SL     : max high of the previous 4 candles
  TP     : entry − rr × risk

All parameters in config.yaml under engulfing_consolidation.
"""

import numpy as np
import pandas as pd

from algoTrading.strategies.SupertrendEngulfingReversalStrategy import (
    Mark2Strategy,
    _load_rr,
    _load_lot_size,
    _load_risk_per_trade,
    _load_param,
)

_KEY = "engulfing_consolidation"


class EngulfingConsolidationStrategy(Mark2Strategy):

    _STRATEGY_KEY = _KEY

    def __init__(self, period=None, multiplier=None):
        p = period     or int(_load_param(_KEY,   "st_period",   10))
        m = multiplier or float(_load_param(_KEY, "st_mult",     3.0))
        super().__init__(p, m)

        self.rr             = _load_rr(_KEY)
        self.lot_size       = _load_lot_size(_KEY)
        self.risk_per_trade = _load_risk_per_trade(_KEY)

        # Candles allowed after ST turns RED before window closes
        self.red_window  = int(_load_param(_KEY,   "red_window",  10))
        # Upper wick must be > lower wick × this ratio
        self.wick_ratio  = float(_load_param(_KEY, "wick_ratio",  1.0))

    # ─────────────────────────────────────────────────────────────────

    def _resolve_st_columns(self, df: pd.DataFrame):
        trend_col = "st_trend" if "st_trend" in df.columns else "trend"
        for c in ("st_value", "supertrend", "st"):
            if c in df.columns:
                return trend_col, c
        raise KeyError(f"Supertrend column not found. Available: {df.columns.tolist()}")

    # ─────────────────────────────────────────────────────────────────
    # Bearish engulfing with upper-wick dominance
    # ─────────────────────────────────────────────────────────────────

    def _is_valid_candle(self, prev: pd.Series, curr: pd.Series) -> bool:
        # ── Bearish engulfing ─────────────────────────────────────────
        if not (
            prev["close"] > prev["open"]                                          # prev bullish
            and curr["open"]  > curr["close"]                                     # curr bearish
            and curr["open"]  >= prev["close"]                                    # opens at/above prev close
            and prev["open"]  >= curr["close"]                                    # fully engulfs prev body
            and (curr["open"] - curr["close"]) > (prev["close"] - prev["open"])   # bigger body
        ):
            return False

        # ── Upper wick must dominate lower wick ──────────────────────
        upper_wick = curr["high"]  - curr["open"]    # wick above body (rejection from top)
        lower_wick = curr["close"] - curr["low"]     # tail below body

        # Avoid zero-division; if no lower wick at all, upper wick always wins
        if lower_wick <= 0:
            return upper_wick > 0

        return upper_wick >= lower_wick * self.wick_ratio

    # ─────────────────────────────────────────────────────────────────
    # Signal generation
    # ─────────────────────────────────────────────────────────────────

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = self.calculate_supertrend(df)

        trend_col, _ = self._resolve_st_columns(df)
        trend = df[trend_col].values

        df["signal"]           = 0
        df["sl"]               = np.nan
        df["tp"]               = np.nan
        df["lot"]              = 0.0
        df["risk_per_trade"]   = np.nan
        df["sl_exit_on_close"] = 0

        highs = df["high"].values
        n     = len(df)

        bars_since_red = 0

        for i in range(7, n):

            # ── Track red-window counter ──────────────────────────────
            if trend[i] == -1 and trend[i - 1] == 1:
                bars_since_red = 1
            elif trend[i] == -1:
                if bars_since_red > 0:
                    bars_since_red += 1
            else:
                bars_since_red = 0

            # ── Supertrend filter ─────────────────────────────────────
            # GREEN → always allowed
            # RED   → only within first red_window bars after flip
            st_allows = (
                trend[i] == 1
                or (trend[i] == -1 and 0 < bars_since_red <= self.red_window)
            )
            if not st_allows:
                continue

            # ── Candle check: bearish engulfing + upper wick ──────────
            if not self._is_valid_candle(df.iloc[i - 1], df.iloc[i]):
                continue

            # ── Build trade ───────────────────────────────────────────
            entry = df.iloc[i]["close"]
            sl    = float(np.max(highs[i - 4: i]))   # highest high of prev 4 bars
            risk  = sl - entry
            if risk <= 0:
                continue

            df.at[i, "signal"]           = -1
            df.at[i, "sl"]               = round(sl,                    5)
            df.at[i, "tp"]               = round(entry - self.rr * risk, 5)
            df.at[i, "lot"]              = self.lot_size
            df.at[i, "risk_per_trade"]   = self.risk_per_trade
            df.at[i, "sl_exit_on_close"] = 1

        return df
