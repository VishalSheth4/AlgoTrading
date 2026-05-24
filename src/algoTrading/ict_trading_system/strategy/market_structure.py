"""
strategy/market_structure.py
============================
Comprehensive Market Structure analysis.

Concepts covered:
  BOS  — Break of Structure  : price CLOSES beyond a prior swing level
         → continuation of the current trend
  CHoCH— Change of Character : first BOS in the OPPOSITE direction
         → early warning of a potential trend reversal
  MSS  — Market Structure Shift : CHoCH confirmed by displacement
         → strong reversal signal, used for entry bias

Swing structure labels:
  HH — Higher High   (bullish continuation)
  HL — Higher Low    (bullish continuation)
  LH — Lower High    (bearish continuation)
  LL — Lower Low     (bearish continuation)

Market Flow:
  Determined by the MOST RECENT two swing pairs.
  BULLISH = HH + HL sequence
  BEARISH = LH + LL sequence
  TRANSITION = mixed (CHoCH territory)

ICT usage:
  - Identify HTF flow first (D1 / H4)
  - Then wait for LTF structure confirmation (M15 / M5)
  - Only trade in the direction of HTF flow
  - BOS on LTF = continuation entry
  - CHoCH on LTF = reversal setup (combine with sweep + FVG)
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
    Bias, Instrument, Timeframe, PIP_SIZE, MIN_DISPLACEMENT_BODY_RATIO,
)
from algoTrading.ict_trading_system.data.models import Candle, SwingPoint

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# ENUMS & LABELS
# ─────────────────────────────────────────────

class SwingLabel(str, Enum):
    HH = "HH"   # Higher High
    HL = "HL"   # Higher Low
    LH = "LH"   # Lower High
    LL = "LL"   # Lower Low
    EH = "EH"   # Equal High (within tolerance)
    EL = "EL"   # Equal Low


class StructureType(str, Enum):
    BOS   = "BOS"     # Break of Structure (continuation)
    CHOCH = "CHoCH"   # Change of Character (reversal warning)
    MSS   = "MSS"     # Market Structure Shift (confirmed reversal)


# ─────────────────────────────────────────────
# DATA MODELS
# ─────────────────────────────────────────────

@dataclass
class StructurePoint:
    """A labeled swing high or swing low in market structure."""
    timestamp:  datetime
    price:      float
    is_high:    bool
    label:      SwingLabel
    candle_idx: int
    broken:     bool  = False
    broken_at:  Optional[datetime] = None

    @property
    def is_bullish_structure(self) -> bool:
        return self.label in (SwingLabel.HH, SwingLabel.HL)

    @property
    def is_bearish_structure(self) -> bool:
        return self.label in (SwingLabel.LH, SwingLabel.LL)


@dataclass
class StructureBreak:
    """A BOS or CHoCH event."""
    break_type:     StructureType
    direction:      Bias           # direction of the BREAK (new trend)
    break_price:    float          # level that was broken
    break_candle:   int            # bar index of the break
    break_time:     datetime
    displaced:      bool  = False  # was there a displacement candle?
    prior_structure: Optional[StructurePoint] = None


@dataclass
class MarketFlow:
    """
    Overall market flow from the last two complete swing pairs.

    bias        : current directional flow
    phase       : TRENDING | RANGING | TRANSITIONING
    last_bos    : most recent BOS
    last_choch  : most recent CHoCH (if any)
    swing_highs : recent labeled swing highs
    swing_lows  : recent labeled swing lows
    """
    bias:         Bias
    phase:        str = "RANGING"    # TRENDING | RANGING | TRANSITIONING
    last_bos:     Optional[StructureBreak] = None
    last_choch:   Optional[StructureBreak] = None
    swing_highs:  List[StructurePoint] = field(default_factory=list)
    swing_lows:   List[StructurePoint] = field(default_factory=list)

    @property
    def is_bullish(self) -> bool:
        return self.bias == Bias.BULLISH

    @property
    def is_bearish(self) -> bool:
        return self.bias == Bias.BEARISH

    @property
    def last_swing_high(self) -> Optional[StructurePoint]:
        return self.swing_highs[-1] if self.swing_highs else None

    @property
    def last_swing_low(self) -> Optional[StructurePoint]:
        return self.swing_lows[-1] if self.swing_lows else None


# ─────────────────────────────────────────────
# SWING STRUCTURE BUILDER
# ─────────────────────────────────────────────

def build_swing_structure(
    candles: List[Candle],
    lookback: int = 3,
    equal_pips: float = 2.0,
) -> Tuple[List[StructurePoint], List[StructurePoint]]:
    """
    Build labeled swing structure from candles.

    Returns (swing_highs, swing_lows) as StructurePoint lists,
    each labeled HH/LH or HL/LL relative to the prior swing of the same type.

    equal_pips : pip tolerance for equal highs/lows classification
    """
    if not candles:
        return [], []

    instrument = candles[-1].instrument
    pip        = PIP_SIZE.get(instrument, 0.0001)
    tol        = equal_pips * pip

    # Detect raw swing pivots
    n        = len(candles)
    raw_highs: List[Tuple[int, float]] = []
    raw_lows:  List[Tuple[int, float]] = []

    for i in range(lookback, n - lookback):
        h = candles[i].high
        l = candles[i].low
        if all(h > candles[i - j].high for j in range(1, lookback + 1)) and \
           all(h > candles[i + j].high for j in range(1, lookback + 1)):
            raw_highs.append((i, h))
        if all(l < candles[i - j].low for j in range(1, lookback + 1)) and \
           all(l < candles[i + j].low for j in range(1, lookback + 1)):
            raw_lows.append((i, l))

    # Label swing highs: HH / LH / EH
    structured_highs: List[StructurePoint] = []
    for idx, (ci, price) in enumerate(raw_highs):
        if idx == 0:
            label = SwingLabel.HH   # default first swing to HH
        else:
            prev_price = raw_highs[idx - 1][1]
            if price > prev_price + tol:
                label = SwingLabel.HH
            elif price < prev_price - tol:
                label = SwingLabel.LH
            else:
                label = SwingLabel.EH
        structured_highs.append(StructurePoint(
            timestamp  = candles[ci].timestamp,
            price      = price,
            is_high    = True,
            label      = label,
            candle_idx = ci,
        ))

    # Label swing lows: HL / LL / EL
    structured_lows: List[StructurePoint] = []
    for idx, (ci, price) in enumerate(raw_lows):
        if idx == 0:
            label = SwingLabel.HL
        else:
            prev_price = raw_lows[idx - 1][1]
            if price > prev_price + tol:
                label = SwingLabel.HL
            elif price < prev_price - tol:
                label = SwingLabel.LL
            else:
                label = SwingLabel.EL
        structured_lows.append(StructurePoint(
            timestamp  = candles[ci].timestamp,
            price      = price,
            is_high    = False,
            label      = label,
            candle_idx = ci,
        ))

    return structured_highs, structured_lows


# ─────────────────────────────────────────────
# BOS DETECTION
# ─────────────────────────────────────────────

def detect_bos(
    candles: List[Candle],
    swing_highs: List[StructurePoint],
    swing_lows:  List[StructurePoint],
    current_bias: Bias = Bias.NEUTRAL,
) -> Optional[StructureBreak]:
    """
    Detect the most recent Break of Structure.

    BOS in a BULLISH trend : close above the most recent swing high (HH continuation)
    BOS in a BEARISH trend : close below the most recent swing low  (LL continuation)

    In a neutral/transitioning context, look for any structure break.
    """
    if not candles or (not swing_highs and not swing_lows):
        return None

    last = candles[-1]
    displaced = _has_displacement(candles)

    # Bullish BOS: close above prior swing high
    if swing_highs and current_bias in (Bias.BULLISH, Bias.NEUTRAL):
        prior_high = swing_highs[-1]
        if not prior_high.broken and last.close > prior_high.price:
            prior_high.broken    = True
            prior_high.broken_at = last.timestamp
            logger.info(f"BOS BULLISH @ {last.timestamp} — broke {prior_high.price:.5f}")
            return StructureBreak(
                break_type      = StructureType.BOS,
                direction       = Bias.BULLISH,
                break_price     = prior_high.price,
                break_candle    = len(candles) - 1,
                break_time      = last.timestamp,
                displaced       = displaced,
                prior_structure = prior_high,
            )

    # Bearish BOS: close below prior swing low
    if swing_lows and current_bias in (Bias.BEARISH, Bias.NEUTRAL):
        prior_low = swing_lows[-1]
        if not prior_low.broken and last.close < prior_low.price:
            prior_low.broken    = True
            prior_low.broken_at = last.timestamp
            logger.info(f"BOS BEARISH @ {last.timestamp} — broke {prior_low.price:.5f}")
            return StructureBreak(
                break_type      = StructureType.BOS,
                direction       = Bias.BEARISH,
                break_price     = prior_low.price,
                break_candle    = len(candles) - 1,
                break_time      = last.timestamp,
                displaced       = displaced,
                prior_structure = prior_low,
            )

    return None


# ─────────────────────────────────────────────
# CHoCH DETECTION
# ─────────────────────────────────────────────

def detect_choch(
    candles: List[Candle],
    swing_highs: List[StructurePoint],
    swing_lows:  List[StructurePoint],
    current_bias: Bias,
) -> Optional[StructureBreak]:
    """
    Detect a Change of Character (CHoCH).

    In a BULLISH market: CHoCH = close BELOW the most recent swing low (HL broken)
    In a BEARISH market: CHoCH = close ABOVE the most recent swing high (LH broken)

    CHoCH does NOT require displacement (unlike MSS).
    It's the first warning sign of a potential reversal.
    """
    if not candles or current_bias == Bias.NEUTRAL:
        return None

    last      = candles[-1]
    displaced = _has_displacement(candles)

    if current_bias == Bias.BULLISH and swing_lows:
        # Price broke below the last HL — bearish CHoCH
        last_hl = next((sp for sp in reversed(swing_lows) if sp.label == SwingLabel.HL), None)
        if last_hl and not last_hl.broken and last.close < last_hl.price:
            last_hl.broken    = True
            last_hl.broken_at = last.timestamp
            logger.info(f"CHoCH BEARISH @ {last.timestamp} — HL broken at {last_hl.price:.5f}")
            return StructureBreak(
                break_type      = StructureType.CHOCH,
                direction       = Bias.BEARISH,
                break_price     = last_hl.price,
                break_candle    = len(candles) - 1,
                break_time      = last.timestamp,
                displaced       = displaced,
                prior_structure = last_hl,
            )

    if current_bias == Bias.BEARISH and swing_highs:
        # Price broke above the last LH — bullish CHoCH
        last_lh = next((sp for sp in reversed(swing_highs) if sp.label == SwingLabel.LH), None)
        if last_lh and not last_lh.broken and last.close > last_lh.price:
            last_lh.broken    = True
            last_lh.broken_at = last.timestamp
            logger.info(f"CHoCH BULLISH @ {last.timestamp} — LH broken at {last_lh.price:.5f}")
            return StructureBreak(
                break_type      = StructureType.CHOCH,
                direction       = Bias.BULLISH,
                break_price     = last_lh.price,
                break_candle    = len(candles) - 1,
                break_time      = last.timestamp,
                displaced       = displaced,
                prior_structure = last_lh,
            )

    return None


# ─────────────────────────────────────────────
# MSS (CONFIRMED REVERSAL)
# ─────────────────────────────────────────────

def detect_mss_with_structure(
    candles: List[Candle],
    swing_highs: List[StructurePoint],
    swing_lows:  List[StructurePoint],
    current_bias: Bias,
) -> Optional[StructureBreak]:
    """
    Detect a Market Structure Shift (MSS) — CHoCH + displacement.

    MSS = CHoCH that was accompanied by a strong displacement candle.
    This is the primary reversal signal in ICT.
    """
    choch = detect_choch(candles, swing_highs, swing_lows, current_bias)
    if choch and choch.displaced:
        choch.break_type = StructureType.MSS
        logger.info(f"MSS confirmed @ {choch.break_time}")
    return choch


# ─────────────────────────────────────────────
# MARKET FLOW ANALYSIS
# ─────────────────────────────────────────────

def analyze_market_flow(
    candles: List[Candle],
    lookback: int = 5,
    swing_lookback: int = 3,
) -> MarketFlow:
    """
    Determine the current market flow from swing structure.

    Uses the last `lookback` swing highs and lows to assess:
      BULLISH    : consecutive HH + HL
      BEARISH    : consecutive LH + LL
      RANGING    : mixed or EH/EL dominance
      TRANSITIONING : CHoCH observed (one opposing break)
    """
    highs, lows = build_swing_structure(candles, lookback=swing_lookback)

    if not highs and not lows:
        return MarketFlow(bias=Bias.NEUTRAL, phase="RANGING")

    # Look at the last `lookback` of each
    recent_h = highs[-lookback:] if len(highs) >= lookback else highs
    recent_l = lows[-lookback:]  if len(lows)  >= lookback else lows

    bullish_h = sum(1 for sp in recent_h if sp.label == SwingLabel.HH)
    bearish_h = sum(1 for sp in recent_h if sp.label == SwingLabel.LH)
    bullish_l = sum(1 for sp in recent_l if sp.label == SwingLabel.HL)
    bearish_l = sum(1 for sp in recent_l if sp.label == SwingLabel.LL)

    bull_score = bullish_h + bullish_l
    bear_score = bearish_h + bearish_l

    # Determine bias
    if bull_score > bear_score and bull_score >= 2:
        bias  = Bias.BULLISH
        phase = "TRENDING"
    elif bear_score > bull_score and bear_score >= 2:
        bias  = Bias.BEARISH
        phase = "TRENDING"
    else:
        bias  = Bias.NEUTRAL
        phase = "RANGING"

    # Check for CHoCH transition
    if bias == Bias.BULLISH and bearish_l > 0:
        phase = "TRANSITIONING"
    elif bias == Bias.BEARISH and bullish_h > 0:
        phase = "TRANSITIONING"

    # Detect any recent BOS/CHoCH
    last_bos   = detect_bos(candles, highs, lows, bias)
    last_choch = detect_choch(candles, highs, lows, bias)

    flow = MarketFlow(
        bias        = bias,
        phase       = phase,
        last_bos    = last_bos,
        last_choch  = last_choch,
        swing_highs = highs,
        swing_lows  = lows,
    )

    logger.debug(
        f"Market Flow: {bias.value} | Phase: {phase} | "
        f"Bulls={bull_score} Bears={bear_score}"
    )
    return flow


# ─────────────────────────────────────────────
# STANDALONE DATAFRAME INTERFACE
# ─────────────────────────────────────────────

def scan_market_structure(
    df: pd.DataFrame,
    instrument: Instrument,
    swing_lookback: int = 3,
    equal_pips: float = 2.0,
) -> pd.DataFrame:
    """
    Scan an OHLCV DataFrame and label each candle with market structure info.

    Returns a DataFrame with one row per candle:
        candle_index, timestamp, swing_type (HIGH/LOW/NONE),
        swing_label (HH/HL/LH/LL/EH/EL/NONE), structure_break,
        break_direction, market_flow_bias, market_flow_phase
    """
    ts_col = next(
        (c for c in ("time", "timestamp", "datetime", "date") if c in df.columns),
        None
    )
    if df.empty or len(df) < swing_lookback * 2 + 2:
        return pd.DataFrame()

    pip = PIP_SIZE.get(instrument, 0.0001)
    tol = equal_pips * pip

    highs  = df["high"].values
    lows   = df["low"].values
    closes = df["close"].values
    opens  = df["open"].values
    n      = len(df)

    rows = []
    raw_swing_highs: List[Tuple[int, float]] = []
    raw_swing_lows:  List[Tuple[int, float]] = []

    for i in range(swing_lookback, n - swing_lookback):
        h = highs[i]
        l = lows[i]
        ts = df.iloc[i][ts_col] if ts_col else i

        is_swing_high = all(h > highs[i - j] for j in range(1, swing_lookback + 1)) and \
                        all(h > highs[i + j] for j in range(1, swing_lookback + 1))
        is_swing_low  = all(l < lows[i - j] for j in range(1, swing_lookback + 1)) and \
                        all(l < lows[i + j] for j in range(1, swing_lookback + 1))

        swing_type  = "NONE"
        swing_label = "NONE"
        bos         = "NONE"
        bos_dir     = "NONE"

        if is_swing_high:
            swing_type = "HIGH"
            if raw_swing_highs:
                prev = raw_swing_highs[-1][1]
                swing_label = "HH" if h > prev + tol else ("LH" if h < prev - tol else "EH")
            else:
                swing_label = "HH"
            raw_swing_highs.append((i, h))

            # BOS check: breaks above prior LH in bearish flow
            if len(raw_swing_highs) >= 2:
                prev_h = raw_swing_highs[-2][1]
                if h > prev_h and swing_label == "HH":
                    bos     = "BOS"
                    bos_dir = "BULLISH"

        elif is_swing_low:
            swing_type = "LOW"
            if raw_swing_lows:
                prev = raw_swing_lows[-1][1]
                swing_label = "HL" if l > prev + tol else ("LL" if l < prev - tol else "EL")
            else:
                swing_label = "HL"
            raw_swing_lows.append((i, l))

            # CHoCH check: HL broken in bullish flow
            if len(raw_swing_lows) >= 2:
                prev_l = raw_swing_lows[-2][1]
                if l < prev_l and swing_label == "LL":
                    bos     = "BOS"
                    bos_dir = "BEARISH"

        # Market flow: last 2 swing pairs
        bull_score = (
            sum(1 for _, lbl in [(x[0], "HH") for x in raw_swing_highs[-3:]]) +
            sum(1 for x in raw_swing_lows[-3:] if True)
        )
        recent_h_labels = [r[1] for r in (raw_swing_highs[-3:] if raw_swing_highs else [])]
        recent_l_labels = [r[1] for r in (raw_swing_lows[-3:]  if raw_swing_lows  else [])]

        # Simplified flow: last swing highs/lows determine bias
        if raw_swing_highs and raw_swing_lows:
            last_h = raw_swing_highs[-1][1]
            last_l = raw_swing_lows[-1][1]
            # If we see HH+HL pattern → bullish
            if len(raw_swing_highs) >= 2 and len(raw_swing_lows) >= 2:
                hh = raw_swing_highs[-1][1] > raw_swing_highs[-2][1]
                hl = raw_swing_lows[-1][1]  > raw_swing_lows[-2][1]
                lh = raw_swing_highs[-1][1] < raw_swing_highs[-2][1]
                ll = raw_swing_lows[-1][1]  < raw_swing_lows[-2][1]

                if hh and hl:
                    flow_bias  = "BULLISH"
                    flow_phase = "TRENDING"
                elif lh and ll:
                    flow_bias  = "BEARISH"
                    flow_phase = "TRENDING"
                elif hh and ll:
                    flow_bias  = "NEUTRAL"
                    flow_phase = "TRANSITIONING"
                else:
                    flow_bias  = "NEUTRAL"
                    flow_phase = "RANGING"
            else:
                flow_bias  = "NEUTRAL"
                flow_phase = "RANGING"
        else:
            flow_bias  = "NEUTRAL"
            flow_phase = "RANGING"

        rows.append({
            "candle_index":    i,
            "timestamp":       ts,
            "swing_type":      swing_type,
            "swing_label":     swing_label,
            "swing_price":     h if swing_type == "HIGH" else (l if swing_type == "LOW" else None),
            "structure_break": bos,
            "break_direction": bos_dir,
            "flow_bias":       flow_bias,
            "flow_phase":      flow_phase,
        })

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows)
    # Only include swing rows + broadcast flow to all candles via merge
    return result


# ─────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────

def _has_displacement(candles: List[Candle], lookback: int = 3) -> bool:
    """Check if any of the last `lookback` candles was a displacement."""
    for c in candles[-lookback:]:
        if c.body_ratio >= MIN_DISPLACEMENT_BODY_RATIO:
            return True
    return False


def get_key_structure_levels(
    flow: MarketFlow,
    n: int = 3,
) -> Tuple[List[float], List[float]]:
    """
    Extract the `n` most recent swing highs and lows as price levels.
    Useful as liquidity targets.
    Returns (resistance_levels, support_levels).
    """
    resistances = [sp.price for sp in flow.swing_highs[-n:]]
    supports    = [sp.price for sp in flow.swing_lows[-n:]]
    return sorted(resistances, reverse=True), sorted(supports)
