"""
SupertrendTouchSell  (SHORT + LONG)
=====================================

SHORT setup — RED Supertrend:
  1. Supertrend is RED  (trend = -1)
  2. Bearish engulfing candle
  3. Candle HIGH touches the red ST line  (high ≥ st_line)
  4. Candle range ≥ min_candle_size       (skip tiny candles)
  Entry  : close of signal candle
  SL     : high of signal candle × (1 + sl_buffer_pct)
  TP     : entry − rr × risk
  SL exit: only when a subsequent candle CLOSES above SL (not on wick)
  Flip   : if Supertrend flips GREEN while trade is open → exit immediately

LONG setup — GREEN Supertrend (mirror):
  1. Supertrend is GREEN (trend = +1)
  2. Bullish engulfing candle
  3. Candle LOW touches the green ST line (low ≤ st_line)
  4. Candle range ≥ min_candle_size
  Entry  : close of signal candle
  SL     : low of signal candle × (1 − sl_buffer_pct)
  TP     : entry + rr × risk
  SL exit: only when a subsequent candle CLOSES below SL
  Flip   : if Supertrend flips RED while trade is open → exit immediately

All parameters under supertrend_touch_sell in config.yaml.
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

_KEY = "supertrend_touch_sell"


class SupertrendTouchSellStrategy(Mark2Strategy):

    _STRATEGY_KEY  = _KEY
    _STRATEGY_NAME = "SupertrendTouchSell"

    def __init__(self, period=None, multiplier=None):
        p = period     or int(_load_param(_KEY,   "st_period",      10))
        m = multiplier or float(_load_param(_KEY, "st_mult",        3.0))
        super().__init__(p, m)

        self.rr             = _load_rr(_KEY)
        self.lot_size       = _load_lot_size(_KEY)
        self.risk_per_trade = _load_risk_per_trade(_KEY)

        self.ema_filter     = bool(_load_param(_KEY,  "ema_filter",     False))
        self.ema_period     = int(_load_param(_KEY,   "ema_period",     200))

        self.atr_filter     = bool(_load_param(_KEY,  "atr_filter",     False))
        self.atr_period     = int(_load_param(_KEY,   "atr_period",     14))
        self.atr_mult       = float(_load_param(_KEY, "atr_mult",       2.0))

        self.min_body_ratio = float(_load_param(_KEY, "min_body_ratio", 0.0))
        self.sl_buffer_pct  = float(_load_param(_KEY, "sl_buffer_pct",  0.001))

        # Minimum candle range required to take a trade
        self.min_candle_size = float(_load_param(_KEY, "min_candle_size", 10.0))

    # ─────────────────────────────────────────────────────────────────

    def _compute_atr(self, df: pd.DataFrame) -> np.ndarray:
        h  = df["high"].values
        l  = df["low"].values
        c  = df["close"].values
        pc = np.concatenate([[c[0]], c[:-1]])
        tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
        return (
            pd.Series(tr)
            .ewm(alpha=1 / self.atr_period, min_periods=self.atr_period, adjust=False)
            .mean()
            .values
        )

    def _resolve_st_columns(self, df: pd.DataFrame):
        trend_col = "st_trend" if "st_trend" in df.columns else "trend"
        for c in ("st_value", "supertrend", "st"):
            if c in df.columns:
                return trend_col, c
        raise KeyError(f"Supertrend column not found. Available: {df.columns.tolist()}")

    def _body_ok(self, candle: pd.Series) -> bool:
        if self.min_body_ratio <= 0:
            return True
        rng  = candle["high"] - candle["low"]
        body = abs(candle["close"] - candle["open"])
        return rng <= 0 or body / rng >= self.min_body_ratio

    def _is_bearish_engulfing(self, prev: pd.Series, curr: pd.Series) -> bool:
        return (
            prev["close"] > prev["open"]
            and curr["open"]  > curr["close"]
            and curr["open"]  >= prev["close"]
            and prev["open"]  >= curr["close"]
            and (curr["open"] - curr["close"]) > (prev["close"] - prev["open"])
            and self._body_ok(curr)
        )

    def _is_bullish_engulfing(self, prev: pd.Series, curr: pd.Series) -> bool:
        return (
            prev["close"] < prev["open"]
            and curr["close"] > curr["open"]
            and curr["open"]  <= prev["close"]
            and prev["open"]  <= curr["close"]
            and (curr["close"] - curr["open"]) > (prev["open"] - prev["close"])
            and self._body_ok(curr)
        )

    # ─────────────────────────────────────────────────────────────────
    # Signal generation
    # ─────────────────────────────────────────────────────────────────

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = self.calculate_supertrend(df)
        n  = len(df)

        trend_col, st_col = self._resolve_st_columns(df)
        trend   = df[trend_col].values
        st_line = df[st_col].values
        highs   = df["high"].values
        lows    = df["low"].values
        closes  = df["close"].values

        ema_vals = (
            df["close"].ewm(span=self.ema_period, adjust=False).mean().values
            if self.ema_filter else None
        )
        atr_vals = self._compute_atr(df) if self.atr_filter else None

        df["signal"]           = 0
        df["sl"]               = np.nan
        df["tp"]               = np.nan
        df["lot"]              = 0.0
        df["risk_per_trade"]   = np.nan
        df["entry_mode"]       = ""
        df["sl_exit_on_close"] = 0
        df["reverse_exit"]     = 0

        # ── FIRST PASS: entry signals ─────────────────────────────────
        for i in range(1, n):

            candle_range = highs[i] - lows[i]

            # Skip candles smaller than min_candle_size (0 = disabled)
            if self.min_candle_size > 0 and candle_range < self.min_candle_size:
                continue

            # ATR spike filter
            if atr_vals is not None and not np.isnan(atr_vals[i]):
                if candle_range > self.atr_mult * atr_vals[i]:
                    continue

            prev = df.iloc[i - 1]
            curr = df.iloc[i]

            # ── SHORT: RED Supertrend ─────────────────────────────────
            if trend[i] == -1:
                if not self._is_bearish_engulfing(prev, curr):
                    continue
                if highs[i] < st_line[i]:           # must touch red line
                    continue
                if ema_vals is not None and closes[i] > ema_vals[i]:
                    continue

                entry    = closes[i]
                sl_price = highs[i] * (1.0 + self.sl_buffer_pct)
                risk     = sl_price - entry
                if risk <= 0:
                    continue

                df.at[i, "signal"]           = -1
                df.at[i, "sl"]               = round(sl_price,              5)
                df.at[i, "tp"]               = round(entry - self.rr * risk, 5)
                df.at[i, "lot"]              = self.lot_size
                df.at[i, "risk_per_trade"]   = self.risk_per_trade
                df.at[i, "entry_mode"]       = "st_touch_sell"
                df.at[i, "sl_exit_on_close"] = 1

            # ── LONG: GREEN Supertrend ────────────────────────────────
            elif trend[i] == 1:
                if not self._is_bullish_engulfing(prev, curr):
                    continue
                if lows[i] > st_line[i]:            # must touch green line
                    continue
                if ema_vals is not None and closes[i] < ema_vals[i]:
                    continue

                entry    = closes[i]
                sl_price = lows[i] * (1.0 - self.sl_buffer_pct)
                risk     = entry - sl_price
                if risk <= 0:
                    continue

                df.at[i, "signal"]           = 1
                df.at[i, "sl"]               = round(sl_price,              5)
                df.at[i, "tp"]               = round(entry + self.rr * risk, 5)
                df.at[i, "lot"]              = self.lot_size
                df.at[i, "risk_per_trade"]   = self.risk_per_trade
                df.at[i, "entry_mode"]       = "st_touch_buy"
                df.at[i, "sl_exit_on_close"] = 1

        # ── SECOND PASS: mark Supertrend flip exits ───────────────────
        # While a trade is open, if ST flips direction → reverse_exit = 1
        sigs   = df["signal"].values
        sl_arr = df["sl"].values
        tp_arr = df["tp"].values

        trade_dir = 0
        trade_sl  = 0.0
        trade_tp  = 0.0

        for i in range(1, n):
            # New entry opens a trade
            if sigs[i] != 0:
                trade_dir = int(sigs[i])
                trade_sl  = float(sl_arr[i])
                trade_tp  = float(tp_arr[i])
                continue

            if trade_dir == 0:
                continue

            flipped = trend[i] != trend[i - 1]

            # ST flipped — exit the trade on this bar
            if flipped:
                df.at[i, "reverse_exit"] = 1
                trade_dir = 0
                continue

            # Track SL hit (close-based) to stop marking future bars
            if trade_dir == -1:
                if closes[i] > trade_sl or closes[i] <= trade_tp:
                    trade_dir = 0
            elif trade_dir == 1:
                if closes[i] < trade_sl or closes[i] >= trade_tp:
                    trade_dir = 0

        return df
