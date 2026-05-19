"""
signals/signal_generator.py
============================
Orchestrates all strategies across instruments and timeframes.
Applies pre-trade checklist, deduplication, and signal ranking.
Produces a prioritised list of TradeSignal objects for execution.
"""

from __future__ import annotations
import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from config.settings import (
    Bias, Instrument, Session, StrategyID, Timeframe,
    RiskConfig, STRATEGY_INSTRUMENTS, STRATEGY_TIMEFRAMES
)
from data.models import Candle, TradeSignal
from data.market_data import detect_session, get_asian_range
from strategy.strategies import STRATEGY_REGISTRY, BaseStrategy

logger = logging.getLogger(__name__)
_risk_cfg = RiskConfig()


# ─────────────────────────────────────────────
# PRE-TRADE CHECKLIST
# ─────────────────────────────────────────────

class PreTradeChecklist:
    """
    Validates a signal against the Master ICT checklist.
    Returns (passed: bool, reasons: List[str]).
    """

    @staticmethod
    def validate(
        signal:         TradeSignal,
        open_trades:    int,
        daily_loss_pct: float,
        weekly_loss_pct: float,
    ) -> Tuple[bool, List[str]]:
        reasons = []

        # Context checks
        if signal.confidence < 0.6:
            reasons.append(f"Low confidence: {signal.confidence:.0%}")

        if signal.rr_ratio < _risk_cfg.min_rr_ratio:
            reasons.append(f"RR {signal.rr_ratio:.1f} < min {_risk_cfg.min_rr_ratio}")

        if open_trades >= _risk_cfg.max_open_trades:
            reasons.append(f"Max open trades reached ({open_trades})")

        if daily_loss_pct >= _risk_cfg.daily_max_loss_pct:
            reasons.append(f"Daily loss limit hit: {daily_loss_pct:.1%}")

        if weekly_loss_pct >= _risk_cfg.weekly_max_loss_pct:
            reasons.append(f"Weekly loss limit hit: {weekly_loss_pct:.1%}")

        if signal.session == Session.DEAD:
            reasons.append("Dead session — no trading")

        passed = len(reasons) == 0
        return passed, reasons


# ─────────────────────────────────────────────
# SIGNAL GENERATOR
# ─────────────────────────────────────────────

class SignalGenerator:
    """
    Main signal engine.
    For each instrument, runs all applicable strategies and
    returns ranked, validated signals.
    """

    def __init__(
        self,
        strategies:     Optional[List[StrategyID]] = None,
        instruments:    Optional[List[Instrument]]  = None,
    ):
        self.strategy_ids  = strategies or list(STRATEGY_REGISTRY.keys())
        self.instruments   = instruments or list(Instrument)
        self._checklist    = PreTradeChecklist()
        self.logger        = logging.getLogger(self.__class__.__name__)

    def generate(
        self,
        candles_by_instrument_tf: Dict[Instrument, Dict[Timeframe, List[Candle]]],
        current_time:   datetime,
        open_trades:    int              = 0,
        daily_loss_pct: float            = 0.0,
        weekly_loss_pct: float           = 0.0,
        extra_context:  Optional[Dict]   = None,
    ) -> List[TradeSignal]:
        """
        Run all strategies and return valid, ranked signals.

        Parameters
        ----------
        candles_by_instrument_tf : Dict[Instrument, Dict[Timeframe, List[Candle]]]
            Full market data for all instruments and timeframes.
        current_time : datetime  (UTC)
        open_trades : int
            Currently open trade count.
        daily_loss_pct : float
            Current day's loss as a fraction of account.
        weekly_loss_pct : float
            Current week's loss as a fraction of account.
        extra_context : dict
            Optional extra data: asian_range, correlated_candles, dxy_candles.
        """
        extra_context = extra_context or {}
        raw_signals: List[TradeSignal] = []

        for sid in self.strategy_ids:
            strategy = STRATEGY_REGISTRY.get(sid)
            if not strategy:
                continue

            allowed_instruments = STRATEGY_INSTRUMENTS.get(sid, list(Instrument))

            for instrument in self.instruments:
                if instrument not in allowed_instruments:
                    continue

                candles_by_tf = candles_by_instrument_tf.get(instrument, {})
                if not candles_by_tf:
                    continue

                try:
                    signal = strategy.evaluate(
                        candles_by_tf  = candles_by_tf,
                        instrument     = instrument,
                        current_time   = current_time,
                        **extra_context,
                    )
                    if signal:
                        raw_signals.append(signal)
                        self.logger.info(
                            f"Signal [{sid.value}] {instrument.value} "
                            f"{signal.direction.value} | "
                            f"Conf={signal.confidence:.0%} RR={signal.rr_ratio:.1f}"
                        )

                except Exception as e:
                    self.logger.error(f"Error in {sid.value} / {instrument.value}: {e}")

        # Filter & rank
        valid_signals = self._filter_and_rank(
            raw_signals, open_trades, daily_loss_pct, weekly_loss_pct
        )
        return valid_signals

    def _filter_and_rank(
        self,
        signals:         List[TradeSignal],
        open_trades:     int,
        daily_loss_pct:  float,
        weekly_loss_pct: float,
    ) -> List[TradeSignal]:
        valid = []
        for sig in signals:
            passed, reasons = self._checklist.validate(
                sig, open_trades, daily_loss_pct, weekly_loss_pct
            )
            if passed:
                valid.append(sig)
            else:
                self.logger.debug(
                    f"Signal rejected [{sig.strategy_id.value}]: {'; '.join(reasons)}"
                )

        # Deduplicate: same instrument + direction → keep highest confidence
        deduped: Dict[Tuple[Instrument, Bias], TradeSignal] = {}
        for sig in valid:
            key = (sig.instrument, sig.direction)
            if key not in deduped or sig.confidence > deduped[key].confidence:
                deduped[key] = sig

        # Rank by: confidence DESC, then RR DESC
        ranked = sorted(
            deduped.values(),
            key=lambda s: (s.confidence, s.rr_ratio),
            reverse=True,
        )

        # Prefer Master Combined signals at the top
        master  = [s for s in ranked if s.strategy_id == StrategyID.MASTER_COMBINED]
        others  = [s for s in ranked if s.strategy_id != StrategyID.MASTER_COMBINED]
        return master + others


# ─────────────────────────────────────────────
# SIGNAL FORMATTER (human-readable output)
# ─────────────────────────────────────────────

def format_signal(signal: TradeSignal) -> str:
    direction_str = "🟢 LONG" if signal.is_long else "🔴 SHORT"
    return (
        f"\n{'='*60}\n"
        f"  TRADE SIGNAL — {signal.strategy_id.value}\n"
        f"{'='*60}\n"
        f"  Instrument : {signal.instrument.value}\n"
        f"  Direction  : {direction_str}\n"
        f"  Session    : {signal.session.value}\n"
        f"  Timestamp  : {signal.timestamp:%Y-%m-%d %H:%M UTC}\n"
        f"  ─────────────────────────────────────────────\n"
        f"  Entry      : {signal.entry_price:.5f}\n"
        f"  Stop Loss  : {signal.stop_loss:.5f}\n"
        f"  TP1 (1:2)  : {signal.take_profit_1:.5f}\n"
        f"  TP2 (1:3+) : {signal.take_profit_2:.5f}\n"
        f"  R:R Ratio  : 1:{signal.rr_ratio:.1f}\n"
        f"  ─────────────────────────────────────────────\n"
        f"  Confidence : {signal.confidence:.0%}  "
        f"({signal.checklist_score}/{signal.checklist_total})\n"
        f"  Notes      : {signal.notes}\n"
        f"{'='*60}\n"
    )