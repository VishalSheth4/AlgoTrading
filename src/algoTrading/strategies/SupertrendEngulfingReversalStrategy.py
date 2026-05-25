# =====================================================================
# SupertrendEngulfingReversalStrategy  (strategy key: supertrend_engulfing_reversal)
#
# What it does:
#   When Supertrend flips direction, breakout traders get trapped.
#   The strategy waits for an engulfing candle that traps them and
#   trades against the new Supertrend direction with a tight SL.
#
#   Optional features (all controlled from config.yaml):
#     atr_filter    — skip candles that are too large (news/spike bars)
#     ema_filter    — only trade when price is on the right side of EMA
#     sl_reversal   — when SL is hit with an engulfing candle,
#                     immediately take a trade in the SL-break direction
#     tp_mode       — "rr" | "st" | "both" | "fix_profit"
# =====================================================================

import pandas as pd
import numpy as np
from pathlib import Path
from algoTrading.config import Config

_YAML_PATH = Path(__file__).resolve().parents[1] / "config.yaml"


# ─────────────────────────────────────────────────────────────────────────────
# Shared config loaders — imported by every other strategy file
# ─────────────────────────────────────────────────────────────────────────────

def _load_yaml() -> dict:
    """Load config.yaml once; returns empty dict on any error."""
    try:
        import yaml
        with open(_YAML_PATH, "r") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _load_lot_size(strategy_key: str) -> float:
    """Per-strategy lot_size → fallback Config.LOT_SIZE."""
    try:
        return float(_load_yaml()["strategies"][strategy_key]["lot_size"])
    except Exception:
        return float(Config.LOT_SIZE)


def _load_rr(strategy_key: str) -> float:
    """Per-strategy rr → fallback Config.RR."""
    try:
        return float(_load_yaml()["strategies"][strategy_key]["rr"])
    except Exception:
        return float(Config.RR)


def _load_risk_per_trade(strategy_key: str) -> float:
    """Per-strategy risk_per_trade → global yaml value → Config.RISK_PER_TRADE.

    config.yaml uses PERCENTAGE notation: write 8 to mean 8% (= 0.08 in code).
    Values are divided by 100 here before use. Config.RISK_PER_TRADE fallback
    is already a decimal (0.08) and is returned as-is.
    """
    data = _load_yaml()
    try:
        return float(data["strategies"][strategy_key]["risk_per_trade"]) / 100.0
    except Exception:
        pass
    try:
        return float(data["risk_per_trade"]) / 100.0
    except Exception:
        return float(Config.RISK_PER_TRADE)  # already decimal — no conversion


def _load_param(strategy_key: str, param: str, default):
    """Load any custom param from config.yaml[strategies][key][param]."""
    try:
        return _load_yaml()["strategies"][strategy_key][param]
    except Exception:
        return default


# ─────────────────────────────────────────────────────────────────────────────
# Strategy
# ─────────────────────────────────────────────────────────────────────────────

class SupertrendEngulfingReversalStrategy:
    """
    Supertrend Engulfing Reversal Strategy
    =======================================
    Timeframe : any (M5 / M15 / M30 / H1)
    Instrument: any

    LOGIC
    -----
    1. Wait for Supertrend to flip direction  → X candle (reference only)
    2. Watch the next 2 candles for an engulfing reversal:
       BUY  flip  → SHORT on bearish engulfing (SL = running high)
       SELL flip  → LONG  on bullish engulfing (SL = running low)
    3. Reset after one trade per trend segment OR after 2 candles with no signal.

    OPTIONAL FEATURES (all from config.yaml)
    ------------------------------------------
    atr_filter    : skip signal candles where (high-low) > atr_mult × ATR(atr_period)
    ema_filter    : require LONG entry above ema_period EMA, SHORT below
    sl_reversal   : if the SL candle is also an engulfing candle in the SL
                    direction, immediately fire a reversal trade
    tp_mode       : "rr" | "st" | "both" | "fix_profit"
    """

    _STRATEGY_KEY = "supertrend_engulfing_reversal"

    def __init__(self, period=None, multiplier=None):
        key = self._STRATEGY_KEY

        self.rr             = _load_rr(key)
        self.lot_size       = _load_lot_size(key)
        self.risk_per_trade = _load_risk_per_trade(key)

        # Supertrend params
        self.period     = period     or int(_load_param(key, "st_period",     10))
        self.multiplier = multiplier or float(_load_param(key, "st_mult",      3.0))

        # TP mode
        self.tp_mode   = str(_load_param(key, "tp_mode",   Config.TP_MODE))
        self.fix_profit = float(_load_param(key, "fix_profit", getattr(Config, "FIX_PROFIT", 5)))

        # Trend stability
        self.min_trend_candles = int(_load_param(key, "min_trend_candles",
                                                 getattr(Config, "MIN_TREND_CANDLES", 1)))

        # ATR candle-size filter
        self.atr_filter  = bool(_load_param(key, "atr_filter",  False))
        self.atr_period  = int(_load_param(key, "atr_period",   14))
        self.atr_mult    = float(_load_param(key, "atr_mult",   2.0))

        # EMA trend filter
        self.ema_filter  = bool(_load_param(key, "ema_filter",  False))
        self.ema_period  = int(_load_param(key, "ema_period",   200))

        # SL-hit reversal
        self.sl_reversal    = bool(_load_param(key, "sl_reversal",    False))
        self.sl_reversal_rr = float(_load_param(key, "sl_reversal_rr", 1.5))

    # ─────────────────────────────────────────────────────────────────────────
    # Supertrend (Wilder ATR — matches TradingView ta.supertrend)
    # ─────────────────────────────────────────────────────────────────────────
    def calculate_supertrend(self, df):
        close = df['close'].values
        high  = df['high'].values
        low   = df['low'].values
        n     = len(df)

        hl2        = (high + low) / 2
        prev_close = np.concatenate([[close[0]], close[:-1]])

        tr = np.maximum.reduce([
            high - low,
            np.abs(high - prev_close),
            np.abs(low  - prev_close),
        ])

        atr = pd.Series(tr).ewm(
            alpha=1 / self.period, min_periods=self.period, adjust=False
        ).mean().values

        basic_upper = hl2 + self.multiplier * atr
        basic_lower = hl2 - self.multiplier * atr

        final_upper = basic_upper.copy()
        final_lower = basic_lower.copy()

        for i in range(1, n):
            if np.isnan(basic_lower[i]):
                continue
            prev_lo = final_lower[i - 1]
            prev_hi = final_upper[i - 1]

            if np.isnan(prev_lo):
                final_lower[i] = basic_lower[i]
            elif basic_lower[i] > prev_lo or close[i - 1] < prev_lo:
                final_lower[i] = basic_lower[i]
            else:
                final_lower[i] = prev_lo

            if np.isnan(prev_hi):
                final_upper[i] = basic_upper[i]
            elif basic_upper[i] < prev_hi or close[i - 1] > prev_hi:
                final_upper[i] = basic_upper[i]
            else:
                final_upper[i] = prev_hi

        trend      = -np.ones(n, dtype=int)
        supertrend = np.full(n, np.nan)

        for i in range(1, n):
            lo = final_lower[i]
            hi = final_upper[i]

            if np.isnan(hi):
                trend[i] = trend[i - 1]
                continue

            if trend[i - 1] == 1:
                trend[i] = -1 if close[i] < lo else 1
            else:
                trend[i] = 1  if close[i] > hi else -1

            supertrend[i] = lo if trend[i] == 1 else hi

        df = df.copy()
        df['supertrend'] = supertrend
        df['st_trend']   = trend
        df['st_lower']   = final_lower
        df['st_upper']   = final_upper

        return df

    # ─────────────────────────────────────────────────────────────────────────
    # ATR for candle-size filter
    # ─────────────────────────────────────────────────────────────────────────
    def _compute_atr(self, df) -> np.ndarray:
        close = df['close'].values
        high  = df['high'].values
        low   = df['low'].values
        prev_close = np.concatenate([[close[0]], close[:-1]])
        tr = np.maximum.reduce([
            high - low,
            np.abs(high - prev_close),
            np.abs(low  - prev_close),
        ])
        return pd.Series(tr).ewm(
            alpha=1 / self.atr_period, min_periods=self.atr_period, adjust=False
        ).mean().values

    # ─────────────────────────────────────────────────────────────────────────
    # TP calculator
    # ─────────────────────────────────────────────────────────────────────────
    def calc_tp(self, entry, risk, st_line, direction):
        if self.tp_mode == "fix_profit":
            return float(entry - self.fix_profit) if direction == 'short' else float(entry + self.fix_profit)

        st_valid = not np.isnan(st_line) if st_line is not None else False

        if direction == 'short':
            tp_rr = entry - self.rr * risk
            if self.tp_mode == "rr":
                return float(tp_rr)
            elif self.tp_mode == "st":
                return float(st_line) if st_valid else float(tp_rr)
            else:  # "both"
                return float(max(tp_rr, st_line)) if st_valid else float(tp_rr)
        else:
            tp_rr = entry + self.rr * risk
            if self.tp_mode == "rr":
                return float(tp_rr)
            elif self.tp_mode == "st":
                return float(st_line) if st_valid else float(tp_rr)
            else:  # "both"
                return float(min(tp_rr, st_line)) if st_valid else float(tp_rr)

    # ─────────────────────────────────────────────────────────────────────────
    # Engulfing helpers
    # ─────────────────────────────────────────────────────────────────────────
    def is_bearish_engulfing(self, prev, curr):
        return (
            prev['close'] > prev['open'] and
            curr['close'] < curr['open'] and
            curr['open']  >= prev['close'] and
            curr['close'] <= prev['open']
        )

    def is_bullish_engulfing(self, prev, curr):
        return (
            prev['close'] < prev['open'] and
            curr['close'] > curr['open'] and
            curr['open']  <= prev['close'] and
            curr['close'] >= prev['open']
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Signal generation
    # ─────────────────────────────────────────────────────────────────────────
    def generate_signals(self, df):
        df  = df.copy()
        df  = self.calculate_supertrend(df)

        df['signal'] = 0
        df['sl']     = np.nan
        df['tp']     = np.nan
        df['lot']    = 0.0

        trend  = df['st_trend'].values
        opens  = df['open'].values
        closes = df['close'].values
        highs  = df['high'].values
        lows   = df['low'].values
        n      = len(df)

        # ── Optional indicators ───────────────────────────────────────────────
        atr_vals = self._compute_atr(df) if self.atr_filter else None
        ema_vals = (
            df['close'].ewm(span=self.ema_period, adjust=False).mean().values
            if self.ema_filter else None
        )

        # ── Main state machine ────────────────────────────────────────────────
        x_idx        = None
        active_trend = None
        done         = False
        running_ref  = None
        trend_count  = 0

        # For SL-reversal tracking: (signal_direction, sl_price, entry_bar)
        last_long_sl  = None
        last_short_sl = None

        for i in range(1, n):

            # ── SL-reversal check (runs before new-signal logic) ───────────────
            if self.sl_reversal and i >= 2:
                prev_c = df.iloc[i - 1]
                curr_c = df.iloc[i]

                # Long SL hit: price wicked below sl, close below sl, bearish engulfing
                if (last_long_sl is not None
                        and lows[i] < last_long_sl
                        and closes[i] < last_long_sl
                        and self.is_bearish_engulfing(prev_c, curr_c)):
                    entry = closes[i]
                    sl    = highs[i]
                    risk  = sl - entry
                    if risk > 0:
                        df.at[i, 'signal'] = -1
                        df.at[i, 'sl']     = round(sl, 5)
                        df.at[i, 'tp']     = round(entry - self.sl_reversal_rr * risk, 5)
                        df.at[i, 'lot']    = self.lot_size
                    last_long_sl = None

                # Short SL hit: price wicked above sl, close above sl, bullish engulfing
                elif (last_short_sl is not None
                        and highs[i] > last_short_sl
                        and closes[i] > last_short_sl
                        and self.is_bullish_engulfing(prev_c, curr_c)):
                    entry = closes[i]
                    sl    = lows[i]
                    risk  = entry - sl
                    if risk > 0:
                        df.at[i, 'signal'] = 1
                        df.at[i, 'sl']     = round(sl, 5)
                        df.at[i, 'tp']     = round(entry + self.sl_reversal_rr * risk, 5)
                        df.at[i, 'lot']    = self.lot_size
                    last_short_sl = None

            # ── Supertrend flip: record X candle, reset state ─────────────────
            if trend[i] != trend[i - 1]:
                prev_count  = trend_count
                trend_count = 1

                if prev_count >= self.min_trend_candles:
                    x_idx        = i
                    active_trend = trend[i]
                    done         = False
                    running_ref  = highs[i] if active_trend == 1 else lows[i]
                else:
                    x_idx        = None
                    active_trend = trend[i]
                    done         = True
                continue

            trend_count += 1

            if x_idx is None or done:
                continue

            if i - x_idx > 2:
                done = True
                continue

            # ── ATR filter ────────────────────────────────────────────────────
            if self.atr_filter and atr_vals is not None:
                candle_range = highs[i] - lows[i]
                if not np.isnan(atr_vals[i]) and candle_range > self.atr_mult * atr_vals[i]:
                    running_ref = max(running_ref, highs[i]) if active_trend == 1 else min(running_ref, lows[i])
                    continue

            prev_c = df.iloc[i - 1]
            curr_c = df.iloc[i]

            # ── BUY trend: bearish engulfing → SHORT ──────────────────────────
            if active_trend == 1:
                if closes[i] > running_ref:
                    done = True
                elif self.is_bearish_engulfing(prev_c, curr_c):
                    # EMA filter: price must be below EMA for SHORT
                    if self.ema_filter and ema_vals is not None:
                        if not np.isnan(ema_vals[i]) and closes[i] > ema_vals[i]:
                            running_ref = max(running_ref, highs[i])
                            continue

                    entry = closes[i]
                    sl    = max(running_ref, highs[i])
                    risk  = sl - entry
                    if risk > 0:
                        tp = self.calc_tp(entry, risk, df.iloc[i]['st_lower'], 'short')
                        df.at[i, 'signal'] = -1
                        df.at[i, 'sl']     = round(sl, 5)
                        df.at[i, 'tp']     = round(tp, 5)
                        df.at[i, 'lot']    = self.lot_size
                        if self.sl_reversal:
                            last_short_sl = sl
                    done = True
                else:
                    running_ref = max(running_ref, highs[i])

            # ── SELL trend: bullish engulfing → LONG ──────────────────────────
            elif active_trend == -1:
                if closes[i] < running_ref:
                    done = True
                elif self.is_bullish_engulfing(prev_c, curr_c):
                    # EMA filter: price must be above EMA for LONG
                    if self.ema_filter and ema_vals is not None:
                        if not np.isnan(ema_vals[i]) and closes[i] < ema_vals[i]:
                            running_ref = min(running_ref, lows[i])
                            continue

                    entry = closes[i]
                    sl    = min(running_ref, lows[i])
                    risk  = entry - sl
                    if risk > 0:
                        tp = self.calc_tp(entry, risk, df.iloc[i]['st_upper'], 'long')
                        df.at[i, 'signal'] = 1
                        df.at[i, 'sl']     = round(sl, 5)
                        df.at[i, 'tp']     = round(tp, 5)
                        df.at[i, 'lot']    = self.lot_size
                        if self.sl_reversal:
                            last_long_sl = sl
                    done = True
                else:
                    running_ref = min(running_ref, lows[i])

        return df


# Backward-compatible alias — subclasses (EmaCrossoverRetestStrategy,
# Ema200PullbackEngulfingStrategy, MarkDollarSuperTrendStrategy, etc.)
# inherit from this name and it must keep working.
Mark2Strategy = SupertrendEngulfingReversalStrategy
