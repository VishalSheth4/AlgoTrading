"""
strategy/ict_concepts.py
========================
Pure ICT concept detectors. Each function takes candle lists and
returns domain objects. No trading logic here — only detection.

Concepts covered:
  - Market Structure (HH/HL/LH/LL)
  - Market Structure Shift (MSS)
  - Displacement candle detection
  - Liquidity level identification
  - Fair Value Gap (FVG)
  - Order Block (OB)
  - Breaker Block
  - Premium / Discount zone
  - SMT Divergence
  - AMD phase detection
"""

from __future__ import annotations
import logging
from typing import List, Optional, Tuple
from datetime import datetime

from config.settings import (
    Bias, Instrument, Timeframe,
    MIN_DISPLACEMENT_BODY_RATIO, MIN_FVG_PIPS, PIP_SIZE
)
from data.models import (
    Candle, SwingPoint, MarketStructure, MarketStructureShift,
    LiquidityLevel, FairValueGap, OrderBlock, PremiumDiscountZone,
    SMTDivergence
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# SWING POINT DETECTION
# ─────────────────────────────────────────────

def detect_swing_highs(candles: List[Candle], lookback: int = 3) -> List[SwingPoint]:
    """
    A swing high is a candle whose high is greater than the `lookback`
    candles on both sides.
    """
    swings = []
    n = len(candles)
    for i in range(lookback, n - lookback):
        pivot = candles[i].high
        if all(pivot > candles[i - j].high for j in range(1, lookback + 1)) and \
           all(pivot > candles[i + j].high for j in range(1, lookback + 1)):
            swings.append(SwingPoint(
                timestamp = candles[i].timestamp,
                price     = pivot,
                is_high   = True,
                timeframe = candles[i].timeframe,
            ))
    return swings


def detect_swing_lows(candles: List[Candle], lookback: int = 3) -> List[SwingPoint]:
    """Symmetric to swing highs."""
    swings = []
    n = len(candles)
    for i in range(lookback, n - lookback):
        pivot = candles[i].low
        if all(pivot < candles[i - j].low for j in range(1, lookback + 1)) and \
           all(pivot < candles[i + j].low for j in range(1, lookback + 1)):
            swings.append(SwingPoint(
                timestamp = candles[i].timestamp,
                price     = pivot,
                is_high   = False,
                timeframe = candles[i].timeframe,
            ))
    return swings


# ─────────────────────────────────────────────
# MARKET STRUCTURE
# ─────────────────────────────────────────────

def analyze_market_structure(
    candles: List[Candle],
    lookback: int = 3,
) -> MarketStructure:
    """
    Identify HH/HL (bullish) or LH/LL (bearish) using swing points.
    Returns a MarketStructure object with current bias.
    """
    instrument = candles[-1].instrument
    timeframe  = candles[-1].timeframe

    highs = detect_swing_highs(candles, lookback)
    lows  = detect_swing_lows(candles, lookback)

    if len(highs) < 2 or len(lows) < 2:
        return MarketStructure(instrument, timeframe, Bias.NEUTRAL)

    # Last two swing highs and lows
    sh1, sh2 = highs[-2], highs[-1]
    sl1, sl2 = lows[-2],  lows[-1]

    hh = sh2.price > sh1.price   # Higher High
    hl = sl2.price > sl1.price   # Higher Low
    lh = sh2.price < sh1.price   # Lower High
    ll = sl2.price < sl1.price   # Lower Low

    if hh and hl:
        bias = Bias.BULLISH
    elif lh and ll:
        bias = Bias.BEARISH
    else:
        bias = Bias.NEUTRAL

    ms = MarketStructure(
        instrument   = instrument,
        timeframe    = timeframe,
        bias         = bias,
        last_hh      = sh2 if hh else None,
        last_hl      = sl2 if hl else None,
        last_lh      = sh2 if lh else None,
        last_ll      = sl2 if ll else None,
    )
    logger.debug(f"Market Structure [{timeframe.value}]: {bias.value}")
    return ms


# ─────────────────────────────────────────────
# DISPLACEMENT CANDLE
# ─────────────────────────────────────────────

def is_displacement_candle(candle: Candle) -> bool:
    """
    A displacement candle must:
    - Have body ratio >= MIN_DISPLACEMENT_BODY_RATIO
    - Close near the extreme (body dominates wicks)
    """
    return candle.body_ratio >= MIN_DISPLACEMENT_BODY_RATIO


def find_displacement_candles(candles: List[Candle]) -> List[Tuple[int, Candle]]:
    """Return (index, candle) pairs for all displacement candles."""
    return [(i, c) for i, c in enumerate(candles) if is_displacement_candle(c)]


# ─────────────────────────────────────────────
# MARKET STRUCTURE SHIFT (MSS)
# ─────────────────────────────────────────────

def detect_mss(
    candles: List[Candle],
    lookback: int = 3,
    require_displacement: bool = True,
) -> Optional[MarketStructureShift]:
    """
    Detect the most recent Market Structure Shift.
    Bullish MSS: price breaks above a prior swing high with displacement.
    Bearish MSS: price breaks below a prior swing low with displacement.
    """
    if len(candles) < 10:
        return None

    instrument = candles[-1].instrument
    timeframe  = candles[-1].timeframe
    last       = candles[-1]

    # Bullish MSS: recent candles break above a prior swing high
    highs = detect_swing_highs(candles[:-3], lookback)
    if highs:
        prior_high = highs[-1].price
        if last.close > prior_high:
            # Check displacement
            displacement = None
            for c in reversed(candles[-5:]):
                if is_displacement_candle(c) and c.is_bullish:
                    displacement = c
                    break
            if displacement or not require_displacement:
                logger.info(f"Bullish MSS detected on {timeframe.value} at {last.timestamp}")
                return MarketStructureShift(
                    instrument           = instrument,
                    timeframe            = timeframe,
                    direction            = Bias.BULLISH,
                    break_price          = prior_high,
                    displacement_candle  = displacement,
                    timestamp            = last.timestamp,
                    confirmed            = displacement is not None,
                )

    # Bearish MSS: recent candles break below a prior swing low
    lows = detect_swing_lows(candles[:-3], lookback)
    if lows:
        prior_low = lows[-1].price
        if last.close < prior_low:
            displacement = None
            for c in reversed(candles[-5:]):
                if is_displacement_candle(c) and c.is_bearish:
                    displacement = c
                    break
            if displacement or not require_displacement:
                logger.info(f"Bearish MSS detected on {timeframe.value} at {last.timestamp}")
                return MarketStructureShift(
                    instrument           = instrument,
                    timeframe            = timeframe,
                    direction            = Bias.BEARISH,
                    break_price          = prior_low,
                    displacement_candle  = displacement,
                    timestamp            = last.timestamp,
                    confirmed            = displacement is not None,
                )

    return None


# ─────────────────────────────────────────────
# LIQUIDITY LEVELS
# ─────────────────────────────────────────────

def detect_equal_highs_lows(
    candles: List[Candle],
    tolerance_pips: float = 2.0,
) -> List[LiquidityLevel]:
    """
    Equal highs / equal lows are price levels where two or more
    candles touched the same high or low within a pip tolerance.
    These represent liquidity pools (stop clusters).
    """
    if not candles:
        return []

    instrument = candles[-1].instrument
    pip        = PIP_SIZE.get(instrument, 0.0001)
    tolerance  = tolerance_pips * pip
    levels     = []

    highs = [(i, c.high) for i, c in enumerate(candles)]
    lows  = [(i, c.low)  for i, c in enumerate(candles)]

    # Cluster highs
    used = set()
    for i, (_, h) in enumerate(highs):
        if i in used:
            continue
        cluster = [j for j, (_, hh) in enumerate(highs) if abs(hh - h) <= tolerance and j != i]
        if cluster:
            levels.append(LiquidityLevel(
                instrument  = instrument,
                price       = h,
                level_type  = "equal_highs",
                timeframe   = candles[-1].timeframe,
                formed_at   = candles[max(cluster)].timestamp,
            ))
            used.update(cluster)

    # Cluster lows
    used = set()
    for i, (_, l) in enumerate(lows):
        if i in used:
            continue
        cluster = [j for j, (_, ll) in enumerate(lows) if abs(ll - l) <= tolerance and j != i]
        if cluster:
            levels.append(LiquidityLevel(
                instrument  = instrument,
                price       = l,
                level_type  = "equal_lows",
                timeframe   = candles[-1].timeframe,
                formed_at   = candles[max(cluster)].timestamp,
            ))
            used.update(cluster)

    return levels


def detect_liquidity_sweep(
    candles: List[Candle],
    level: LiquidityLevel,
) -> bool:
    """
    A liquidity sweep occurs when price wicks beyond a liquidity level
    but CLOSES back on the other side (wick rejection).
    Returns True if the last candle swept the level.
    """
    last = candles[-1]
    if level.is_buy_side:
        # Buy-side: wick above level, close below
        return last.high > level.price and last.close < level.price
    else:
        # Sell-side: wick below level, close above
        return last.low < level.price and last.close > level.price


# ─────────────────────────────────────────────
# FAIR VALUE GAP (FVG)
# ─────────────────────────────────────────────

def detect_fvg(candles: List[Candle]) -> List[FairValueGap]:
    """
    A Fair Value Gap (imbalance) forms when:
    Bullish FVG: candle[i].low > candle[i-2].high  (gap left above)
    Bearish FVG: candle[i].high < candle[i-2].low  (gap left below)

    Requires at least 3 candles.
    """
    fvgs = []
    if len(candles) < 3:
        return fvgs

    instrument = candles[-1].instrument
    timeframe  = candles[-1].timeframe
    min_size   = MIN_FVG_PIPS.get(instrument, 3.0) * PIP_SIZE.get(instrument, 0.0001)

    for i in range(2, len(candles)):
        c0, c1, c2 = candles[i - 2], candles[i - 1], candles[i]

        # Bullish FVG
        if c2.low > c0.high:
            size = c2.low - c0.high
            if size >= min_size:
                fvgs.append(FairValueGap(
                    instrument   = instrument,
                    timeframe    = timeframe,
                    direction    = Bias.BULLISH,
                    top          = c2.low,
                    bottom       = c0.high,
                    formed_at    = c2.timestamp,
                    candle_index = i,
                ))

        # Bearish FVG
        elif c2.high < c0.low:
            size = c0.low - c2.high
            if size >= min_size:
                fvgs.append(FairValueGap(
                    instrument   = instrument,
                    timeframe    = timeframe,
                    direction    = Bias.BEARISH,
                    top          = c0.low,
                    bottom       = c2.high,
                    formed_at    = c2.timestamp,
                    candle_index = i,
                ))

    return fvgs


def is_price_in_fvg(price: float, fvg: FairValueGap) -> bool:
    return fvg.bottom <= price <= fvg.top


def mark_filled_fvgs(candles: List[Candle], fvgs: List[FairValueGap]) -> List[FairValueGap]:
    """Mark FVGs as filled when price trades through them."""
    for fvg in fvgs:
        if fvg.filled:
            continue
        for c in candles:
            if c.timestamp <= fvg.formed_at:
                continue
            if fvg.is_bullish and c.low <= fvg.bottom:
                fvg.filled    = True
                fvg.filled_at = c.timestamp
                break
            elif not fvg.is_bullish and c.high >= fvg.top:
                fvg.filled    = True
                fvg.filled_at = c.timestamp
                break
    return fvgs


# ─────────────────────────────────────────────
# ORDER BLOCK (OB)
# ─────────────────────────────────────────────

def detect_order_blocks(
    candles: List[Candle],
    lookforward: int = 3,
) -> List[OrderBlock]:
    """
    An order block is the LAST opposing candle before a strong
    displacement move.
    Bullish OB: last bearish candle before strong bullish displacement.
    Bearish OB: last bullish candle before strong bearish displacement.
    """
    obs = []
    n   = len(candles)
    if n < lookforward + 1:
        return obs

    instrument = candles[-1].instrument
    timeframe  = candles[-1].timeframe

    for i in range(1, n - lookforward):
        c = candles[i]
        # Look for displacement in the next `lookforward` candles
        future = candles[i + 1: i + 1 + lookforward]
        bullish_displacement = any(
            fc.is_bullish and is_displacement_candle(fc) for fc in future
        )
        bearish_displacement = any(
            fc.is_bearish and is_displacement_candle(fc) for fc in future
        )

        if c.is_bearish and bullish_displacement:
            obs.append(OrderBlock(
                instrument = instrument,
                timeframe  = timeframe,
                direction  = Bias.BULLISH,
                top        = c.high,
                bottom     = c.low,
                formed_at  = c.timestamp,
            ))

        elif c.is_bullish and bearish_displacement:
            obs.append(OrderBlock(
                instrument = instrument,
                timeframe  = timeframe,
                direction  = Bias.BEARISH,
                top        = c.high,
                bottom     = c.low,
                formed_at  = c.timestamp,
            ))

    return obs


def detect_breaker_blocks(
    candles: List[Candle],
    order_blocks: List[OrderBlock],
) -> List[OrderBlock]:
    """
    A Breaker Block is a failed Order Block.
    When price trades through an OB and structure breaks, the OB
    becomes a Breaker — a high-probability reversal zone on retest.
    """
    breakers = []
    for ob in order_blocks:
        if ob.is_breaker:
            continue
        for c in candles:
            if c.timestamp <= ob.formed_at:
                continue
            # Bullish OB fails (price breaks below its low)
            if ob.is_bullish and c.close < ob.bottom:
                ob_breaker         = OrderBlock(
                    instrument = ob.instrument,
                    timeframe  = ob.timeframe,
                    direction  = Bias.BEARISH,   # now acts as bearish resistance
                    top        = ob.top,
                    bottom     = ob.bottom,
                    formed_at  = ob.formed_at,
                    broken     = True,
                    is_breaker = True,
                )
                breakers.append(ob_breaker)
                ob.broken = True
                break
            # Bearish OB fails (price breaks above its high)
            elif not ob.is_bullish and c.close > ob.top:
                ob_breaker = OrderBlock(
                    instrument = ob.instrument,
                    timeframe  = ob.timeframe,
                    direction  = Bias.BULLISH,   # now acts as bullish support
                    top        = ob.top,
                    bottom     = ob.bottom,
                    formed_at  = ob.formed_at,
                    broken     = True,
                    is_breaker = True,
                )
                breakers.append(ob_breaker)
                ob.broken = True
                break
    return breakers


# ─────────────────────────────────────────────
# PREMIUM / DISCOUNT
# ─────────────────────────────────────────────

def compute_premium_discount(
    candles: List[Candle],
    lookback: int = 20,
) -> Optional[PremiumDiscountZone]:
    """
    Identify the most recent significant swing high and swing low
    to create a Premium/Discount zone.
    """
    recent = candles[-lookback:] if len(candles) >= lookback else candles
    if not recent:
        return None
    swing_high = max(c.high for c in recent)
    swing_low  = min(c.low  for c in recent)
    return PremiumDiscountZone(
        instrument  = recent[-1].instrument,
        swing_high  = swing_high,
        swing_low   = swing_low,
    )


# ─────────────────────────────────────────────
# SMT DIVERGENCE
# ─────────────────────────────────────────────

def detect_smt_divergence(
    primary_candles:    List[Candle],
    correlated_candles: List[Candle],
    lookback: int = 5,
) -> Optional[SMTDivergence]:
    """
    Bullish SMT: primary instrument makes lower low, correlated does NOT.
    Bearish SMT: primary makes higher high, correlated does NOT.
    Both must align on the same timestamps (last `lookback` candles).
    """
    if len(primary_candles) < lookback or len(correlated_candles) < lookback:
        return None

    p  = primary_candles[-lookback:]
    c  = correlated_candles[-lookback:]

    p_low_now  = min(x.low for x in p[-3:])
    p_low_prev = min(x.low for x in p[:3])
    c_low_now  = min(x.low for x in c[-3:])
    c_low_prev = min(x.low for x in c[:3])

    # Bullish SMT
    if p_low_now < p_low_prev and c_low_now >= c_low_prev:
        return SMTDivergence(
            primary_instrument    = primary_candles[-1].instrument,
            correlated_instrument = correlated_candles[-1].instrument,
            direction             = Bias.BULLISH,
            primary_low           = p_low_now,
            correlated_low        = c_low_now,
            timestamp             = primary_candles[-1].timestamp,
            confirmed             = True,
        )

    p_high_now  = max(x.high for x in p[-3:])
    p_high_prev = max(x.high for x in p[:3])
    c_high_now  = max(x.high for x in c[-3:])
    c_high_prev = max(x.high for x in c[:3])

    # Bearish SMT
    if p_high_now > p_high_prev and c_high_now <= c_high_prev:
        return SMTDivergence(
            primary_instrument    = primary_candles[-1].instrument,
            correlated_instrument = correlated_candles[-1].instrument,
            direction             = Bias.BEARISH,
            primary_low           = p_high_now,
            correlated_low        = c_high_now,
            timestamp             = primary_candles[-1].timestamp,
            confirmed             = True,
        )

    return None


# ─────────────────────────────────────────────
# AMD PHASE DETECTION
# ─────────────────────────────────────────────

def detect_amd_phase(candles: List[Candle], lookback: int = 30) -> str:
    """
    Classify current price action into AMD cycle phase:
    ACCUMULATION, MANIPULATION, or DISTRIBUTION.
    """
    recent = candles[-lookback:] if len(candles) >= lookback else candles
    if not recent:
        return "UNKNOWN"

    highs  = [c.high  for c in recent]
    lows   = [c.low   for c in recent]
    ranges = [c.full_range for c in recent]

    avg_range    = sum(ranges) / len(ranges)
    current_range = recent[-1].full_range
    high_spread   = max(highs) - min(highs)
    low_spread    = max(lows)  - min(lows)

    # Tight range → Accumulation
    if high_spread < avg_range * 5 and low_spread < avg_range * 5:
        return "ACCUMULATION"

    # Strong directional move → Distribution
    if current_range > avg_range * 2.5 and is_displacement_candle(recent[-1]):
        return "DISTRIBUTION"

    # False breakout + reversal → Manipulation
    last3 = recent[-3:]
    if len(last3) == 3:
        wick_sizes = [c.full_range - c.body for c in last3]
        if max(wick_sizes) > avg_range:
            return "MANIPULATION"

    return "NEUTRAL"