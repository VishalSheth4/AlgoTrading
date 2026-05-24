"""
strategy/ob.py
==============
Order Block (OB) — comprehensive detection, mitigation tracking,
and Breaker Block logic.

ICT definition:
  Bullish OB : last BEARISH candle before a strong bullish displacement.
               Price returns to this zone → institutional buy orders resting here.
  Bearish OB : last BULLISH candle before a strong bearish displacement.
               Price returns to this zone → institutional sell orders resting here.

Lifecycle:
  ACTIVE  → price has not yet returned to the OB
  TESTED  → price wicked into the OB (first touch — highest probability)
  MITIGATED → price closed through the OB (zone is used up)
  BROKEN  → OB failed → becomes a Breaker Block

Breaker Block:
  A failed OB flips polarity:
    Failed Bullish OB → Bearish Breaker (now acts as resistance)
    Failed Bearish OB → Bullish Breaker (now acts as support)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

import pandas as pd

from algoTrading.ict_trading_system.settings import (
    Bias, Instrument, Timeframe,
    MIN_DISPLACEMENT_BODY_RATIO, PIP_SIZE,
)
from algoTrading.ict_trading_system.data.models import Candle, OrderBlock
from algoTrading.ict_trading_system.strategy.ict_concepts import is_displacement_candle

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# EXTENDED ORDER BLOCK STATE
# ─────────────────────────────────────────────

@dataclass
class OBState:
    """
    Wraps an OrderBlock and tracks its full lifecycle.
    """
    ob:             OrderBlock
    test_count:     int   = 0                    # how many times price touched the OB
    first_test_at:  Optional[datetime] = None
    last_test_at:   Optional[datetime] = None
    mitigated:      bool  = False
    mitigated_at:   Optional[datetime] = None
    mitigated_price: float = 0.0

    @property
    def is_active(self) -> bool:
        return not self.mitigated and not self.ob.broken

    @property
    def is_first_test(self) -> bool:
        return self.test_count == 1

    @property
    def status(self) -> str:
        if self.ob.is_breaker:
            return "BREAKER"
        if self.mitigated:
            return "MITIGATED"
        if self.ob.broken:
            return "BROKEN"
        if self.ob.tested:
            return "TESTED"
        return "ACTIVE"


# ─────────────────────────────────────────────
# DETECTION
# ─────────────────────────────────────────────

def detect_order_blocks_enhanced(
    candles: List[Candle],
    lookforward: int = 5,
    require_close_beyond: bool = True,
) -> List[OrderBlock]:
    """
    Detect Order Blocks with stricter displacement requirement.

    For each candidate OB candle, at least one of the next `lookforward`
    candles must:
      - Pass is_displacement_candle() (body_ratio ≥ threshold)
      - If require_close_beyond=True: close BEYOND the OB's high/low,
        not just trade through it

    Args:
        candles:              ordered Candle list
        lookforward:          how many candles ahead to look for displacement
        require_close_beyond: displacement candle must also close past the OB extreme
    """
    if len(candles) < lookforward + 2:
        return []

    instrument = candles[-1].instrument
    timeframe  = candles[-1].timeframe
    obs: List[OrderBlock] = []

    for i in range(1, len(candles) - lookforward):
        c = candles[i]
        future = candles[i + 1: i + 1 + lookforward]

        # Bullish OB candidate: bearish candle followed by bullish displacement
        if c.is_bearish:
            for fc in future:
                if fc.is_bullish and is_displacement_candle(fc):
                    if require_close_beyond and fc.close <= c.high:
                        continue
                    obs.append(OrderBlock(
                        instrument = instrument,
                        timeframe  = timeframe,
                        direction  = Bias.BULLISH,
                        top        = c.high,
                        bottom     = c.low,
                        formed_at  = c.timestamp,
                    ))
                    logger.debug(
                        f"Bullish OB @ {c.timestamp} [{c.low:.5f}–{c.high:.5f}]"
                    )
                    break   # only one OB per candidate candle

        # Bearish OB candidate: bullish candle followed by bearish displacement
        elif c.is_bullish:
            for fc in future:
                if fc.is_bearish and is_displacement_candle(fc):
                    if require_close_beyond and fc.close >= c.low:
                        continue
                    obs.append(OrderBlock(
                        instrument = instrument,
                        timeframe  = timeframe,
                        direction  = Bias.BEARISH,
                        top        = c.high,
                        bottom     = c.low,
                        formed_at  = c.timestamp,
                    ))
                    logger.debug(
                        f"Bearish OB @ {c.timestamp} [{c.low:.5f}–{c.high:.5f}]"
                    )
                    break

    return obs


# ─────────────────────────────────────────────
# MITIGATION TRACKING
# ─────────────────────────────────────────────

def update_ob_states(
    states: List[OBState],
    candles: List[Candle],
) -> List[OBState]:
    """
    Walk forward through candles and update each OBState:

      - First touch (wick into OB)  → mark as tested
      - Close inside the OB         → increment test count
      - Close fully THROUGH the OB  → mark as mitigated
      - Price runs past the wrong side → mark as broken → becomes Breaker

    Modifies states in-place and returns them.
    """
    for state in states:
        if not state.is_active:
            continue

        ob = state.ob

        for c in candles:
            if c.timestamp <= ob.formed_at:
                continue
            if state.mitigated or ob.broken:
                break

            # Bullish OB: price retraces DOWN into the zone
            if ob.is_bullish:
                in_zone = c.low <= ob.top and c.high >= ob.bottom

                if in_zone:
                    state.test_count += 1
                    ob.tested = True
                    if state.first_test_at is None:
                        state.first_test_at = c.timestamp
                    state.last_test_at = c.timestamp

                    # Mitigated: close inside or below OB bottom
                    if c.close <= ob.top:
                        state.mitigated       = True
                        state.mitigated_at    = c.timestamp
                        state.mitigated_price = c.close

                # Broken: price closes below the OB without returning
                if c.close < ob.bottom:
                    ob.broken = True
                    logger.debug(f"Bullish OB broken @ {c.timestamp}")
                    break

            # Bearish OB: price retraces UP into the zone
            else:
                in_zone = c.high >= ob.bottom and c.low <= ob.top

                if in_zone:
                    state.test_count += 1
                    ob.tested = True
                    if state.first_test_at is None:
                        state.first_test_at = c.timestamp
                    state.last_test_at = c.timestamp

                    if c.close >= ob.bottom:
                        state.mitigated       = True
                        state.mitigated_at    = c.timestamp
                        state.mitigated_price = c.close

                if c.close > ob.top:
                    ob.broken = True
                    logger.debug(f"Bearish OB broken @ {c.timestamp}")
                    break

    return states


# ─────────────────────────────────────────────
# BREAKER BLOCKS
# ─────────────────────────────────────────────

def identify_breaker_blocks(states: List[OBState]) -> List[OrderBlock]:
    """
    Convert broken OBs into Breaker Blocks.
    A broken Bullish OB → Bearish Breaker (resistance on retest).
    A broken Bearish OB → Bullish Breaker (support on retest).
    """
    breakers: List[OrderBlock] = []
    for state in states:
        if not state.ob.broken or state.ob.is_breaker:
            continue

        ob = state.ob
        breaker = OrderBlock(
            instrument = ob.instrument,
            timeframe  = ob.timeframe,
            direction  = Bias.BEARISH if ob.is_bullish else Bias.BULLISH,
            top        = ob.top,
            bottom     = ob.bottom,
            formed_at  = ob.formed_at,
            broken     = True,
            is_breaker = True,
        )
        breakers.append(breaker)
        logger.info(
            f"Breaker Block formed from failed {ob.direction.value} OB "
            f"@ {ob.formed_at} [{ob.bottom:.5f}–{ob.top:.5f}]"
        )

    return breakers


# ─────────────────────────────────────────────
# ENTRY SIGNAL FROM OB
# ─────────────────────────────────────────────

def price_at_ob(
    current_price: float,
    state: OBState,
    entry_pct: float = 0.5,
) -> Optional[Tuple[float, float, str]]:
    """
    Check whether current price is at a valid, untested OB zone and
    return (entry_price, sl_price, note) if so.

    entry_pct : how deep into the OB to set the entry (0.5 = midpoint,
                0.0 = top/bottom of zone, 1.0 = opposite end)
    Returns None if zone is not valid or price is not in range.
    """
    if not state.is_active or state.test_count > 2:
        return None

    ob = state.ob

    if ob.is_bullish:
        # Price must be retracing into the zone
        if not (ob.bottom <= current_price <= ob.top):
            return None
        entry = ob.top - (ob.size * entry_pct)
        sl    = ob.bottom - ob.size * 0.1    # buffer below the OB
        note  = f"Bullish OB entry | test#{state.test_count}"

    else:
        if not (ob.bottom <= current_price <= ob.top):
            return None
        entry = ob.bottom + (ob.size * entry_pct)
        sl    = ob.top + ob.size * 0.1
        note  = f"Bearish OB entry | test#{state.test_count}"

    return entry, sl, note


# ─────────────────────────────────────────────
# STANDALONE DATAFRAME INTERFACE
# ─────────────────────────────────────────────

def scan_order_blocks(
    df: pd.DataFrame,
    instrument: Instrument,
    timeframe: Timeframe,
    lookforward: int = 5,
) -> pd.DataFrame:
    """
    Scan a raw OHLCV DataFrame for Order Blocks.

    Expected columns: open, high, low, close, (optional) time/timestamp

    Returns a DataFrame with:
        formed_at, direction, top, bottom, midpoint,
        tested, mitigated, broken, is_breaker
    """
    if df.empty or len(df) < lookforward + 2:
        return pd.DataFrame()

    ts_col = next(
        (c for c in ("time", "timestamp", "datetime", "date") if c in df.columns),
        None
    )
    pip = PIP_SIZE.get(instrument, 0.0001)

    rows = []

    for i in range(1, len(df) - lookforward):
        c0 = df.iloc[i]

        # Bullish OB: bearish candle
        c0_bearish = c0["close"] < c0["open"]
        c0_bullish = c0["close"] > c0["open"]

        for j in range(i + 1, i + 1 + lookforward):
            fc = df.iloc[j]
            fc_range = fc["high"] - fc["low"]
            fc_body  = abs(fc["close"] - fc["open"])
            fc_ratio = fc_body / fc_range if fc_range > 0 else 0

            is_disp = fc_ratio >= MIN_DISPLACEMENT_BODY_RATIO

            if c0_bearish and fc["close"] > fc["open"] and is_disp and fc["close"] > c0["high"]:
                rows.append({
                    "candle_index": i,
                    "formed_at":   c0[ts_col] if ts_col else i,
                    "direction":   "BULLISH",
                    "top":         round(c0["high"], 6),
                    "bottom":      round(c0["low"],  6),
                    "midpoint":    round((c0["high"] + c0["low"]) / 2, 6),
                    "size_pips":   round((c0["high"] - c0["low"]) / pip, 1),
                    "tested":      False,
                    "mitigated":   False,
                    "broken":      False,
                    "is_breaker":  False,
                })
                break

            if c0_bullish and fc["close"] < fc["open"] and is_disp and fc["close"] < c0["low"]:
                rows.append({
                    "candle_index": i,
                    "formed_at":   c0[ts_col] if ts_col else i,
                    "direction":   "BEARISH",
                    "top":         round(c0["high"], 6),
                    "bottom":      round(c0["low"],  6),
                    "midpoint":    round((c0["high"] + c0["low"]) / 2, 6),
                    "size_pips":   round((c0["high"] - c0["low"]) / pip, 1),
                    "tested":      False,
                    "mitigated":   False,
                    "broken":      False,
                    "is_breaker":  False,
                })
                break

    if not rows:
        return pd.DataFrame(columns=[
            "candle_index", "formed_at", "direction", "top", "bottom",
            "midpoint", "size_pips", "tested", "mitigated", "broken", "is_breaker"
        ])

    ob_df = pd.DataFrame(rows)

    # Forward-fill status
    close_vals = df["close"].values
    high_vals  = df["high"].values
    low_vals   = df["low"].values

    for idx, row in ob_df.iterrows():
        ci  = int(row["candle_index"])
        top = row["top"]
        bot = row["bottom"]
        is_bull = row["direction"] == "BULLISH"

        for k in range(ci + 2, len(df)):
            h, l, c = high_vals[k], low_vals[k], close_vals[k]

            if is_bull:
                if l <= top and h >= bot:
                    ob_df.at[idx, "tested"] = True
                    if c <= top:
                        ob_df.at[idx, "mitigated"] = True
                        break
                if c < bot:
                    ob_df.at[idx, "broken"]    = True
                    ob_df.at[idx, "is_breaker"] = True
                    break
            else:
                if h >= bot and l <= top:
                    ob_df.at[idx, "tested"] = True
                    if c >= bot:
                        ob_df.at[idx, "mitigated"] = True
                        break
                if c > top:
                    ob_df.at[idx, "broken"]    = True
                    ob_df.at[idx, "is_breaker"] = True
                    break

    return ob_df


# ─────────────────────────────────────────────
# CONVENIENCE BUILDER
# ─────────────────────────────────────────────

def build_ob_states(obs: List[OrderBlock]) -> List[OBState]:
    return [OBState(ob=ob) for ob in obs]


def active_obs(states: List[OBState]) -> List[OBState]:
    return [s for s in states if s.is_active]


def first_test_obs(states: List[OBState]) -> List[OBState]:
    """Return OBs on their first touch — highest probability zone."""
    return [s for s in states if s.ob.tested and s.test_count == 1 and not s.mitigated]
