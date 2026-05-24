"""
strategy/top_down.py
====================
Top Down Analysis — the highest layer of the ICT system.

Aggregates all ICT concept scans across multiple timeframes (H4, H1, M15, M5)
and produces a unified trade bias with confluence scoring.

TIMEFRAME CASCADE (ICT Top Down)
─────────────────────────────────
  HTF (H4 / Daily): determine macro bias — is price Premium or Discount?
                    identify key liquidity pools, order blocks, sweep targets
  MTF (H1 / M15):  confirm structure shift (CHoCH / MSS), AMD phase,
                    kill zone alignment, Judas swing
  LTF (M5):        precision entry — FVG, OTE, displacement confirmation,
                    inversion FVG retests

TOP DOWN LOGIC
──────────────
  1. HTF bias sets the trade direction (LONG or SHORT)
  2. MTF confirms institutional intent (AMD phase, sweep of opposing liquidity)
  3. LTF gives entry timing (FVG / OTE / displacement)
  4. All three timeframes must agree — confluence is scored 0–100

OUTPUTS
───────
  TimeframeAnalysis   — full ICT scan result for a single timeframe
  TopDownContext      — aggregated HTF + MTF + LTF analyses
  TradeOpportunity    — actionable setup with direction, levels, confluence
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from algoTrading.ict_trading_system.settings import (
    Bias, Instrument, Session, Timeframe,
    PIP_SIZE,
)
from algoTrading.ict_trading_system.strategy.market_structure import (
    scan_market_structure,
)
from algoTrading.ict_trading_system.strategy.liquidity import (
    scan_liquidity_pools, scan_liquidity_voids,
)
from algoTrading.ict_trading_system.strategy.fvg import scan_fvgs
from algoTrading.ict_trading_system.strategy.ob import scan_order_blocks
from algoTrading.ict_trading_system.strategy.ote import scan_ote_zones
from algoTrading.ict_trading_system.strategy.sessions import (
    add_session_labels, scan_judas_swings,
)
from algoTrading.ict_trading_system.strategy.displacement import scan_displacements
from algoTrading.ict_trading_system.strategy.dealing_range import (
    scan_pd_zones, scan_amd_phases,
)
from algoTrading.ict_trading_system.strategy.inversion import (
    scan_inversion_fvgs, scan_mitigation_blocks,
)
from algoTrading.ict_trading_system.strategy.ict_models import (
    ICTSetup, scan_ict_models,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# TIMEFRAME ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TimeframeAnalysis:
    """
    Complete ICT analysis snapshot for a single timeframe.

    All scan_* DataFrames are stored here. Properties distill them
    into the single most-important value for top-down logic.
    """
    timeframe:    Timeframe
    instrument:   Instrument
    df:           pd.DataFrame          # raw OHLCV with session labels added

    # ── Scan results ─────────────────────────────────────────────────────────
    structure_df:    pd.DataFrame = field(default_factory=pd.DataFrame)
    liquidity_df:    pd.DataFrame = field(default_factory=pd.DataFrame)
    voids_df:        pd.DataFrame = field(default_factory=pd.DataFrame)
    fvg_df:          pd.DataFrame = field(default_factory=pd.DataFrame)
    ob_df:           pd.DataFrame = field(default_factory=pd.DataFrame)
    ote_df:          pd.DataFrame = field(default_factory=pd.DataFrame)
    judas_df:        pd.DataFrame = field(default_factory=pd.DataFrame)
    displacement_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    pd_zone_df:      pd.DataFrame = field(default_factory=pd.DataFrame)
    amd_df:          pd.DataFrame = field(default_factory=pd.DataFrame)
    inversion_fvg_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    mitigation_df:   pd.DataFrame = field(default_factory=pd.DataFrame)
    model_signals_df: pd.DataFrame = field(default_factory=pd.DataFrame)

    # ── Derived summaries (populated by analyze_timeframe) ───────────────────
    bias:         Bias  = Bias.NEUTRAL
    flow_phase:   str   = "UNKNOWN"     # TRENDING / RANGING / TRANSITIONING
    amd_phase:    str   = "UNKNOWN"     # ACCUMULATION / MANIPULATION / DISTRIBUTION
    current_zone: str   = "UNKNOWN"     # PREMIUM / DISCOUNT / EQUILIBRIUM
    last_structure_break: str = ""      # "BOS_BULL", "CHOCH_BEAR", etc.
    swept_buyside:  bool = False
    swept_sellside: bool = False
    active_fvgs_count:  int = 0
    active_obs_count:   int = 0
    key_highs:  List[float] = field(default_factory=list)
    key_lows:   List[float] = field(default_factory=list)

    @property
    def current_price(self) -> float:
        return float(self.df["close"].iloc[-1]) if not self.df.empty else 0.0

    @property
    def has_active_fvg(self) -> bool:
        if self.fvg_df.empty:
            return False
        return bool((~self.fvg_df["filled"]).any())

    @property
    def has_unswept_buyside(self) -> bool:
        if self.liquidity_df.empty:
            return False
        mask = (self.liquidity_df["liq_type"] == "BUY_SIDE") & (~self.liquidity_df["swept"])
        return bool(mask.any())

    @property
    def has_unswept_sellside(self) -> bool:
        if self.liquidity_df.empty:
            return False
        mask = (self.liquidity_df["liq_type"] == "SELL_SIDE") & (~self.liquidity_df["swept"])
        return bool(mask.any())

    @property
    def nearest_buyside_liq(self) -> Optional[float]:
        if self.liquidity_df.empty:
            return None
        mask = (self.liquidity_df["liq_type"] == "BUY_SIDE") & (~self.liquidity_df["swept"])
        sub = self.liquidity_df[mask]
        if sub.empty:
            return None
        above = sub[sub["price"] > self.current_price]
        if above.empty:
            return None
        return float(above["price"].min())

    @property
    def nearest_sellside_liq(self) -> Optional[float]:
        if self.liquidity_df.empty:
            return None
        mask = (self.liquidity_df["liq_type"] == "SELL_SIDE") & (~self.liquidity_df["swept"])
        sub = self.liquidity_df[mask]
        if sub.empty:
            return None
        below = sub[sub["price"] < self.current_price]
        if below.empty:
            return None
        return float(below["price"].max())

    def summary(self) -> str:
        lines = [
            f"[{self.timeframe.value}] {self.instrument.value} @ {self.current_price:.5f}",
            f"  Bias={self.bias.value}  Flow={self.flow_phase}  AMD={self.amd_phase}  Zone={self.current_zone}",
            f"  StructBreak={self.last_structure_break}  SweptBS={self.swept_buyside}  SweptSS={self.swept_sellside}",
            f"  ActiveFVGs={self.active_fvgs_count}  ActiveOBs={self.active_obs_count}",
        ]
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# TOP DOWN CONTEXT
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TopDownContext:
    """
    Aggregated analysis across HTF → MTF → LTF.

    The three tiers follow ICT's top-down cascade:
      HTF (H4/Daily)   : macro direction + key liquidity targets
      MTF (H1/M15)     : institutional intent confirmation
      LTF (M5/M1)      : entry precision
    """
    instrument: Instrument
    htf: Optional[TimeframeAnalysis] = None    # H4 or Daily
    mtf: Optional[TimeframeAnalysis] = None    # H1 or M15
    ltf: Optional[TimeframeAnalysis] = None    # M5 or M1
    extra: Dict[str, TimeframeAnalysis] = field(default_factory=dict)

    @property
    def all_analyses(self) -> List[TimeframeAnalysis]:
        result = []
        if self.htf:
            result.append(self.htf)
        if self.mtf:
            result.append(self.mtf)
        if self.ltf:
            result.append(self.ltf)
        result.extend(self.extra.values())
        return result

    @property
    def macro_bias(self) -> Bias:
        """HTF bias is the anchor — always use this as the trade direction."""
        return self.htf.bias if self.htf else Bias.NEUTRAL

    @property
    def aligned(self) -> bool:
        """True when HTF, MTF, and LTF all agree on direction."""
        biases = [a.bias for a in self.all_analyses if a.bias != Bias.NEUTRAL]
        if len(biases) < 2:
            return False
        return len(set(b.value for b in biases)) == 1

    @property
    def confluence_score(self) -> int:
        """0–100 score: how many timeframes agree with the HTF bias."""
        if not self.htf or self.macro_bias == Bias.NEUTRAL:
            return 0
        target = self.macro_bias
        matches = sum(1 for a in self.all_analyses if a.bias == target)
        total   = len(self.all_analyses)
        return round(100 * matches / total) if total > 0 else 0

    def summary(self) -> str:
        lines = [
            f"=== Top Down Context: {self.instrument.value} ===",
            f"  MacroBias={self.macro_bias.value}  Aligned={self.aligned}  Confluence={self.confluence_score}%",
        ]
        for a in self.all_analyses:
            lines.append(a.summary())
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# TRADE OPPORTUNITY
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TradeOpportunity:
    """
    Actionable trade setup produced by top-down analysis.

    Includes entry/SL/TP levels, a confluence score (0–100),
    and the underlying ICTSetup from the LTF model scan.
    """
    instrument:   Instrument
    direction:    Bias
    entry:        float
    sl:           float
    tp1:          float
    tp2:          float
    confluence:   int           # 0-100
    timeframes:   List[str]     # aligned timeframes, e.g. ["H4", "H1", "M5"]
    timestamp:    datetime
    model:        str           # which ICT model triggered
    ltf_setup:    Optional[ICTSetup] = None
    notes:        str = ""

    @property
    def rr_ratio(self) -> float:
        risk   = abs(self.entry - self.sl)
        reward = abs(self.tp1 - self.entry)
        return round(reward / risk, 2) if risk > 0 else 0.0

    @property
    def is_long(self) -> bool:
        return self.direction == Bias.BULLISH

    def __repr__(self):
        return (
            f"TradeOpportunity({self.instrument.value} {self.direction.value} "
            f"@ {self.entry:.5f} SL={self.sl:.5f} TP1={self.tp1:.5f} "
            f"RR={self.rr_ratio} Conf={self.confluence}%)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# SINGLE TIMEFRAME ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

def analyze_timeframe(
    df: pd.DataFrame,
    instrument: Instrument,
    timeframe: Timeframe,
    run_models: bool = False,
) -> TimeframeAnalysis:
    """
    Run all ICT concept scans on a single OHLCV DataFrame.

    Parameters
    ----------
    df          : OHLCV DataFrame with datetime index
    instrument  : instrument enum
    timeframe   : timeframe enum (used as label in scan outputs)
    run_models  : if True, also run the full ICTModel scan (slower)

    Returns
    -------
    TimeframeAnalysis with all scan DataFrames populated and
    derived bias/phase properties set.
    """
    if df is None or df.empty or len(df) < 20:
        return TimeframeAnalysis(timeframe=timeframe, instrument=instrument, df=pd.DataFrame())

    df = df.copy()
    df = add_session_labels(df)

    ta = TimeframeAnalysis(timeframe=timeframe, instrument=instrument, df=df)

    # ── Market structure ──────────────────────────────────────────────────────
    try:
        ta.structure_df = scan_market_structure(df, instrument)
        if not ta.structure_df.empty:
            last = ta.structure_df.dropna(subset=["flow_bias"]).iloc[-1] if "flow_bias" in ta.structure_df.columns else None
            if last is not None:
                bias_str = str(last.get("flow_bias", "NEUTRAL")).upper()
                ta.bias = Bias.BULLISH if "BULL" in bias_str else (Bias.BEARISH if "BEAR" in bias_str else Bias.NEUTRAL)
                ta.flow_phase = str(last.get("flow_phase", "UNKNOWN")).upper()
            if "structure_break" in ta.structure_df.columns:
                breaks = ta.structure_df["structure_break"].dropna()
                ta.last_structure_break = str(breaks.iloc[-1]) if not breaks.empty else ""
    except Exception as e:
        logger.debug("structure scan failed: %s", e)

    # ── Liquidity pools & voids ───────────────────────────────────────────────
    pip = PIP_SIZE.get(instrument, 0.0001)
    tol_pips = 3.0 if pip < 0.01 else 30.0
    void_pips = 10.0 if pip < 0.01 else 100.0

    try:
        ta.liquidity_df = scan_liquidity_pools(df, instrument, tolerance_pips=tol_pips)
        if not ta.liquidity_df.empty:
            ta.swept_buyside  = bool((ta.liquidity_df["liq_type"] == "BUY_SIDE"  ) & (ta.liquidity_df["swept"])).any()
            ta.swept_sellside = bool((ta.liquidity_df["liq_type"] == "SELL_SIDE" ) & (ta.liquidity_df["swept"])).any()
    except Exception as e:
        logger.debug("liquidity pool scan failed: %s", e)

    try:
        ta.voids_df = scan_liquidity_voids(df, instrument, min_void_pips=void_pips)
    except Exception as e:
        logger.debug("liquidity void scan failed: %s", e)

    # ── FVG ───────────────────────────────────────────────────────────────────
    try:
        ta.fvg_df = scan_fvgs(df, instrument, timeframe)
        if not ta.fvg_df.empty:
            ta.active_fvgs_count = int((~ta.fvg_df["filled"]).sum())
    except Exception as e:
        logger.debug("fvg scan failed: %s", e)

    # ── Order blocks ──────────────────────────────────────────────────────────
    try:
        ta.ob_df = scan_order_blocks(df, instrument, timeframe)
        if not ta.ob_df.empty:
            ta.active_obs_count = int((~ta.ob_df["mitigated"]).sum()) if "mitigated" in ta.ob_df.columns else 0
    except Exception as e:
        logger.debug("ob scan failed: %s", e)

    # ── OTE zones ─────────────────────────────────────────────────────────────
    try:
        ta.ote_df = scan_ote_zones(df, instrument)
    except Exception as e:
        logger.debug("ote scan failed: %s", e)

    # ── Judas swing ───────────────────────────────────────────────────────────
    try:
        ta.judas_df = scan_judas_swings(df, instrument, timeframe)
    except Exception as e:
        logger.debug("judas scan failed: %s", e)

    # ── Displacement ──────────────────────────────────────────────────────────
    try:
        ta.displacement_df = scan_displacements(df, instrument)
    except Exception as e:
        logger.debug("displacement scan failed: %s", e)

    # ── Premium / Discount zones & AMD ───────────────────────────────────────
    try:
        ta.pd_zone_df = scan_pd_zones(df, instrument)
        if not ta.pd_zone_df.empty and "zone_class" in ta.pd_zone_df.columns:
            ta.current_zone = str(ta.pd_zone_df["zone_class"].iloc[-1]).upper()
    except Exception as e:
        logger.debug("pd zone scan failed: %s", e)

    try:
        ta.amd_df = scan_amd_phases(df, instrument)
        if not ta.amd_df.empty and "phase" in ta.amd_df.columns:
            ta.amd_phase = str(ta.amd_df["phase"].iloc[-1]).upper()
    except Exception as e:
        logger.debug("amd scan failed: %s", e)

    # ── Inversion FVGs & Mitigation blocks ───────────────────────────────────
    try:
        if not ta.fvg_df.empty:
            ta.inversion_fvg_df = scan_inversion_fvgs(ta.fvg_df, df)
    except Exception as e:
        logger.debug("inversion fvg scan failed: %s", e)

    try:
        if not ta.ob_df.empty:
            ta.mitigation_df = scan_mitigation_blocks(ta.ob_df, df)
    except Exception as e:
        logger.debug("mitigation block scan failed: %s", e)

    # ── Key structure levels ──────────────────────────────────────────────────
    try:
        if not ta.structure_df.empty and "swing_price" in ta.structure_df.columns:
            highs = ta.structure_df[ta.structure_df.get("is_high", pd.Series(False, index=ta.structure_df.index))]["swing_price"]
            lows  = ta.structure_df[~ta.structure_df.get("is_high", pd.Series(True, index=ta.structure_df.index))]["swing_price"]
            ta.key_highs = list(highs.dropna().tail(5))
            ta.key_lows  = list(lows.dropna().tail(5))
    except Exception as e:
        logger.debug("key levels extraction failed: %s", e)

    # ── ICT model signals (optional, slow) ────────────────────────────────────
    if run_models:
        try:
            ta.model_signals_df = scan_ict_models(df, instrument, timeframe)
        except Exception as e:
            logger.debug("model scan failed: %s", e)

    return ta


# ─────────────────────────────────────────────────────────────────────────────
# BUILD TOP DOWN CONTEXT
# ─────────────────────────────────────────────────────────────────────────────

def build_top_down_context(
    dfs_by_timeframe: Dict[str, pd.DataFrame],
    instrument: Instrument,
    run_ltf_models: bool = False,
) -> TopDownContext:
    """
    Build a complete top-down ICT analysis from multiple timeframe DataFrames.

    Parameters
    ----------
    dfs_by_timeframe : dict mapping timeframe key → OHLCV DataFrame
        Recognized keys (case-insensitive):
          HTF: "D1", "H4", "H8"
          MTF: "H1", "M30", "M15"
          LTF: "M5", "M1"
    instrument       : Instrument enum
    run_ltf_models   : run full ICT model scan on LTF (slower)

    Returns
    -------
    TopDownContext with htf, mtf, ltf populated.
    """
    # Normalize keys
    normalized: Dict[str, pd.DataFrame] = {k.upper(): v for k, v in dfs_by_timeframe.items()}

    HTF_KEYS = ["D1", "H4"]
    MTF_KEYS = ["H1", "M15"]
    LTF_KEYS = ["M5", "M1"]

    TF_MAP = {
        "D1": Timeframe.D1,
        "H4": Timeframe.H4,
        "H1": Timeframe.H1,
        "M15": Timeframe.M15,
        "M5":  Timeframe.M5,
        "M1":  Timeframe.M1,
    }

    ctx = TopDownContext(instrument=instrument)

    def _pick(keys: List[str]) -> Optional[Tuple[str, pd.DataFrame]]:
        for k in keys:
            if k in normalized and not normalized[k].empty:
                return k, normalized[k]
        return None

    # ── HTF ───────────────────────────────────────────────────────────────────
    htf_pick = _pick(HTF_KEYS)
    if htf_pick:
        key, df = htf_pick
        tf = TF_MAP.get(key, Timeframe.H4)
        logger.info("TopDown: analyzing HTF=%s (%d bars)", key, len(df))
        ctx.htf = analyze_timeframe(df, instrument, tf, run_models=False)

    # ── MTF ───────────────────────────────────────────────────────────────────
    mtf_pick = _pick(MTF_KEYS)
    if mtf_pick:
        key, df = mtf_pick
        tf = TF_MAP.get(key, Timeframe.H1)
        logger.info("TopDown: analyzing MTF=%s (%d bars)", key, len(df))
        ctx.mtf = analyze_timeframe(df, instrument, tf, run_models=False)

    # ── LTF ───────────────────────────────────────────────────────────────────
    ltf_pick = _pick(LTF_KEYS)
    if ltf_pick:
        key, df = ltf_pick
        tf = TF_MAP.get(key, Timeframe.M5)
        logger.info("TopDown: analyzing LTF=%s (%d bars)", key, len(df))
        ctx.ltf = analyze_timeframe(df, instrument, tf, run_models=run_ltf_models)

    # ── Extra timeframes ──────────────────────────────────────────────────────
    known = set(HTF_KEYS + MTF_KEYS + LTF_KEYS)
    for k, df in normalized.items():
        if k not in known:
            tf = TF_MAP.get(k, Timeframe.M5)
            ctx.extra[k] = analyze_timeframe(df, instrument, tf, run_models=False)

    logger.info("TopDown: %s  MacroBias=%s  Aligned=%s  Confluence=%d%%",
                instrument.value, ctx.macro_bias.value, ctx.aligned, ctx.confluence_score)
    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# TRADE BIAS EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

def get_trade_bias(context: TopDownContext) -> Tuple[Bias, int, List[str]]:
    """
    Extract the final trade direction from a TopDownContext.

    Returns
    -------
    (bias, confluence_score, reasons)
      bias             : Bias.BULLISH / Bias.BEARISH / Bias.NEUTRAL
      confluence_score : 0–100
      reasons          : list of human-readable reason strings
    """
    reasons: List[str] = []
    bias_votes: Dict[str, int] = {"BULL": 0, "BEAR": 0}

    for ta in context.all_analyses:
        tf_label = ta.timeframe.value

        if ta.bias == Bias.BULLISH:
            bias_votes["BULL"] += 2
            reasons.append(f"{tf_label}: bullish structure bias")
        elif ta.bias == Bias.BEARISH:
            bias_votes["BEAR"] += 2
            reasons.append(f"{tf_label}: bearish structure bias")

        if ta.swept_sellside and ta.bias == Bias.BULLISH:
            bias_votes["BULL"] += 1
            reasons.append(f"{tf_label}: sell-side liquidity swept (bullish confirmation)")

        if ta.swept_buyside and ta.bias == Bias.BEARISH:
            bias_votes["BEAR"] += 1
            reasons.append(f"{tf_label}: buy-side liquidity swept (bearish confirmation)")

        if ta.current_zone in ("DISCOUNT", "DEEP_DISCOUNT") and ta.bias == Bias.BULLISH:
            bias_votes["BULL"] += 1
            reasons.append(f"{tf_label}: price in discount zone (good for longs)")

        if ta.current_zone in ("PREMIUM", "DEEP_PREMIUM") and ta.bias == Bias.BEARISH:
            bias_votes["BEAR"] += 1
            reasons.append(f"{tf_label}: price in premium zone (good for shorts)")

        if ta.amd_phase == "DISTRIBUTION":
            if ta.bias == Bias.BULLISH:
                bias_votes["BULL"] += 1
                reasons.append(f"{tf_label}: AMD distribution phase (bull direction)")
            elif ta.bias == Bias.BEARISH:
                bias_votes["BEAR"] += 1
                reasons.append(f"{tf_label}: AMD distribution phase (bear direction)")

    total = bias_votes["BULL"] + bias_votes["BEAR"]
    if total == 0:
        return Bias.NEUTRAL, 0, ["No confluence found"]

    if bias_votes["BULL"] > bias_votes["BEAR"]:
        score = round(100 * bias_votes["BULL"] / total)
        return Bias.BULLISH, score, reasons
    elif bias_votes["BEAR"] > bias_votes["BULL"]:
        score = round(100 * bias_votes["BEAR"] / total)
        return Bias.BEARISH, score, reasons
    else:
        return Bias.NEUTRAL, 50, reasons + ["Equal bull/bear votes — no clear bias"]


# ─────────────────────────────────────────────────────────────────────────────
# TRADE OPPORTUNITY FINDER
# ─────────────────────────────────────────────────────────────────────────────

def find_trade_opportunities(
    context: TopDownContext,
    min_confluence: int = 60,
    rr1: float = 2.0,
    rr2: float = 3.0,
) -> List[TradeOpportunity]:
    """
    Convert a TopDownContext into actionable TradeOpportunity objects.

    Uses the LTF model_signals_df (requires run_ltf_models=True in
    build_top_down_context) and filters by HTF/MTF alignment.

    Parameters
    ----------
    context        : TopDownContext from build_top_down_context()
    min_confluence : minimum confluence score to keep a setup (default 60%)
    rr1, rr2       : risk-reward targets for TP1 and TP2

    Returns
    -------
    List of TradeOpportunity, sorted by confluence descending.
    """
    bias, score, _ = get_trade_bias(context)

    if bias == Bias.NEUTRAL or score < min_confluence:
        return []

    opportunities: List[TradeOpportunity] = []

    # ── Use LTF model signals if available ───────────────────────────────────
    ltf = context.ltf
    if ltf is not None and not ltf.model_signals_df.empty:
        sig_df = ltf.model_signals_df
        # filter by top-down bias direction
        dir_filter = bias.value.upper()   # "BULLISH" or "BEARISH"
        sig_df = sig_df[sig_df["direction"].str.upper() == dir_filter]

        for _, row in sig_df.iterrows():
            opp = TradeOpportunity(
                instrument  = context.instrument,
                direction   = bias,
                entry       = float(row["entry"]),
                sl          = float(row["sl"]),
                tp1         = float(row.get("tp1", 0.0)),
                tp2         = float(row.get("tp2", 0.0)),
                confluence  = score,
                timeframes  = [a.timeframe.value for a in context.all_analyses if a.bias == bias],
                timestamp   = row.get("timestamp", datetime.utcnow()),
                model       = str(row.get("model", "unknown")),
                notes       = str(row.get("notes", "")),
            )
            opportunities.append(opp)

    # ── Fallback: derive setup from LTF FVG if no model signals ──────────────
    if not opportunities and ltf is not None and not ltf.fvg_df.empty:
        pip = PIP_SIZE.get(context.instrument, 0.0001)
        active = ltf.fvg_df[~ltf.fvg_df["filled"]]
        dir_str = bias.value.upper()   # "BULLISH" or "BEARISH"
        active = active[active["direction"] == dir_str]

        for _, fvg_row in active.tail(3).iterrows():
            entry  = float(fvg_row["midpoint"])
            bottom = float(fvg_row["bottom"])
            top    = float(fvg_row["top"])

            if bias == Bias.BULLISH:
                sl  = bottom - 5 * pip
                tp1 = entry + rr1 * abs(entry - sl)
                tp2 = entry + rr2 * abs(entry - sl)
            else:
                sl  = top + 5 * pip
                tp1 = entry - rr1 * abs(entry - sl)
                tp2 = entry - rr2 * abs(entry - sl)

            opp = TradeOpportunity(
                instrument  = context.instrument,
                direction   = bias,
                entry       = entry,
                sl          = sl,
                tp1         = tp1,
                tp2         = tp2,
                confluence  = score,
                timeframes  = [a.timeframe.value for a in context.all_analyses if a.bias == bias],
                timestamp   = datetime.utcnow(),
                model       = "FVG_fallback",
                notes       = f"LTF FVG at {fvg_row.get('formed_at', '')}",
            )
            opportunities.append(opp)

    opportunities.sort(key=lambda x: x.confluence, reverse=True)
    return opportunities


# ─────────────────────────────────────────────────────────────────────────────
# DATAFRAME INTERFACE
# ─────────────────────────────────────────────────────────────────────────────

def scan_top_down(
    df_dict: Dict[str, pd.DataFrame],
    instrument: Instrument,
    run_ltf_models: bool = False,
    min_confluence: int = 60,
    rr1: float = 2.0,
    rr2: float = 3.0,
) -> pd.DataFrame:
    """
    Run a full top-down ICT scan and return a summary DataFrame.

    Parameters
    ----------
    df_dict         : {"H4": df_h4, "H1": df_h1, "M5": df_m5, ...}
    instrument      : Instrument enum
    run_ltf_models  : run full model scan on LTF (slower, needed for signals)
    min_confluence  : filter threshold for opportunities
    rr1, rr2        : risk-reward targets

    Returns
    -------
    DataFrame with columns:
      timestamp, instrument, direction, entry, sl, tp1, tp2,
      rr_ratio, confluence, timeframes, model, notes
    """
    ctx = build_top_down_context(df_dict, instrument, run_ltf_models=run_ltf_models)
    opportunities = find_trade_opportunities(ctx, min_confluence=min_confluence, rr1=rr1, rr2=rr2)

    if not opportunities:
        return pd.DataFrame(columns=[
            "timestamp", "instrument", "direction", "entry", "sl",
            "tp1", "tp2", "rr_ratio", "confluence", "timeframes", "model", "notes",
        ])

    rows = []
    for opp in opportunities:
        rows.append({
            "timestamp":  opp.timestamp,
            "instrument": opp.instrument.value,
            "direction":  opp.direction.value,
            "entry":      opp.entry,
            "sl":         opp.sl,
            "tp1":        opp.tp1,
            "tp2":        opp.tp2,
            "rr_ratio":   opp.rr_ratio,
            "confluence": opp.confluence,
            "timeframes": ",".join(opp.timeframes),
            "model":      opp.model,
            "notes":      opp.notes,
        })

    return pd.DataFrame(rows)
