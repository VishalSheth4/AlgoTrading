"""
"Mark Dollar Supertrend" -- ported from
algoTrading/strategies/mark_dollar_supertrend.py.

Extends Mark2Strategy with two entry rules evaluated in order on every
Supertrend flip:
  Rule 1 (direct): X candle itself engulfs in the new trend's direction
                    -> immediate LONG/SHORT.
  Rule 2 (counter-trend, only if Rule 1 didn't fire): X candle is a
                    GreenDollar-style volume-spike candle -> watch the
                    next 2 candles for an opposing engulfing, same as
                    Mark2Strategy's counter-trend watch.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .mark2 import Mark2Strategy


class MarkDollarSupertrendStrategy(Mark2Strategy):
    name = "mark_dollar_supertrend"

    def __init__(
        self,
        period: int = 10,
        multiplier: float = 3.0,
        ema_period: int = 5,
        avg_lookback: int = 12,
        max_lookback: int = 6,
        quiet_lookback: int = 5,
        rr: float = 1.0,
    ):
        super().__init__(period, multiplier, rr=rr)
        self._ema_period = ema_period
        self._avg_lookback = avg_lookback
        self._max_lookback = max_lookback
        self._quiet_lookback = quiet_lookback

    def _dollar_flags(self, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        close = df["close"]
        volume = df["tick_volume"].astype("float64")
        ema = close.ewm(span=self._ema_period, adjust=False).mean()
        is_above_ema = (df["open"] > ema) & (df["low"] > ema)
        not_above_ema = ~is_above_ema

        avg_vol = volume.rolling(self._avg_lookback).mean()
        max_vol = volume.rolling(self._max_lookback).max().shift(1)
        quiet_prior = pd.Series(True, index=df.index)
        for lag in range(1, self._quiet_lookback + 1):
            quiet_prior &= volume.shift(lag) < avg_vol
        vol_spike = (volume > max_vol) & (volume > avg_vol) & quiet_prior

        dollar_long = (vol_spike & (close > df["open"]) & not_above_ema).fillna(False).to_numpy()
        dollar_short = (vol_spike & (df["open"] > close) & is_above_ema).fillna(False).to_numpy()
        return dollar_long, dollar_short

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.reset_index(drop=True).copy()
        n = len(df)

        bull = np.zeros(n, dtype=bool)
        bear = np.zeros(n, dtype=bool)
        bull_sl = np.full(n, np.nan)
        bear_sl = np.full(n, np.nan)

        warmup = self._period + self._avg_lookback + self._quiet_lookback + 3
        if n < warmup:
            df[f"{self.name}_bull"] = bull
            df[f"{self.name}_bear"] = bear
            df[f"{self.name}_bull_price"] = df["low"] if n else pd.Series(dtype="float64")
            df[f"{self.name}_bear_price"] = df["high"] if n else pd.Series(dtype="float64")
            df[f"{self.name}_bull_sl"] = pd.Series(dtype="float64") if not n else bull_sl
            df[f"{self.name}_bull_tp"] = pd.Series(dtype="float64") if not n else bull_sl
            df[f"{self.name}_bear_sl"] = pd.Series(dtype="float64") if not n else bear_sl
            df[f"{self.name}_bear_tp"] = pd.Series(dtype="float64") if not n else bear_sl
            return df

        trend = self._supertrend_trend(df)
        dollar_long, dollar_short = self._dollar_flags(df)
        opens = df["open"].to_numpy(dtype="float64")
        closes = df["close"].to_numpy(dtype="float64")
        highs = df["high"].to_numpy(dtype="float64")
        lows = df["low"].to_numpy(dtype="float64")

        x_idx = None
        active_trend = None
        done = False
        running_ref = None

        for i in range(1, n):
            if trend[i] != trend[i - 1]:
                new_trend = trend[i]

                if new_trend == 1 and self._is_bullish_engulfing(opens[i - 1], closes[i - 1], opens[i], closes[i]):
                    bull[i] = True
                    bull_sl[i] = lows[i]  # Rule 1 (direct): SL = X candle's own low
                    x_idx, active_trend, done = None, new_trend, True
                elif new_trend == -1 and self._is_bearish_engulfing(opens[i - 1], closes[i - 1], opens[i], closes[i]):
                    bear[i] = True
                    bear_sl[i] = highs[i]  # Rule 1 (direct): SL = X candle's own high
                    x_idx, active_trend, done = None, new_trend, True
                elif new_trend == 1 and dollar_long[i]:
                    x_idx, active_trend, done, running_ref = i, new_trend, False, highs[i]
                elif new_trend == -1 and dollar_short[i]:
                    x_idx, active_trend, done, running_ref = i, new_trend, False, lows[i]
                else:
                    x_idx, active_trend, done = None, new_trend, True
                continue

            if x_idx is None or done:
                continue
            if i - x_idx > 2:
                done = True
                continue

            if active_trend == 1:
                if closes[i] > running_ref:
                    done = True
                elif self._is_bearish_engulfing(opens[i - 1], closes[i - 1], opens[i], closes[i]):
                    bear[i] = True
                    bear_sl[i] = max(running_ref, highs[i])  # Rule 2 (counter-trend), same as Mark2
                    done = True
                else:
                    running_ref = max(running_ref, highs[i])
            elif active_trend == -1:
                if closes[i] < running_ref:
                    done = True
                elif self._is_bullish_engulfing(opens[i - 1], closes[i - 1], opens[i], closes[i]):
                    bull[i] = True
                    bull_sl[i] = min(running_ref, lows[i])  # Rule 2 (counter-trend), same as Mark2
                    done = True
                else:
                    running_ref = min(running_ref, lows[i])

        df[f"{self.name}_bull"] = bull
        df[f"{self.name}_bear"] = bear
        df[f"{self.name}_bull_price"] = df["low"]
        df[f"{self.name}_bear_price"] = df["high"]

        # Per-signal SL/TP for auto-trading (AutoTradingService reads
        # {rule.column}_sl/_tp). TP is RR-based only, same simplification
        # as Mark2Strategy.
        bull_risk = closes - bull_sl
        bear_risk = bear_sl - closes
        df[f"{self.name}_bull_sl"] = np.where(bull_risk > 0, bull_sl, np.nan)
        df[f"{self.name}_bull_tp"] = np.where(bull_risk > 0, closes + self._rr * bull_risk, np.nan)
        df[f"{self.name}_bear_sl"] = np.where(bear_risk > 0, bear_sl, np.nan)
        df[f"{self.name}_bear_tp"] = np.where(bear_risk > 0, closes - self._rr * bear_risk, np.nan)
        return df
