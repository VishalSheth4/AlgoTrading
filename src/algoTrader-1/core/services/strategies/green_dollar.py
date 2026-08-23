"""
"Green Dollar" volume-spike reversal strategy.

Ported from the "Mark-2" Pine Script indicator's Bullvolumecheck /
Bearishvolumecheck logic (the 'dollar sign' markers). Flags a candle when:
- it's a bull/bear-bodied candle (close vs open),
- its volume is a genuine spike: above both the max of the last 6 bars
  AND the average of the last 12 bars,
- the preceding 5 bars were all "quiet" (below that same 12-bar average),
- price is NOT clearly riding above the fast EMA (an "overextended" filter
  -- the signal only fires near/below the EMA).

The marker price sits on an ATR-trailing TrendLine (the same
Bollinger-Band-breakout trailing stop the source script draws), offset by
an 8-period ATR -- exactly where the Pine script plots its markers.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


def _wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    n = len(close)
    if n == 0:
        return np.array([])

    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    true_range = np.maximum.reduce([
        high - low,
        np.abs(high - prev_close),
        np.abs(low - prev_close),
    ])

    atr = np.zeros(n)
    warmup = min(period, n)
    atr[:warmup] = np.cumsum(true_range[:warmup]) / np.arange(1, warmup + 1)
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + true_range[i]) / period
    return atr


class GreenDollarStrategy(Strategy):
    name = "green_dollar"

    def __init__(
        self,
        bb_period: int = 21,
        bb_deviations: float = 1.0,
        trend_atr_period: int = 5,
        marker_atr_period: int = 8,
        ema_period: int = 5,
        quiet_lookback: int = 5,
        avg_lookback: int = 12,
        max_lookback: int = 6,
        rr: float = 1.0,
    ):
        self._bb_period = bb_period
        self._bb_deviations = bb_deviations
        self._trend_atr_period = trend_atr_period
        self._marker_atr_period = marker_atr_period
        self._ema_period = ema_period
        self._quiet_lookback = quiet_lookback
        self._avg_lookback = avg_lookback
        self._max_lookback = max_lookback
        # RR for auto-trading's own SL/TP (see {name}_bull_sl/_tp below) --
        # independent of the backtest RR matrix; defaults to 1:1 same as
        # everywhere else in this project.
        self._rr = rr

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

        open_ = df["open"].to_numpy(dtype="float64")
        high = df["high"].to_numpy(dtype="float64")
        low = df["low"].to_numpy(dtype="float64")
        close = df["close"].to_numpy(dtype="float64")
        volume = df["tick_volume"].to_numpy(dtype="float64")

        # Bollinger Bands on close (population stdev, matching Pine's ta.stdev)
        close_s = pd.Series(close)
        bb_mid = close_s.rolling(self._bb_period).mean()
        bb_std = close_s.rolling(self._bb_period).std(ddof=0)
        bb_upper = (bb_mid + bb_std * self._bb_deviations).to_numpy()
        bb_lower = (bb_mid - bb_std * self._bb_deviations).to_numpy()

        atr_trend = _wilder_atr(high, low, close, self._trend_atr_period)
        atr_marker = _wilder_atr(high, low, close, self._marker_atr_period)

        # ATR-trailing TrendLine + direction, ratcheting exactly like the
        # source script: a BB breakout starts a trailing stop that only
        # ever moves in its favorable direction until price breaks it.
        trend_line = np.zeros(n)

        for i in range(n):
            if np.isnan(bb_upper[i]) or np.isnan(bb_lower[i]):
                bb_signal = 0
            elif close[i] > bb_upper[i]:
                bb_signal = 1
            elif close[i] < bb_lower[i]:
                bb_signal = -1
            else:
                bb_signal = 0

            prev_trend_line = trend_line[i - 1] if i > 0 else 0.0

            if bb_signal == 1:
                candidate = low[i] - atr_trend[i]
                trend_line[i] = max(candidate, prev_trend_line) if i > 0 else candidate
            elif bb_signal == -1:
                candidate = high[i] + atr_trend[i]
                trend_line[i] = min(candidate, prev_trend_line) if i > 0 else candidate
            else:
                trend_line[i] = prev_trend_line

        # Fast EMA "not overextended" filter
        ema_fast = close_s.ewm(span=self._ema_period, adjust=False).mean().to_numpy()
        is_above_ema = (open_ > ema_fast) & (low > ema_fast)
        alert_is_above_ema = ~is_above_ema  # only alert when NOT clearly above the EMA

        # Volume-spike filter: current bar beats both the recent max and
        # recent average, and the preceding quiet_lookback bars were calm.
        volume_s = pd.Series(volume)
        maxx = volume_s.shift(1).rolling(self._max_lookback).max().to_numpy()
        avgg = volume_s.shift(1).rolling(self._avg_lookback).mean().to_numpy()

        quiet_prior = np.ones(n, dtype=bool)
        for lag in range(1, self._quiet_lookback + 1):
            shifted_volume = volume_s.shift(lag).to_numpy()
            quiet_prior &= shifted_volume < avgg

        volume_spike = (volume > maxx) & (volume > avgg) & quiet_prior

        bull_candle = close > open_
        bear_candle = open_ > close

        bull_signal = bull_candle & volume_spike & alert_is_above_ema
        bear_signal = bear_candle & volume_spike & alert_is_above_ema

        # Rows still inside the rolling warm-up window can't have a valid signal.
        valid = ~(np.isnan(maxx) | np.isnan(avgg))
        bull_signal &= valid
        bear_signal &= valid

        df[f"{self.name}_bull"] = bull_signal
        df[f"{self.name}_bear"] = bear_signal
        df[f"{self.name}_bull_price"] = trend_line + atr_marker
        df[f"{self.name}_bear_price"] = trend_line - atr_marker

        # Per-signal SL/TP for auto-trading (AutoTradingService reads
        # {rule.column}_sl/_tp -- see its docstring), mirroring
        # algoTrading/strategies/green_dollar.py's backtest SL exactly:
        # the signal candle's own low/high, widened to include the
        # PREVIOUS candle's low/high if that previous candle was a
        # doji/small-body (< 30% of its own high-low range).
        body = (close_s - pd.Series(open_)).abs()
        candle_range = pd.Series(high) - pd.Series(low)
        small_body = (candle_range > 0) & (body < 0.3 * candle_range)
        prev_small_body = small_body.shift(1).fillna(False).to_numpy()

        prev_low = pd.Series(low).shift(1).to_numpy()
        prev_high = pd.Series(high).shift(1).to_numpy()
        bull_sl = np.where(prev_small_body, np.minimum(low, prev_low), low)
        bear_sl = np.where(prev_small_body, np.maximum(high, prev_high), high)

        bull_risk = close - bull_sl
        bear_risk = bear_sl - close
        df[f"{self.name}_bull_sl"] = np.where(bull_risk > 0, bull_sl, np.nan)
        df[f"{self.name}_bull_tp"] = np.where(bull_risk > 0, close + self._rr * bull_risk, np.nan)
        df[f"{self.name}_bear_sl"] = np.where(bear_risk > 0, bear_sl, np.nan)
        df[f"{self.name}_bear_tp"] = np.where(bear_risk > 0, close - self._rr * bear_risk, np.nan)
        return df
