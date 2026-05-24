"""
strategy/liquidity.py
=====================
Comprehensive Liquidity analysis.

Concepts:
  Liquidity Pool  — cluster of equal highs/lows where stop orders accumulate
                    (buy stops above equal highs, sell stops below equal lows)
  Liquidity Void  — a thin price area created by a large displacement candle;
                    price moved too fast for two-way trade → unfilled orders remain
  Liquidity Sweep — price raids a pool (takes the stops) then reverses;
                    classified by strength and type
  Sweep Quality   — CLEAN (rejection wick, hard close back) vs DIRTY (slow drift past)

ICT principle:
  "Price is always seeking liquidity."
  Institutions use price to reach stop clusters before reversing.

Market Flow of Liquidity:
  BUY-SIDE   : stops sitting ABOVE equal highs (smart money sells INTO these)
  SELL-SIDE  : stops sitting BELOW equal lows  (smart money buys  INTO these)
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
from algoTrading.ict_trading_system.data.models import Candle

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────

class LiquidityType(str, Enum):
    BUY_SIDE    = "BUY_SIDE"     # stops above equal highs
    SELL_SIDE   = "SELL_SIDE"    # stops below equal lows
    VOID_UP     = "VOID_UP"      # upward liquidity void
    VOID_DOWN   = "VOID_DOWN"    # downward liquidity void


class SweepQuality(str, Enum):
    CLEAN   = "CLEAN"    # hard wick rejection, strong close back
    DIRTY   = "DIRTY"    # slow drift, weak rejection
    FAILED  = "FAILED"   # price broke through without returning


# ─────────────────────────────────────────────
# DATA MODELS
# ─────────────────────────────────────────────

@dataclass
class LiquidityPool:
    """
    A cluster of equal highs or equal lows — stop-order concentration zone.

    strength : number of touches that formed the level (more = stronger)
    pip_range: the pip span of the cluster (how "equal" the levels are)
    """
    instrument:  Instrument
    price:       float          # representative price of the pool
    liq_type:    LiquidityType
    timeframe:   Timeframe
    formed_at:   datetime
    candle_index: int
    touches:     int  = 2       # how many swings contributed
    pip_range:   float = 0.0    # spread within the cluster
    swept:       bool  = False
    swept_at:    Optional[datetime] = None
    sweep_wick:  float = 0.0    # how far price went past the pool
    sweep_quality: Optional[SweepQuality] = None

    @property
    def is_buy_side(self) -> bool:
        return self.liq_type == LiquidityType.BUY_SIDE

    @property
    def is_sell_side(self) -> bool:
        return self.liq_type == LiquidityType.SELL_SIDE

    @property
    def strength_label(self) -> str:
        if self.touches >= 4:
            return "STRONG"
        if self.touches == 3:
            return "MODERATE"
        return "WEAK"


@dataclass
class LiquidityVoid:
    """
    A thin price area created by aggressive displacement.

    Price moved so fast that very little two-way trading occurred.
    These voids act like magnets — price tends to return to fill them.

    size_pips   : total unfilled range in pips
    fill_pct    : 0.0–1.0, how much of the void has been filled
    """
    instrument:  Instrument
    timeframe:   Timeframe
    top:         float
    bottom:      float
    direction:   Bias       # direction of the void (which way price moved)
    formed_at:   datetime
    candle_index: int
    filled:      bool  = False
    filled_at:   Optional[datetime] = None
    fill_pct:    float = 0.0

    @property
    def size(self) -> float:
        return self.top - self.bottom

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def size_pips(self) -> float:
        pip = PIP_SIZE.get(self.instrument, 0.0001)
        return self.size / pip


@dataclass
class SweepEvent:
    """
    A detected liquidity sweep (stop hunt).

    sweep_candle_idx : bar that performed the sweep
    rejected         : True if candle closed back inside (real sweep)
    continuation     : True if price HELD beyond the level (not a reversal)
    """
    instrument:       Instrument
    pool:             LiquidityPool
    sweep_candle_idx: int
    sweep_time:       datetime
    wick_extension:   float        # how far beyond the pool price went
    close_back:       bool         # did candle close back inside the pool?
    quality:          SweepQuality
    displacement_after: bool = False   # was there a displacement candle after?


# ─────────────────────────────────────────────
# LIQUIDITY POOL DETECTION
# ─────────────────────────────────────────────

def detect_liquidity_pools(
    candles: List[Candle],
    tolerance_pips: float = 3.0,
    min_touches: int = 2,
    lookback: int = 100,
) -> List[LiquidityPool]:
    """
    Find clusters of equal highs and equal lows (liquidity pools).

    Algorithm:
      1. Collect all recent swing highs and lows within `lookback`
      2. Group prices within `tolerance_pips` of each other
      3. Groups with >= `min_touches` members → LiquidityPool

    Args:
        tolerance_pips : pip range to group equal levels
        min_touches    : minimum candles at the same level
        lookback       : maximum bars to look back
    """
    if not candles:
        return []

    instrument = candles[-1].instrument
    timeframe  = candles[-1].timeframe
    pip        = PIP_SIZE.get(instrument, 0.0001)
    tol        = tolerance_pips * pip
    recent     = candles[-lookback:] if len(candles) >= lookback else candles
    offset     = max(0, len(candles) - lookback)

    pools: List[LiquidityPool] = []

    # --- Collect highs and lows ---
    all_highs = [(offset + i, c.high, c.timestamp) for i, c in enumerate(recent)]
    all_lows  = [(offset + i, c.low,  c.timestamp) for i, c in enumerate(recent)]

    # --- Cluster highs → buy-side liquidity ---
    used = set()
    for i, (ci, h, ts) in enumerate(all_highs):
        if i in used:
            continue
        cluster = [
            j for j, (_, hh, _) in enumerate(all_highs)
            if j != i and abs(hh - h) <= tol
        ]
        if len(cluster) + 1 >= min_touches:
            all_idxs = cluster + [i]
            all_prices = [all_highs[k][1] for k in all_idxs]
            rep_price = sum(all_prices) / len(all_prices)
            pools.append(LiquidityPool(
                instrument   = instrument,
                price        = rep_price,
                liq_type     = LiquidityType.BUY_SIDE,
                timeframe    = timeframe,
                formed_at    = ts,
                candle_index = ci,
                touches      = len(all_idxs),
                pip_range    = (max(all_prices) - min(all_prices)) / pip,
            ))
            used.update(all_idxs)

    # --- Cluster lows → sell-side liquidity ---
    used = set()
    for i, (ci, l, ts) in enumerate(all_lows):
        if i in used:
            continue
        cluster = [
            j for j, (_, ll, _) in enumerate(all_lows)
            if j != i and abs(ll - l) <= tol
        ]
        if len(cluster) + 1 >= min_touches:
            all_idxs = cluster + [i]
            all_prices = [all_lows[k][1] for k in all_idxs]
            rep_price = sum(all_prices) / len(all_prices)
            pools.append(LiquidityPool(
                instrument   = instrument,
                price        = rep_price,
                liq_type     = LiquidityType.SELL_SIDE,
                timeframe    = timeframe,
                formed_at    = ts,
                candle_index = ci,
                touches      = len(all_idxs),
                pip_range    = (max(all_prices) - min(all_prices)) / pip,
            ))
            used.update(all_idxs)

    # Sort by strength (touches descending)
    return sorted(pools, key=lambda p: p.touches, reverse=True)


# ─────────────────────────────────────────────
# LIQUIDITY VOID DETECTION
# ─────────────────────────────────────────────

def detect_liquidity_voids(
    candles: List[Candle],
    min_void_pips: float = 10.0,
    body_ratio_threshold: float = 0.7,
) -> List[LiquidityVoid]:
    """
    Detect Liquidity Voids — large single-candle imbalances.

    A void forms when a displacement candle moves price so aggressively
    that the previous candle's close and the next candle's open are far apart,
    leaving a zone with minimal trading activity.

    Types:
      UPWARD VOID   : fast bullish candle, gap between prior close and next open
      DOWNWARD VOID : fast bearish candle, gap between prior close and next open

    More practically in FX (no gaps): a void is identified when
    a displacement candle's body exceeds `min_void_pips` and the body_ratio >= threshold.
    The void zone = the body of that candle (area with no two-sided price action).
    """
    if len(candles) < 3:
        return []

    instrument = candles[-1].instrument
    timeframe  = candles[-1].timeframe
    pip        = PIP_SIZE.get(instrument, 0.0001)
    min_size   = min_void_pips * pip

    voids: List[LiquidityVoid] = []

    for i in range(1, len(candles) - 1):
        c = candles[i]
        if c.body_ratio < body_ratio_threshold:
            continue
        if c.body < min_size:
            continue

        if c.is_bullish:
            # Upward void: the bullish body is the void zone
            voids.append(LiquidityVoid(
                instrument   = instrument,
                timeframe    = timeframe,
                top          = c.close,
                bottom       = c.open,
                direction    = Bias.BULLISH,
                formed_at    = c.timestamp,
                candle_index = i,
            ))
        elif c.is_bearish:
            # Downward void: the bearish body is the void zone
            voids.append(LiquidityVoid(
                instrument   = instrument,
                timeframe    = timeframe,
                top          = c.open,
                bottom       = c.close,
                direction    = Bias.BEARISH,
                formed_at    = c.timestamp,
                candle_index = i,
            ))

    logger.debug(f"Detected {len(voids)} liquidity voids")
    return voids


# ─────────────────────────────────────────────
# FILL TRACKING FOR VOIDS
# ─────────────────────────────────────────────

def update_void_fills(
    voids: List[LiquidityVoid],
    candles: List[Candle],
) -> List[LiquidityVoid]:
    """
    Walk forward through candles and track fill percentage of each void.
    A void is fully filled when price trades through its entire range.
    """
    for void in voids:
        if void.filled:
            continue

        for c in candles:
            if c.timestamp <= void.formed_at:
                continue

            if void.direction == Bias.BULLISH:
                # Price retracing DOWN into void
                if c.low <= void.top:
                    fill_depth = void.top - max(c.low, void.bottom)
                    void.fill_pct = min(fill_depth / void.size, 1.0) if void.size > 0 else 0
                    if c.close <= void.bottom:
                        void.filled   = True
                        void.fill_pct = 1.0
                        void.filled_at = c.timestamp
                        break
            else:
                # Price retracing UP into void
                if c.high >= void.bottom:
                    fill_depth = min(c.high, void.top) - void.bottom
                    void.fill_pct = min(fill_depth / void.size, 1.0) if void.size > 0 else 0
                    if c.close >= void.top:
                        void.filled   = True
                        void.fill_pct = 1.0
                        void.filled_at = c.timestamp
                        break

    return voids


# ─────────────────────────────────────────────
# SWEEP DETECTION & CLASSIFICATION
# ─────────────────────────────────────────────

def detect_sweep_events(
    candles: List[Candle],
    pools: List[LiquidityPool],
    require_rejection: bool = True,
    min_wick_pips: float = 2.0,
) -> List[SweepEvent]:
    """
    Match candlestick data against known liquidity pools to detect sweeps.

    A sweep is:
      BUY-SIDE   sweep: wick ABOVE the pool price, candle CLOSES BELOW it (rejection)
      SELL-SIDE  sweep: wick BELOW the pool price, candle CLOSES ABOVE it (rejection)

    quality:
      CLEAN  : wick extended >= 2 × pool pip_range, closed decisively back
      DIRTY  : just barely pierced the level
    """
    if not candles or not pools:
        return []

    instrument = candles[-1].instrument
    pip        = PIP_SIZE.get(instrument, 0.0001)
    min_wick   = min_wick_pips * pip
    events: List[SweepEvent] = []

    for i, c in enumerate(candles):
        for pool in pools:
            if pool.swept:
                continue

            swept     = False
            close_back = False
            wick_ext  = 0.0

            if pool.is_buy_side:
                if c.high > pool.price:
                    wick_ext  = c.high - pool.price
                    close_back = c.close < pool.price
                    swept = wick_ext >= min_wick and (not require_rejection or close_back)

            else:
                if c.low < pool.price:
                    wick_ext  = pool.price - c.low
                    close_back = c.close > pool.price
                    swept = wick_ext >= min_wick and (not require_rejection or close_back)

            if swept:
                pool.swept       = True
                pool.swept_at    = c.timestamp
                pool.sweep_wick  = wick_ext

                # Classify quality
                if close_back and wick_ext >= pip * 5:
                    quality = SweepQuality.CLEAN
                elif close_back:
                    quality = SweepQuality.DIRTY
                else:
                    quality = SweepQuality.FAILED

                pool.sweep_quality = quality

                # Check for displacement in the next 3 candles
                disp_after = False
                for fc in candles[i + 1: i + 4]:
                    if fc.body_ratio >= 0.6:
                        if pool.is_buy_side and fc.is_bearish:
                            disp_after = True
                        elif pool.is_sell_side and fc.is_bullish:
                            disp_after = True
                        if disp_after:
                            break

                events.append(SweepEvent(
                    instrument        = instrument,
                    pool              = pool,
                    sweep_candle_idx  = i,
                    sweep_time        = c.timestamp,
                    wick_extension    = wick_ext,
                    close_back        = close_back,
                    quality           = quality,
                    displacement_after = disp_after,
                ))

                logger.info(
                    f"Liquidity sweep [{quality.value}] {pool.liq_type.value} "
                    f"@ {pool.price:.5f} at {c.timestamp} | "
                    f"wick={wick_ext/pip:.1f}pip | disp={disp_after}"
                )

    return events


# ─────────────────────────────────────────────
# STANDALONE DATAFRAME INTERFACES
# ─────────────────────────────────────────────

def scan_liquidity_pools(
    df: pd.DataFrame,
    instrument: Instrument,
    tolerance_pips: float = 3.0,
    min_touches: int = 2,
    lookback: int = 100,
) -> pd.DataFrame:
    """
    Scan OHLCV DataFrame for liquidity pools.

    Returns DataFrame with:
        price, liq_type, touches, pip_range, strength,
        formed_at, candle_index, swept, swept_at
    """
    ts_col = next(
        (c for c in ("time", "timestamp", "datetime", "date") if c in df.columns),
        None
    )
    if df.empty or len(df) < 4:
        return pd.DataFrame()

    pip = PIP_SIZE.get(instrument, 0.0001)
    tol = tolerance_pips * pip

    highs  = df["high"].values
    lows   = df["low"].values
    n      = len(df)
    start  = max(0, n - lookback)

    rows = []

    def _cluster(vals, liq_type_str):
        used = set()
        for i in range(len(vals)):
            if i in used:
                continue
            cluster = [j for j in range(len(vals)) if j != i and abs(vals[j] - vals[i]) <= tol]
            if len(cluster) + 1 >= min_touches:
                all_idx    = cluster + [i]
                all_v      = [vals[k] for k in all_idx]
                rep        = sum(all_v) / len(all_v)
                max_ci     = start + max(all_idx)
                ts         = df.iloc[max_ci][ts_col] if ts_col else max_ci
                rows.append({
                    "candle_index": max_ci,
                    "formed_at":    ts,
                    "liq_type":     liq_type_str,
                    "price":        round(rep, 6),
                    "touches":      len(all_idx),
                    "pip_range":    round((max(all_v) - min(all_v)) / pip, 1),
                    "strength":     "STRONG" if len(all_idx) >= 4 else ("MODERATE" if len(all_idx) == 3 else "WEAK"),
                    "swept":        False,
                    "swept_at":     None,
                })
                used.update(all_idx)

    window_highs = highs[start:].tolist()
    window_lows  = lows[start:].tolist()

    _cluster(window_highs, "BUY_SIDE")
    _cluster(window_lows,  "SELL_SIDE")

    # Forward pass: mark swept
    closes = df["close"].values
    for row in rows:
        ci    = int(row["candle_index"])
        price = row["price"]
        for k in range(ci + 1, n):
            if row["liq_type"] == "BUY_SIDE":
                if highs[k] > price and closes[k] < price:
                    row["swept"]    = True
                    row["swept_at"] = df.iloc[k][ts_col] if ts_col else k
                    break
            else:
                if lows[k] < price and closes[k] > price:
                    row["swept"]    = True
                    row["swept_at"] = df.iloc[k][ts_col] if ts_col else k
                    break

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("candle_index").reset_index(drop=True)


def scan_liquidity_voids(
    df: pd.DataFrame,
    instrument: Instrument,
    min_void_pips: float = 10.0,
    body_ratio_threshold: float = 0.70,
) -> pd.DataFrame:
    """
    Scan OHLCV DataFrame for liquidity voids.

    Returns DataFrame with:
        candle_index, timestamp, direction, top, bottom,
        midpoint, size_pips, fill_pct, filled, filled_at
    """
    ts_col = next(
        (c for c in ("time", "timestamp", "datetime", "date") if c in df.columns),
        None
    )
    if df.empty or len(df) < 3:
        return pd.DataFrame()

    pip      = PIP_SIZE.get(instrument, 0.0001)
    min_size = min_void_pips * pip
    opens    = df["open"].values
    closes   = df["close"].values
    highs    = df["high"].values
    lows     = df["low"].values
    n        = len(df)

    rows = []

    for i in range(1, n - 1):
        rng  = highs[i] - lows[i]
        body = abs(closes[i] - opens[i])
        if rng == 0 or body / rng < body_ratio_threshold or body < min_size:
            continue

        is_bull = closes[i] > opens[i]
        top     = closes[i] if is_bull else opens[i]
        bot     = opens[i]  if is_bull else closes[i]
        ts      = df.iloc[i][ts_col] if ts_col else i

        rows.append({
            "candle_index": i,
            "timestamp":    ts,
            "direction":    "BULLISH" if is_bull else "BEARISH",
            "top":          round(top, 6),
            "bottom":       round(bot, 6),
            "midpoint":     round((top + bot) / 2, 6),
            "size_pips":    round(body / pip, 1),
            "fill_pct":     0.0,
            "filled":       False,
            "filled_at":    None,
        })

    if not rows:
        return pd.DataFrame()

    void_df = pd.DataFrame(rows)

    # Forward fill tracking
    for idx, row in void_df.iterrows():
        ci     = int(row["candle_index"])
        top    = row["top"]
        bot    = row["bottom"]
        is_b   = row["direction"] == "BULLISH"
        size   = top - bot
        best_fill = 0.0

        for k in range(ci + 1, n):
            if is_b:
                if lows[k] <= top:
                    depth = top - max(lows[k], bot)
                    fill  = min(depth / size, 1.0) if size > 0 else 0
                    best_fill = max(best_fill, fill)
                    if closes[k] <= bot:
                        void_df.at[idx, "filled"]    = True
                        void_df.at[idx, "filled_at"] = df.iloc[k][ts_col] if ts_col else k
                        void_df.at[idx, "fill_pct"]  = 1.0
                        break
            else:
                if highs[k] >= bot:
                    depth = min(highs[k], top) - bot
                    fill  = min(depth / size, 1.0) if size > 0 else 0
                    best_fill = max(best_fill, fill)
                    if closes[k] >= top:
                        void_df.at[idx, "filled"]    = True
                        void_df.at[idx, "filled_at"] = df.iloc[k][ts_col] if ts_col else k
                        void_df.at[idx, "fill_pct"]  = 1.0
                        break

        if not void_df.at[idx, "filled"]:
            void_df.at[idx, "fill_pct"] = round(best_fill, 2)

    return void_df
