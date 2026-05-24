"""
strategy/dealing_range.py
=========================
Dealing Range, Premium/Discount Zones, and Power of Three (AMD cycle).

Dealing Range
─────────────
The range between a significant swing high and swing low.
Every ICT concept (OTE, FVG, OB, liquidity targets) is interpreted
relative to the dealing range.

Premium / Discount
──────────────────
Equilibrium (50%) divides the range into two zones:
  Discount : below 50% → cheap → BUY zone
  Premium  : above 50% → expensive → SELL zone

ICT Rule: Buy in Discount, Sell in Premium.

Power of Three (AMD)
────────────────────
Markets move in 3 phases every session:
  1. ACCUMULATION  — tight range, institutions building positions
  2. MANIPULATION  — false breakout, stops taken (Judas Swing)
  3. DISTRIBUTION  — real directional move begins

Detection uses intra-session price action relative to the Asian range
and session open price.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

import pandas as pd

from algoTrading.ict_trading_system.settings import (
    Bias, Instrument, Timeframe, PIP_SIZE,
)
from algoTrading.ict_trading_system.data.models import Candle

logger = logging.getLogger(__name__)

# Fibonacci zone boundaries used in ICT
_EQ   = 0.500
_OTE_LO = 0.620
_OTE_HI = 0.790
_EXT_LO = 0.886   # Deep discount / deep premium
_EXT_HI = 1.000


# ─────────────────────────────────────────────
# PREMIUM / DISCOUNT ZONE (ENHANCED)
# ─────────────────────────────────────────────

@dataclass
class PremiumDiscountZoneEx:
    """
    Extended Premium/Discount Zone with named sub-zones.

    Sub-zones (bullish: measuring from swing low):
      0%   – 38.2%  : Deep Discount
      38.2% – 50%   : Standard Discount
      50%           : Equilibrium
      50% – 61.8%   : Standard Premium
      61.8% – 100%  : Deep Premium

    OTE zone sits inside deep discount (for buys) or deep premium (for sells).
    """
    instrument:  Instrument
    swing_high:  float
    swing_low:   float
    direction:   Bias = Bias.NEUTRAL   # direction of the impulse that created this range

    def __post_init__(self):
        self.range_size   = self.swing_high - self.swing_low
        self.equilibrium  = self.swing_low + self.range_size * _EQ

    # ── Zone boundaries ──────────────────────────────────────────────
    @property
    def deep_discount_zone(self) -> Tuple[float, float]:
        return (self.swing_low, self.swing_low + self.range_size * 0.382)

    @property
    def discount_zone(self) -> Tuple[float, float]:
        return (self.swing_low + self.range_size * 0.382, self.equilibrium)

    @property
    def premium_zone(self) -> Tuple[float, float]:
        return (self.equilibrium, self.swing_low + self.range_size * 0.618)

    @property
    def deep_premium_zone(self) -> Tuple[float, float]:
        return (self.swing_low + self.range_size * 0.618, self.swing_high)

    @property
    def ote_buy_zone(self) -> Tuple[float, float]:
        """62%–79% retracement into discount (for bullish setups)."""
        ote_hi = self.swing_high - self.range_size * _OTE_LO
        ote_lo = self.swing_high - self.range_size * _OTE_HI
        return (ote_lo, ote_hi)

    @property
    def ote_sell_zone(self) -> Tuple[float, float]:
        """62%–79% retracement into premium (for bearish setups)."""
        ote_lo = self.swing_low + self.range_size * _OTE_LO
        ote_hi = self.swing_low + self.range_size * _OTE_HI
        return (ote_lo, ote_hi)

    # ── Classification ──────────────────────────────────────────────
    def classify(self, price: float) -> str:
        if price <= self.deep_discount_zone[1]:
            return "DEEP_DISCOUNT"
        if price <= self.equilibrium:
            return "DISCOUNT"
        if price <= self.premium_zone[1]:
            return "PREMIUM"
        return "DEEP_PREMIUM"

    def is_premium(self, price: float) -> bool:
        return price > self.equilibrium

    def is_discount(self, price: float) -> bool:
        return price < self.equilibrium

    def in_ote_buy_zone(self, price: float) -> bool:
        lo, hi = self.ote_buy_zone
        return lo <= price <= hi

    def in_ote_sell_zone(self, price: float) -> bool:
        lo, hi = self.ote_sell_zone
        return lo <= price <= hi

    def fibo_pct(self, price: float) -> float:
        """0.0=low … 1.0=high."""
        if self.range_size == 0:
            return 0.0
        return (price - self.swing_low) / self.range_size

    def __repr__(self):
        return (
            f"PD({self.instrument.value} H={self.swing_high:.5f} "
            f"L={self.swing_low:.5f} EQ={self.equilibrium:.5f} "
            f"OTE_buy=[{self.ote_buy_zone[0]:.5f}–{self.ote_buy_zone[1]:.5f}] "
            f"OTE_sell=[{self.ote_sell_zone[0]:.5f}–{self.ote_sell_zone[1]:.5f}])"
        )


def compute_pd_zone(
    candles: List[Candle],
    lookback: int = 50,
) -> Optional[PremiumDiscountZoneEx]:
    """
    Build a PremiumDiscountZoneEx from recent candles.
    """
    if not candles:
        return None

    recent = candles[-lookback:] if len(candles) >= lookback else candles
    sh = max(c.high for c in recent)
    sl = min(c.low  for c in recent)
    hi_idx = max(range(len(recent)), key=lambda i: recent[i].high)
    lo_idx = min(range(len(recent)), key=lambda i: recent[i].low)
    direction = Bias.BEARISH if hi_idx > lo_idx else Bias.BULLISH

    return PremiumDiscountZoneEx(
        instrument = recent[-1].instrument,
        swing_high = sh,
        swing_low  = sl,
        direction  = direction,
    )


# ─────────────────────────────────────────────
# POWER OF THREE — AMD CYCLE
# ─────────────────────────────────────────────

@dataclass
class AMDCycle:
    """
    Accumulation → Manipulation → Distribution analysis for a session.

    Fields are populated incrementally as the phase is identified.
    """
    session_open:      float
    asian_high:        Optional[float] = None
    asian_low:         Optional[float] = None
    manipulation_high: Optional[float] = None
    manipulation_low:  Optional[float] = None
    distribution_dir:  Optional[Bias]  = None
    phase:             str = "ACCUMULATION"   # ACCUMULATION | MANIPULATION | DISTRIBUTION
    confirmed:         bool = False

    @property
    def has_bullish_manipulation(self) -> bool:
        """Price swept above accumulation range (Judas swing up) → bearish dist."""
        if self.asian_high and self.manipulation_high:
            return self.manipulation_high > self.asian_high
        return False

    @property
    def has_bearish_manipulation(self) -> bool:
        """Price swept below accumulation range → bullish distribution."""
        if self.asian_low and self.manipulation_low:
            return self.manipulation_low < self.asian_low
        return False


def classify_amd_phase(
    candles: List[Candle],
    asian_high: float,
    asian_low:  float,
    lookback:   int = 30,
    atr_mult:   float = 2.0,
) -> AMDCycle:
    """
    Classify the current AMD phase based on intra-session candles.

    Logic:
      ACCUMULATION:
        - All candles trade within a tight range (< 5× avg ATR range)
        - No sustained break above/below the Asian range
      MANIPULATION:
        - At least one candle wicks OUTSIDE the Asian range with a wick
          rejection (close back inside) — stop hunt
      DISTRIBUTION:
        - A strong displacement candle breaks outside the range AND holds
          (close stays outside) — the real move begins

    Args:
        candles:    candles AFTER Asian session (London/NY)
        asian_high: Asian session high
        asian_low:  Asian session low
        lookback:   candles to analyse
        atr_mult:   ATR multiple for "strong" displacement
    """
    recent = candles[-lookback:] if len(candles) >= lookback else candles
    if not recent:
        return AMDCycle(session_open=asian_high)

    session_open = recent[0].open

    # Compute avg range for ATR approximation
    ranges = [c.full_range for c in recent]
    avg_range = sum(ranges) / len(ranges) if ranges else 0

    cycle = AMDCycle(
        session_open = session_open,
        asian_high   = asian_high,
        asian_low    = asian_low,
    )

    for c in recent:
        if cycle.phase == "ACCUMULATION":
            wicked_above = c.high > asian_high and c.close < asian_high
            wicked_below = c.low  < asian_low  and c.close > asian_low

            if wicked_above:
                cycle.phase            = "MANIPULATION"
                cycle.manipulation_high = c.high
            elif wicked_below:
                cycle.phase            = "MANIPULATION"
                cycle.manipulation_low  = c.low

        elif cycle.phase == "MANIPULATION":
            broke_above = c.close > asian_high and c.full_range >= avg_range * atr_mult
            broke_below = c.close < asian_low  and c.full_range >= avg_range * atr_mult

            if broke_above:
                cycle.phase            = "DISTRIBUTION"
                cycle.distribution_dir = Bias.BULLISH
                cycle.confirmed        = True
            elif broke_below:
                cycle.phase            = "DISTRIBUTION"
                cycle.distribution_dir = Bias.BEARISH
                cycle.confirmed        = True

    logger.debug(
        f"AMD phase={cycle.phase} dist_dir={cycle.distribution_dir} "
        f"manip_h={cycle.manipulation_high} manip_l={cycle.manipulation_low}"
    )
    return cycle


# ─────────────────────────────────────────────
# STANDALONE DATAFRAME INTERFACE
# ─────────────────────────────────────────────

def scan_pd_zones(
    df: pd.DataFrame,
    instrument: Instrument,
    lookback: int = 50,
) -> pd.DataFrame:
    """
    Scan a OHLCV DataFrame and return rolling Premium/Discount zone stats.

    Returns a DataFrame with one row per candle (from index `lookback` onward):
        candle_index, timestamp, zone_class, fibo_pct,
        swing_high, swing_low, equilibrium,
        ote_buy_lo, ote_buy_hi, ote_sell_lo, ote_sell_hi
    """
    ts_col = next(
        (c for c in ("time", "timestamp", "datetime", "date") if c in df.columns),
        None
    )
    if df.empty or len(df) < lookback:
        return pd.DataFrame()

    rows = []
    for i in range(lookback, len(df)):
        w  = df.iloc[i - lookback: i]
        sh = float(w["high"].max())
        sl = float(w["low"].min())
        rng = sh - sl
        if rng == 0:
            continue

        eq        = sl + rng * _EQ
        ob_lo     = sh - rng * _OTE_HI
        ob_hi     = sh - rng * _OTE_LO
        os_lo     = sl + rng * _OTE_LO
        os_hi     = sl + rng * _OTE_HI
        price     = float(df.iloc[i]["close"])
        fibo      = (price - sl) / rng
        zone_cls  = (
            "DEEP_DISCOUNT" if fibo <= 0.382 else
            "DISCOUNT"      if fibo < 0.5   else
            "PREMIUM"       if fibo <= 0.618 else
            "DEEP_PREMIUM"
        )

        rows.append({
            "candle_index": i,
            "timestamp":    df.iloc[i][ts_col] if ts_col else i,
            "zone_class":   zone_cls,
            "fibo_pct":     round(fibo, 3),
            "swing_high":   round(sh, 6),
            "swing_low":    round(sl, 6),
            "equilibrium":  round(eq, 6),
            "ote_buy_lo":   round(ob_lo, 6),
            "ote_buy_hi":   round(ob_hi, 6),
            "ote_sell_lo":  round(os_lo, 6),
            "ote_sell_hi":  round(os_hi, 6),
        })

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def scan_amd_phases(
    df: pd.DataFrame,
    instrument: Instrument,
    asian_window: Tuple[int, int] = (0, 6),
    session_window: Tuple[int, int] = (7, 20),
) -> pd.DataFrame:
    """
    Scan a OHLCV DataFrame for AMD (Power of Three) phases by trading day.

    Returns a DataFrame with one row per day:
        date, asian_high, asian_low, phase, distribution_direction,
        manipulation_high, manipulation_low, confirmed
    """
    ts_col = next(
        (c for c in ("time", "timestamp", "datetime", "date") if c in df.columns),
        None
    )
    if ts_col is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    df[ts_col] = pd.to_datetime(df[ts_col], utc=True)
    df["_date"] = df[ts_col].dt.date
    df["_hour"] = df[ts_col].dt.hour

    rows = []

    for date, day_df in df.groupby("_date"):
        asian_df   = day_df[
            (day_df["_hour"] >= asian_window[0]) &
            (day_df["_hour"] <  asian_window[1])
        ]
        session_df = day_df[
            (day_df["_hour"] >= session_window[0]) &
            (day_df["_hour"] <  session_window[1])
        ]

        if asian_df.empty or session_df.empty:
            continue

        a_high = float(asian_df["high"].max())
        a_low  = float(asian_df["low"].min())
        avg_rng = float(session_df["high"].sub(session_df["low"]).mean())

        phase   = "ACCUMULATION"
        dist_dir = None
        manip_h = None
        manip_l = None
        confirmed = False

        for _, row in session_df.iterrows():
            rng = row["high"] - row["low"]

            if phase == "ACCUMULATION":
                if row["high"] > a_high and row["close"] < a_high:
                    phase   = "MANIPULATION"
                    manip_h = float(row["high"])
                elif row["low"] < a_low and row["close"] > a_low:
                    phase   = "MANIPULATION"
                    manip_l = float(row["low"])

            elif phase == "MANIPULATION":
                strong = rng >= avg_rng * 2.0
                if row["close"] > a_high and strong:
                    phase     = "DISTRIBUTION"
                    dist_dir  = "BULLISH"
                    confirmed = True
                    break
                elif row["close"] < a_low and strong:
                    phase     = "DISTRIBUTION"
                    dist_dir  = "BEARISH"
                    confirmed = True
                    break

        rows.append({
            "date":                    str(date),
            "asian_high":              round(a_high, 6),
            "asian_low":               round(a_low,  6),
            "phase":                   phase,
            "distribution_direction":  dist_dir,
            "manipulation_high":       round(manip_h, 6) if manip_h else None,
            "manipulation_low":        round(manip_l, 6) if manip_l else None,
            "confirmed":               confirmed,
        })

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)
