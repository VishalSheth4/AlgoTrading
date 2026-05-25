"""
SupertrendCounterFlip_X1
========================
Counter-trend engulfing entry after a Supertrend direction flip.

ENTRY LOGIC
───────────
X   = candle where Supertrend changes direction
X+1 = immediate next candle

After a BUY flip (trend → +1) at X:
  If X+1 is a bearish engulfing candle  →  SELL at X+1 close
  Bearish engulfing: X+1 opens ≥ X close  AND  X+1 closes ≤ X open

After a SELL flip (trend → -1) at X:
  If X+1 is a bullish engulfing candle  →  BUY at X+1 close
  Bullish engulfing: X+1 opens ≤ X close  AND  X+1 closes ≥ X open

STOP LOSS
─────────
SELL trade:  SL = high[X]   × (1 + sl_buffer_pct)
             If X-1 is a doji → SL = high[X-1] × (1 + sl_buffer_pct)

BUY trade:   SL = low[X]    × (1 − sl_buffer_pct)
             If X-1 is a doji → SL = low[X-1] × (1 − sl_buffer_pct)

Doji: |close − open| / (high − low) < doji_ratio  (configurable)

FILTERS
───────
ema_filter     : LONG only above EMA, SHORT only below EMA
atr_filter     : skip X+1 bars where range > atr_mult × ATR
min_body_ratio : engulfing candle body must be ≥ this fraction of its range

TP MODES  (tp_mode in config.yaml)
────────────────────────────────────
"rr"   → fixed entry ± rr × risk
"st"   → Supertrend line (updated dynamically each bar)
"both" → whichever of {rr_tp, st_tp} is reached first
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


class SupertrendCounterFlipX1Strategy(Mark2Strategy):

    _STRATEGY_KEY  = "supertrend_counter_flip_x1"
    _STRATEGY_NAME = "SupertrendCounterFlip_X1"

    def __init__(self, period=None, multiplier=None):
        key = self._STRATEGY_KEY
        p   = period     or int(_load_param(key,   "st_period",  10))
        m   = multiplier or float(_load_param(key, "st_mult",    3.0))
        super().__init__(p, m)

        self.rr             = _load_rr(key)
        self.lot_size       = _load_lot_size(key)
        self.risk_per_trade = _load_risk_per_trade(key)
        self.tp_mode        = str(_load_param(key,  "tp_mode",          "rr"))

        # EMA trend filter
        self.ema_filter     = bool(_load_param(key, "ema_filter",        True))
        self.ema_period     = int(_load_param(key,  "ema_period",        200))

        # ATR spike filter
        self.atr_filter     = bool(_load_param(key, "atr_filter",        False))
        self.atr_period     = int(_load_param(key,  "atr_period",        14))
        self.atr_mult       = float(_load_param(key, "atr_mult",         2.0))

        # Engulfing / doji thresholds
        self.min_body_ratio = float(_load_param(key, "min_body_ratio",   0.50))
        self.doji_ratio     = float(_load_param(key, "doji_ratio",       0.10))

        # SL buffer
        self.sl_buffer_pct  = float(_load_param(key, "sl_buffer_pct",   0.01))

    # ─────────────────────────────────────────────────────────────────
    # ATR (Wilder RMA — matches Pine Script)
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

    # ─────────────────────────────────────────────────────────────────
    # Resolve Supertrend column names (base-class agnostic)
    # ─────────────────────────────────────────────────────────────────

    def _resolve_st_columns(self, df: pd.DataFrame):
        trend_col = "st_trend" if "st_trend" in df.columns else "trend"
        for c in ("st_value", "supertrend", "st"):
            if c in df.columns:
                return trend_col, c
        raise KeyError(
            f"Supertrend column not found. Available: {df.columns.tolist()}"
        )

    # ─────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────

    def _is_doji(self, o: float, h: float, l: float, c: float) -> bool:
        rng = h - l
        if rng <= 0:
            return True
        return abs(c - o) / rng < self.doji_ratio

    def _is_bearish_engulfing(
        self,
        x_open: float, x_close: float,
        x1_open: float, x1_close: float,
        x1_high: float, x1_low: float,
    ) -> bool:
        """X+1 bearish candle whose body fully engulfs X body."""
        if x1_close >= x1_open:          # must be a down-close candle
            return False
        body = abs(x1_close - x1_open)
        rng  = x1_high - x1_low
        if rng > 0 and body / rng < self.min_body_ratio:
            return False                 # body too small (wick-heavy)
        return x1_open >= x_close and x1_close <= x_open

    def _is_bullish_engulfing(
        self,
        x_open: float, x_close: float,
        x1_open: float, x1_close: float,
        x1_high: float, x1_low: float,
    ) -> bool:
        """X+1 bullish candle whose body fully engulfs X body."""
        if x1_close <= x1_open:          # must be an up-close candle
            return False
        body = abs(x1_close - x1_open)
        rng  = x1_high - x1_low
        if rng > 0 and body / rng < self.min_body_ratio:
            return False
        return x1_open <= x_close and x1_close >= x_open

    # ─────────────────────────────────────────────────────────────────
    # Signal generation
    # ─────────────────────────────────────────────────────────────────

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df  = df.copy()
        df  = self.calculate_supertrend(df)
        n   = len(df)

        opens  = df["open"].values
        highs  = df["high"].values
        lows   = df["low"].values
        closes = df["close"].values

        trend_col, st_col = self._resolve_st_columns(df)
        trend   = df[trend_col].values
        st_line = df[st_col].values

        # Pre-compute indicators
        ema_vals = (
            df["close"].ewm(span=self.ema_period, adjust=False).mean().values
            if self.ema_filter else None
        )
        atr_vals = self._compute_atr(df) if self.atr_filter else None

        # Output columns
        df["signal"]         = 0
        df["sl"]             = np.nan
        df["tp"]             = np.nan
        df["_rr_tp"]         = np.nan
        df["lot"]            = 0.0
        df["risk_per_trade"] = np.nan
        df["entry_mode"]     = ""

        # ─────────────────────────────────────────────────────────────
        # FIRST PASS — detect flip at X, check X+1 for engulfing
        # Start at i=2 so X-1 (i-1), X (i-1 after flip), X+1 (i) are valid.
        # At each bar i, we check if i-1 was a flip candle and i is engulfing.
        # ─────────────────────────────────────────────────────────────
        for i in range(2, n):
            x   = i - 1   # flip candle index
            xm1 = i - 2   # X-1 index (one before the flip)

            # Is x actually a flip candle?
            if trend[x] == trend[xm1]:
                continue

            # X+1 is the current bar i — it must have already closed
            x1 = i

            # ATR spike filter on X+1
            bar_range = highs[x1] - lows[x1]
            if atr_vals is not None and not np.isnan(atr_vals[x1]):
                if bar_range > self.atr_mult * atr_vals[x1]:
                    continue

            # ── BUY flip at X → look for bearish engulfing at X+1 → SELL ──
            if trend[x] == 1:
                if not self._is_bearish_engulfing(
                    opens[x], closes[x],
                    opens[x1], closes[x1], highs[x1], lows[x1],
                ):
                    continue

                # EMA filter: SELL only when price is below EMA
                if ema_vals is not None and closes[x1] > ema_vals[x1]:
                    continue

                # SL: use X-1 high if X-1 is a doji, else X candle high
                if self._is_doji(opens[xm1], highs[xm1], lows[xm1], closes[xm1]):
                    sl_ref = highs[xm1]
                else:
                    sl_ref = highs[x]

                sl_price = sl_ref * (1.0 + self.sl_buffer_pct)
                entry    = closes[x1]
                risk     = sl_price - entry
                if risk <= 0:
                    continue

                rr_tp = entry - self.rr * risk
                tp_val = rr_tp if self.tp_mode == "rr" else st_line[x1]

                df.at[x1, "signal"]         = -1
                df.at[x1, "sl"]             = round(sl_price, 5)
                df.at[x1, "tp"]             = round(tp_val,   5)
                df.at[x1, "_rr_tp"]         = round(rr_tp,    5)
                df.at[x1, "lot"]            = self.lot_size
                df.at[x1, "risk_per_trade"] = self.risk_per_trade
                df.at[x1, "entry_mode"]     = "engulf_counter"

            # ── SELL flip at X → look for bullish engulfing at X+1 → BUY ──
            elif trend[x] == -1:
                if not self._is_bullish_engulfing(
                    opens[x], closes[x],
                    opens[x1], closes[x1], highs[x1], lows[x1],
                ):
                    continue

                # EMA filter: BUY only when price is above EMA
                if ema_vals is not None and closes[x1] < ema_vals[x1]:
                    continue

                # SL: use X-1 low if X-1 is a doji, else X candle low
                if self._is_doji(opens[xm1], highs[xm1], lows[xm1], closes[xm1]):
                    sl_ref = lows[xm1]
                else:
                    sl_ref = lows[x]

                sl_price = sl_ref * (1.0 - self.sl_buffer_pct)
                entry    = closes[x1]
                risk     = entry - sl_price
                if risk <= 0:
                    continue

                rr_tp = entry + self.rr * risk
                tp_val = rr_tp if self.tp_mode == "rr" else st_line[x1]

                df.at[x1, "signal"]         = 1
                df.at[x1, "sl"]             = round(sl_price, 5)
                df.at[x1, "tp"]             = round(tp_val,   5)
                df.at[x1, "_rr_tp"]         = round(rr_tp,    5)
                df.at[x1, "lot"]            = self.lot_size
                df.at[x1, "risk_per_trade"] = self.risk_per_trade
                df.at[x1, "entry_mode"]     = "engulf_counter"

        # ─────────────────────────────────────────────────────────────
        # SECOND PASS — dynamic TP updates for "st" and "both" modes
        # ─────────────────────────────────────────────────────────────
        if self.tp_mode in ("st", "both"):
            sigs      = df["signal"].values
            sl_arr    = df["sl"].values
            rr_tp_arr = df["_rr_tp"].values

            trade_idx  = None
            trade_dir  = 0
            trade_rr_t = 0.0

            for i in range(1, n):
                if sigs[i] != 0:
                    trade_idx  = i
                    trade_dir  = int(sigs[i])
                    trade_rr_t = float(rr_tp_arr[i]) if not np.isnan(rr_tp_arr[i]) else 0.0
                    continue

                if trade_idx is None:
                    continue

                sl_p    = float(sl_arr[trade_idx])
                st_val  = st_line[i]
                flipped = trend[i] != trend[i - 1]

                if self.tp_mode == "st":
                    df.at[i, "tp"] = st_val

                else:  # "both": whichever target is closer to entry
                    if trade_dir == 1:
                        if trade_rr_t > 0 and not np.isnan(st_val):
                            df.at[i, "tp"] = min(trade_rr_t, st_val)
                        elif trade_rr_t > 0:
                            df.at[i, "tp"] = trade_rr_t
                        else:
                            df.at[i, "tp"] = st_val
                    else:
                        if trade_rr_t > 0 and not np.isnan(st_val):
                            df.at[i, "tp"] = max(trade_rr_t, st_val)
                        elif trade_rr_t > 0:
                            df.at[i, "tp"] = trade_rr_t
                        else:
                            df.at[i, "tp"] = st_val

                tp_p = float(df.at[i, "tp"])

                if trade_dir == -1:  # SHORT
                    if (closes[i] >= sl_p
                            or closes[i] <= tp_p
                            or (flipped and trend[i] == -1)):
                        trade_idx = None

                elif trade_dir == 1:  # LONG
                    if (closes[i] <= sl_p
                            or closes[i] >= tp_p
                            or (flipped and trend[i] == 1)):
                        trade_idx = None

        return df
