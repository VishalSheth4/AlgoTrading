"""
"20 EMA" -- engulfing rejection off a 20-period EMA, both directions:

- SELL: a bearish engulfing candle (previous candle bullish, current
  candle bearish, current body fully engulfs the previous body) whose
  own body crosses the EMA line. SL = that bearish candle's own high.
- BUY: the mirror image -- a bullish engulfing candle (previous candle
  bearish, current candle bullish, current body fully engulfs the
  previous body) whose own body crosses the EMA line. SL = that
  bullish candle's own low.
- Either direction: every one of the `lookback` candles immediately
  before the engulfing candle must sit entirely on one side of the EMA
  -- not even a wick may touch it -- so the cross is a clean,
  first-time break rather than the EMA already being chopped through.

Optional Supertrend confirmation (off by default, toggled via
set_supertrend_filter() -- see the Settings UI): when ON, BUY only
fires while the Supertrend is in an uptrend (green) and SELL only
fires while it's in a downtrend (red). Reads the "trend" column
already computed by SupertrendIndicator earlier in CandleService's
pipeline (1 = uptrend, -1 = downtrend) -- see candle_service.py.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


class Ema20Strategy(Strategy):
    name = "20ema"

    def __init__(self, ema_period: int = 20, lookback: int = 3, rr: float = 1.0):
        # lookback: how many candles before the engulfing candle must
        # stay clear of the EMA, wicks included. Defaults to 3, tune via
        # the constructor.
        self._ema_period = ema_period
        self._lookback = lookback
        # RR for auto-trading's own SL/TP (see {name}_bull_sl/_tp and
        # {name}_bear_sl/_tp below). Mutable via set_rr() so the UI can
        # change it at runtime without recreating the strategy instance
        # (see candle_service.py).
        self._rr = rr
        # Off by default -- an opt-in filter, not a behavior change for
        # anyone already running this strategy. See set_supertrend_filter().
        self._supertrend_filter = False

    @property
    def rr(self) -> float:
        return self._rr

    def set_rr(self, rr: float) -> None:
        self._rr = float(rr)

    @property
    def supertrend_filter(self) -> bool:
        return self._supertrend_filter

    def set_supertrend_filter(self, enabled: bool) -> None:
        self._supertrend_filter = bool(enabled)

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.reset_index(drop=True).copy()
        n = len(df)

        if n == 0:
            df[f"{self.name}_bull"] = pd.Series(dtype="bool")
            df[f"{self.name}_bear"] = pd.Series(dtype="bool")
            df[f"{self.name}_bull_price"] = pd.Series(dtype="float64")
            df[f"{self.name}_bear_price"] = pd.Series(dtype="float64")
            df[f"{self.name}_bull_sl"] = pd.Series(dtype="float64")
            df[f"{self.name}_bull_tp"] = pd.Series(dtype="float64")
            df[f"{self.name}_bear_sl"] = pd.Series(dtype="float64")
            df[f"{self.name}_bear_tp"] = pd.Series(dtype="float64")
            return df

        open_ = df["open"]
        close = df["close"]
        high = df["high"]
        low = df["low"]

        ema = close.ewm(span=self._ema_period, adjust=False).mean()

        prev_open = open_.shift(1)
        prev_close = close.shift(1)

        # Bearish engulfing: previous candle bullish, current candle
        # bearish, current body fully engulfs the previous body.
        bearish_engulfing = (
            (prev_close > prev_open)
            & (close < open_)
            & (open_ >= prev_close)
            & (close <= prev_open)
        ).fillna(False)

        # Bullish engulfing: the mirror image.
        bullish_engulfing = (
            (prev_close < prev_open)
            & (close > open_)
            & (open_ <= prev_close)
            & (close >= prev_open)
        ).fillna(False)

        # Body crosses the EMA: the average sits between this candle's
        # open and close (order-independent, so it catches the cross
        # regardless of which side price started the candle on, and
        # works for both the bull and bear case below).
        body_top = pd.concat([open_, close], axis=1).max(axis=1)
        body_bottom = pd.concat([open_, close], axis=1).min(axis=1)
        body_crosses_ema = (body_bottom <= ema) & (ema <= body_top)

        # Every one of the `lookback` candles right before the engulfing
        # candle must be entirely clear of the EMA -- high below it or
        # low above it, no wick touching -- so this is a clean first
        # break of the average, not one it was already chopping through.
        clear_of_ema = (high < ema) | (low > ema)
        prior_all_clear = pd.Series(True, index=df.index)
        for i in range(1, self._lookback + 1):
            prior_all_clear &= clear_of_ema.shift(i).fillna(False)

        bull_signal = (bullish_engulfing & body_crosses_ema & prior_all_clear).fillna(False)
        bear_signal = (bearish_engulfing & body_crosses_ema & prior_all_clear).fillna(False)

        if self._supertrend_filter and "trend" in df.columns:
            trend = df["trend"]
            bull_signal &= (trend == 1)
            bear_signal &= (trend == -1)

        df[f"{self.name}_bull"] = bull_signal
        df[f"{self.name}_bear"] = bear_signal
        df[f"{self.name}_bull_price"] = low
        df[f"{self.name}_bear_price"] = high

        # BUY: SL beyond the bullish engulfing candle's own low, TP =
        # RR-based off that risk.
        bull_risk = close - low
        df[f"{self.name}_bull_sl"] = np.where(bull_risk > 0, low, np.nan)
        df[f"{self.name}_bull_tp"] = np.where(bull_risk > 0, close + self._rr * bull_risk, np.nan)

        # SELL: SL beyond the bearish engulfing candle's own high, TP =
        # RR-based off that risk.
        bear_risk = high - close
        df[f"{self.name}_bear_sl"] = np.where(bear_risk > 0, high, np.nan)
        df[f"{self.name}_bear_tp"] = np.where(bear_risk > 0, close - self._rr * bear_risk, np.nan)
        return df
