"""
SessionStrategy
===============
Trades candle closes at specific session times.

Logic:
  At each configured session_time (HH:MM in session_timezone):
    - GREEN candle (close > open)  →  BUY   entry at close
    - RED candle   (close < open)  →  SELL  entry at close

  SL  = candle low  × (1 – sl_buffer_pct)   for BUY
        candle high × (1 + sl_buffer_pct)   for SELL
  TP  = entry ± rr × risk

Optional filters (each toggled independently in config.yaml):
  supertrend_filter — skip signals against Supertrend direction
  ema_filter        — skip signals against 200-EMA direction

All parameters live under `session_strategy` in config.yaml.
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

_KEY = "session_strategy"

# ── Timezone map ──────────────────────────────────────────────────────────────
_TZ_MAP = {
    "IST": "Asia/Kolkata",       # UTC+5:30
    "NYC": "America/New_York",   # US Eastern
    "NYT": "America/New_York",
    "EST": "America/New_York",
    "EDT": "America/New_York",
    "LN":  "Europe/London",      # London
    "LON": "Europe/London",
    "GMT": "Europe/London",
    "UTC": "UTC",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_session_times(raw: str) -> list:
    """'09:00, 11:00, 22:00' → [(9, 0), (11, 0), (22, 0)]"""
    result = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            h, m = part.split(":")
            result.append((int(h), int(m)))
        except ValueError:
            pass
    return result


def _ts_to_hm(ts, tz_name: str):
    """Return (hour, minute) of a pandas Timestamp in the target timezone."""
    pt = pd.Timestamp(ts)
    if pt.tzinfo is None:
        pt = pt.tz_localize("UTC")
    pt = pt.tz_convert(tz_name)
    return pt.hour, pt.minute


# ── Strategy ──────────────────────────────────────────────────────────────────

class SessionStrategy(Mark2Strategy):
    """Session-time close strategy — buy green, sell red at configured hours."""

    _STRATEGY_KEY  = _KEY
    _STRATEGY_NAME = "session_strategy"

    def __init__(self):
        st_period = int(_load_param(_KEY,   "st_period", 10))
        st_mult   = float(_load_param(_KEY, "st_mult",   3.0))
        super().__init__(st_period, st_mult)

        self.rr             = _load_rr(_KEY)
        self.lot_size       = _load_lot_size(_KEY)
        self.risk_per_trade = _load_risk_per_trade(_KEY)

        # Session times
        raw_times           = str(_load_param(_KEY, "session_times",    "09:00"))
        self.session_hm     = _parse_session_times(raw_times)
        tz_raw              = str(_load_param(_KEY, "session_timezone", "IST")).upper().strip()
        self.tz_name        = _TZ_MAP.get(tz_raw, "Asia/Kolkata")

        # Candle filter
        self.candle_size_min = float(_load_param(_KEY, "candle_size_min", 0.0))
        self.sl_buffer_pct   = float(_load_param(_KEY, "sl_buffer_pct",  0.001))

        # Optional filters
        self.supertrend_filter = bool(_load_param(_KEY, "supertrend_filter", False))
        self.ema_filter        = bool(_load_param(_KEY, "ema_filter",        False))
        self.ema_period        = int(_load_param(_KEY,  "ema_period",        200))

    # ─────────────────────────────────────────────────────────────────────────

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        # ── Detect candle interval for diagnostics ────────────────────────
        if len(df) >= 2:
            sample_diff = (pd.Timestamp(df["time"].iloc[1]) - pd.Timestamp(df["time"].iloc[0]))
            mins = int(sample_diff.total_seconds() / 60)
            if mins < 60:
                tf_label = f"M{mins}"
            elif mins < 1440:
                tf_label = f"H{mins//60}"
            else:
                tf_label = "D1"
        else:
            tf_label = "?"

        # ── Supertrend (only computed when filter is ON) ──────────────────
        if self.supertrend_filter:
            df = self.calculate_supertrend(df)
            trend_col = "st_trend" if "st_trend" in df.columns else "trend"
            trend = df[trend_col].values
        else:
            trend = None

        # ── EMA ───────────────────────────────────────────────────────────
        ema_vals = (
            df["close"].ewm(span=self.ema_period, adjust=False).mean().values
            if self.ema_filter else None
        )

        n      = len(df)
        opens  = df["open"].values
        highs  = df["high"].values
        lows   = df["low"].values
        closes = df["close"].values
        times  = df["time"].values

        # ── Output columns ────────────────────────────────────────────────
        df["signal"]           = 0
        df["sl"]               = np.nan
        df["tp"]               = np.nan
        df["lot"]              = 0.0
        df["risk_per_trade"]   = np.nan
        df["entry_mode"]       = ""
        df["sl_exit_on_close"] = 1
        df["reverse_exit"]     = 0
        df["force_entry"]      = 0   # session trades always close the prior trade and re-enter
        df["_tp_mode"]         = "rr"  # always exit at R:R target, never Supertrend label

        # diagnostic counters
        _c_session = 0   # matched a session time
        _c_size    = 0   # filtered by candle_size_min
        _c_doji    = 0   # filtered as doji
        _c_ema     = 0   # filtered by EMA
        _c_st      = 0   # filtered by Supertrend

        for i in range(n):
            ts = pd.Timestamp(times[i])

            # ── 1. Session time check ─────────────────────────────────────
            h, m = _ts_to_hm(ts, self.tz_name)
            if not any(h == sh and m == sm for sh, sm in self.session_hm):
                continue
            _c_session += 1

            # ── 2. Candle size filter ─────────────────────────────────────
            rng = highs[i] - lows[i]
            if self.candle_size_min > 0 and rng < self.candle_size_min:
                _c_size += 1
                continue

            is_green = closes[i] > opens[i]
            is_red   = closes[i] < opens[i]
            if not is_green and not is_red:
                _c_doji += 1
                continue   # doji — skip

            # ── 3. BUY (green candle) ─────────────────────────────────────
            if is_green:
                if ema_vals is not None and closes[i] < ema_vals[i]:
                    _c_ema += 1
                    continue
                if trend is not None and trend[i] != 1:
                    _c_st += 1
                    continue
                entry    = closes[i]
                sl_price = lows[i] * (1.0 - self.sl_buffer_pct)
                risk     = entry - sl_price
                if risk <= 0:
                    continue
                df.at[i, "signal"]         = 1
                df.at[i, "sl"]             = round(sl_price, 5)
                df.at[i, "tp"]             = round(entry + self.rr * risk, 5)
                df.at[i, "lot"]            = self.lot_size
                df.at[i, "risk_per_trade"] = self.risk_per_trade
                df.at[i, "entry_mode"]     = "session_buy"
                df.at[i, "force_entry"]    = 1

            # ── 4. SELL (red candle) ──────────────────────────────────────
            else:
                if ema_vals is not None and closes[i] > ema_vals[i]:
                    _c_ema += 1
                    continue
                if trend is not None and trend[i] != -1:
                    _c_st += 1
                    continue
                entry    = closes[i]
                sl_price = highs[i] * (1.0 + self.sl_buffer_pct)
                risk     = sl_price - entry
                if risk <= 0:
                    continue
                df.at[i, "signal"]         = -1
                df.at[i, "sl"]             = round(sl_price, 5)
                df.at[i, "tp"]             = round(entry - self.rr * risk, 5)
                df.at[i, "lot"]            = self.lot_size
                df.at[i, "risk_per_trade"] = self.risk_per_trade
                df.at[i, "entry_mode"]     = "session_sell"
                df.at[i, "force_entry"]    = 1

        # ── Diagnostic summary ────────────────────────────────────────────
        _c_signals = int((df["signal"] != 0).sum())
        session_times_str = ", ".join(f"{h:02d}:{m:02d}" for h,m in self.session_hm)
        tz_short = {v: k for k, v in _TZ_MAP.items()}.get(self.tz_name, self.tz_name)
        print(f"  [SessionStrategy] TF={tf_label}  bars={n:,}  session_times={session_times_str} {tz_short}")
        print(f"    session-time matches : {_c_session:,}")
        if self.candle_size_min > 0:
            print(f"    filtered (size<{self.candle_size_min}) : {_c_size:,}  "
                  f"({'PROBLEM: too high for '+tf_label if _c_size > _c_session * 0.5 else 'ok'})")
        print(f"    filtered (doji)      : {_c_doji:,}")
        if self.ema_filter:
            print(f"    filtered (EMA)       : {_c_ema:,}")
        if self.supertrend_filter:
            print(f"    filtered (ST)        : {_c_st:,}")
        print(f"    => final signals     : {_c_signals:,}")
        if _c_signals == 0 and _c_session > 0:
            print(f"    WARNING: session times matched {_c_session} bars but 0 signals after filters.")
            print(f"    Hint: candle_size_min={self.candle_size_min} may be too large for {tf_label} data.")
        if _c_session == 0:
            print(f"    WARNING: 0 candles matched any session time!")
            print(f"    Hint: use timeframe H1 so candles open exactly at {session_times_str} {tz_short}.")

        return df
