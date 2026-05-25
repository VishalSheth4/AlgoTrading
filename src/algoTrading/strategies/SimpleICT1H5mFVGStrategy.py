"""
SimpleICT1H5mFVGStrategy
========================
ICT-inspired strategy combining:
  - 1H Supertrend for directional bias
  - Entry timeframe (M5 / M15 — whatever df is passed) for setup

SIGNAL LOGIC
────────────
BULLISH setup (1H Supertrend = UP):
  1. Liquidity sweep  : candle wicks BELOW a recent swing low AND closes back above
  2. Displacement     : within SWEEP_WINDOW bars, a strong bullish candle (body ≥ threshold)
  3. FVG formed       : the bar AFTER the displacement — its low > the bar BEFORE displacement's high
  4. Retrace entry    : within FVG_WINDOW bars, price re-enters the FVG from above
  5. Entry            : FVG midpoint
     SL              : below the sweep wick low (+ buffer)
     TP              : entry + RR × risk

BEARISH setup (1H Supertrend = DOWN):
  Symmetric — sweeps swing high, displacement bearish, bearish FVG, short entry.

Key parameters (all configurable via config.yaml):
  rr              : reward-to-risk ratio
  lot_size        : lot size per trade
  risk_per_trade  : fraction of capital risked
  st_period       : Supertrend ATR period (default 10)
  st_mult         : Supertrend multiplier (default 3)
  liq_lookback    : bars to scan for swing high/low (default 20)
  min_body_ratio  : displacement candle body filter (default 0.55)
  sweep_window    : bars after sweep to find displacement (default 4)
  fvg_window      : bars after FVG confirmation to wait for retrace (default 8)
  sl_buffer_pct   : SL pad beyond sweep extreme as % of risk (default 0.1)
"""

import numpy as np
import pandas as pd

from algoTrading.strategies.SupertrendEngulfingReversalStrategy import (
    _load_rr, _load_lot_size, _load_risk_per_trade, _load_yaml,
)


# ─────────────────────────────────────────────
# YAML PARAMETER LOADERS
# ─────────────────────────────────────────────

_KEY = "ict_simple_1h5m_fvg"


def _load_param(key: str, default):
    """Read a strategy-level param from config.yaml with fallback to default."""
    try:
        data = _load_yaml()
        return type(default)(data["strategies"][_KEY][key])
    except Exception:
        return default


class SimpleICT1H5mFVGStrategy:
    """
    ICT Simple Strategy — 1H Supertrend bias + M5 FVG entry.
    """

    _STRATEGY_KEY = _KEY

    def __init__(self):
        self.rr             = _load_rr(_KEY)
        self.lot_size       = _load_lot_size(_KEY)
        self.risk_per_trade = _load_risk_per_trade(_KEY)

        # ICT-specific params (overridable in config.yaml)
        self.st_period      = _load_param("st_period",      10)
        self.st_mult        = _load_param("st_mult",        3.0)
        self.liq_lookback   = _load_param("liq_lookback",   20)
        self.min_body_ratio = _load_param("min_body_ratio", 0.55)
        self.sweep_window   = _load_param("sweep_window",   4)
        self.fvg_window     = _load_param("fvg_window",     8)
        self.sl_buffer_pct  = _load_param("sl_buffer_pct",  0.1)
        # Minimum SL distance in price units — prevents micro-stop blowups.
        # Default 0.50 = 50 ticks on XAUUSD (pip=0.01); set in config.yaml as min_sl_dist.
        self.min_sl_dist    = _load_param("min_sl_dist",    0.50)

        # TP mode: "rr" = fixed RR target | "ema50" = trail with EMA, exit on close cross
        self.tp_mode        = str(_load_param("tp_mode",      "rr"))
        self.trail_ema_period = int(_load_param("trail_ema_period", 50))

    # ─────────────────────────────────────────────
    # SUPERTREND (Wilder ATR — matches TradingView)
    # ─────────────────────────────────────────────

    def _supertrend(self, df: pd.DataFrame):
        """Returns (trend[], lower[], upper[]) arrays. trend 1=bull, -1=bear."""
        close = df["close"].values
        high  = df["high"].values
        low   = df["low"].values
        n     = len(df)

        hl2        = (high + low) / 2
        prev_close = np.concatenate([[close[0]], close[:-1]])

        tr = np.maximum.reduce([
            high - low,
            np.abs(high - prev_close),
            np.abs(low  - prev_close),
        ])

        atr = pd.Series(tr).ewm(
            alpha=1 / self.st_period,
            min_periods=self.st_period,
            adjust=False,
        ).mean().values

        basic_upper = hl2 + self.st_mult * atr
        basic_lower = hl2 - self.st_mult * atr

        final_upper = basic_upper.copy()
        final_lower = basic_lower.copy()

        for i in range(1, n):
            if np.isnan(basic_lower[i]):
                continue
            prev_lo = final_lower[i - 1]
            prev_hi = final_upper[i - 1]

            final_lower[i] = (
                basic_lower[i] if (np.isnan(prev_lo) or basic_lower[i] > prev_lo or close[i - 1] < prev_lo)
                else prev_lo
            )
            final_upper[i] = (
                basic_upper[i] if (np.isnan(prev_hi) or basic_upper[i] < prev_hi or close[i - 1] > prev_hi)
                else prev_hi
            )

        trend = -np.ones(n, dtype=int)
        for i in range(1, n):
            if np.isnan(final_upper[i]):
                trend[i] = trend[i - 1]
                continue
            if trend[i - 1] == 1:
                trend[i] = -1 if close[i] < final_lower[i] else 1
            else:
                trend[i] = 1  if close[i] > final_upper[i] else -1

        return trend, final_lower, final_upper

    # ─────────────────────────────────────────────
    # H1 TREND MAPPING
    # ─────────────────────────────────────────────

    def _compute_h1_trends(self, df: pd.DataFrame) -> np.ndarray:
        """
        Resample the entry-TF DataFrame to 1H, compute Supertrend on H1,
        then map each entry-TF bar to the corresponding H1 trend.

        Uses the last COMPLETED H1 bar so there is no look-ahead:
        e.g. M5 bar at 07:35 uses the trend from the 06:00 H1 candle.
        """
        if "time" not in df.columns:
            return -np.ones(len(df), dtype=int)

        df_t = df.copy()
        df_t["time"] = pd.to_datetime(df_t["time"], utc=True)

        # Resample to 1H
        df_h1 = (
            df_t.set_index("time")
            .resample("1h")
            .agg({"open": "first", "high": "max", "low": "min",
                  "close": "last", "volume": "sum"})
            .dropna(subset=["close"])
            .reset_index()
        )

        if len(df_h1) < self.st_period + 5:
            return -np.ones(len(df), dtype=int)

        trend_h1, _, _ = self._supertrend(df_h1)
        df_h1["h1_trend"] = trend_h1

        # Shift trend by 1 period: use the CLOSED H1 bar's trend
        # bar at 07:00 closed → valid for M5 bars starting 08:00+
        df_h1["h1_valid_from"] = df_h1["time"] + pd.Timedelta(hours=1)

        # Merge: for each entry-TF bar, find the last H1 bar whose
        # h1_valid_from <= bar time
        merged = pd.merge_asof(
            df_t[["time"]].sort_values("time"),
            df_h1[["h1_valid_from", "h1_trend"]].rename(
                columns={"h1_valid_from": "time"}
            ).sort_values("time"),
            on="time",
            direction="backward",
        )

        # Realign to original df order
        result = pd.merge(
            df_t[["time"]].reset_index(),
            merged,
            on="time",
            how="left",
        ).set_index("index").sort_index()["h1_trend"]

        return result.fillna(-1).astype(int).values

    # ─────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────

    def _is_displacement(self, o, h, l, c) -> bool:
        rng = h - l
        if rng == 0:
            return False
        return abs(c - o) / rng >= self.min_body_ratio

    # ─────────────────────────────────────────────
    # SIGNAL GENERATION
    # ─────────────────────────────────────────────

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["signal"]        = 0
        df["sl"]            = np.nan
        df["tp"]            = np.nan
        df["lot"]           = 0.0
        df["risk_per_trade"] = np.nan

        h1_trends = self._compute_h1_trends(df)

        opens  = df["open"].values
        highs  = df["high"].values
        lows   = df["low"].values
        closes = df["close"].values
        n      = len(df)

        min_start = max(self.liq_lookback + 3, self.st_period + 2)

        # ── State machine — BULL ─────────────────────────────────────
        bull_state      = "idle"   # idle | swept | fvg_active
        bull_sweep_low  = 0.0
        bull_fvg_top    = 0.0
        bull_fvg_bot    = 0.0
        bull_sweep_bar  = -1
        bull_disp_bar   = -1
        bull_fvg_bar    = -1

        # ── State machine — BEAR ─────────────────────────────────────
        bear_state      = "idle"
        bear_sweep_high = 0.0
        bear_fvg_top    = 0.0
        bear_fvg_bot    = 0.0
        bear_sweep_bar  = -1
        bear_disp_bar   = -1
        bear_fvg_bar    = -1

        for i in range(min_start, n):
            h1 = h1_trends[i]

            # ════════════════════════════════════════
            #  BULLISH MACHINE  (1H trend UP)
            # ════════════════════════════════════════
            if h1 == 1:
                # Reset bear machine when bias flips
                if bear_state != "idle":
                    bear_state = "idle"

                # ── IDLE: look for liquidity sweep ────────────────
                if bull_state == "idle":
                    lb_lo   = max(0, i - self.liq_lookback)
                    swing_lo = lows[lb_lo:i].min()

                    # Sweep: wick below swing_low, close back above
                    if lows[i] < swing_lo and closes[i] > swing_lo:
                        bull_state     = "swept"
                        bull_sweep_low = lows[i]
                        bull_sweep_bar = i

                # ── SWEPT: wait for bullish displacement ──────────
                elif bull_state == "swept":
                    if i - bull_sweep_bar > self.sweep_window:
                        bull_state = "idle"
                        continue

                    is_bull_candle = closes[i] > opens[i]
                    is_disp = self._is_displacement(opens[i], highs[i], lows[i], closes[i])

                    if is_bull_candle and is_disp:
                        bull_disp_bar = i
                        bull_state    = "disp_seen"

                # ── DISP_SEEN: confirm FVG on the NEXT bar ────────
                elif bull_state == "disp_seen":
                    # c0 = bar before displacement, c2 = this bar (after displacement)
                    c0_idx = bull_disp_bar - 1
                    if c0_idx >= 0:
                        # Bullish FVG: this bar's low > bar-before-displacement's high
                        if lows[i] > highs[c0_idx]:
                            bull_fvg_top = lows[i]
                            bull_fvg_bot = highs[c0_idx]
                            bull_fvg_bar = i
                            bull_state   = "fvg_active"
                        else:
                            bull_state = "idle"   # no FVG created
                    else:
                        bull_state = "idle"

                # ── FVG_ACTIVE: wait for price to retrace ─────────
                elif bull_state == "fvg_active":
                    if i - bull_fvg_bar > self.fvg_window:
                        bull_state = "idle"
                        continue

                    # Price retraces into the FVG zone (wick touches, close stays above bottom)
                    in_fvg = lows[i] <= bull_fvg_top and closes[i] >= bull_fvg_bot

                    if in_fvg:
                        midpoint = (bull_fvg_top + bull_fvg_bot) / 2
                        entry    = midpoint
                        buf      = (bull_fvg_top - bull_sweep_low) * self.sl_buffer_pct
                        sl       = bull_sweep_low - buf
                        risk     = entry - sl

                        if risk >= self.min_sl_dist:
                            df.at[i, "signal"]        = 1
                            df.at[i, "sl"]            = round(sl, 5)
                            df.at[i, "tp"]            = round(entry + self.rr * risk, 5)
                            df.at[i, "lot"]           = self.lot_size
                            df.at[i, "risk_per_trade"] = self.risk_per_trade

                        bull_state = "idle"   # one trade per setup

            # ════════════════════════════════════════
            #  BEARISH MACHINE  (1H trend DOWN)
            # ════════════════════════════════════════
            elif h1 == -1:
                # Reset bull machine when bias flips
                if bull_state != "idle":
                    bull_state = "idle"

                # ── IDLE: look for liquidity sweep ────────────────
                if bear_state == "idle":
                    lb_lo    = max(0, i - self.liq_lookback)
                    swing_hi = highs[lb_lo:i].max()

                    # Sweep: wick above swing_high, close back below
                    if highs[i] > swing_hi and closes[i] < swing_hi:
                        bear_state      = "swept"
                        bear_sweep_high = highs[i]
                        bear_sweep_bar  = i

                # ── SWEPT: wait for bearish displacement ──────────
                elif bear_state == "swept":
                    if i - bear_sweep_bar > self.sweep_window:
                        bear_state = "idle"
                        continue

                    is_bear_candle = closes[i] < opens[i]
                    is_disp = self._is_displacement(opens[i], highs[i], lows[i], closes[i])

                    if is_bear_candle and is_disp:
                        bear_disp_bar = i
                        bear_state    = "disp_seen"

                # ── DISP_SEEN: confirm FVG ────────────────────────
                elif bear_state == "disp_seen":
                    c0_idx = bear_disp_bar - 1
                    if c0_idx >= 0:
                        # Bearish FVG: this bar's high < bar-before-displacement's low
                        if highs[i] < lows[c0_idx]:
                            bear_fvg_top = lows[c0_idx]
                            bear_fvg_bot = highs[i]
                            bear_fvg_bar = i
                            bear_state   = "fvg_active"
                        else:
                            bear_state = "idle"
                    else:
                        bear_state = "idle"

                # ── FVG_ACTIVE: wait for retrace ──────────────────
                elif bear_state == "fvg_active":
                    if i - bear_fvg_bar > self.fvg_window:
                        bear_state = "idle"
                        continue

                    # Price retraces UP into bearish FVG
                    in_fvg = highs[i] >= bear_fvg_bot and closes[i] <= bear_fvg_top

                    if in_fvg:
                        midpoint = (bear_fvg_top + bear_fvg_bot) / 2
                        entry    = midpoint
                        buf      = (bear_sweep_high - bear_fvg_bot) * self.sl_buffer_pct
                        sl       = bear_sweep_high + buf
                        risk     = sl - entry

                        if risk >= self.min_sl_dist:
                            df.at[i, "signal"]        = -1
                            df.at[i, "sl"]            = round(sl, 5)
                            df.at[i, "tp"]            = round(entry - self.rr * risk, 5)
                            df.at[i, "lot"]           = self.lot_size
                            df.at[i, "risk_per_trade"] = self.risk_per_trade

                        bear_state = "idle"

        # ── EMA50 trailing exit (second pass) ─────────────────────────
        # When tp_mode = "ema50": exit LONG when close < EMA50,
        #                         exit SHORT when close > EMA50.
        # Marks reverse_exit = 1 on the crossing bar.
        # RR TP is still set at entry as a hard cap (acts as max profit target).
        if self.tp_mode == "ema50":
            ema_trail = (
                df["close"]
                .ewm(span=self.trail_ema_period, adjust=False)
                .mean()
                .values
            )
            df["reverse_exit"] = 0

            sigs   = df["signal"].values
            sl_arr = df["sl"].values
            tp_arr = df["tp"].values

            trade_dir = 0
            trade_sl  = 0.0
            trade_tp  = 0.0

            for i in range(1, len(df)):
                if sigs[i] != 0:
                    trade_dir = int(sigs[i])
                    trade_sl  = float(sl_arr[i])
                    trade_tp  = float(tp_arr[i])
                    continue

                if trade_dir == 0:
                    continue

                c = closes[i]
                e = ema_trail[i]

                # EMA cross exit
                if trade_dir == 1 and c < e:      # LONG: close below EMA → exit
                    df.at[i, "reverse_exit"] = 1
                    trade_dir = 0
                    continue
                if trade_dir == -1 and c > e:     # SHORT: close above EMA → exit
                    df.at[i, "reverse_exit"] = 1
                    trade_dir = 0
                    continue

                # Track SL/TP hits so we stop marking future bars
                if trade_dir == 1:
                    if c <= trade_sl or c >= trade_tp:
                        trade_dir = 0
                elif trade_dir == -1:
                    if c >= trade_sl or c <= trade_tp:
                        trade_dir = 0

        return df
