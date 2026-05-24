"""
strategy/inversion.py
=====================
Inversion FVG and Mitigation Blocks.

INVERSION FVG
─────────────
When a Fair Value Gap (FVG) is FULLY FILLED by price, it does not disappear.
Instead, it INVERTS its role:
  - A Bullish FVG that was filled → becomes RESISTANCE (bearish zone on retest)
  - A Bearish FVG that was filled → becomes SUPPORT   (bullish zone on retest)

ICT context:
  "The market returns to inversion FVGs after filling them."
  They act as institutional reference points even after fill.

Detection:
  1. Start with an active FVG
  2. Price fills it completely (close beyond the gap)
  3. The gap is now "Inverted"
  4. If price returns to the inverted zone and respects it → HIGH PROBABILITY setup

MITIGATION BLOCKS
─────────────────
A Mitigation Block is a zone where an Order Block was PARTIALLY mitigated
(price entered the OB and moved away, but did NOT fully close through it).
This partial mitigation is actually a CONFIRMATION of the OB's importance:
  - Institutions placed orders there
  - Price reacted, confirming the level
  - The unmitigated portion is a refined entry zone

Types:
  CONTINUATION  : price came back, mitigated 30–70% of the OB, bounced
                  → enter at the untouched lower portion (bull) or upper (bear)
  INVALIDATION  : price closed fully through the OB → OB is done, becomes breaker
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from algoTrading.ict_trading_system.settings import (
    Bias, Instrument, Timeframe, PIP_SIZE,
)
from algoTrading.ict_trading_system.data.models import Candle, FairValueGap, OrderBlock

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────

class InversionStatus(str, Enum):
    WAITING     = "WAITING"      # filled, not yet retested
    RETESTED    = "RETESTED"     # price came back to the inverted zone
    CONFIRMED   = "CONFIRMED"    # price respected the inversion (bounced)
    FAILED      = "FAILED"       # price broke through the inverted zone


class MitigationType(str, Enum):
    CONTINUATION  = "CONTINUATION"
    INVALIDATION  = "INVALIDATION"


# ─────────────────────────────────────────────
# DATA MODELS
# ─────────────────────────────────────────────

@dataclass
class InversionFVG:
    """
    A Fair Value Gap that has been filled and inverted its polarity.

    original_fvg  : the source FVG
    inverted_dir  : the NEW direction (opposite of the original)
    inverted_top  : same as original top
    inverted_bot  : same as original bottom
    fill_time     : when the original FVG was filled
    """
    instrument:   Instrument
    timeframe:    Timeframe
    original_fvg: FairValueGap
    inverted_dir: Bias           # BEARISH if original was BULLISH, vice versa
    inverted_top: float
    inverted_bot: float
    fill_time:    datetime
    status:       InversionStatus = InversionStatus.WAITING
    retest_at:    Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    retest_count: int = 0

    @property
    def midpoint(self) -> float:
        return (self.inverted_top + self.inverted_bot) / 2

    @property
    def size(self) -> float:
        return self.inverted_top - self.inverted_bot

    @property
    def is_bullish_inversion(self) -> bool:
        return self.inverted_dir == Bias.BULLISH

    @property
    def is_bearish_inversion(self) -> bool:
        return self.inverted_dir == Bias.BEARISH

    @property
    def is_active(self) -> bool:
        return self.status not in (InversionStatus.FAILED,)


@dataclass
class MitigationBlock:
    """
    An Order Block where partial mitigation occurred — a refined entry zone.

    mitigation_pct : what fraction of the OB was traded through (0=untouched, 1=fully done)
    entry_zone_top : top of the remaining unmit portion
    entry_zone_bot : bottom of the remaining unmit portion
    """
    instrument:      Instrument
    timeframe:       Timeframe
    original_ob:     OrderBlock
    mitigation_type: MitigationType
    mitigation_pct:  float          # 0.0–1.0
    mitigated_at:    datetime
    entry_zone_top:  float
    entry_zone_bot:  float
    tested:          bool = False
    confirmed:       bool = False

    @property
    def is_bullish(self) -> bool:
        return self.original_ob.is_bullish

    @property
    def is_bearish(self) -> bool:
        return not self.original_ob.is_bullish

    @property
    def entry_midpoint(self) -> float:
        return (self.entry_zone_top + self.entry_zone_bot) / 2

    @property
    def remaining_size(self) -> float:
        return self.entry_zone_top - self.entry_zone_bot


# ─────────────────────────────────────────────
# INVERSION FVG CREATION
# ─────────────────────────────────────────────

def create_inversion_fvgs(
    fvgs: List[FairValueGap],
    candles: List[Candle],
) -> List[InversionFVG]:
    """
    Scan FVGs that have been fully filled and create InversionFVG objects.

    An FVG is "fully filled" when:
      Bullish FVG: a candle CLOSES below the FVG bottom
      Bearish FVG: a candle CLOSES above the FVG top
    """
    if not fvgs or not candles:
        return []

    instrument = candles[-1].instrument
    timeframe  = candles[-1].timeframe
    inversions: List[InversionFVG] = []

    for fvg in fvgs:
        if not fvg.filled or fvg.filled_at is None:
            continue

        inverted_dir = Bias.BEARISH if fvg.is_bullish else Bias.BULLISH

        inversions.append(InversionFVG(
            instrument   = instrument,
            timeframe    = timeframe,
            original_fvg = fvg,
            inverted_dir = inverted_dir,
            inverted_top = fvg.top,
            inverted_bot = fvg.bottom,
            fill_time    = fvg.filled_at,
        ))
        logger.debug(
            f"Inversion FVG created: {inverted_dir.value} zone "
            f"[{fvg.bottom:.5f}–{fvg.top:.5f}] filled @ {fvg.filled_at}"
        )

    return inversions


# ─────────────────────────────────────────────
# INVERSION FVG STATE TRACKING
# ─────────────────────────────────────────────

def update_inversion_states(
    inversions: List[InversionFVG],
    candles: List[Candle],
    confirmation_bars: int = 3,
) -> List[InversionFVG]:
    """
    Track inversion FVG state as new candles arrive.

    State transitions:
      WAITING → RETESTED  : price re-enters the zone
      RETESTED → CONFIRMED: price closes AWAY from the zone (bounce confirmation)
      RETESTED → FAILED   : price closes THROUGH the zone
    """
    for inv in inversions:
        if inv.status == InversionStatus.FAILED:
            continue

        for c in candles:
            if c.timestamp <= inv.fill_time:
                continue

            price_in_zone = inv.inverted_bot <= c.close <= inv.inverted_top

            if inv.status == InversionStatus.WAITING:
                if price_in_zone or (
                    inv.is_bullish_inversion and c.low <= inv.inverted_top and c.close > inv.inverted_bot
                ) or (
                    inv.is_bearish_inversion and c.high >= inv.inverted_bot and c.close < inv.inverted_top
                ):
                    inv.status    = InversionStatus.RETESTED
                    inv.retest_at = c.timestamp
                    inv.retest_count += 1
                    logger.debug(f"Inversion FVG retested @ {c.timestamp}")

            elif inv.status == InversionStatus.RETESTED:
                if inv.is_bullish_inversion:
                    if c.close > inv.inverted_top:
                        inv.status       = InversionStatus.CONFIRMED
                        inv.confirmed_at = c.timestamp
                        logger.info(f"Inversion FVG CONFIRMED BULLISH @ {c.timestamp}")
                        break
                    elif c.close < inv.inverted_bot:
                        inv.status = InversionStatus.FAILED
                        break
                else:
                    if c.close < inv.inverted_bot:
                        inv.status       = InversionStatus.CONFIRMED
                        inv.confirmed_at = c.timestamp
                        logger.info(f"Inversion FVG CONFIRMED BEARISH @ {c.timestamp}")
                        break
                    elif c.close > inv.inverted_top:
                        inv.status = InversionStatus.FAILED
                        break

    return inversions


# ─────────────────────────────────────────────
# MITIGATION BLOCK DETECTION
# ─────────────────────────────────────────────

def detect_mitigation_blocks(
    order_blocks: List[OrderBlock],
    candles: List[Candle],
    min_mitigation_pct: float = 0.25,
    max_mitigation_pct: float = 0.75,
) -> List[MitigationBlock]:
    """
    Find Order Blocks that have been PARTIALLY mitigated (continuation zones).

    An OB is partially mitigated when:
      - Price entered the OB (wick reached into it)
      - Price bounced WITHOUT closing through the OB
      - The mitigation percentage is between min and max

    The remaining portion of the OB is the refined entry zone.

    Args:
        min_mitigation_pct : minimum fill to consider it "mitigated" (not just wicked)
        max_mitigation_pct : maximum fill before it's considered invalidated
    """
    if not order_blocks or not candles:
        return []

    instrument = candles[-1].instrument
    timeframe  = candles[-1].timeframe
    blocks: List[MitigationBlock] = []

    for ob in order_blocks:
        if ob.broken:
            continue

        ob_size = ob.top - ob.bottom
        if ob_size <= 0:
            continue

        best_mitigation = 0.0
        mit_time        = None
        mit_type        = None

        for c in candles:
            if c.timestamp <= ob.formed_at:
                continue

            if ob.is_bullish:
                # Price coming down into bullish OB
                if c.low <= ob.top:
                    depth       = ob.top - max(c.low, ob.bottom)
                    mit_pct     = min(depth / ob_size, 1.0)
                    if mit_pct > best_mitigation:
                        best_mitigation = mit_pct
                        mit_time        = c.timestamp
                    # Fully through = INVALIDATION
                    if c.close < ob.bottom:
                        mit_type = MitigationType.INVALIDATION
                        break
            else:
                # Price coming up into bearish OB
                if c.high >= ob.bottom:
                    depth       = min(c.high, ob.top) - ob.bottom
                    mit_pct     = min(depth / ob_size, 1.0)
                    if mit_pct > best_mitigation:
                        best_mitigation = mit_pct
                        mit_time        = c.timestamp
                    if c.close > ob.top:
                        mit_type = MitigationType.INVALIDATION
                        break

        if best_mitigation < min_mitigation_pct or mit_time is None:
            continue

        if mit_type is None:
            if best_mitigation <= max_mitigation_pct:
                mit_type = MitigationType.CONTINUATION
            else:
                mit_type = MitigationType.INVALIDATION

        # Refined entry zone = untouched portion of OB
        if ob.is_bullish:
            mitigated_from = ob.top - best_mitigation * ob_size
            entry_top      = mitigated_from
            entry_bot      = ob.bottom
        else:
            mitigated_from = ob.bottom + best_mitigation * ob_size
            entry_top      = ob.top
            entry_bot      = mitigated_from

        blocks.append(MitigationBlock(
            instrument      = instrument,
            timeframe       = timeframe,
            original_ob     = ob,
            mitigation_type = mit_type,
            mitigation_pct  = round(best_mitigation, 3),
            mitigated_at    = mit_time,
            entry_zone_top  = round(entry_top, 6),
            entry_zone_bot  = round(entry_bot, 6),
        ))
        logger.debug(
            f"Mitigation Block [{mit_type.value}] {'BULL' if ob.is_bullish else 'BEAR'} "
            f"OB @ {ob.formed_at} | mit={best_mitigation:.0%} "
            f"entry=[{entry_bot:.5f}–{entry_top:.5f}]"
        )

    return [b for b in blocks if b.mitigation_type == MitigationType.CONTINUATION]


# ─────────────────────────────────────────────
# STANDALONE DATAFRAME INTERFACES
# ─────────────────────────────────────────────

def scan_inversion_fvgs(
    fvg_df: pd.DataFrame,
    df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Given a FVG scan result (from scan_fvgs) and the original OHLCV DataFrame,
    return all inversion zones with their current status.

    fvg_df : output of scan_fvgs() — must have: direction, top, bottom,
             formed_at, filled, filled_at
    df     : original OHLCV DataFrame (must have same time column)
    """
    if fvg_df.empty:
        return pd.DataFrame()

    ts_col = next(
        (c for c in ("time", "timestamp", "datetime", "date") if c in df.columns),
        None
    )

    filled_fvgs = fvg_df[fvg_df["filled"] == True].copy()
    if filled_fvgs.empty:
        return pd.DataFrame()

    rows = []
    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    n      = len(df)

    for _, fvg_row in filled_fvgs.iterrows():
        inverted_dir = "BEARISH" if fvg_row["direction"] == "BULLISH" else "BULLISH"
        top          = fvg_row["top"]
        bot          = fvg_row["bottom"]
        fill_ci      = fvg_row.get("candle_index", 0)

        status       = "WAITING"
        retest_at    = None
        confirmed_at = None

        for k in range(int(fill_ci) + 1, n):
            in_zone = bot <= closes[k] <= top

            if status == "WAITING":
                entered = (
                    (inverted_dir == "BULLISH" and lows[k] <= top and closes[k] > bot) or
                    (inverted_dir == "BEARISH" and highs[k] >= bot and closes[k] < top) or
                    in_zone
                )
                if entered:
                    status    = "RETESTED"
                    retest_at = df.iloc[k][ts_col] if ts_col else k

            elif status == "RETESTED":
                if inverted_dir == "BULLISH":
                    if closes[k] > top:
                        status       = "CONFIRMED"
                        confirmed_at = df.iloc[k][ts_col] if ts_col else k
                        break
                    elif closes[k] < bot:
                        status = "FAILED"
                        break
                else:
                    if closes[k] < bot:
                        status       = "CONFIRMED"
                        confirmed_at = df.iloc[k][ts_col] if ts_col else k
                        break
                    elif closes[k] > top:
                        status = "FAILED"
                        break

        rows.append({
            "original_direction": fvg_row["direction"],
            "inverted_direction": inverted_dir,
            "top":                round(top, 6),
            "bottom":             round(bot, 6),
            "midpoint":           round((top + bot) / 2, 6),
            "filled_at":          fvg_row["filled_at"],
            "status":             status,
            "retest_at":          retest_at,
            "confirmed_at":       confirmed_at,
        })

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def scan_mitigation_blocks(
    ob_df: pd.DataFrame,
    df: pd.DataFrame,
    min_mit_pct: float = 0.25,
    max_mit_pct: float = 0.75,
) -> pd.DataFrame:
    """
    Given an OB scan result (from scan_order_blocks) and OHLCV DataFrame,
    return all mitigation blocks.

    ob_df : output of scan_order_blocks() — must have:
            direction, top, bottom, candle_index, mitigated
    df    : original OHLCV DataFrame
    """
    if ob_df.empty:
        return pd.DataFrame()

    ts_col = next(
        (c for c in ("time", "timestamp", "datetime", "date") if c in df.columns),
        None
    )

    active_obs = ob_df[(ob_df["mitigated"] == False) & (ob_df["broken"] == False)].copy()
    if active_obs.empty:
        return pd.DataFrame()

    highs  = df["high"].values
    lows   = df["low"].values
    closes = df["close"].values
    n      = len(df)

    rows = []

    for _, ob_row in active_obs.iterrows():
        ci      = int(ob_row["candle_index"])
        top     = ob_row["top"]
        bot     = ob_row["bottom"]
        ob_size = top - bot
        is_bull = ob_row["direction"] == "BULLISH"

        if ob_size <= 0:
            continue

        best_mit  = 0.0
        mit_ci    = None
        mit_type  = None

        for k in range(ci + 2, n):
            if is_bull:
                if lows[k] <= top:
                    depth = top - max(lows[k], bot)
                    pct   = min(depth / ob_size, 1.0)
                    if pct > best_mit:
                        best_mit = pct
                        mit_ci   = k
                    if closes[k] < bot:
                        mit_type = "INVALIDATION"
                        break
            else:
                if highs[k] >= bot:
                    depth = min(highs[k], top) - bot
                    pct   = min(depth / ob_size, 1.0)
                    if pct > best_mit:
                        best_mit = pct
                        mit_ci   = k
                    if closes[k] > top:
                        mit_type = "INVALIDATION"
                        break

        if best_mit < min_mit_pct or mit_ci is None:
            continue
        if mit_type is None:
            mit_type = "CONTINUATION" if best_mit <= max_mit_pct else "INVALIDATION"
        if mit_type == "INVALIDATION":
            continue

        # Refined entry zone
        if is_bull:
            mitigated_from = top - best_mit * ob_size
            entry_top      = mitigated_from
            entry_bot      = bot
        else:
            mitigated_from = bot + best_mit * ob_size
            entry_top      = top
            entry_bot      = mitigated_from

        rows.append({
            "ob_candle_index":   ci,
            "ob_direction":      ob_row["direction"],
            "ob_top":            round(top, 6),
            "ob_bottom":         round(bot, 6),
            "mitigation_pct":    round(best_mit, 3),
            "mitigation_type":   mit_type,
            "mitigated_at":      df.iloc[mit_ci][ts_col] if ts_col else mit_ci,
            "entry_zone_top":    round(entry_top, 6),
            "entry_zone_bot":    round(entry_bot, 6),
            "entry_midpoint":    round((entry_top + entry_bot) / 2, 6),
        })

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)
