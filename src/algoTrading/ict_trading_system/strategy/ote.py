"""
strategy/ote.py
===============
Optimal Trade Entry (OTE) — ICT Fibonacci retracement logic.

ICT uses three key Fibonacci levels to define the OTE zone:
  62.0%  — minimum retracement threshold
  70.5%  — "sweet spot" (often best fill)
  79.0%  — maximum retracement before invalidation

Usage:
  1. Identify a displacement (impulse move) from swing low to swing high
     (or vice versa for shorts).
  2. Wait for price to retrace into the 62%–79% zone.
  3. Look for confluence: FVG, OB, or liquidity level inside the zone.
  4. Enter with SL beyond the swing extreme.

Dealing Range:
  The full range between the most significant swing high and swing low.
  Subdivided into:
    Premium  : above 50% equilibrium (sell zone)
    Discount : below 50% equilibrium (buy zone)
    OTE zone : 62%–79% (premium for sells, discount for buys)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

import pandas as pd

from algoTrading.ict_trading_system.settings import (
    Bias, Instrument, Timeframe,
    OTE_FIBO_LOW, OTE_FIBO_HIGH, PIP_SIZE,
)
from algoTrading.ict_trading_system.data.models import Candle, PremiumDiscountZone

logger = logging.getLogger(__name__)

# ICT Fibonacci levels
FIBO_LEVELS = {
    "0.0":   0.000,
    "23.6":  0.236,
    "38.2":  0.382,
    "50.0":  0.500,   # Equilibrium
    "62.0":  0.620,   # OTE start
    "70.5":  0.705,   # OTE sweet spot
    "79.0":  0.790,   # OTE end / invalidation border
    "88.6":  0.886,
    "100.0": 1.000,
}


# ─────────────────────────────────────────────
# DEALING RANGE
# ─────────────────────────────────────────────

@dataclass
class DealingRange:
    """
    The range between the most relevant swing high and swing low.
    All ICT premium/discount and OTE calculations are relative to this range.
    """
    instrument:    Instrument
    swing_high:    float
    swing_low:     float
    high_time:     Optional[datetime] = None
    low_time:      Optional[datetime] = None
    direction:     Bias = Bias.NEUTRAL   # direction of most recent displacement

    @property
    def range_size(self) -> float:
        return self.swing_high - self.swing_low

    @property
    def equilibrium(self) -> float:
        """50% level — the midpoint."""
        return self.swing_low + self.range_size * 0.5

    @property
    def ote_zone(self) -> Tuple[float, float]:
        """
        OTE zone relative to direction:
          Bullish : retracement into discount (62%–79% FROM the high DOWN)
          Bearish : retracement into premium (62%–79% FROM the low UP)
        """
        if self.direction == Bias.BULLISH:
            # Price moved up; OTE is in the discount zone (retracement down)
            ote_high = self.swing_high - self.range_size * OTE_FIBO_LOW   # 62% retrace
            ote_low  = self.swing_high - self.range_size * OTE_FIBO_HIGH  # 79% retrace
            return (ote_low, ote_high)
        else:
            # Price moved down; OTE is in the premium zone (retracement up)
            ote_low  = self.swing_low + self.range_size * OTE_FIBO_LOW
            ote_high = self.swing_low + self.range_size * OTE_FIBO_HIGH
            return (ote_low, ote_high)

    @property
    def sweet_spot(self) -> float:
        """70.5% Fibonacci — highest probability entry inside OTE."""
        if self.direction == Bias.BULLISH:
            return self.swing_high - self.range_size * FIBO_LEVELS["70.5"]
        return self.swing_low + self.range_size * FIBO_LEVELS["70.5"]

    def is_premium(self, price: float) -> bool:
        return price > self.equilibrium

    def is_discount(self, price: float) -> bool:
        return price < self.equilibrium

    def in_ote_zone(self, price: float) -> bool:
        lo, hi = self.ote_zone
        return lo <= price <= hi

    def fibo_level(self, price: float) -> float:
        """Return the Fibonacci retracement level (0.0–1.0) for a given price."""
        if self.range_size == 0:
            return 0.0
        if self.direction == Bias.BULLISH:
            return (self.swing_high - price) / self.range_size
        return (price - self.swing_low) / self.range_size

    def all_levels(self) -> dict:
        """Return all key Fibonacci prices."""
        result = {}
        for label, ratio in FIBO_LEVELS.items():
            if self.direction == Bias.BULLISH:
                result[label] = round(self.swing_high - self.range_size * ratio, 6)
            else:
                result[label] = round(self.swing_low + self.range_size * ratio, 6)
        return result

    def __repr__(self):
        lo, hi = self.ote_zone
        return (
            f"DealingRange({self.instrument.value} {self.direction.value} "
            f"H={self.swing_high:.5f} L={self.swing_low:.5f} "
            f"EQ={self.equilibrium:.5f} OTE=[{lo:.5f}–{hi:.5f}])"
        )


# ─────────────────────────────────────────────
# DEALING RANGE DETECTION
# ─────────────────────────────────────────────

def compute_dealing_range(
    candles: List[Candle],
    lookback: int = 50,
) -> Optional[DealingRange]:
    """
    Find the most significant dealing range from recent price action.

    Method:
      1. Take the last `lookback` candles
      2. Find absolute swing high and swing low
      3. Determine direction from which end was more recently formed
    """
    if not candles:
        return None

    recent = candles[-lookback:] if len(candles) >= lookback else candles
    if not recent:
        return None

    instrument = recent[-1].instrument

    swing_high = max(c.high for c in recent)
    swing_low  = min(c.low  for c in recent)

    # Find most recent high and low candles
    high_idx = max(range(len(recent)), key=lambda i: recent[i].high)
    low_idx  = min(range(len(recent)), key=lambda i: recent[i].low)

    # Direction: if high came after the low → bullish displacement, expect bearish retracement
    # if low came after the high → bearish displacement, expect bullish retracement
    if high_idx > low_idx:
        direction = Bias.BEARISH   # last move was UP, now looking for retrace DOWN
    else:
        direction = Bias.BULLISH   # last move was DOWN, now looking for retrace UP

    return DealingRange(
        instrument  = instrument,
        swing_high  = swing_high,
        swing_low   = swing_low,
        high_time   = recent[high_idx].timestamp,
        low_time    = recent[low_idx].timestamp,
        direction   = direction,
    )


def compute_dealing_range_from_swing(
    swing_high: float,
    swing_low: float,
    instrument: Instrument,
    direction: Bias,
    high_time: Optional[datetime] = None,
    low_time: Optional[datetime]  = None,
) -> DealingRange:
    """Create a DealingRange from explicitly provided swing points."""
    return DealingRange(
        instrument = instrument,
        swing_high = swing_high,
        swing_low  = swing_low,
        high_time  = high_time,
        low_time   = low_time,
        direction  = direction,
    )


# ─────────────────────────────────────────────
# OTE ENTRY SIGNAL
# ─────────────────────────────────────────────

@dataclass
class OTEEntry:
    """A detected OTE entry opportunity."""
    instrument:    Instrument
    direction:     Bias
    entry_price:   float         # sweet spot (70.5%)
    ote_low:       float
    ote_high:      float
    sl_price:      float         # beyond swing extreme
    tp1_price:     float         # swing opposite end
    tp2_price:     float         # extended target
    fibo_pct:      float         # current price as % retrace
    confluence:    List[str] = None   # list of additional confirmations found

    def __post_init__(self):
        if self.confluence is None:
            self.confluence = []

    @property
    def in_sweet_spot(self) -> bool:
        """Price is within 5% of the 70.5 level."""
        return abs(self.entry_price - self.ote_low) / max(self.ote_high - self.ote_low, 1e-9) <= 0.3

    @property
    def rr_ratio(self) -> float:
        risk   = abs(self.entry_price - self.sl_price)
        reward = abs(self.tp1_price - self.entry_price)
        return round(reward / risk, 2) if risk > 0 else 0.0


def check_ote_entry(
    current_price: float,
    dr: DealingRange,
    sl_buffer_pips: float = 5.0,
) -> Optional[OTEEntry]:
    """
    Check if the current price is inside the OTE zone and
    return an OTEEntry if so.

    sl_buffer_pips : extra pip buffer beyond the swing extreme for SL
    """
    if not dr.in_ote_zone(current_price):
        return None

    pip     = PIP_SIZE.get(dr.instrument, 0.0001)
    sl_buf  = sl_buffer_pips * pip
    ote_lo, ote_hi = dr.ote_zone

    if dr.direction == Bias.BULLISH:
        # Bullish setup: price retraced down into discount OTE
        # We BUY at the 70.5% level expecting continuation UP
        entry = dr.sweet_spot
        sl    = dr.swing_low - sl_buf
        tp1   = dr.swing_high
        tp2   = dr.swing_high + (dr.swing_high - entry)
    else:
        # Bearish setup: price retraced up into premium OTE
        # We SELL at the 70.5% level expecting continuation DOWN
        entry = dr.sweet_spot
        sl    = dr.swing_high + sl_buf
        tp1   = dr.swing_low
        tp2   = dr.swing_low - (entry - dr.swing_low)

    fibo_pct = dr.fibo_level(current_price)

    logger.info(
        f"OTE zone entry | {dr.instrument.value} {dr.direction.value} "
        f"price={current_price:.5f} fibo={fibo_pct:.1%} "
        f"entry={entry:.5f} sl={sl:.5f} tp1={tp1:.5f}"
    )

    return OTEEntry(
        instrument  = dr.instrument,
        direction   = dr.direction,
        entry_price = entry,
        ote_low     = ote_lo,
        ote_high    = ote_hi,
        sl_price    = sl,
        tp1_price   = tp1,
        tp2_price   = tp2,
        fibo_pct    = fibo_pct,
    )


# ─────────────────────────────────────────────
# STANDALONE DATAFRAME INTERFACE
# ─────────────────────────────────────────────

def scan_ote_zones(
    df: pd.DataFrame,
    instrument: Instrument,
    lookback: int = 50,
) -> pd.DataFrame:
    """
    Scan a OHLCV DataFrame and return all detected OTE zone windows.

    Returns a DataFrame with one row per dealing range window:
        window_end, direction, swing_high, swing_low, equilibrium,
        ote_low, ote_high, sweet_spot, all Fibonacci levels
    """
    if df.empty or len(df) < lookback:
        return pd.DataFrame()

    ts_col = next(
        (c for c in ("time", "timestamp", "datetime", "date") if c in df.columns),
        None
    )

    rows = []

    for i in range(lookback, len(df)):
        window = df.iloc[i - lookback: i]

        swing_high = window["high"].max()
        swing_low  = window["low"].min()

        high_pos = window["high"].idxmax()
        low_pos  = window["low"].idxmin()
        high_idx = df.index.get_loc(high_pos)
        low_idx  = df.index.get_loc(low_pos)

        direction = Bias.BEARISH if high_idx > low_idx else Bias.BULLISH
        rng       = swing_high - swing_low

        if rng == 0:
            continue

        eq      = swing_low + rng * 0.5
        ts      = df.iloc[i][ts_col] if ts_col else i

        if direction == Bias.BULLISH:
            ote_hi   = swing_high - rng * OTE_FIBO_LOW
            ote_lo   = swing_high - rng * OTE_FIBO_HIGH
            sweet    = swing_high - rng * FIBO_LEVELS["70.5"]
        else:
            ote_lo   = swing_low + rng * OTE_FIBO_LOW
            ote_hi   = swing_low + rng * OTE_FIBO_HIGH
            sweet    = swing_low + rng * FIBO_LEVELS["70.5"]

        current_close = df.iloc[i]["close"]
        in_ote = ote_lo <= current_close <= ote_hi

        row = {
            "window_end":   ts,
            "candle_index": i,
            "direction":    direction.value,
            "swing_high":   round(swing_high, 6),
            "swing_low":    round(swing_low,  6),
            "equilibrium":  round(eq, 6),
            "ote_low":      round(ote_lo, 6),
            "ote_high":     round(ote_hi, 6),
            "sweet_spot":   round(sweet, 6),
            "in_ote_zone":  in_ote,
        }

        # Add all Fibonacci levels
        for label, ratio in FIBO_LEVELS.items():
            if direction == Bias.BULLISH:
                row[f"fib_{label}"] = round(swing_high - rng * ratio, 6)
            else:
                row[f"fib_{label}"] = round(swing_low + rng * ratio, 6)

        rows.append(row)

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows)
