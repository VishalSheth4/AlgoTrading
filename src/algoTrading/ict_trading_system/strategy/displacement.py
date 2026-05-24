"""
strategy/displacement.py
========================
Displacement — detection, classification, and ATR-based filtering.

ICT definition:
  A displacement is a strong, aggressive candle (or sequence of candles)
  that shows institutional participation and leaves an FVG imbalance.

Characteristics:
  - Large candle body (body_ratio ≥ threshold)
  - Breaks through a prior swing high/low (structure break)
  - Often creates a Fair Value Gap
  - Closes near the extreme (minimal wick on the impulse side)
  - May come in a sequence of 2–3 strong candles in the same direction

Types:
  SINGLE    — one large impulse candle
  SEQUENCE  — 2–3 consecutive candles in the same direction, each strong
  ATR_BASED — candle range > N × ATR (volatility-relative filter)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

import pandas as pd

from algoTrading.ict_trading_system.settings import (
    Bias, Instrument, Timeframe,
    MIN_DISPLACEMENT_BODY_RATIO, PIP_SIZE,
)
from algoTrading.ict_trading_system.data.models import Candle

logger = logging.getLogger(__name__)

# How many ATR multiples a candle must exceed to count as displacement
ATR_DISPLACEMENT_MULT: float = 1.5

# Minimum sequence length for "sequence displacement"
MIN_SEQUENCE_LEN: int = 2


# ─────────────────────────────────────────────
# DISPLACEMENT EVENT
# ─────────────────────────────────────────────

@dataclass
class DisplacementEvent:
    """
    A confirmed displacement move.

    disp_type : "SINGLE" | "SEQUENCE" | "ATR_BASED"
    direction : BULLISH (upward) | BEARISH (downward)
    start_idx : index of first displacement candle
    end_idx   : index of last displacement candle in the move
    range_size: total price range covered by the displacement
    body_pct  : average body ratio of the candle(s) involved
    created_fvg: True if a valid FVG was left by this displacement
    """
    instrument:   Instrument
    timeframe:    Timeframe
    direction:    Bias
    disp_type:    str
    start_idx:    int
    end_idx:      int
    start_price:  float
    end_price:    float
    range_size:   float
    body_pct:     float
    candles:      List[Candle]
    timestamp:    datetime
    created_fvg:  bool = False
    atr_multiple: float = 0.0

    @property
    def pip_range(self) -> float:
        pip = PIP_SIZE.get(self.instrument, 0.0001)
        return self.range_size / pip

    @property
    def is_bullish(self) -> bool:
        return self.direction == Bias.BULLISH

    @property
    def is_strong(self) -> bool:
        """True if body_pct is above threshold AND range is significant."""
        return self.body_pct >= MIN_DISPLACEMENT_BODY_RATIO and self.range_size > 0


# ─────────────────────────────────────────────
# ATR CALCULATION
# ─────────────────────────────────────────────

def compute_atr(candles: List[Candle], period: int = 14) -> List[float]:
    """
    Calculate Average True Range using Wilder's smoothing.
    Returns a list of ATR values aligned with the candle list.
    ATR values before `period` are 0.0.
    """
    n = len(candles)
    if n < 2:
        return [0.0] * n

    trs = []
    for i in range(1, n):
        c  = candles[i]
        pc = candles[i - 1]
        tr = max(
            c.high - c.low,
            abs(c.high - pc.close),
            abs(c.low  - pc.close),
        )
        trs.append(tr)

    atrs = [0.0]  # first candle has no ATR

    # Simple average for first `period` TRs
    if len(trs) >= period:
        first_atr = sum(trs[:period]) / period
        atrs.extend([0.0] * (period - 1))
        atrs.append(first_atr)

        for j in range(period, len(trs)):
            atr = (atrs[-1] * (period - 1) + trs[j]) / period
            atrs.append(atr)
    else:
        atrs.extend([0.0] * len(trs))

    return atrs


# ─────────────────────────────────────────────
# SINGLE CANDLE DETECTION
# ─────────────────────────────────────────────

def is_displacement(candle: Candle) -> bool:
    """Basic body-ratio check (same as ict_concepts.is_displacement_candle)."""
    return candle.body_ratio >= MIN_DISPLACEMENT_BODY_RATIO


def is_atr_displacement(
    candle: Candle,
    atr: float,
    mult: float = ATR_DISPLACEMENT_MULT,
) -> bool:
    """
    ATR-relative check: candle range must exceed `mult` × ATR.
    Use this when you want to filter by volatility, not just body ratio.
    """
    if atr <= 0:
        return False
    return candle.full_range >= atr * mult


def classify_candle(
    candle: Candle,
    atr: float = 0.0,
) -> Tuple[bool, str]:
    """
    Return (is_displacement, reason).
    Checks both body-ratio and ATR filters.
    """
    body_ok = is_displacement(candle)
    atr_ok  = is_atr_displacement(candle, atr) if atr > 0 else False

    if body_ok and atr_ok:
        return True, "SINGLE+ATR"
    if body_ok:
        return True, "SINGLE"
    if atr_ok:
        return True, "ATR_BASED"
    return False, "NONE"


# ─────────────────────────────────────────────
# DISPLACEMENT DETECTION
# ─────────────────────────────────────────────

def detect_displacements(
    candles: List[Candle],
    atr_period: int = 14,
    atr_mult: float = ATR_DISPLACEMENT_MULT,
    min_sequence: int = MIN_SEQUENCE_LEN,
    check_fvg: bool = True,
) -> List[DisplacementEvent]:
    """
    Detect all displacement events in a candle list.

    For each candle:
      - If it passes body-ratio OR ATR-based test → record as SINGLE displacement
      - If it's part of a consecutive run of strong candles → also record as SEQUENCE

    Args:
        candles:      ordered Candle list
        atr_period:   ATR lookback period
        atr_mult:     ATR multiplier threshold
        min_sequence: minimum consecutive candles for SEQUENCE type
        check_fvg:    if True, mark created_fvg when a gap is detected
    """
    if len(candles) < 3:
        return []

    instrument = candles[-1].instrument
    timeframe  = candles[-1].timeframe
    atrs       = compute_atr(candles, atr_period)
    events: List[DisplacementEvent] = []

    i = 0
    while i < len(candles):
        c   = candles[i]
        atr = atrs[i]

        is_disp, disp_type = classify_candle(c, atr)
        if not is_disp:
            i += 1
            continue

        direction = Bias.BULLISH if c.is_bullish else Bias.BEARISH

        # Check for sequence: consecutive same-direction displacement candles
        seq_end = i
        for j in range(i + 1, min(i + 6, len(candles))):
            nxt      = candles[j]
            nxt_atr  = atrs[j]
            nxt_ok, _ = classify_candle(nxt, nxt_atr)
            nxt_dir  = Bias.BULLISH if nxt.is_bullish else Bias.BEARISH
            if nxt_ok and nxt_dir == direction:
                seq_end = j
            else:
                break

        seq_len    = seq_end - i + 1
        seq_candles = candles[i: seq_end + 1]

        start_price = candles[i].open
        end_price   = candles[seq_end].close
        rng         = abs(end_price - start_price)
        avg_body    = sum(c.body_ratio for c in seq_candles) / len(seq_candles)
        atr_mult_actual = rng / atr if atr > 0 else 0.0

        if seq_len >= min_sequence:
            event_type = "SEQUENCE"
        elif atr_mult_actual >= atr_mult:
            event_type = "ATR_BASED"
        else:
            event_type = "SINGLE"

        # FVG check: if 3 candles available around the sequence
        created_fvg = False
        if check_fvg and i >= 1 and seq_end + 1 < len(candles):
            c_before = candles[i - 1]
            c_after  = candles[seq_end + 1]
            if direction == Bias.BULLISH:
                created_fvg = c_after.low > c_before.high
            else:
                created_fvg = c_after.high < c_before.low

        events.append(DisplacementEvent(
            instrument   = instrument,
            timeframe    = timeframe,
            direction    = direction,
            disp_type    = event_type,
            start_idx    = i,
            end_idx      = seq_end,
            start_price  = start_price,
            end_price    = end_price,
            range_size   = rng,
            body_pct     = avg_body,
            candles      = seq_candles,
            timestamp    = candles[i].timestamp,
            created_fvg  = created_fvg,
            atr_multiple = round(atr_mult_actual, 2),
        ))

        logger.debug(
            f"Displacement [{event_type}] {direction.value} @ {candles[i].timestamp} "
            f"range={rng:.5f} body={avg_body:.0%} atrX={atr_mult_actual:.1f} "
            f"fvg={created_fvg}"
        )

        i = seq_end + 1

    return events


def find_most_recent_displacement(
    candles: List[Candle],
    direction: Optional[Bias] = None,
    min_type: str = "SINGLE",
) -> Optional[DisplacementEvent]:
    """
    Return the most recent displacement event, optionally filtered by direction.

    min_type: "SINGLE" accepts all, "ATR_BASED" only ATR-qualified, "SEQUENCE" only sequences
    """
    events = detect_displacements(candles)
    if not events:
        return None

    priority = {"SEQUENCE": 3, "ATR_BASED": 2, "SINGLE": 1}
    min_rank = priority.get(min_type, 1)

    filtered = [
        e for e in events
        if priority.get(e.disp_type, 0) >= min_rank
        and (direction is None or e.direction == direction)
    ]

    return filtered[-1] if filtered else None


# ─────────────────────────────────────────────
# STANDALONE DATAFRAME INTERFACE
# ─────────────────────────────────────────────

def scan_displacements(
    df: pd.DataFrame,
    instrument: Instrument,
    atr_period: int = 14,
    atr_mult: float = ATR_DISPLACEMENT_MULT,
) -> pd.DataFrame:
    """
    Scan a raw OHLCV DataFrame for displacement candles.

    Returns a DataFrame with:
        candle_index, timestamp, direction, disp_type,
        body_ratio, atr_multiple, range_pips, created_fvg
    """
    if df.empty or len(df) < 3:
        return pd.DataFrame()

    ts_col = next(
        (c for c in ("time", "timestamp", "datetime", "date") if c in df.columns),
        None
    )
    pip = PIP_SIZE.get(instrument, 0.0001)

    # Compute ATR manually on DataFrame
    high  = df["high"].values
    low   = df["low"].values
    close = df["close"].values
    n     = len(df)

    trs = [0.0]
    for i in range(1, n):
        tr = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i]  - close[i - 1]),
        )
        trs.append(tr)

    atrs = [0.0] * n
    if n >= atr_period + 1:
        atrs[atr_period] = sum(trs[1: atr_period + 1]) / atr_period
        for i in range(atr_period + 1, n):
            atrs[i] = (atrs[i - 1] * (atr_period - 1) + trs[i]) / atr_period

    rows = []
    opens  = df["open"].values
    closes = df["close"].values

    for i in range(1, n - 1):
        rng      = high[i] - low[i]
        body     = abs(closes[i] - opens[i])
        body_pct = body / rng if rng > 0 else 0.0
        atr      = atrs[i]

        body_ok = body_pct >= MIN_DISPLACEMENT_BODY_RATIO
        atr_ok  = (rng >= atr * atr_mult) if atr > 0 else False

        if not body_ok and not atr_ok:
            continue

        direction = "BULLISH" if closes[i] > opens[i] else "BEARISH"

        if body_ok and atr_ok:
            disp_type = "SINGLE+ATR"
        elif body_ok:
            disp_type = "SINGLE"
        else:
            disp_type = "ATR_BASED"

        # FVG check
        created_fvg = False
        if direction == "BULLISH":
            created_fvg = low[i + 1] > high[i - 1]
        else:
            created_fvg = high[i + 1] < low[i - 1]

        rows.append({
            "candle_index": i,
            "timestamp":    df.iloc[i][ts_col] if ts_col else i,
            "direction":    direction,
            "disp_type":    disp_type,
            "body_ratio":   round(body_pct, 3),
            "atr_multiple": round(rng / atr, 2) if atr > 0 else 0.0,
            "range_pips":   round(rng / pip, 1),
            "created_fvg":  created_fvg,
        })

    if not rows:
        return pd.DataFrame(columns=[
            "candle_index", "timestamp", "direction", "disp_type",
            "body_ratio", "atr_multiple", "range_pips", "created_fvg",
        ])

    return pd.DataFrame(rows)
