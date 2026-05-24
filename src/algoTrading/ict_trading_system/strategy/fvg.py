"""
strategy/fvg.py
===============
Fair Value Gap (FVG) — comprehensive detection, state management,
and ICT sequence entry signals.

ICT sequence:
  1. Liquidity sweep (wick beyond equal highs/lows, close back inside)
  2. Price returns into an active FVG left by the displacement
  3. Entry at FVG midpoint (or zone), stop beyond the swept level

This module extends the basic detect_fvg() in ict_concepts.py with:
  - Displacement candle requirement on the middle candle (true imbalance)
  - Partial-fill tracking (price entered the gap but didn't fully close it)
  - ICT sweep→FVG entry signal generation
  - Standalone scan_fvgs() for direct DataFrame use
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

import pandas as pd

from algoTrading.ict_trading_system.settings import (
    Bias, Instrument, Timeframe, StrategyID, Session,
    MIN_FVG_PIPS, PIP_SIZE, MIN_DISPLACEMENT_BODY_RATIO,
)
from algoTrading.ict_trading_system.data.models import (
    Candle, FairValueGap, LiquidityLevel, TradeSignal,
)
from algoTrading.ict_trading_system.strategy.ict_concepts import (
    is_displacement_candle,
    detect_liquidity_sweep,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# FILL STATE
# ─────────────────────────────────────────────

@dataclass
class FVGState:
    """Tracks a FairValueGap's lifecycle: active → partial → filled."""

    fvg: FairValueGap
    partial_fill_price: Optional[float] = None   # deepest penetration so far
    partial_fill_at:    Optional[datetime] = None
    entry_triggered:    bool = False              # ICT entry fired from this FVG

    @property
    def is_active(self) -> bool:
        return not self.fvg.filled

    @property
    def is_partially_filled(self) -> bool:
        return self.partial_fill_price is not None and not self.fvg.filled

    @property
    def fill_pct(self) -> float:
        """0.0 → 1.0 fraction of the gap that has been retraced."""
        if self.fvg.size == 0:
            return 0.0
        if self.fvg.is_bullish:
            depth = self.fvg.top - (self.partial_fill_price or self.fvg.top)
            return min(depth / self.fvg.size, 1.0)
        else:
            depth = (self.partial_fill_price or self.fvg.bottom) - self.fvg.bottom
            return min(depth / self.fvg.size, 1.0)


# ─────────────────────────────────────────────
# FVG DETECTION (ENHANCED)
# ─────────────────────────────────────────────

def detect_fvgs_enhanced(
    candles: List[Candle],
    require_displacement: bool = True,
) -> List[FairValueGap]:
    """
    Detect Fair Value Gaps with optional displacement requirement on
    the middle (impulse) candle.

    Bullish FVG : c2.low > c0.high  — gap above c0, below c2
    Bearish FVG : c2.high < c0.low  — gap below c0, above c2

    Args:
        candles:              ordered list of Candle objects
        require_displacement: if True, middle candle must pass
                              is_displacement_candle() (body_ratio ≥ threshold)
    """
    if len(candles) < 3:
        return []

    instrument = candles[-1].instrument
    timeframe  = candles[-1].timeframe
    pip        = PIP_SIZE.get(instrument, 0.0001)
    min_size   = MIN_FVG_PIPS.get(instrument, 3.0) * pip

    fvgs: List[FairValueGap] = []

    for i in range(2, len(candles)):
        c0, c1, c2 = candles[i - 2], candles[i - 1], candles[i]

        if require_displacement and not is_displacement_candle(c1):
            continue

        # Bullish FVG: gap between c0.high and c2.low
        if c2.low > c0.high:
            size = c2.low - c0.high
            if size >= min_size:
                fvgs.append(FairValueGap(
                    instrument   = instrument,
                    timeframe    = timeframe,
                    direction    = Bias.BULLISH,
                    top          = c2.low,
                    bottom       = c0.high,
                    formed_at    = c1.timestamp,   # impulse candle timestamp
                    candle_index = i - 1,          # middle candle index
                ))
                logger.debug(
                    f"Bullish FVG @ {c1.timestamp} [{c0.high:.5f}–{c2.low:.5f}]"
                    f" size={size/pip:.1f}pip"
                )

        # Bearish FVG: gap between c2.high and c0.low
        elif c2.high < c0.low:
            size = c0.low - c2.high
            if size >= min_size:
                fvgs.append(FairValueGap(
                    instrument   = instrument,
                    timeframe    = timeframe,
                    direction    = Bias.BEARISH,
                    top          = c0.low,
                    bottom       = c2.high,
                    formed_at    = c1.timestamp,
                    candle_index = i - 1,
                ))
                logger.debug(
                    f"Bearish FVG @ {c1.timestamp} [{c2.high:.5f}–{c0.low:.5f}]"
                    f" size={size/pip:.1f}pip"
                )

    return fvgs


# ─────────────────────────────────────────────
# FILL TRACKING
# ─────────────────────────────────────────────

def update_fvg_states(
    states: List[FVGState],
    candles: List[Candle],
) -> List[FVGState]:
    """
    Walk forward through `candles` and update each FVGState:
      - Mark fully filled when price closes through the gap entirely
      - Track partial fill (deepest penetration without full close)

    Modifies states in-place and returns them.
    """
    for state in states:
        if not state.is_active:
            continue

        fvg = state.fvg

        for c in candles:
            if c.timestamp <= fvg.formed_at:
                continue

            if fvg.is_bullish:
                # Price re-enters from above (retracement down into gap)
                if c.low <= fvg.top:
                    depth = min(c.low, fvg.bottom)
                    if state.partial_fill_price is None or depth < state.partial_fill_price:
                        state.partial_fill_price = depth
                        state.partial_fill_at    = c.timestamp

                # Fully filled: candle closes below gap bottom
                if c.close <= fvg.bottom:
                    fvg.filled    = True
                    fvg.filled_at = c.timestamp
                    break

            else:
                # Bearish FVG: price re-enters from below (retracement up into gap)
                if c.high >= fvg.bottom:
                    depth = max(c.high, fvg.top)
                    if state.partial_fill_price is None or depth > state.partial_fill_price:
                        state.partial_fill_price = depth
                        state.partial_fill_at    = c.timestamp

                # Fully filled: candle closes above gap top
                if c.close >= fvg.top:
                    fvg.filled    = True
                    fvg.filled_at = c.timestamp
                    break

    return states


# ─────────────────────────────────────────────
# ICT ENTRY: SWEEP → FVG
# ─────────────────────────────────────────────

def detect_sweep_then_fvg_entry(
    candles: List[Candle],
    liquidity_levels: List[LiquidityLevel],
    fvg_states: List[FVGState],
    rr_tp1: float = 2.0,
    rr_tp2: float = 3.0,
    entry_at_midpoint: bool = True,
    max_candles_after_sweep: int = 10,
) -> List[TradeSignal]:
    """
    ICT sequence: liquidity sweep → FVG entry.

    For each candle (starting from index 1):
      1. Check if it sweeps a liquidity level (wick past level, close back)
      2. After the sweep, watch the next `max_candles_after_sweep` candles
      3. If price enters an ACTIVE, UNFILLED FVG that aligns with the sweep direction:
           - Bullish sweep (sell-side swept) → look for Bullish FVG
           - Bearish sweep (buy-side swept)  → look for Bearish FVG
      4. Generate a TradeSignal:
           - Entry : FVG midpoint (or gap top/bottom depending on direction)
           - SL    : beyond the sweep wick extreme
           - TP1   : entry ± rr_tp1 * risk
           - TP2   : entry ± rr_tp2 * risk

    Returns:
        List of TradeSignal objects (one per valid ICT setup found)
    """
    signals: List[TradeSignal] = []
    if len(candles) < 3 or not liquidity_levels or not fvg_states:
        return signals

    instrument = candles[-1].instrument
    timeframe  = candles[-1].timeframe

    # Determine session from timestamp (simple UTC-hour heuristic)
    def _session(ts: datetime) -> Session:
        h = ts.hour
        if 7 <= h < 12:
            return Session.LONDON
        if 13 <= h < 17:
            return Session.NEW_YORK
        if 0 <= h < 7:
            return Session.ASIAN
        return Session.DEAD

    sweep_events: List[Tuple[int, LiquidityLevel, Candle]] = []

    # Pass 1: find all sweep candles
    for i, candle in enumerate(candles):
        for level in liquidity_levels:
            if level.swept:
                continue
            if detect_liquidity_sweep([candle], level):
                level.swept    = True
                level.swept_at = candle.timestamp
                level.sweep_wick = (
                    candle.high - level.price if level.is_buy_side
                    else level.price - candle.low
                )
                sweep_events.append((i, level, candle))
                logger.info(
                    f"Liquidity sweep detected: {level.level_type} "
                    f"@ {level.price:.5f} on {candle.timestamp}"
                )

    # Pass 2: for each sweep, look ahead for FVG entry
    for sweep_idx, level, sweep_candle in sweep_events:
        # Bullish setup: sell-side liquidity swept → expect bullish continuation
        # Bearish setup: buy-side liquidity swept  → expect bearish continuation
        is_bullish_setup = level.is_sell_side   # swept lows → price goes up

        fvg_direction = Bias.BULLISH if is_bullish_setup else Bias.BEARISH
        sl_extreme    = sweep_candle.low if is_bullish_setup else sweep_candle.high

        window_end = min(sweep_idx + max_candles_after_sweep + 1, len(candles))

        for j in range(sweep_idx + 1, window_end):
            c = candles[j]

            for state in fvg_states:
                fvg = state.fvg
                if fvg.filled or fvg.direction != fvg_direction:
                    continue
                if fvg.formed_at >= c.timestamp:
                    continue
                if state.entry_triggered:
                    continue

                price_in_fvg = fvg.bottom <= c.close <= fvg.top

                if not price_in_fvg:
                    # Also accept when candle wicks into the gap
                    if is_bullish_setup:
                        price_in_fvg = c.low <= fvg.top and c.close > fvg.bottom
                    else:
                        price_in_fvg = c.high >= fvg.bottom and c.close < fvg.top

                if not price_in_fvg:
                    continue

                # Build signal
                entry = fvg.midpoint if entry_at_midpoint else (
                    fvg.bottom if is_bullish_setup else fvg.top
                )
                sl    = sl_extreme
                risk  = abs(entry - sl)

                if risk <= 0:
                    continue

                if is_bullish_setup:
                    tp1 = entry + rr_tp1 * risk
                    tp2 = entry + rr_tp2 * risk
                else:
                    tp1 = entry - rr_tp1 * risk
                    tp2 = entry - rr_tp2 * risk

                direction = Bias.BULLISH if is_bullish_setup else Bias.BEARISH
                pip       = PIP_SIZE.get(instrument, 0.0001)
                confidence = _compute_confidence(fvg, state, risk, pip)

                signal = TradeSignal(
                    strategy_id    = StrategyID.BEGINNER_ICT,
                    instrument     = instrument,
                    direction      = direction,
                    entry_price    = entry,
                    stop_loss      = sl,
                    take_profit_1  = tp1,
                    take_profit_2  = tp2,
                    session        = _session(c.timestamp),
                    timestamp      = c.timestamp,
                    confidence     = confidence,
                    notes          = (
                        f"ICT FVG entry | sweep={level.level_type}@{level.price:.5f} "
                        f"| FVG [{fvg.bottom:.5f}–{fvg.top:.5f}] "
                        f"| fill={state.fill_pct*100:.0f}%"
                    ),
                )

                state.entry_triggered = True
                signals.append(signal)

                logger.info(
                    f"ICT FVG signal: {direction.value} {instrument.value} "
                    f"entry={entry:.5f} sl={sl:.5f} tp1={tp1:.5f} rr={signal.rr_ratio}"
                )
                break  # one signal per sweep event

    return signals


def _compute_confidence(
    fvg: FairValueGap,
    state: FVGState,
    risk: float,
    pip: float,
) -> float:
    """
    Simple 0–1 confidence score based on:
      - FVG size (bigger = more significant)
      - Whether price only partially filled the gap (not over-extended)
      - Risk-to-pip ratio (tighter SL relative to gap size = higher quality)
    """
    score = 0.0
    size_pips = fvg.size / pip if pip > 0 else 0

    # 0.3 — decent gap size (≥5 pips for FX, scaled for metals)
    if size_pips >= 5:
        score += 0.3

    # 0.3 — only partial fill (price respected the gap, didn't smash through)
    if state.is_partially_filled and state.fill_pct < 0.5:
        score += 0.3
    elif not state.is_partially_filled:
        score += 0.2   # untouched gap is also good

    # 0.4 — tight risk (risk ≤ 2× gap size = well-placed stop)
    if risk <= fvg.size * 2:
        score += 0.4

    return round(min(score, 1.0), 2)


# ─────────────────────────────────────────────
# STANDALONE DATAFRAME INTERFACE
# ─────────────────────────────────────────────

def scan_fvgs(
    df: pd.DataFrame,
    instrument: Instrument,
    timeframe: Timeframe,
    require_displacement: bool = True,
) -> pd.DataFrame:
    """
    Scan a raw OHLCV DataFrame for Fair Value Gaps.

    Expected columns: open, high, low, close, (optional) volume, (optional) time/timestamp

    Returns a DataFrame with one row per FVG:
        formed_at, direction, top, bottom, midpoint, size_pips, filled, filled_at
    """
    if df.empty or len(df) < 3:
        return pd.DataFrame()

    # Resolve timestamp column
    ts_col = None
    for name in ("time", "timestamp", "datetime", "date"):
        if name in df.columns:
            ts_col = name
            break

    pip      = PIP_SIZE.get(instrument, 0.0001)
    min_size = MIN_FVG_PIPS.get(instrument, 3.0) * pip

    rows = []

    for i in range(2, len(df)):
        c0 = df.iloc[i - 2]
        c1 = df.iloc[i - 1]
        c2 = df.iloc[i]

        # Displacement check on middle candle
        if require_displacement:
            body_ratio = (
                abs(c1["close"] - c1["open"]) / (c1["high"] - c1["low"])
                if (c1["high"] - c1["low"]) > 0 else 0.0
            )
            if body_ratio < MIN_DISPLACEMENT_BODY_RATIO:
                continue

        ts = c1[ts_col] if ts_col else i

        # Bullish FVG
        if c2["low"] > c0["high"]:
            size = c2["low"] - c0["high"]
            if size >= min_size:
                rows.append({
                    "candle_index": i - 1,
                    "formed_at":    ts,
                    "direction":    "BULLISH",
                    "top":          round(c2["low"],  6),
                    "bottom":       round(c0["high"], 6),
                    "midpoint":     round((c2["low"] + c0["high"]) / 2, 6),
                    "size_pips":    round(size / pip, 1),
                    "filled":       False,
                    "filled_at":    None,
                })

        # Bearish FVG
        elif c2["high"] < c0["low"]:
            size = c0["low"] - c2["high"]
            if size >= min_size:
                rows.append({
                    "candle_index": i - 1,
                    "formed_at":    ts,
                    "direction":    "BEARISH",
                    "top":          round(c0["low"],  6),
                    "bottom":       round(c2["high"], 6),
                    "midpoint":     round((c0["low"] + c2["high"]) / 2, 6),
                    "size_pips":    round(size / pip, 1),
                    "filled":       False,
                    "filled_at":    None,
                })

    if not rows:
        return pd.DataFrame(columns=[
            "candle_index", "formed_at", "direction", "top", "bottom",
            "midpoint", "size_pips", "filled", "filled_at",
        ])

    fvg_df = pd.DataFrame(rows)

    # Mark fills: walk forward price to check if gaps were later visited
    price_highs = df["high"].values
    price_lows  = df["low"].values
    price_close = df["close"].values

    for idx, row in fvg_df.iterrows():
        ci = int(row["candle_index"])
        for k in range(ci + 2, len(df)):
            if row["direction"] == "BULLISH":
                # Filled when close goes below the gap bottom
                if price_close[k] <= row["bottom"]:
                    fvg_df.at[idx, "filled"]    = True
                    fvg_df.at[idx, "filled_at"] = df.iloc[k][ts_col] if ts_col else k
                    break
            else:
                # Filled when close goes above the gap top
                if price_close[k] >= row["top"]:
                    fvg_df.at[idx, "filled"]    = True
                    fvg_df.at[idx, "filled_at"] = df.iloc[k][ts_col] if ts_col else k
                    break

    return fvg_df


# ─────────────────────────────────────────────
# CONVENIENCE BUILDER
# ─────────────────────────────────────────────

def build_fvg_states(fvgs: List[FairValueGap]) -> List[FVGState]:
    """Wrap a list of FairValueGap objects into FVGState trackers."""
    return [FVGState(fvg=fvg) for fvg in fvgs]


def active_fvgs(states: List[FVGState]) -> List[FVGState]:
    """Return only unfilled, un-expired FVG states."""
    return [s for s in states if s.is_active]
