"""
strategy/ict_models.py
======================
ICT Buy / Sell Models — the composition engine.

This module combines every ICT concept into structured, checklist-driven
trade models. Each model is a sequence of checks. The number of checks
passed determines confidence. Models can be configured to require
certain concepts and treat others as optional confluence.

MODEL ARCHITECTURE
──────────────────
Each ICTModel subclass defines:
  REQUIRED_CHECKS  : must all pass for a signal to fire
  OPTIONAL_CHECKS  : each adds to confidence score
  MIN_SCORE        : minimum total score to produce a signal

Built-in models:
  ICTClassicBuyModel      — Sweep lows → Displacement → FVG → BOS
  ICTClassicSellModel     — Sweep highs → Displacement → FVG → BOS
  ICTOTEBuyModel          — Impulse → OTE retrace → OB/FVG confluence
  ICTOTESellModel         — Impulse → OTE retrace → OB/FVG confluence
  ICTBreakerBuyModel      — Failed bearish OB → Breaker retest → entry
  ICTBreakerSellModel     — Failed bullish OB → Breaker retest → entry
  ICTJudasBuyModel        — London false breakout below → bullish reversal
  ICTJudasSellModel       — London false breakout above → bearish reversal
  ICTAMDModel             — AMD phase: Manipulation → Distribution entry
  ICTMasterModel          — All concepts combined, highest bar

CONCEPT FLAGS (for ConceptResult)
──────────────────────────────────
  htf_bias, liq_pool_identified, liq_swept, displacement,
  bos_confirmed, choch_confirmed, mss_confirmed, fvg_present,
  ob_present, breaker_present, ote_zone, premium_discount,
  kill_zone, smt_divergence, amd_phase, inversion_fvg,
  mitigation_block, judas_swing
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from algoTrading.ict_trading_system.settings import (
    Bias, Instrument, Session, StrategyID, Timeframe,
    PIP_SIZE, MIN_DISPLACEMENT_BODY_RATIO,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# CONCEPT CHECK RESULT
# ─────────────────────────────────────────────

@dataclass
class ConceptResult:
    """
    Result of a single ICT concept check.
    Composable — multiple ConceptResults build an ICTChecklist.
    """
    concept:     str
    passed:      bool
    weight:      int   = 1       # scoring weight (some concepts worth more)
    price_level: float = 0.0
    details:     str   = ""
    data:        Any   = None    # optional raw object (FVG, OB, etc.)

    def __repr__(self):
        mark = "✓" if self.passed else "✗"
        return f"[{mark}] {self.concept:<30} {self.details}"


@dataclass
class ICTChecklist:
    """
    Collection of ConceptResults for one trade evaluation.

    score   : sum of weights for passed checks
    total   : sum of all weights
    passed  : True if all REQUIRED checks passed and score >= min_score
    """
    results:    List[ConceptResult] = field(default_factory=list)
    min_score:  int = 5

    @property
    def score(self) -> int:
        return sum(r.weight for r in self.results if r.passed)

    @property
    def total(self) -> int:
        return sum(r.weight for r in self.results)

    @property
    def confidence(self) -> float:
        return round(self.score / self.total, 2) if self.total > 0 else 0.0

    @property
    def passed(self) -> bool:
        return self.score >= self.min_score

    def add(self, result: ConceptResult):
        self.results.append(result)

    def get(self, concept: str) -> Optional[ConceptResult]:
        return next((r for r in self.results if r.concept == concept), None)

    def summary(self) -> str:
        lines = [f"ICT Checklist — {self.score}/{self.total} ({'PASS' if self.passed else 'FAIL'})"]
        for r in self.results:
            lines.append(f"  {r}")
        return "\n".join(lines)


# ─────────────────────────────────────────────
# TRADE SETUP
# ─────────────────────────────────────────────

@dataclass
class ICTSetup:
    """
    A complete ICT trade setup with entry levels and checklist.

    This is the output of every ICTModel.evaluate() call.
    """
    model:        str
    instrument:   Instrument
    direction:    Bias
    entry:        float
    sl:           float
    tp1:          float
    tp2:          float
    checklist:    ICTChecklist
    session:      Session
    timestamp:    datetime
    notes:        str = ""

    @property
    def rr_ratio(self) -> float:
        risk   = abs(self.entry - self.sl)
        reward = abs(self.tp1 - self.entry)
        return round(reward / risk, 2) if risk > 0 else 0.0

    @property
    def confidence(self) -> float:
        return self.checklist.confidence

    @property
    def is_long(self) -> bool:
        return self.direction == Bias.BULLISH

    def __repr__(self):
        d = "BUY" if self.is_long else "SELL"
        return (
            f"ICTSetup({self.model} | {self.instrument.value} {d} "
            f"@ {self.entry:.5f} | SL {self.sl:.5f} TP {self.tp1:.5f} "
            f"RR 1:{self.rr_ratio} | Conf {self.confidence:.0%})"
        )


# ─────────────────────────────────────────────
# BASE MODEL
# ─────────────────────────────────────────────

class ICTModel(ABC):
    """
    Abstract base for all ICT trade models.

    Each model defines a list of ConceptChecks that are evaluated
    against a DataFrame window. The evaluate() method returns
    an ICTSetup or None.
    """
    name:       str
    min_score:  int = 5

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def evaluate(
        self,
        df:          pd.DataFrame,
        instrument:  Instrument,
        timeframe:   Timeframe,
        current_idx: int,
        session:     Session,
        htf_df:      Optional[pd.DataFrame] = None,
    ) -> Optional[ICTSetup]:
        """
        Evaluate the model at bar `current_idx`.

        df          : entry-timeframe OHLCV DataFrame
        instrument  : traded instrument
        timeframe   : entry timeframe
        current_idx : current bar index
        session     : current trading session
        htf_df      : optional higher-timeframe DataFrame for bias
        """
        ...

    # ── Shared helpers ──────────────────────────────────────────────

    def _supertrend_trend(self, df: pd.DataFrame, period: int = 10, mult: float = 3.0) -> np.ndarray:
        """Compute Supertrend direction array. +1=bullish, -1=bearish."""
        c = df["close"].values
        h = df["high"].values
        l = df["low"].values
        n = len(df)
        hl2 = (h + l) / 2
        prev_c = np.concatenate([[c[0]], c[:-1]])
        tr = np.maximum.reduce([h - l, np.abs(h - prev_c), np.abs(l - prev_c)])
        atr = pd.Series(tr).ewm(alpha=1/period, min_periods=period, adjust=False).mean().values
        bu  = hl2 + mult * atr
        bl  = hl2 - mult * atr
        fu  = bu.copy()
        fl  = bl.copy()
        for i in range(1, n):
            if np.isnan(bl[i]):
                continue
            fl[i] = bl[i] if (np.isnan(fl[i-1]) or bl[i] > fl[i-1] or c[i-1] < fl[i-1]) else fl[i-1]
            fu[i] = bu[i] if (np.isnan(fu[i-1]) or bu[i] < fu[i-1] or c[i-1] > fu[i-1]) else fu[i-1]
        trend = -np.ones(n, dtype=int)
        for i in range(1, n):
            if np.isnan(fu[i]):
                trend[i] = trend[i-1]
                continue
            trend[i] = (-1 if c[i] < fl[i] else 1) if trend[i-1] == 1 else (1 if c[i] > fu[i] else -1)
        return trend

    def _find_swing_high(self, df: pd.DataFrame, lookback: int, end_idx: int) -> float:
        start = max(0, end_idx - lookback)
        return float(df["high"].iloc[start:end_idx].max())

    def _find_swing_low(self, df: pd.DataFrame, lookback: int, end_idx: int) -> float:
        start = max(0, end_idx - lookback)
        return float(df["low"].iloc[start:end_idx].min())

    def _is_displacement(self, row: pd.Series) -> bool:
        rng = row["high"] - row["low"]
        return (abs(row["close"] - row["open"]) / rng >= MIN_DISPLACEMENT_BODY_RATIO) if rng > 0 else False

    def _find_fvg(
        self,
        df: pd.DataFrame,
        start_idx: int,
        end_idx: int,
        direction: Bias,
    ) -> Optional[Tuple[float, float, int]]:
        """
        Scan [start_idx, end_idx] for first FVG matching `direction`.
        Returns (top, bottom, candle_index) or None.
        """
        pip        = PIP_SIZE.get(Instrument.EURUSD, 0.0001)   # default; overridden per instrument
        h = df["high"].values
        l = df["low"].values
        o = df["open"].values
        c = df["close"].values

        for i in range(max(2, start_idx), min(end_idx, len(df))):
            body_ratio = abs(c[i-1] - o[i-1]) / (h[i-1] - l[i-1]) if (h[i-1] - l[i-1]) > 0 else 0
            if body_ratio < MIN_DISPLACEMENT_BODY_RATIO:
                continue
            if direction == Bias.BULLISH and l[i] > h[i-2]:
                return (l[i], h[i-2], i)
            if direction == Bias.BEARISH and h[i] < l[i-2]:
                return (l[i-2], h[i], i)
        return None

    def _build_setup(
        self,
        model: str,
        instrument: Instrument,
        direction: Bias,
        entry: float,
        sl: float,
        tp1_rr: float,
        tp2_rr: float,
        checklist: ICTChecklist,
        session: Session,
        timestamp: datetime,
        notes: str = "",
    ) -> Optional[ICTSetup]:
        risk = abs(entry - sl)
        if risk <= 0:
            return None
        tp1 = entry + tp1_rr * risk if direction == Bias.BULLISH else entry - tp1_rr * risk
        tp2 = entry + tp2_rr * risk if direction == Bias.BULLISH else entry - tp2_rr * risk
        return ICTSetup(
            model=model, instrument=instrument, direction=direction,
            entry=entry, sl=sl, tp1=tp1, tp2=tp2,
            checklist=checklist, session=session, timestamp=timestamp, notes=notes,
        )


# ─────────────────────────────────────────────
# MODEL 1: ICT CLASSIC BUY
# ─────────────────────────────────────────────

class ICTClassicBuyModel(ICTModel):
    """
    Classic ICT bullish reversal:
      HTF trend UP → sell-side liq swept → displacement → FVG → BOS
    """
    name = "ICT_CLASSIC_BUY"

    def __init__(self, liq_lookback=30, fvg_window=8, sweep_window=5,
                 rr1=2.0, rr2=3.0, htf_period=10, htf_mult=3.0):
        super().__init__()
        self.liq_lookback = liq_lookback
        self.fvg_window   = fvg_window
        self.sweep_window = sweep_window
        self.rr1 = rr1
        self.rr2 = rr2
        self.htf_period = htf_period
        self.htf_mult   = htf_mult

    def evaluate(self, df, instrument, timeframe, current_idx, session, htf_df=None):
        if current_idx < self.liq_lookback + 5:
            return None

        chk = ICTChecklist(min_score=self.min_score)
        pip = PIP_SIZE.get(instrument, 0.0001)

        # 1. HTF bias ─────────────────────────────────────────────
        htf_bias = Bias.NEUTRAL
        if htf_df is not None and len(htf_df) > self.htf_period + 2:
            htf_trend = self._supertrend_trend(htf_df, self.htf_period, self.htf_mult)
            htf_bias  = Bias.BULLISH if htf_trend[-1] == 1 else Bias.BEARISH
        chk.add(ConceptResult(
            "htf_bias", htf_bias == Bias.BULLISH, weight=2,
            details=f"HTF={htf_bias.value}",
        ))

        # 2. Price in discount ────────────────────────────────────
        lb_start = max(0, current_idx - self.liq_lookback)
        s_high   = float(df["high"].iloc[lb_start:current_idx].max())
        s_low    = float(df["low"].iloc[lb_start:current_idx].min())
        eq       = (s_high + s_low) / 2
        price    = float(df["close"].iloc[current_idx])
        in_discount = price < eq
        chk.add(ConceptResult(
            "premium_discount", in_discount, weight=1,
            details=f"EQ={eq:.5f} price={'DISCOUNT' if in_discount else 'PREMIUM'}",
        ))

        # 3. Kill zone ────────────────────────────────────────────
        ts_col = next((c for c in ("time","timestamp","datetime","date") if c in df.columns), None)
        in_kz  = session in (Session.LONDON, Session.NEW_YORK)
        chk.add(ConceptResult("kill_zone", in_kz, weight=1, details=f"session={session.value}"))

        # 4. Liquidity sweep ──────────────────────────────────────
        swing_lo  = self._find_swing_low(df, self.liq_lookback, current_idx)
        swept_lo  = False
        sweep_bar = -1
        for k in range(max(0, current_idx - self.liq_lookback), current_idx):
            row = df.iloc[k]
            if row["low"] < swing_lo and row["close"] > swing_lo:
                swept_lo  = True
                sweep_bar = k
                sweep_extreme = row["low"]
                break

        chk.add(ConceptResult(
            "liq_swept", swept_lo, weight=2,
            details=f"swing_lo={swing_lo:.5f}" + (f" swept@bar{sweep_bar}" if swept_lo else ""),
        ))

        if not swept_lo:
            if not chk.passed:
                return None

        # 5. Displacement after sweep ─────────────────────────────
        disp_found = False
        disp_bar   = -1
        if swept_lo and sweep_bar >= 0:
            for k in range(sweep_bar, min(sweep_bar + self.sweep_window, current_idx + 1)):
                row = df.iloc[k]
                if row["close"] > row["open"] and self._is_displacement(row):
                    disp_found = True
                    disp_bar   = k
                    break
        chk.add(ConceptResult(
            "displacement", disp_found, weight=2,
            details=f"disp_bar={disp_bar}" if disp_found else "no displacement",
        ))

        # 6. BOS (close above prior swing high) ───────────────────
        prior_hi  = self._find_swing_high(df, self.liq_lookback, current_idx - 5)
        bos_found = float(df["close"].iloc[current_idx]) > prior_hi
        chk.add(ConceptResult(
            "bos_confirmed", bos_found, weight=1,
            details=f"prior_hi={prior_hi:.5f}",
        ))

        # 7. FVG present ──────────────────────────────────────────
        fvg_start = max(0, sweep_bar) if swept_lo else max(0, current_idx - self.fvg_window)
        fvg_res   = self._find_fvg(df, fvg_start, current_idx + 1, Bias.BULLISH)
        chk.add(ConceptResult(
            "fvg_present", fvg_res is not None, weight=2,
            details=f"FVG [{fvg_res[1]:.5f}–{fvg_res[0]:.5f}]" if fvg_res else "no FVG",
            data=fvg_res,
        ))

        self.logger.debug(chk.summary())
        if not chk.passed:
            return None

        # Build levels
        fvg_top  = fvg_res[0] if fvg_res else price
        fvg_bot  = fvg_res[1] if fvg_res else price
        entry    = (fvg_top + fvg_bot) / 2
        sl_raw   = sweep_extreme if swept_lo else self._find_swing_low(df, 10, current_idx)
        sl_buf   = (entry - sl_raw) * 0.1
        sl       = sl_raw - sl_buf

        ts = df.iloc[current_idx][ts_col] if ts_col else datetime.utcnow()

        return self._build_setup(
            self.name, instrument, Bias.BULLISH,
            entry, sl, self.rr1, self.rr2, chk, session, ts,
            notes=f"Classic Buy | swept {swing_lo:.5f} | FVG [{fvg_bot:.5f}–{fvg_top:.5f}]",
        )


# ─────────────────────────────────────────────
# MODEL 2: ICT CLASSIC SELL
# ─────────────────────────────────────────────

class ICTClassicSellModel(ICTModel):
    """
    Classic ICT bearish reversal:
      HTF trend DOWN → buy-side liq swept → displacement → FVG → BOS
    """
    name = "ICT_CLASSIC_SELL"

    def __init__(self, liq_lookback=30, fvg_window=8, sweep_window=5,
                 rr1=2.0, rr2=3.0, htf_period=10, htf_mult=3.0):
        super().__init__()
        self.liq_lookback = liq_lookback
        self.fvg_window   = fvg_window
        self.sweep_window = sweep_window
        self.rr1 = rr1
        self.rr2 = rr2
        self.htf_period = htf_period
        self.htf_mult   = htf_mult

    def evaluate(self, df, instrument, timeframe, current_idx, session, htf_df=None):
        if current_idx < self.liq_lookback + 5:
            return None

        chk = ICTChecklist(min_score=self.min_score)
        pip = PIP_SIZE.get(instrument, 0.0001)

        # 1. HTF bias
        htf_bias = Bias.NEUTRAL
        if htf_df is not None and len(htf_df) > self.htf_period + 2:
            htf_trend = self._supertrend_trend(htf_df, self.htf_period, self.htf_mult)
            htf_bias  = Bias.BULLISH if htf_trend[-1] == 1 else Bias.BEARISH
        chk.add(ConceptResult("htf_bias", htf_bias == Bias.BEARISH, weight=2,
                              details=f"HTF={htf_bias.value}"))

        # 2. Price in premium
        lb_start   = max(0, current_idx - self.liq_lookback)
        s_high     = float(df["high"].iloc[lb_start:current_idx].max())
        s_low      = float(df["low"].iloc[lb_start:current_idx].min())
        eq         = (s_high + s_low) / 2
        price      = float(df["close"].iloc[current_idx])
        in_premium = price > eq
        chk.add(ConceptResult("premium_discount", in_premium, weight=1,
                              details=f"EQ={eq:.5f} price={'PREMIUM' if in_premium else 'DISCOUNT'}"))

        # 3. Kill zone
        in_kz = session in (Session.LONDON, Session.NEW_YORK)
        chk.add(ConceptResult("kill_zone", in_kz, weight=1, details=f"session={session.value}"))

        # 4. Buy-side sweep
        swing_hi      = self._find_swing_high(df, self.liq_lookback, current_idx)
        swept_hi      = False
        sweep_bar     = -1
        sweep_extreme = 0.0
        for k in range(max(0, current_idx - self.liq_lookback), current_idx):
            row = df.iloc[k]
            if row["high"] > swing_hi and row["close"] < swing_hi:
                swept_hi      = True
                sweep_bar     = k
                sweep_extreme = row["high"]
                break
        chk.add(ConceptResult("liq_swept", swept_hi, weight=2,
                              details=f"swing_hi={swing_hi:.5f}" + (f" swept@{sweep_bar}" if swept_hi else "")))

        # 5. Displacement after sweep
        disp_found = False
        disp_bar   = -1
        if swept_hi and sweep_bar >= 0:
            for k in range(sweep_bar, min(sweep_bar + self.sweep_window, current_idx + 1)):
                row = df.iloc[k]
                if row["close"] < row["open"] and self._is_displacement(row):
                    disp_found = True
                    disp_bar   = k
                    break
        chk.add(ConceptResult("displacement", disp_found, weight=2))

        # 6. BOS
        prior_lo  = self._find_swing_low(df, self.liq_lookback, current_idx - 5)
        bos_found = float(df["close"].iloc[current_idx]) < prior_lo
        chk.add(ConceptResult("bos_confirmed", bos_found, weight=1))

        # 7. Bearish FVG
        fvg_start = max(0, sweep_bar) if swept_hi else max(0, current_idx - self.fvg_window)
        fvg_res   = self._find_fvg(df, fvg_start, current_idx + 1, Bias.BEARISH)
        chk.add(ConceptResult("fvg_present", fvg_res is not None, weight=2, data=fvg_res))

        self.logger.debug(chk.summary())
        if not chk.passed:
            return None

        fvg_top  = fvg_res[0] if fvg_res else price
        fvg_bot  = fvg_res[1] if fvg_res else price
        entry    = (fvg_top + fvg_bot) / 2
        sl_raw   = sweep_extreme if swept_hi else self._find_swing_high(df, 10, current_idx)
        sl_buf   = (sl_raw - entry) * 0.1
        sl       = sl_raw + sl_buf

        ts_col = next((c for c in ("time","timestamp","datetime","date") if c in df.columns), None)
        ts     = df.iloc[current_idx][ts_col] if ts_col else datetime.utcnow()

        return self._build_setup(
            self.name, instrument, Bias.BEARISH,
            entry, sl, self.rr1, self.rr2, chk, session, ts,
            notes=f"Classic Sell | swept {swing_hi:.5f} | FVG [{fvg_bot:.5f}–{fvg_top:.5f}]",
        )


# ─────────────────────────────────────────────
# MODEL 3: ICT OTE BUY
# ─────────────────────────────────────────────

class ICTOTEBuyModel(ICTModel):
    """
    ICT OTE (Optimal Trade Entry) buy model.
    Impulse high → retrace to 62–79% → entry at 70.5% sweet spot
    with OB or FVG confluence in the zone.
    """
    name = "ICT_OTE_BUY"

    def __init__(self, lookback=50, rr1=2.0, rr2=3.0):
        super().__init__()
        self.lookback = lookback
        self.rr1 = rr1
        self.rr2 = rr2
        self.min_score = 4

    def evaluate(self, df, instrument, timeframe, current_idx, session, htf_df=None):
        if current_idx < self.lookback + 5:
            return None

        chk   = ICTChecklist(min_score=self.min_score)
        lb    = max(0, current_idx - self.lookback)
        price = float(df["close"].iloc[current_idx])
        sh    = float(df["high"].iloc[lb:current_idx].max())
        sl    = float(df["low"].iloc[lb:current_idx].min())
        rng   = sh - sl
        if rng == 0:
            return None

        hi_idx = df["high"].iloc[lb:current_idx].idxmax()
        lo_idx = df["low"].iloc[lb:current_idx].idxmin()
        hi_pos = df.index.get_loc(hi_idx)
        lo_pos = df.index.get_loc(lo_idx)

        # Impulse was UP → expect retrace DOWN into discount
        is_bull_setup = hi_pos > lo_pos
        chk.add(ConceptResult("htf_bias", is_bull_setup, weight=1,
                              details="Impulse direction: UP" if is_bull_setup else "No bullish impulse"))
        if not is_bull_setup:
            return None

        # OTE zone: 62–79% retrace from high
        ote_hi    = sh - rng * 0.620
        ote_lo    = sh - rng * 0.790
        sweet     = sh - rng * 0.705
        in_ote    = ote_lo <= price <= ote_hi
        chk.add(ConceptResult("ote_zone", in_ote, weight=2,
                              details=f"OTE [{ote_lo:.5f}–{ote_hi:.5f}] price={price:.5f}"))

        # Premium / discount
        eq = (sh + sl) / 2
        chk.add(ConceptResult("premium_discount", price < eq, weight=1,
                              details=f"{'DISCOUNT' if price < eq else 'PREMIUM'}"))

        # FVG in zone
        fvg_res = self._find_fvg(df, lb, current_idx + 1, Bias.BULLISH)
        fvg_in_zone = False
        if fvg_res:
            fvg_in_zone = ote_lo <= fvg_res[1] <= ote_hi or ote_lo <= fvg_res[0] <= ote_hi
        chk.add(ConceptResult("fvg_present", fvg_in_zone, weight=2,
                              details="FVG inside OTE" if fvg_in_zone else "no FVG in zone"))

        # Kill zone
        in_kz = session in (Session.LONDON, Session.NEW_YORK)
        chk.add(ConceptResult("kill_zone", in_kz, weight=1))

        self.logger.debug(chk.summary())
        if not chk.passed:
            return None

        entry  = sweet if in_ote else ote_lo
        sl_raw = sl - rng * 0.05
        ts_col = next((c for c in ("time","timestamp","datetime","date") if c in df.columns), None)
        ts     = df.iloc[current_idx][ts_col] if ts_col else datetime.utcnow()

        return self._build_setup(
            self.name, instrument, Bias.BULLISH,
            entry, sl_raw, self.rr1, self.rr2, chk, session, ts,
            notes=f"OTE Buy | sweet={sweet:.5f} OTE=[{ote_lo:.5f}–{ote_hi:.5f}]",
        )


# ─────────────────────────────────────────────
# MODEL 4: ICT OTE SELL
# ─────────────────────────────────────────────

class ICTOTESellModel(ICTModel):
    """ICT OTE sell model — impulse down → retrace up → sell at 70.5%."""
    name = "ICT_OTE_SELL"

    def __init__(self, lookback=50, rr1=2.0, rr2=3.0):
        super().__init__()
        self.lookback = lookback
        self.rr1 = rr1
        self.rr2 = rr2
        self.min_score = 4

    def evaluate(self, df, instrument, timeframe, current_idx, session, htf_df=None):
        if current_idx < self.lookback + 5:
            return None

        chk   = ICTChecklist(min_score=self.min_score)
        lb    = max(0, current_idx - self.lookback)
        price = float(df["close"].iloc[current_idx])
        sh    = float(df["high"].iloc[lb:current_idx].max())
        sl    = float(df["low"].iloc[lb:current_idx].min())
        rng   = sh - sl
        if rng == 0:
            return None

        hi_pos = df.index.get_loc(df["high"].iloc[lb:current_idx].idxmax())
        lo_pos = df.index.get_loc(df["low"].iloc[lb:current_idx].idxmin())
        is_bear_setup = lo_pos > hi_pos   # impulse was DOWN

        chk.add(ConceptResult("htf_bias", is_bear_setup, weight=1,
                              details="Impulse direction: DOWN" if is_bear_setup else "No bearish impulse"))
        if not is_bear_setup:
            return None

        # OTE zone: 62–79% retrace FROM low (upward)
        ote_lo    = sl + rng * 0.620
        ote_hi    = sl + rng * 0.790
        sweet     = sl + rng * 0.705
        in_ote    = ote_lo <= price <= ote_hi
        chk.add(ConceptResult("ote_zone", in_ote, weight=2,
                              details=f"OTE [{ote_lo:.5f}–{ote_hi:.5f}]"))
        eq = (sh + sl) / 2
        chk.add(ConceptResult("premium_discount", price > eq, weight=1))

        fvg_res     = self._find_fvg(df, lb, current_idx + 1, Bias.BEARISH)
        fvg_in_zone = False
        if fvg_res:
            fvg_in_zone = ote_lo <= fvg_res[1] <= ote_hi or ote_lo <= fvg_res[0] <= ote_hi
        chk.add(ConceptResult("fvg_present", fvg_in_zone, weight=2))
        chk.add(ConceptResult("kill_zone", session in (Session.LONDON, Session.NEW_YORK), weight=1))

        if not chk.passed:
            return None

        entry  = sweet if in_ote else ote_hi
        sl_raw = sh + rng * 0.05
        ts_col = next((c for c in ("time","timestamp","datetime","date") if c in df.columns), None)
        ts     = df.iloc[current_idx][ts_col] if ts_col else datetime.utcnow()

        return self._build_setup(
            self.name, instrument, Bias.BEARISH,
            entry, sl_raw, self.rr1, self.rr2, chk, session, ts,
            notes=f"OTE Sell | sweet={sweet:.5f}",
        )


# ─────────────────────────────────────────────
# MODEL 5: ICT AMD (POWER OF THREE)
# ─────────────────────────────────────────────

class ICTAMDModel(ICTModel):
    """
    AMD (Accumulation → Manipulation → Distribution) trade model.

    Detects the Manipulation phase (Judas swing) and enters during
    the Distribution phase in the TRUE direction.
    """
    name = "ICT_AMD"

    def __init__(self, session_lookback=50, amd_atr_mult=2.0, rr1=2.0, rr2=3.5):
        super().__init__()
        self.session_lookback = session_lookback
        self.amd_atr_mult     = amd_atr_mult
        self.rr1 = rr1
        self.rr2 = rr2
        self.min_score = 4

    def evaluate(self, df, instrument, timeframe, current_idx, session, htf_df=None):
        if current_idx < self.session_lookback + 5:
            return None

        chk   = ICTChecklist(min_score=self.min_score)
        lb    = max(0, current_idx - self.session_lookback)
        price = float(df["close"].iloc[current_idx])
        h_arr = df["high"].values
        l_arr = df["low"].values
        c_arr = df["close"].values
        o_arr = df["open"].values

        # Session open (approximate: first bar in lookback window)
        sess_open   = float(df["open"].iloc[lb])
        window_high = float(df["high"].iloc[lb:current_idx].max())
        window_low  = float(df["low"].iloc[lb:current_idx].min())
        avg_range   = float((df["high"].iloc[lb:current_idx] - df["low"].iloc[lb:current_idx]).mean())

        # Detect AMD phase
        phase     = "ACCUMULATION"
        dist_dir  = None
        manip_h   = None
        manip_l   = None

        for k in range(lb, current_idx):
            rng = h_arr[k] - l_arr[k]
            if phase == "ACCUMULATION":
                if h_arr[k] > window_high * 0.998 and c_arr[k] < window_high:
                    phase   = "MANIPULATION"
                    manip_h = h_arr[k]
                elif l_arr[k] < window_low * 1.002 and c_arr[k] > window_low:
                    phase   = "MANIPULATION"
                    manip_l = l_arr[k]
            elif phase == "MANIPULATION":
                if c_arr[k] > window_high and rng >= avg_range * self.amd_atr_mult:
                    phase    = "DISTRIBUTION"
                    dist_dir = Bias.BULLISH
                    break
                elif c_arr[k] < window_low and rng >= avg_range * self.amd_atr_mult:
                    phase    = "DISTRIBUTION"
                    dist_dir = Bias.BEARISH
                    break

        chk.add(ConceptResult("amd_phase", phase == "DISTRIBUTION", weight=3,
                              details=f"Phase={phase} dir={dist_dir}"))

        if dist_dir is None:
            return None

        chk.add(ConceptResult("kill_zone", session in (Session.LONDON, Session.NEW_YORK), weight=1))
        chk.add(ConceptResult("liq_swept", manip_h is not None or manip_l is not None, weight=2,
                              details=f"manip_h={manip_h} manip_l={manip_l}"))

        # FVG in distribution direction
        fvg_res = self._find_fvg(df, lb, current_idx + 1, dist_dir)
        chk.add(ConceptResult("fvg_present", fvg_res is not None, weight=2))

        if not chk.passed:
            return None

        fvg_top  = fvg_res[0] if fvg_res else price
        fvg_bot  = fvg_res[1] if fvg_res else price
        entry    = (fvg_top + fvg_bot) / 2

        if dist_dir == Bias.BULLISH:
            sl_raw = (manip_l or window_low) - avg_range * 0.2
        else:
            sl_raw = (manip_h or window_high) + avg_range * 0.2

        ts_col = next((c for c in ("time","timestamp","datetime","date") if c in df.columns), None)
        ts     = df.iloc[current_idx][ts_col] if ts_col else datetime.utcnow()

        return self._build_setup(
            self.name, instrument, dist_dir,
            entry, sl_raw, self.rr1, self.rr2, chk, session, ts,
            notes=f"AMD | {phase} → {dist_dir.value if dist_dir else 'N/A'}",
        )


# ─────────────────────────────────────────────
# MODEL 6: ICT MASTER (all concepts)
# ─────────────────────────────────────────────

class ICTMasterModel(ICTModel):
    """
    Master ICT model combining ALL concepts.
    Requires 8/12 checks to pass.

    Top-down:
      W/D bias → H4 flow → M15 setup → M5 entry
    """
    name = "ICT_MASTER"

    def __init__(self, lookback=50, rr1=2.0, rr2=4.0, htf_period=10, htf_mult=3.0):
        super().__init__()
        self.lookback   = lookback
        self.rr1        = rr1
        self.rr2        = rr2
        self.htf_period = htf_period
        self.htf_mult   = htf_mult
        self.min_score  = 8

    def evaluate(self, df, instrument, timeframe, current_idx, session, htf_df=None):
        if current_idx < self.lookback + 5:
            return None

        chk   = ICTChecklist(min_score=self.min_score)
        lb    = max(0, current_idx - self.lookback)
        price = float(df["close"].iloc[current_idx])
        sh    = float(df["high"].iloc[lb:current_idx].max())
        sl_p  = float(df["low"].iloc[lb:current_idx].min())
        rng   = sh - sl_p
        eq    = (sh + sl_p) / 2

        # 1. HTF bias (weight 2)
        htf_bias = Bias.NEUTRAL
        if htf_df is not None and len(htf_df) > self.htf_period + 2:
            ht = self._supertrend_trend(htf_df, self.htf_period, self.htf_mult)
            htf_bias = Bias.BULLISH if ht[-1] == 1 else Bias.BEARISH
        bias_pass = htf_bias != Bias.NEUTRAL
        direction = htf_bias if bias_pass else (Bias.BULLISH if price < eq else Bias.BEARISH)
        chk.add(ConceptResult("htf_bias", bias_pass, weight=2, details=f"{htf_bias.value}"))

        # 2. Premium/discount alignment (weight 1)
        aligned_pd = (direction == Bias.BULLISH and price < eq) or \
                     (direction == Bias.BEARISH and price > eq)
        chk.add(ConceptResult("premium_discount", aligned_pd, weight=1))

        # 3. Kill zone (weight 1)
        in_kz = session in (Session.LONDON, Session.NEW_YORK)
        chk.add(ConceptResult("kill_zone", in_kz, weight=1))

        # 4. Liquidity sweep (weight 2)
        swing_ref = self._find_swing_low(df, self.lookback, current_idx) if direction == Bias.BULLISH \
                    else self._find_swing_high(df, self.lookback, current_idx)
        swept = False
        sweep_extreme = 0.0
        sweep_bar     = -1
        for k in range(lb, current_idx):
            row = df.iloc[k]
            if direction == Bias.BULLISH:
                if row["low"] < swing_ref and row["close"] > swing_ref:
                    swept = True; sweep_extreme = row["low"]; sweep_bar = k; break
            else:
                if row["high"] > swing_ref and row["close"] < swing_ref:
                    swept = True; sweep_extreme = row["high"]; sweep_bar = k; break
        chk.add(ConceptResult("liq_swept", swept, weight=2,
                              details=f"swept={swept} bar={sweep_bar}"))

        # 5. Displacement (weight 2)
        disp   = False
        disp_b = -1
        if swept and sweep_bar >= 0:
            for k in range(sweep_bar, min(sweep_bar + 5, current_idx + 1)):
                row = df.iloc[k]
                bull_ok = direction == Bias.BULLISH and row["close"] > row["open"]
                bear_ok = direction == Bias.BEARISH and row["close"] < row["open"]
                if (bull_ok or bear_ok) and self._is_displacement(row):
                    disp = True; disp_b = k; break
        chk.add(ConceptResult("displacement", disp, weight=2))

        # 6. BOS/MSS (weight 2)
        bos = False
        if direction == Bias.BULLISH:
            prior_hi = self._find_swing_high(df, self.lookback, current_idx - 3)
            bos = price > prior_hi
        else:
            prior_lo = self._find_swing_low(df, self.lookback, current_idx - 3)
            bos = price < prior_lo
        chk.add(ConceptResult("bos_confirmed", bos, weight=2))

        # 7. FVG (weight 2)
        fvg_start = max(0, sweep_bar) if swept else lb
        fvg_res   = self._find_fvg(df, fvg_start, current_idx + 1, direction)
        chk.add(ConceptResult("fvg_present", fvg_res is not None, weight=2, data=fvg_res))

        # 8. OTE zone (weight 1)
        if direction == Bias.BULLISH:
            ote_lo = sh - rng * 0.790; ote_hi = sh - rng * 0.620
        else:
            ote_lo = sl_p + rng * 0.620; ote_hi = sl_p + rng * 0.790
        in_ote = ote_lo <= price <= ote_hi
        chk.add(ConceptResult("ote_zone", in_ote, weight=1))

        self.logger.info(chk.summary())
        if not chk.passed:
            return None

        # Build levels
        fvg_top = fvg_res[0] if fvg_res else price
        fvg_bot = fvg_res[1] if fvg_res else price
        entry   = (fvg_top + fvg_bot) / 2

        if direction == Bias.BULLISH:
            sl_raw = (sweep_extreme if swept else sl_p) - rng * 0.05
        else:
            sl_raw = (sweep_extreme if swept else sh) + rng * 0.05

        ts_col = next((c for c in ("time","timestamp","datetime","date") if c in df.columns), None)
        ts     = df.iloc[current_idx][ts_col] if ts_col else datetime.utcnow()

        return self._build_setup(
            self.name, instrument, direction,
            entry, sl_raw, self.rr1, self.rr2, chk, session, ts,
            notes=f"MASTER {direction.value} | conf={chk.confidence:.0%}",
        )


# ─────────────────────────────────────────────
# MODEL REGISTRY
# ─────────────────────────────────────────────

_DEFAULT_MODELS: Dict[str, ICTModel] = {}


def register_model(model: ICTModel):
    _DEFAULT_MODELS[model.name] = model


def get_model(name: str) -> Optional[ICTModel]:
    return _DEFAULT_MODELS.get(name)


def build_default_registry(
    liq_lookback: int = 30,
    fvg_window: int = 8,
    rr1: float = 2.0,
    rr2: float = 3.0,
) -> Dict[str, ICTModel]:
    """Create and return a default model registry with all built-in models."""
    registry = {
        "ICT_CLASSIC_BUY":  ICTClassicBuyModel(liq_lookback, fvg_window, rr1=rr1, rr2=rr2),
        "ICT_CLASSIC_SELL": ICTClassicSellModel(liq_lookback, fvg_window, rr1=rr1, rr2=rr2),
        "ICT_OTE_BUY":      ICTOTEBuyModel(liq_lookback, rr1=rr1, rr2=rr2),
        "ICT_OTE_SELL":     ICTOTESellModel(liq_lookback, rr1=rr1, rr2=rr2),
        "ICT_AMD":          ICTAMDModel(liq_lookback, rr1=rr1, rr2=rr2),
        "ICT_MASTER":       ICTMasterModel(liq_lookback, rr1=rr1, rr2=rr2),
    }
    return registry


# ─────────────────────────────────────────────
# SCAN INTERFACE (DataFrame-based)
# ─────────────────────────────────────────────

def scan_ict_models(
    df: pd.DataFrame,
    instrument: Instrument,
    timeframe: Timeframe,
    htf_df: Optional[pd.DataFrame] = None,
    models: Optional[List[str]] = None,
    liq_lookback: int = 30,
    fvg_window: int = 8,
    rr1: float = 2.0,
    rr2: float = 3.0,
) -> pd.DataFrame:
    """
    Scan an OHLCV DataFrame with all (or specified) ICT models.

    Returns a DataFrame with one row per signal:
        bar_index, timestamp, model, direction, entry, sl, tp1, tp2,
        rr_ratio, confidence, score, total, notes
    """
    ts_col   = next((c for c in ("time","timestamp","datetime","date") if c in df.columns), None)
    registry = build_default_registry(liq_lookback, fvg_window, rr1, rr2)
    active   = {k: v for k, v in registry.items() if (models is None or k in models)}

    rows = []
    for i in range(50, len(df)):
        # Determine session from timestamp
        session = Session.DEAD
        if ts_col:
            try:
                ts = pd.to_datetime(df.iloc[i][ts_col], utc=True)
                h  = ts.hour
                session = (Session.LONDON if 7 <= h < 12 else
                           Session.NEW_YORK if 13 <= h < 17 else
                           Session.ASIAN if 0 <= h < 7 else Session.DEAD)
            except Exception:
                pass

        for model_name, model in active.items():
            setup = model.evaluate(df, instrument, timeframe, i, session, htf_df)
            if setup:
                rows.append({
                    "bar_index":  i,
                    "timestamp":  df.iloc[i][ts_col] if ts_col else i,
                    "model":      setup.model,
                    "direction":  setup.direction.value,
                    "entry":      round(setup.entry,  6),
                    "sl":         round(setup.sl,     6),
                    "tp1":        round(setup.tp1,    6),
                    "tp2":        round(setup.tp2,    6),
                    "rr_ratio":   setup.rr_ratio,
                    "confidence": setup.confidence,
                    "score":      setup.checklist.score,
                    "total":      setup.checklist.total,
                    "notes":      setup.notes,
                })

    if not rows:
        return pd.DataFrame(columns=[
            "bar_index","timestamp","model","direction","entry","sl","tp1","tp2",
            "rr_ratio","confidence","score","total","notes",
        ])
    return pd.DataFrame(rows)
