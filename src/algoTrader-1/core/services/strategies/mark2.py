"""
"Mark2" counter-trend engulfing strategy -- ported from
algoTrading/strategies/mark2_strategy.py.

Computes its own Supertrend (self-contained, deliberately not sharing
CandleService's indicators.supertrend.SupertrendIndicator column, so this
strategy's period/multiplier can be tuned independently). On a trend flip:
  - the first candle of the new trend (X) starts a 2-candle watch window,
  - a BUY trend watches for a bearish engulfing -> SHORT signal,
  - a SELL trend watches for a bullish engulfing -> LONG signal,
  - price running away from X (past its high/low) invalidates the watch,
  - one attempt per trend segment; the previous segment must have lasted
    at least `min_trend_candles` bars for a new watch to start at all.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


class Mark2Strategy(Strategy):
    name = "mark2"

    def __init__(self, period: int = 10, multiplier: float = 3.0, min_trend_candles: int = 3, rr: float = 1.0):
        self._period = period
        self._multiplier = multiplier
        self._min_trend_candles = min_trend_candles
        # RR for auto-trading's own SL/TP (see {name}_bull_sl/_tp below) --
        # independent of the backtest RR matrix; defaults to 1:1 same as
        # everywhere else in this project.
        self._rr = rr

    # ---- Supertrend (own copy -- see module docstring) --------------
    def _supertrend_trend(self, df: pd.DataFrame) -> np.ndarray:
        close = df["close"].to_numpy(dtype="float64")
        high = df["high"].to_numpy(dtype="float64")
        low = df["low"].to_numpy(dtype="float64")
        n = len(df)

        hl2 = (high + low) / 2
        prev_close = np.concatenate([[close[0]], close[:-1]])
        tr = np.maximum.reduce([high - low, np.abs(high - prev_close), np.abs(low - prev_close)])
        atr = pd.Series(tr).ewm(alpha=1 / self._period, min_periods=self._period, adjust=False).mean().to_numpy()

        basic_upper = hl2 + self._multiplier * atr
        basic_lower = hl2 - self._multiplier * atr
        final_upper = basic_upper.copy()
        final_lower = basic_lower.copy()

        for i in range(1, n):
            if np.isnan(basic_lower[i]):
                continue
            prev_lo, prev_hi = final_lower[i - 1], final_upper[i - 1]
            final_lower[i] = (
                basic_lower[i]
                if (np.isnan(prev_lo) or basic_lower[i] > prev_lo or close[i - 1] < prev_lo)
                else prev_lo
            )
            final_upper[i] = (
                basic_upper[i]
                if (np.isnan(prev_hi) or basic_upper[i] < prev_hi or close[i - 1] > prev_hi)
                else prev_hi
            )

        trend = -np.ones(n, dtype=int)
        for i in range(1, n):
            lo, hi = final_lower[i], final_upper[i]
            if np.isnan(hi):
                trend[i] = trend[i - 1]
                continue
            if trend[i - 1] == 1:
                trend[i] = -1 if close[i] < lo else 1
            else:
                trend[i] = 1 if close[i] > hi else -1
        return trend

    @staticmethod
    def _is_bearish_engulfing(prev_open, prev_close, curr_open, curr_close) -> bool:
        return (
            prev_close > prev_open
            and curr_close < curr_open
            and curr_open >= prev_close
            and curr_close <= prev_open
        )

    @staticmethod
    def _is_bullish_engulfing(prev_open, prev_close, curr_open, curr_close) -> bool:
        return (
            prev_close < prev_open
            and curr_close > curr_open
            and curr_open <= prev_close
            and curr_close >= prev_open
        )

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.reset_index(drop=True).copy()
        n = len(df)

        bull = np.zeros(n, dtype=bool)
        bear = np.zeros(n, dtype=bool)
        bull_sl = np.full(n, np.nan)
        bear_sl = np.full(n, np.nan)

        if n < self._period + self._min_trend_candles + 3:
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
        opens = df["open"].to_numpy(dtype="float64")
        closes = df["close"].to_numpy(dtype="float64")
        highs = df["high"].to_numpy(dtype="float64")
        lows = df["low"].to_numpy(dtype="float64")

        x_idx = None
        active_trend = None
        done = False
        running_ref = None
        trend_count = 0

        for i in range(1, n):
            if trend[i] != trend[i - 1]:
                prev_count = trend_count
                trend_count = 1
                if prev_count >= self._min_trend_candles:
                    x_idx = i
                    active_trend = trend[i]
                    done = False
                    running_ref = highs[i] if active_trend == 1 else lows[i]
                else:
                    x_idx = None
                    active_trend = trend[i]
                    done = True
                continue

            trend_count += 1
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
                    bear_sl[i] = max(running_ref, highs[i])  # matches algoTrading/mark2_strategy.py exactly
                    done = True
                else:
                    running_ref = max(running_ref, highs[i])
            elif active_trend == -1:
                if closes[i] < running_ref:
                    done = True
                elif self._is_bullish_engulfing(opens[i - 1], closes[i - 1], opens[i], closes[i]):
                    bull[i] = True
                    bull_sl[i] = min(running_ref, lows[i])  # matches algoTrading/mark2_strategy.py exactly
                    done = True
                else:
                    running_ref = min(running_ref, lows[i])

        df[f"{self.name}_bull"] = bull
        df[f"{self.name}_bear"] = bear
        df[f"{self.name}_bull_price"] = df["low"]
        df[f"{self.name}_bear_price"] = df["high"]

        # Per-signal SL/TP for auto-trading (AutoTradingService reads
        # {rule.column}_sl/_tp). TP is RR-based only (unlike the backtest's
        # TP_MODE with a Supertrend-line alternative) -- live trading has
        # no equivalent setting yet.
        bull_risk = closes - bull_sl
        bear_risk = bear_sl - closes
        df[f"{self.name}_bull_sl"] = np.where(bull_risk > 0, bull_sl, np.nan)
        df[f"{self.name}_bull_tp"] = np.where(bull_risk > 0, closes + self._rr * bull_risk, np.nan)
        df[f"{self.name}_bear_sl"] = np.where(bear_risk > 0, bear_sl, np.nan)
        df[f"{self.name}_bear_tp"] = np.where(bear_risk > 0, closes - self._rr * bear_risk, np.nan)
        return df
