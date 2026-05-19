"""
strategy/strategies.py
======================
Implements all 10 ICT strategies + the Master Combined Model.
Each strategy is a class with a .evaluate() method that returns
a TradeSignal or None.

Strategies:
  S1  — Beginner ICT Model
  S2  — OTE + MSS Model
  S3  — London Judas Swing Reversal
  S4  — SMT Divergence Reversal
  S5  — AMD Cycle Model
  S6  — Gold (XAUUSD) Master Model
  S7  — NASDAQ Opening Drive
  S8  — EURUSD London Reversal
  S9  — GBPUSD Volatility Raid
  S10 — Breaker Block Model
  S_M — Master Combined Model
"""

from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from config.settings import (
    Bias, Instrument, Session, StrategyID, Timeframe,
    OTE_FIBO_LOW, OTE_FIBO_HIGH, STRATEGY_INSTRUMENTS,
    SessionConfig
)
from data.models import (
    Candle, FairValueGap, LiquidityLevel, MarketStructure,
    MarketStructureShift, OrderBlock, PremiumDiscountZone,
    SMTDivergence, TradeSignal
)
from data.market_data import detect_session
from strategy.ict_concepts import (
    analyze_market_structure, detect_mss, detect_fvg,
    detect_order_blocks, detect_breaker_blocks, compute_premium_discount,
    detect_equal_highs_lows, detect_smt_divergence, is_displacement_candle,
    detect_liquidity_sweep, detect_amd_phase
)

logger = logging.getLogger(__name__)
_sess_cfg = SessionConfig()


# ─────────────────────────────────────────────
# BASE STRATEGY
# ─────────────────────────────────────────────

class BaseStrategy(ABC):
    """
    Abstract base for all ICT strategies.
    Each concrete strategy implements evaluate() and returns
    a TradeSignal or None.
    """
    strategy_id: StrategyID
    allowed_sessions: Tuple[Session, ...]

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def evaluate(
        self,
        candles_by_tf: Dict[Timeframe, List[Candle]],
        instrument:    Instrument,
        current_time:  datetime,
        **kwargs,
    ) -> Optional[TradeSignal]:
        ...

    def _current_session(self, dt: datetime) -> Session:
        return detect_session(dt)

    def _is_valid_session(self, dt: datetime) -> bool:
        sess = self._current_session(dt)
        return sess in self.allowed_sessions

    def _find_best_fvg(
        self,
        fvgs:      List[FairValueGap],
        direction: Bias,
        price:     float,
    ) -> Optional[FairValueGap]:
        """Return the nearest unfilled FVG in the given direction."""
        candidates = [
            f for f in fvgs
            if f.direction == direction and not f.filled
        ]
        if not candidates:
            return None
        if direction == Bias.BULLISH:
            # Best bullish FVG = one nearest above current price
            above = [f for f in candidates if f.bottom > price]
            return min(above, key=lambda f: f.bottom) if above else None
        else:
            below = [f for f in candidates if f.top < price]
            return max(below, key=lambda f: f.top) if below else None

    def _build_signal(
        self,
        instrument:     Instrument,
        direction:      Bias,
        entry:          float,
        sl:             float,
        tp1:            float,
        tp2:            float,
        session:        Session,
        timestamp:      datetime,
        score:          int,
        total:          int,
        notes:          str = "",
    ) -> TradeSignal:
        return TradeSignal(
            strategy_id     = self.strategy_id,
            instrument      = instrument,
            direction       = direction,
            entry_price     = entry,
            stop_loss       = sl,
            take_profit_1   = tp1,
            take_profit_2   = tp2,
            session         = session,
            timestamp       = timestamp,
            confidence      = round(score / total, 2) if total > 0 else 0.0,
            checklist_score = score,
            checklist_total = total,
            notes           = notes,
        )


# ─────────────────────────────────────────────
# S1 — BEGINNER ICT MODEL
# ─────────────────────────────────────────────

class BeginnerICTStrategy(BaseStrategy):
    """
    H4 bias → M15 setup → M5 entry.
    Conditions: H4 bullish → liq. sweep → bullish MSS → FVG retrace.
    """
    strategy_id      = StrategyID.BEGINNER_ICT
    allowed_sessions = (Session.LONDON, Session.NEW_YORK)

    def evaluate(self, candles_by_tf, instrument, current_time, **kwargs):
        score, total = 0, 7

        if not self._is_valid_session(current_time):
            return None

        h4  = candles_by_tf.get(Timeframe.H4, [])
        m15 = candles_by_tf.get(Timeframe.M15, [])
        m5  = candles_by_tf.get(Timeframe.M5, [])

        if not h4 or not m15 or not m5:
            return None

        # 1. HTF bias (H4)
        ms_h4 = analyze_market_structure(h4)
        if ms_h4.is_bullish:
            score += 1
        elif ms_h4.is_bearish:
            direction = Bias.BEARISH
        else:
            return None

        direction = Bias.BULLISH if ms_h4.is_bullish else Bias.BEARISH

        # 2. Price in discount (for buys) / premium (for sells)
        pd_zone = compute_premium_discount(m15)
        current_price = m5[-1].close if m5 else 0
        if direction == Bias.BULLISH and pd_zone and pd_zone.is_discount(current_price):
            score += 1
        elif direction == Bias.BEARISH and pd_zone and pd_zone.is_premium(current_price):
            score += 1

        # 3. Liquidity sweep on M15
        liq_levels = detect_equal_highs_lows(m15)
        swept_any = any(
            detect_liquidity_sweep(m15, lv)
            for lv in liq_levels
            if (direction == Bias.BULLISH and lv.is_sell_side) or
               (direction == Bias.BEARISH and lv.is_buy_side)
        )
        if swept_any:
            score += 1

        # 4. MSS on M5
        mss = detect_mss(m5)
        if mss and mss.direction == direction:
            score += 1

        # 5. Displacement candle present
        if mss and mss.displacement_candle:
            score += 1

        # 6. FVG present on M5
        fvgs = detect_fvg(m5)
        best_fvg = self._find_best_fvg(fvgs, direction, current_price)
        if best_fvg:
            score += 1

        # 7. Session timing
        if self._is_valid_session(current_time):
            score += 1

        if score < 5 or not mss or not best_fvg:
            return None

        # Build levels
        if direction == Bias.BULLISH:
            entry = best_fvg.midpoint
            sl    = min(c.low for c in m5[-5:]) - (0.0002 * current_price / 1.0)
            tp1   = max(c.high for c in m15[-20:])
            tp2   = tp1 + (tp1 - entry)
        else:
            entry = best_fvg.midpoint
            sl    = max(c.high for c in m5[-5:]) + (0.0002 * current_price / 1.0)
            tp1   = min(c.low for c in m15[-20:])
            tp2   = tp1 - (entry - tp1)

        return self._build_signal(
            instrument, direction, entry, sl, tp1, tp2,
            self._current_session(current_time), current_time,
            score, total, notes=f"BeginnerICT | H4:{ms_h4.bias.value}"
        )


# ─────────────────────────────────────────────
# S2 — OTE + MSS MODEL
# ─────────────────────────────────────────────

class OTEMSSStrategy(BaseStrategy):
    """
    Wait for price to retrace to 62%–79% Fibonacci (OTE zone)
    after a bullish/bearish MSS.
    """
    strategy_id      = StrategyID.OTE_MSS
    allowed_sessions = (Session.LONDON, Session.NEW_YORK)

    def evaluate(self, candles_by_tf, instrument, current_time, **kwargs):
        score, total = 0, 7

        if not self._is_valid_session(current_time):
            return None

        d1  = candles_by_tf.get(Timeframe.D1, [])
        m15 = candles_by_tf.get(Timeframe.M15, [])
        m5  = candles_by_tf.get(Timeframe.M5, [])

        if not d1 or not m15 or not m5:
            return None

        ms_d1 = analyze_market_structure(d1)
        if ms_d1.bias == Bias.NEUTRAL:
            return None
        score += 1
        direction = ms_d1.bias

        current_price = m5[-1].close

        pd_zone = compute_premium_discount(m15, lookback=50)
        if pd_zone:
            if direction == Bias.BULLISH and pd_zone.is_discount(current_price):
                score += 1
            elif direction == Bias.BEARISH and pd_zone.is_premium(current_price):
                score += 1

        liq_levels = detect_equal_highs_lows(m15)
        swept_any = any(
            detect_liquidity_sweep(m15, lv) for lv in liq_levels
        )
        if swept_any:
            score += 1

        mss = detect_mss(m5)
        if mss and mss.direction == direction:
            score += 1

        # OTE zone check
        if pd_zone and pd_zone.in_ote_zone(current_price):
            score += 1

        fvgs = detect_fvg(m5)
        best_fvg = self._find_best_fvg(fvgs, direction, current_price)
        if best_fvg:
            score += 1

        if mss and mss.displacement_candle:
            score += 1

        if score < 5 or not mss or not pd_zone:
            return None

        ote_lo, ote_hi = pd_zone.ote_zone()

        if direction == Bias.BULLISH:
            entry = ote_lo
            sl    = min(c.low for c in m5[-5:]) * 0.9995
            tp1   = max(c.high for c in d1[-5:])
            tp2   = tp1 + (tp1 - entry) * 0.5
        else:
            entry = ote_hi
            sl    = max(c.high for c in m5[-5:]) * 1.0005
            tp1   = min(c.low for c in d1[-5:])
            tp2   = tp1 - (entry - tp1) * 0.5

        return self._build_signal(
            instrument, direction, entry, sl, tp1, tp2,
            self._current_session(current_time), current_time,
            score, total, notes="OTE+MSS"
        )


# ─────────────────────────────────────────────
# S3 — LONDON JUDAS SWING REVERSAL
# ─────────────────────────────────────────────

class LondonJudasSwingStrategy(BaseStrategy):
    """
    Capture the London open liquidity raid.
    Asia range forms → London sweeps one side → reversal trade.
    """
    strategy_id      = StrategyID.LONDON_JUDAS
    allowed_sessions = (Session.LONDON,)

    def evaluate(self, candles_by_tf, instrument, current_time, **kwargs):
        score, total = 0, 6

        if self._current_session(current_time) != Session.LONDON:
            return None
        score += 1

        m15 = candles_by_tf.get(Timeframe.M15, [])
        m5  = candles_by_tf.get(Timeframe.M5, [])
        h4  = candles_by_tf.get(Timeframe.H4, [])

        if not m15 or not m5 or not h4:
            return None

        asian_range = kwargs.get("asian_range", {})
        asia_high   = asian_range.get("high")
        asia_low    = asian_range.get("low")

        if not asia_high or not asia_low:
            return None

        current_price = m5[-1].close
        ms_h4 = analyze_market_structure(h4)
        direction = None

        # London sweeps below Asian low → bullish reversal
        if m5[-1].low < asia_low and m5[-1].close > asia_low:
            if ms_h4.is_bullish or ms_h4.bias == Bias.NEUTRAL:
                direction = Bias.BULLISH
                score += 1

        # London sweeps above Asian high → bearish reversal
        elif m5[-1].high > asia_high and m5[-1].close < asia_high:
            if ms_h4.is_bearish or ms_h4.bias == Bias.NEUTRAL:
                direction = Bias.BEARISH
                score += 1

        if not direction:
            return None

        # Displacement
        if is_displacement_candle(m5[-1]):
            score += 1

        mss = detect_mss(m5, require_displacement=True)
        if mss and mss.direction == direction:
            score += 1

        fvgs = detect_fvg(m5)
        best_fvg = self._find_best_fvg(fvgs, direction, current_price)
        if best_fvg:
            score += 1

        if score < 4 or not mss:
            return None

        if direction == Bias.BULLISH:
            entry = best_fvg.midpoint if best_fvg else current_price
            sl    = min(m5[-1].low, asia_low) - abs(asia_high - asia_low) * 0.1
            tp1   = asia_high
            tp2   = max(c.high for c in h4[-10:])
        else:
            entry = best_fvg.midpoint if best_fvg else current_price
            sl    = max(m5[-1].high, asia_high) + abs(asia_high - asia_low) * 0.1
            tp1   = asia_low
            tp2   = min(c.low for c in h4[-10:])

        return self._build_signal(
            instrument, direction, entry, sl, tp1, tp2,
            Session.LONDON, current_time,
            score, total, notes="Judas Swing | Asia sweep"
        )


# ─────────────────────────────────────────────
# S4 — SMT DIVERGENCE REVERSAL
# ─────────────────────────────────────────────

class SMTDivergenceStrategy(BaseStrategy):
    """
    Uses SMT divergence between correlated pairs as CONFLUENCE ONLY.
    Must combine with MSS and FVG.
    """
    strategy_id      = StrategyID.SMT_DIVERGENCE
    allowed_sessions = (Session.LONDON, Session.NEW_YORK)

    def evaluate(self, candles_by_tf, instrument, current_time, **kwargs):
        score, total = 0, 6

        if not self._is_valid_session(current_time):
            return None

        m5  = candles_by_tf.get(Timeframe.M5, [])
        m15 = candles_by_tf.get(Timeframe.M15, [])
        correlated_candles = kwargs.get("correlated_candles", [])

        if not m5 or not correlated_candles:
            return None

        smt = detect_smt_divergence(m5, correlated_candles)
        if not smt:
            return None
        score += 2   # SMT is strong confluence

        direction = smt.direction
        current_price = m5[-1].close

        mss = detect_mss(m5)
        if mss and mss.direction == direction:
            score += 1

        if mss and mss.displacement_candle:
            score += 1

        fvgs = detect_fvg(m5)
        best_fvg = self._find_best_fvg(fvgs, direction, current_price)
        if best_fvg:
            score += 1

        liq_levels = detect_equal_highs_lows(m15)
        if any(detect_liquidity_sweep(m15, lv) for lv in liq_levels):
            score += 1

        if score < 4 or not mss:
            return None

        if direction == Bias.BULLISH:
            entry = best_fvg.midpoint if best_fvg else current_price
            sl    = smt.primary_low * 0.9998
            tp1   = max(c.high for c in m15[-20:])
            tp2   = tp1 + (tp1 - entry)
        else:
            entry = best_fvg.midpoint if best_fvg else current_price
            sl    = smt.primary_low * 1.0002
            tp1   = min(c.low for c in m15[-20:])
            tp2   = tp1 - (entry - tp1)

        return self._build_signal(
            instrument, direction, entry, sl, tp1, tp2,
            self._current_session(current_time), current_time,
            score, total, notes=f"SMT | {instrument.value} vs {smt.correlated_instrument.value}"
        )


# ─────────────────────────────────────────────
# S5 — AMD CYCLE MODEL
# ─────────────────────────────────────────────

class AMDCycleStrategy(BaseStrategy):
    """
    Trade the full Accumulation → Manipulation → Distribution cycle.
    Only enter during Distribution phase after confirmed Manipulation sweep.
    """
    strategy_id      = StrategyID.AMD_CYCLE
    allowed_sessions = (Session.LONDON, Session.NEW_YORK)

    def evaluate(self, candles_by_tf, instrument, current_time, **kwargs):
        score, total = 0, 5

        if not self._is_valid_session(current_time):
            return None

        m15 = candles_by_tf.get(Timeframe.M15, [])
        m5  = candles_by_tf.get(Timeframe.M5, [])

        if not m15 or not m5:
            return None

        phase = detect_amd_phase(m15)

        if phase not in ("MANIPULATION", "DISTRIBUTION"):
            return None
        score += 1

        current_price = m5[-1].close
        mss = detect_mss(m5)
        if not mss:
            return None
        score += 1
        direction = mss.direction

        if mss.displacement_candle:
            score += 1

        fvgs = detect_fvg(m5)
        best_fvg = self._find_best_fvg(fvgs, direction, current_price)
        if best_fvg:
            score += 1

        liq_levels = detect_equal_highs_lows(m15)
        if any(detect_liquidity_sweep(m15, lv) for lv in liq_levels):
            score += 1

        if score < 4:
            return None

        if direction == Bias.BULLISH:
            entry = best_fvg.midpoint if best_fvg else current_price
            sl    = min(c.low for c in m5[-5:]) * 0.9998
            tp1   = max(c.high for c in m15[-20:])
            tp2   = tp1 + (tp1 - entry)
        else:
            entry = best_fvg.midpoint if best_fvg else current_price
            sl    = max(c.high for c in m5[-5:]) * 1.0002
            tp1   = min(c.low for c in m15[-20:])
            tp2   = tp1 - (entry - tp1)

        return self._build_signal(
            instrument, direction, entry, sl, tp1, tp2,
            self._current_session(current_time), current_time,
            score, total, notes=f"AMD | Phase={phase}"
        )


# ─────────────────────────────────────────────
# S6 — GOLD MASTER MODEL
# ─────────────────────────────────────────────

class GoldMasterStrategy(BaseStrategy):
    """
    Gold-specific model. Aggressive liquidity hunts.
    Focus: NY Open reversals and London manipulation.
    """
    strategy_id      = StrategyID.GOLD_MASTER
    allowed_sessions = (Session.LONDON, Session.NEW_YORK)

    def evaluate(self, candles_by_tf, instrument, current_time, **kwargs):
        if instrument != Instrument.XAUUSD:
            return None

        score, total = 0, 7

        if not self._is_valid_session(current_time):
            return None
        score += 1

        h4  = candles_by_tf.get(Timeframe.H4, [])
        m15 = candles_by_tf.get(Timeframe.M15, [])
        m5  = candles_by_tf.get(Timeframe.M5, [])

        if not h4 or not m15 or not m5:
            return None

        ms_h4 = analyze_market_structure(h4)
        if ms_h4.bias == Bias.NEUTRAL:
            return None
        score += 1
        direction = ms_h4.bias

        liq_levels = detect_equal_highs_lows(m15)
        swept = any(detect_liquidity_sweep(m15, lv) for lv in liq_levels)
        if swept:
            score += 1

        mss = detect_mss(m5)
        if mss and mss.direction == direction:
            score += 1

        if mss and mss.displacement_candle:
            score += 1

        current_price = m5[-1].close
        fvgs = detect_fvg(m5)
        best_fvg = self._find_best_fvg(fvgs, direction, current_price)
        if best_fvg:
            score += 1

        pd_zone = compute_premium_discount(h4, lookback=30)
        if pd_zone:
            if direction == Bias.BULLISH and pd_zone.is_discount(current_price):
                score += 1
            elif direction == Bias.BEARISH and pd_zone.is_premium(current_price):
                score += 1

        if score < 5 or not mss:
            return None

        gold_spread = 0.5   # typical Gold spread buffer
        if direction == Bias.BULLISH:
            entry = best_fvg.midpoint if best_fvg else current_price
            sl    = min(c.low for c in m5[-5:]) - gold_spread
            tp1   = max(c.high for c in m15[-20:])
            tp2   = tp1 + (tp1 - entry) * 0.5
        else:
            entry = best_fvg.midpoint if best_fvg else current_price
            sl    = max(c.high for c in m5[-5:]) + gold_spread
            tp1   = min(c.low for c in m15[-20:])
            tp2   = tp1 - (entry - tp1) * 0.5

        return self._build_signal(
            instrument, direction, entry, sl, tp1, tp2,
            self._current_session(current_time), current_time,
            score, total, notes="Gold Master | H4 bias"
        )


# ─────────────────────────────────────────────
# S7 — NASDAQ OPENING DRIVE
# ─────────────────────────────────────────────

class NASDAQOpeningDriveStrategy(BaseStrategy):
    """
    Capture explosive NY Open moves on NASDAQ.
    Pre-market range → sweep → MSS → FVG → continuation.
    """
    strategy_id      = StrategyID.NASDAQ_DRIVE
    allowed_sessions = (Session.NEW_YORK,)

    def evaluate(self, candles_by_tf, instrument, current_time, **kwargs):
        if instrument != Instrument.NAS100:
            return None

        if self._current_session(current_time) != Session.NEW_YORK:
            return None

        score, total = 0, 6
        score += 1   # session confirmed

        m15 = candles_by_tf.get(Timeframe.M15, [])
        m5  = candles_by_tf.get(Timeframe.M5, [])
        h4  = candles_by_tf.get(Timeframe.H4, [])

        if not m15 or not m5:
            return None

        ms_h4 = analyze_market_structure(h4) if h4 else None
        if ms_h4:
            score += 1
            direction = ms_h4.bias if ms_h4.bias != Bias.NEUTRAL else None
        else:
            direction = None

        if not direction:
            return None

        liq_levels = detect_equal_highs_lows(m15)
        swept = any(detect_liquidity_sweep(m15, lv) for lv in liq_levels)
        if swept:
            score += 1

        mss = detect_mss(m5)
        if mss and mss.direction == direction:
            score += 1

        if mss and mss.displacement_candle:
            score += 1

        current_price = m5[-1].close
        fvgs = detect_fvg(m5)
        best_fvg = self._find_best_fvg(fvgs, direction, current_price)
        if best_fvg:
            score += 1

        if score < 4 or not mss:
            return None

        if direction == Bias.BULLISH:
            entry = best_fvg.midpoint if best_fvg else current_price
            sl    = min(c.low for c in m5[-5:]) - 10
            tp1   = max(c.high for c in m15[-20:])
            tp2   = tp1 + (tp1 - entry)
        else:
            entry = best_fvg.midpoint if best_fvg else current_price
            sl    = max(c.high for c in m5[-5:]) + 10
            tp1   = min(c.low for c in m15[-20:])
            tp2   = tp1 - (entry - tp1)

        return self._build_signal(
            instrument, direction, entry, sl, tp1, tp2,
            Session.NEW_YORK, current_time,
            score, total, notes="NASDAQ Opening Drive"
        )


# ─────────────────────────────────────────────
# S8 — EURUSD LONDON REVERSAL
# ─────────────────────────────────────────────

class EURUSDLondonReversalStrategy(BaseStrategy):
    """
    EURUSD-specific London session reversal with DXY SMT confirmation.
    """
    strategy_id      = StrategyID.EURUSD_LONDON
    allowed_sessions = (Session.LONDON,)

    def evaluate(self, candles_by_tf, instrument, current_time, **kwargs):
        if instrument != Instrument.EURUSD:
            return None
        if self._current_session(current_time) != Session.LONDON:
            return None

        score, total = 0, 6
        score += 1

        m15  = candles_by_tf.get(Timeframe.M15, [])
        m5   = candles_by_tf.get(Timeframe.M5, [])
        h4   = candles_by_tf.get(Timeframe.H4, [])
        dxy_candles = kwargs.get("dxy_candles", [])

        if not m15 or not m5:
            return None

        ms_h4 = analyze_market_structure(h4) if h4 else None
        direction = ms_h4.bias if ms_h4 and ms_h4.bias != Bias.NEUTRAL else None
        if not direction:
            return None
        score += 1

        asian_range = kwargs.get("asian_range", {})
        asia_high   = asian_range.get("high")
        asia_low    = asian_range.get("low")
        current_price = m5[-1].close

        if asia_high and asia_low:
            if (direction == Bias.BULLISH and current_price < asia_low) or \
               (direction == Bias.BEARISH and current_price > asia_high):
                score += 1   # London swept Asian range

        if dxy_candles:
            smt = detect_smt_divergence(m5, dxy_candles)
            if smt:
                score += 1

        mss = detect_mss(m5)
        if mss and mss.direction == direction:
            score += 1

        fvgs = detect_fvg(m5)
        best_fvg = self._find_best_fvg(fvgs, direction, current_price)
        if best_fvg:
            score += 1

        if score < 4 or not mss:
            return None

        if direction == Bias.BULLISH:
            entry = best_fvg.midpoint if best_fvg else current_price
            sl    = min(c.low for c in m5[-5:]) * 0.9999
            tp1   = asia_high if asia_high else max(c.high for c in m15[-20:])
            tp2   = tp1 + (tp1 - entry)
        else:
            entry = best_fvg.midpoint if best_fvg else current_price
            sl    = max(c.high for c in m5[-5:]) * 1.0001
            tp1   = asia_low  if asia_low  else min(c.low for c in m15[-20:])
            tp2   = tp1 - (entry - tp1)

        return self._build_signal(
            instrument, direction, entry, sl, tp1, tp2,
            Session.LONDON, current_time,
            score, total, notes="EURUSD London Reversal"
        )


# ─────────────────────────────────────────────
# S9 — GBPUSD VOLATILITY RAID
# ─────────────────────────────────────────────

class GBPUSDVolatilityRaidStrategy(BaseStrategy):
    """
    GBPUSD has aggressive stop hunts and large wicks.
    Wait for DEEP sweep + strong displacement before entry.
    """
    strategy_id      = StrategyID.GBPUSD_VOLATILITY
    allowed_sessions = (Session.LONDON, Session.NEW_YORK)

    def evaluate(self, candles_by_tf, instrument, current_time, **kwargs):
        if instrument != Instrument.GBPUSD:
            return None
        if not self._is_valid_session(current_time):
            return None

        score, total = 0, 6

        m15 = candles_by_tf.get(Timeframe.M15, [])
        m5  = candles_by_tf.get(Timeframe.M5, [])
        h4  = candles_by_tf.get(Timeframe.H4, [])

        if not m15 or not m5:
            return None

        # Deep sweep check: last candle wick > 2× average range
        avg_range = sum(c.full_range for c in m5[-20:]) / 20 if len(m5) >= 20 else 0
        last_wick = m5[-1].full_range - m5[-1].body
        deep_sweep = last_wick > avg_range * 2
        if deep_sweep:
            score += 2

        ms_h4 = analyze_market_structure(h4) if h4 else None
        direction = ms_h4.bias if ms_h4 and ms_h4.bias != Bias.NEUTRAL else None
        if not direction:
            return None
        score += 1

        mss = detect_mss(m5, require_displacement=True)
        if mss and mss.direction == direction:
            score += 1

        if mss and mss.displacement_candle:
            score += 1

        current_price = m5[-1].close
        fvgs = detect_fvg(m5)
        best_fvg = self._find_best_fvg(fvgs, direction, current_price)
        if best_fvg:
            score += 1

        if score < 4 or not mss:
            return None

        if direction == Bias.BULLISH:
            entry = best_fvg.midpoint if best_fvg else current_price
            sl    = min(c.low for c in m5[-5:]) * 0.9998
            tp1   = max(c.high for c in m15[-20:])
            tp2   = tp1 + (tp1 - entry)
        else:
            entry = best_fvg.midpoint if best_fvg else current_price
            sl    = max(c.high for c in m5[-5:]) * 1.0002
            tp1   = min(c.low for c in m15[-20:])
            tp2   = tp1 - (entry - tp1)

        return self._build_signal(
            instrument, direction, entry, sl, tp1, tp2,
            self._current_session(current_time), current_time,
            score, total, notes="GBPUSD Volatility Raid"
        )


# ─────────────────────────────────────────────
# S10 — BREAKER BLOCK MODEL
# ─────────────────────────────────────────────

class BreakerBlockStrategy(BaseStrategy):
    """
    Trade failed Order Blocks (Breaker Blocks).
    Bearish OB that breaks bullish → becomes bullish support on retest.
    """
    strategy_id      = StrategyID.BREAKER_BLOCK
    allowed_sessions = (Session.LONDON, Session.NEW_YORK)

    def evaluate(self, candles_by_tf, instrument, current_time, **kwargs):
        if not self._is_valid_session(current_time):
            return None

        score, total = 0, 6

        h4  = candles_by_tf.get(Timeframe.H4, [])
        m15 = candles_by_tf.get(Timeframe.M15, [])
        m5  = candles_by_tf.get(Timeframe.M5, [])

        if not m15 or not m5:
            return None

        ms_h4 = analyze_market_structure(h4) if h4 else None
        direction = ms_h4.bias if ms_h4 and ms_h4.bias != Bias.NEUTRAL else None
        if not direction:
            return None
        score += 1

        obs      = detect_order_blocks(m15)
        breakers = detect_breaker_blocks(m15, obs)

        current_price = m5[-1].close
        valid_breakers = [
            bb for bb in breakers
            if bb.direction == direction and bb.bottom <= current_price <= bb.top
        ]
        if valid_breakers:
            score += 2

        liq_levels = detect_equal_highs_lows(m15)
        if any(detect_liquidity_sweep(m15, lv) for lv in liq_levels):
            score += 1

        mss = detect_mss(m5)
        if mss and mss.direction == direction:
            score += 1

        if mss and mss.displacement_candle:
            score += 1

        if score < 4 or not valid_breakers or not mss:
            return None

        bb = valid_breakers[0]

        if direction == Bias.BULLISH:
            entry = bb.midpoint
            sl    = bb.bottom * 0.9998
            tp1   = max(c.high for c in m15[-20:])
            tp2   = tp1 + (tp1 - entry)
        else:
            entry = bb.midpoint
            sl    = bb.top * 1.0002
            tp1   = min(c.low for c in m15[-20:])
            tp2   = tp1 - (entry - tp1)

        return self._build_signal(
            instrument, direction, entry, sl, tp1, tp2,
            self._current_session(current_time), current_time,
            score, total, notes=f"Breaker Block | OB at {bb.formed_at}"
        )


# ─────────────────────────────────────────────
# S_MASTER — MASTER COMBINED MODEL
# ─────────────────────────────────────────────

class MasterCombinedStrategy(BaseStrategy):
    """
    Highest probability model combining ALL ICT concepts.
    Steps: HTF Bias → Liquidity Target → Sweep → MSS → Entry → Risk
    Minimum checklist: 8/10 must pass.
    """
    strategy_id      = StrategyID.MASTER_COMBINED
    allowed_sessions = (Session.LONDON, Session.NEW_YORK)

    def evaluate(self, candles_by_tf, instrument, current_time, **kwargs):
        score, total = 0, 10

        if not self._is_valid_session(current_time):
            return None

        w1  = candles_by_tf.get(Timeframe.W1, [])
        d1  = candles_by_tf.get(Timeframe.D1, [])
        h4  = candles_by_tf.get(Timeframe.H4, [])
        m15 = candles_by_tf.get(Timeframe.M15, [])
        m5  = candles_by_tf.get(Timeframe.M5, [])

        if not h4 or not m15 or not m5:
            return None

        # 1. Weekly bias
        if w1:
            ms_w = analyze_market_structure(w1)
            if ms_w.bias != Bias.NEUTRAL:
                score += 1
                htf_bias = ms_w.bias
            else:
                htf_bias = None
        else:
            htf_bias = None

        # 2. Daily bias aligns
        if d1:
            ms_d = analyze_market_structure(d1)
            if ms_d.bias != Bias.NEUTRAL:
                if htf_bias is None:
                    htf_bias = ms_d.bias
                if ms_d.bias == htf_bias:
                    score += 1

        # 3. H4 bias aligns
        ms_h4 = analyze_market_structure(h4)
        if ms_h4.bias != Bias.NEUTRAL:
            if htf_bias is None:
                htf_bias = ms_h4.bias
            if ms_h4.bias == htf_bias:
                score += 1

        if not htf_bias:
            return None
        direction = htf_bias

        # 4. Liquidity target identified
        liq_levels = detect_equal_highs_lows(m15)
        if liq_levels:
            score += 1

        # 5. Liquidity swept
        swept = any(detect_liquidity_sweep(m15, lv) for lv in liq_levels)
        if swept:
            score += 1

        # 6. Premium / discount aligned
        current_price = m5[-1].close
        pd_zone = compute_premium_discount(h4, lookback=50)
        if pd_zone:
            if direction == Bias.BULLISH and pd_zone.is_discount(current_price):
                score += 1
            elif direction == Bias.BEARISH and pd_zone.is_premium(current_price):
                score += 1

        # 7. MSS confirmed
        mss = detect_mss(m5, require_displacement=True)
        if mss and mss.direction == direction:
            score += 1

        # 8. Strong displacement
        if mss and mss.displacement_candle:
            score += 1

        # 9. FVG present
        fvgs = detect_fvg(m5)
        best_fvg = self._find_best_fvg(fvgs, direction, current_price)
        if best_fvg:
            score += 1

        # 10. Session timing
        if self._current_session(current_time) in self.allowed_sessions:
            score += 1

        self.logger.info(f"Master Model [{instrument.value}]: {score}/{total}")

        # Require minimum 8/10
        if score < 8 or not mss:
            return None

        obs      = detect_order_blocks(m15)
        breakers = detect_breaker_blocks(m15, obs)

        if direction == Bias.BULLISH:
            entry = best_fvg.midpoint if best_fvg else current_price
            sl    = min(c.low for c in m5[-5:]) * 0.9997
            tp1   = max(c.high for c in m15[-30:])
            tp2   = tp1 + (tp1 - entry) * 1.5
        else:
            entry = best_fvg.midpoint if best_fvg else current_price
            sl    = max(c.high for c in m5[-5:]) * 1.0003
            tp1   = min(c.low for c in m15[-30:])
            tp2   = tp1 - (entry - tp1) * 1.5

        return self._build_signal(
            instrument, direction, entry, sl, tp1, tp2,
            self._current_session(current_time), current_time,
            score, total,
            notes=f"MASTER | {score}/10 | HTF:{direction.value}"
        )


# ─────────────────────────────────────────────
# STRATEGY REGISTRY
# ─────────────────────────────────────────────

STRATEGY_REGISTRY: Dict[StrategyID, BaseStrategy] = {
    StrategyID.BEGINNER_ICT:      BeginnerICTStrategy(),
    StrategyID.OTE_MSS:           OTEMSSStrategy(),
    StrategyID.LONDON_JUDAS:      LondonJudasSwingStrategy(),
    StrategyID.SMT_DIVERGENCE:    SMTDivergenceStrategy(),
    StrategyID.AMD_CYCLE:         AMDCycleStrategy(),
    StrategyID.GOLD_MASTER:       GoldMasterStrategy(),
    StrategyID.NASDAQ_DRIVE:      NASDAQOpeningDriveStrategy(),
    StrategyID.EURUSD_LONDON:     EURUSDLondonReversalStrategy(),
    StrategyID.GBPUSD_VOLATILITY: GBPUSDVolatilityRaidStrategy(),
    StrategyID.BREAKER_BLOCK:     BreakerBlockStrategy(),
    StrategyID.MASTER_COMBINED:   MasterCombinedStrategy(),
}


def get_strategy(sid: StrategyID) -> BaseStrategy:
    return STRATEGY_REGISTRY[sid]