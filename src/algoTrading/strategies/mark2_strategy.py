import pandas as pd
import numpy as np
from pathlib import Path
from algoTrading.config import Config

_YAML_PATH = Path(__file__).resolve().parents[1] / "config.yaml"


def _load_yaml() -> dict:
    """Load config.yaml once; returns empty dict on any error."""
    try:
        import yaml
        with open(_YAML_PATH, "r") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _load_lot_size(strategy_key: str) -> float:
    """Per-strategy lot_size from config.yaml → fallback Config.LOT_SIZE."""
    try:
        return float(_load_yaml()["strategies"][strategy_key]["lot_size"])
    except Exception:
        return float(Config.LOT_SIZE)


def _load_rr(strategy_key: str) -> float:
    """Per-strategy rr from config.yaml → fallback Config.RR."""
    try:
        return float(_load_yaml()["strategies"][strategy_key]["rr"])
    except Exception:
        return float(Config.RR)


def _load_risk_per_trade(strategy_key: str) -> float:
    """Per-strategy risk_per_trade from config.yaml → global yaml value → Config.RISK_PER_TRADE."""
    data = _load_yaml()
    try:
        return float(data["strategies"][strategy_key]["risk_per_trade"])
    except Exception:
        pass
    try:
        return float(data["risk_per_trade"])
    except Exception:
        return float(Config.RISK_PER_TRADE)


class Mark2Strategy:

    _STRATEGY_KEY = "mark2"

    def __init__(self, period=10, multiplier=3):
        self.period            = period
        self.multiplier        = multiplier
        self.rr                = _load_rr(self._STRATEGY_KEY)
        self.tp_mode           = Config.TP_MODE
        self.fix_profit        = getattr(Config, 'FIX_PROFIT', 5)
        self.min_trend_candles = Config.MIN_TREND_CANDLES
        self.lot_size          = _load_lot_size(self._STRATEGY_KEY)
        self.risk_per_trade    = _load_risk_per_trade(self._STRATEGY_KEY)

    # =========================================================
    # Supertrend — matches TradingView ta.supertrend(factor, atrPeriod)
    #
    # Three rules that mirror the Pine Script built-in exactly:
    #   1. ATR via Wilder's RMA  (ta.atr)
    #   2. Band ratchet: lower can only move UP, upper can only move DOWN
    #      reset only when prev-close breaches the band
    #   3. Trend flip is STATE-BASED and uses the CURRENT bar's adjusted bands:
    #        bullish → flip to bearish if close[i] < final_lower[i]
    #        bearish → flip to bullish if close[i] > final_upper[i]
    # =========================================================
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
            np.abs(low  - prev_close)
        ])

        # Wilder's RMA — identical to ta.atr() in TradingView
        atr = pd.Series(tr).ewm(
            alpha=1/self.period, min_periods=self.period, adjust=False
        ).mean().values

        basic_upper = hl2 + self.multiplier * atr
        basic_lower = hl2 - self.multiplier * atr

        # ── Band ratchet ──────────────────────────────────────────────
        # lower band : ratchets UP  — resets if prev-close fell below it
        # upper band : ratchets DOWN — resets if prev-close rose above it
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

        # ── Trend: state-based, close[i] vs CURRENT bar's adjusted bands ─
        # TradingView initialises direction=1 (BEARISH) during ATR warm-up.
        # We match that: -1 = bearish, +1 = bullish.
        #   trend ==  1  → bullish (green line below price)
        #   trend == -1  → bearish (red line above price)
        trend      = -np.ones(n, dtype=int)   # start BEARISH — matches TV direction=1
        supertrend = np.full(n, np.nan)

        for i in range(1, n):
            lo = final_lower[i]
            hi = final_upper[i]

            if np.isnan(hi):            # ATR warm-up not complete
                trend[i] = trend[i - 1]
                continue

            if trend[i - 1] == 1:      # currently bullish → flip if close < lower band
                trend[i] = -1 if close[i] < lo else 1
            else:                      # currently bearish → flip if close > upper band
                trend[i] = 1  if close[i] > hi else -1

            supertrend[i] = lo if trend[i] == 1 else hi

        df = df.copy()
        df['supertrend'] = supertrend   # active line (support when green, resistance when red)
        df['st_trend']   = trend        #  1 = buy trend  |  -1 = sell trend
        df['st_lower']   = final_lower  # green support line  (SHORT TP target)
        df['st_upper']   = final_upper  # red resistance line (LONG  TP target)

        return df

    # =========================================================
    # TP calculator — reads RR and TP_MODE from Config
    #
    #   direction = 'short'  →  target is BELOW entry
    #     "rr"   : entry − RR × risk
    #     "st"   : st_lower (buy supertrend support)
    #     "both" : max(rr_tp, st_tp)  ← higher = closer for a short
    #
    #   direction = 'long'   →  target is ABOVE entry
    #     "rr"   : entry + RR × risk
    #     "st"   : st_upper (sell supertrend resistance)
    #     "both" : min(rr_tp, st_tp)  ← lower = closer for a long
    # =========================================================
    def calc_tp(self, entry, risk, st_line, direction):
        # Fixed profit mode — ignore risk and st_line entirely
        if self.tp_mode == "fix_profit":
            return float(entry - self.fix_profit) if direction == 'short' else float(entry + self.fix_profit)

        st_valid = not np.isnan(st_line)

        if direction == 'short':
            tp_rr = entry - self.rr * risk
            if self.tp_mode == "rr":
                return float(tp_rr)
            elif self.tp_mode == "st":
                return float(st_line) if st_valid else float(tp_rr)
            else:  # "both"
                return float(max(tp_rr, st_line)) if st_valid else float(tp_rr)

        else:  # long
            tp_rr = entry + self.rr * risk
            if self.tp_mode == "rr":
                return float(tp_rr)
            elif self.tp_mode == "st":
                return float(st_line) if st_valid else float(tp_rr)
            else:  # "both"
                return float(min(tp_rr, st_line)) if st_valid else float(tp_rr)

    # =========================================================
    # SHORT setup: bearish candle after buy trend starts
    # prev bullish → curr opens at/above prev close → curr closes below prev close
    # =========================================================
    def is_bearish_engulfing(self, prev, curr):
        return (
            prev['close'] > prev['open'] and    # prev candle is bullish
            curr['close'] < curr['open'] and    # curr candle is bearish (not a doji)
            curr['open']  >= prev['close'] and  # opens at/above prev close
            curr['close'] <= prev['open']       # closes at/below prev open — fully engulfs prev body
        )

    # =========================================================
    # LONG setup: bullish candle after sell trend starts
    # prev bearish → curr opens at/below prev close → curr closes above prev open
    # =========================================================
    def is_bullish_engulfing(self, prev, curr):
        return (
            prev['close'] < prev['open'] and    # prev candle is bearish
            curr['close'] > curr['open'] and    # curr candle is bullish (not a doji)
            curr['open']  <= prev['close'] and  # opens at/below prev close
            curr['close'] >= prev['open']       # closes at/above prev open — fully engulfs prev body
        )

    # =========================================================
    # Signal generation
    #
    # Rule:
    #   X candle = first candle after Supertrend flips direction
    #
    #   BUY TREND (trend == +1) — no buy, no sell on bullish engulfing:
    #     Watch first 2 candles after X. running_high = X.high (expands).
    #     close > running_high → invalidated.
    #     bearish engulfing → SHORT  (SL = candle high, TP = st_lower)
    #     bullish engulfing or other → skip, keep watching.
    #
    #   SELL TREND (trend == -1) — no sell, no buy on bearish engulfing:
    #     Watch first 2 candles after X. running_low = X.low (expands).
    #     close < running_low → invalidated.
    #     bullish engulfing → LONG  (SL = candle low, TP = st_upper)
    #     bearish engulfing or other → skip, keep watching.
    #
    #   One attempt per trend segment — resets only on next Supertrend flip.
    # =========================================================
    def generate_signals(self, df):
        df    = df.copy()
        df    = self.calculate_supertrend(df)

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

        x_idx        = None
        active_trend = None
        done         = False   # True once trade taken, invalidated, or window expired
        running_ref  = None    # running_high for buy trend, running_low for sell trend
        trend_count  = 0       # candles elapsed in the current trend

        for i in range(1, n):

            # ── Supertrend flipped: record X candle, reset state ──────────
            if trend[i] != trend[i - 1]:
                prev_count   = trend_count
                trend_count  = 1  # X candle is the first candle of the new trend

                # Require the PREVIOUS trend to have lasted long enough
                if prev_count >= self.min_trend_candles:
                    x_idx        = i
                    active_trend = trend[i]
                    done         = False
                    running_ref  = highs[i] if active_trend == 1 else lows[i]
                else:
                    x_idx        = None
                    active_trend = trend[i]
                    done         = True
                continue   # X candle is reference only

            trend_count += 1

            if x_idx is None or done:
                continue

            # Only watch the first 2 candles after X
            if i - x_idx > 2:
                done = True
                continue

            prev_c = df.iloc[i - 1]
            curr_c = df.iloc[i]

            # ── BUY TREND: no buy — bearish engulfing only → SHORT ───────────
            if active_trend == 1:
                if closes[i] > running_ref:                       # ran away → invalidate
                    done = True
                elif self.is_bearish_engulfing(prev_c, curr_c):  # bearish engulfing → SHORT
                    entry = closes[i]
                    sl    = max(running_ref, highs[i])            # max high across X..signal candle
                    risk  = sl - entry
                    if risk > 0:
                        tp = self.calc_tp(entry, risk, df.iloc[i]['st_lower'], 'short')
                        df.at[i, 'signal'] = -1
                        df.at[i, 'sl']     = sl
                        df.at[i, 'tp']     = tp
                        df.at[i, 'lot']    = self.lot_size
                    done = True
                else:                                             # bullish engulfing or other → skip
                    running_ref = max(running_ref, highs[i])

            # ── SELL TREND: no sell — bullish engulfing only → LONG ───────────
            elif active_trend == -1:
                if closes[i] < running_ref:                       # ran away → invalidate
                    done = True
                elif self.is_bullish_engulfing(prev_c, curr_c):  # bullish engulfing → LONG
                    entry = closes[i]
                    sl    = min(running_ref, lows[i])             # min low across X..signal candle
                    risk  = entry - sl
                    if risk > 0:
                        tp = self.calc_tp(entry, risk, df.iloc[i]['st_upper'], 'long')
                        df.at[i, 'signal'] = 1
                        df.at[i, 'sl']     = sl
                        df.at[i, 'tp']     = tp
                        df.at[i, 'lot']    = self.lot_size
                    done = True
                else:                                             # bearish engulfing or other → skip
                    running_ref = min(running_ref, lows[i])

        return df
