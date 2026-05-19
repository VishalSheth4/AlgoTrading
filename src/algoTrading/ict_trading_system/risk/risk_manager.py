"""
risk/risk_manager.py
====================
Handles all risk management logic:
  - Position sizing (fixed risk %)
  - Daily / weekly drawdown tracking
  - Partial take-profit and break-even management
  - Max open trade enforcement
  - Kill-switch logic
"""

from __future__ import annotations
import logging
import uuid
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple

from config.settings import RiskConfig, Instrument, PIP_SIZE
from data.models import Trade, TradeSignal

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Central risk engine.
    Keeps track of account state and all open/closed trades.
    """

    def __init__(self, account_size: float, config: Optional[RiskConfig] = None):
        self.account_size    = account_size
        self.config          = config or RiskConfig()
        self.open_trades:    List[Trade]  = []
        self.closed_trades:  List[Trade]  = []
        self._daily_pnl:     Dict[date, float] = {}
        self._weekly_pnl:    Dict[int, float]  = {}   # ISO week number → pnl
        self.kill_switch:    bool = False
        self.logger          = logging.getLogger(self.__class__.__name__)

    # ─────────────────────────────────────────────
    # ACCOUNT METRICS
    # ─────────────────────────────────────────────

    @property
    def equity(self) -> float:
        unrealised = sum(t.pnl for t in self.open_trades)
        realised   = sum(t.pnl for t in self.closed_trades)
        return self.account_size + realised + unrealised

    @property
    def daily_loss_pct(self) -> float:
        today = date.today()
        loss  = self._daily_pnl.get(today, 0.0)
        return abs(loss) / self.account_size if loss < 0 else 0.0

    @property
    def weekly_loss_pct(self) -> float:
        week = date.today().isocalendar()[1]
        loss = self._weekly_pnl.get(week, 0.0)
        return abs(loss) / self.account_size if loss < 0 else 0.0

    @property
    def open_trade_count(self) -> int:
        return len(self.open_trades)

    # ─────────────────────────────────────────────
    # POSITION SIZING
    # ─────────────────────────────────────────────

    def calculate_position_size(
        self,
        signal:       TradeSignal,
        risk_pct:     Optional[float] = None,
    ) -> Tuple[float, float]:
        """
        Calculates position size in lots/units.

        Returns
        -------
        (risk_amount, position_size)
        """
        rp          = risk_pct or self.config.risk_per_trade_pct
        risk_amount = self.equity * rp

        sl_distance = abs(signal.entry_price - signal.stop_loss)
        if sl_distance == 0:
            self.logger.warning("SL distance is 0 — cannot size position")
            return 0.0, 0.0

        # For forex: position_size in lots
        # Standard lot = 100,000 units; pip value varies by instrument
        pip = PIP_SIZE.get(signal.instrument, 0.0001)
        sl_in_pips   = sl_distance / pip
        pip_value    = pip * 10_000  if "USD" in signal.instrument.value else pip

        if sl_in_pips > 0:
            # risk_amount = position_size × sl_in_pips × pip_value_per_lot
            # For simplicity: size = risk_amount / sl_distance
            position_size = risk_amount / sl_distance
        else:
            position_size = 0.0

        self.logger.debug(
            f"Position size: equity={self.equity:.2f} | "
            f"risk={risk_amount:.2f} | SL={sl_distance:.5f} | "
            f"size={position_size:.4f}"
        )
        return round(risk_amount, 2), round(position_size, 4)

    # ─────────────────────────────────────────────
    # TRADE APPROVAL
    # ─────────────────────────────────────────────

    def approve_trade(self, signal: TradeSignal) -> Tuple[bool, str]:
        """
        Final go/no-go before sending to execution.
        Returns (approved, reason).
        """
        if self.kill_switch:
            return False, "Kill switch active — all trading halted"

        if self.daily_loss_pct >= self.config.daily_max_loss_pct:
            return False, f"Daily loss limit {self.config.daily_max_loss_pct:.0%} hit"

        if self.weekly_loss_pct >= self.config.weekly_max_loss_pct:
            return False, f"Weekly loss limit {self.config.weekly_max_loss_pct:.0%} hit"

        if self.open_trade_count >= self.config.max_open_trades:
            return False, f"Max {self.config.max_open_trades} open trades"

        if signal.rr_ratio < self.config.min_rr_ratio:
            return False, f"RR {signal.rr_ratio:.1f} below minimum {self.config.min_rr_ratio}"

        return True, "Approved"

    # ─────────────────────────────────────────────
    # OPEN TRADE
    # ─────────────────────────────────────────────

    def open_trade(self, signal: TradeSignal) -> Optional[Trade]:
        approved, reason = self.approve_trade(signal)
        if not approved:
            self.logger.warning(f"Trade not approved: {reason}")
            return None

        risk_amount, position_size = self.calculate_position_size(signal)
        if position_size == 0:
            return None

        trade = Trade(
            trade_id       = str(uuid.uuid4())[:8].upper(),
            signal         = signal,
            account_size   = self.equity,
            risk_amount    = risk_amount,
            position_size  = position_size,
            open_time      = signal.timestamp,
        )
        self.open_trades.append(trade)
        self.logger.info(
            f"Trade OPENED [{trade.trade_id}] "
            f"{signal.instrument.value} {signal.direction.value} | "
            f"Entry={signal.entry_price:.5f} SL={signal.stop_loss:.5f} "
            f"TP1={signal.take_profit_1:.5f} Size={position_size:.4f}"
        )
        return trade

    # ─────────────────────────────────────────────
    # CLOSE TRADE
    # ─────────────────────────────────────────────

    def close_trade(
        self,
        trade:      Trade,
        close_price: float,
        timestamp:  datetime,
    ) -> Trade:
        trade.close(close_price, timestamp)
        self.open_trades  = [t for t in self.open_trades if t.trade_id != trade.trade_id]
        self.closed_trades.append(trade)

        # Update PnL trackers
        today = timestamp.date()
        week  = today.isocalendar()[1]
        self._daily_pnl[today]  = self._daily_pnl.get(today, 0.0)  + trade.pnl
        self._weekly_pnl[week]  = self._weekly_pnl.get(week, 0.0)  + trade.pnl

        # Check kill switch conditions
        if self.daily_loss_pct >= self.config.daily_max_loss_pct:
            self.kill_switch = True
            self.logger.critical("Kill switch activated — daily loss limit reached!")
        if self.weekly_loss_pct >= self.config.weekly_max_loss_pct:
            self.kill_switch = True
            self.logger.critical("Kill switch activated — weekly loss limit reached!")

        self.logger.info(
            f"Trade CLOSED [{trade.trade_id}] | "
            f"PnL={trade.pnl:+.2f} ({trade.pnl_r:+.1f}R) | "
            f"Status={trade.status}"
        )
        return trade

    # ─────────────────────────────────────────────
    # TRADE MANAGEMENT: PARTIAL TP & BREAK-EVEN
    # ─────────────────────────────────────────────

    def check_trade_management(
        self,
        trade:         Trade,
        current_price: float,
        timestamp:     datetime,
    ) -> Dict[str, bool]:
        """
        Called on every price update for open trades.
        Returns a dict with flags: partial_tp, move_to_be.
        """
        actions = {"partial_tp": False, "move_to_be": False}

        if trade.status != "OPEN":
            return actions

        entry = trade.signal.entry_price
        sl    = trade.signal.stop_loss
        tp1   = trade.signal.take_profit_1

        risk    = abs(entry - sl)
        be_dist = risk * self.config.breakeven_rr

        if trade.signal.is_long:
            current_rr = (current_price - entry) / risk if risk > 0 else 0
            # Partial TP at 1:2
            if not trade.partial_closed and current_price >= tp1:
                actions["partial_tp"] = True
                trade.partial_closed  = True
                trade.status          = "PARTIAL"
                self.logger.info(f"Partial TP hit [{trade.trade_id}]")
            # Move to breakeven
            if current_price >= entry + be_dist:
                actions["move_to_be"] = True
        else:
            current_rr = (entry - current_price) / risk if risk > 0 else 0
            if not trade.partial_closed and current_price <= tp1:
                actions["partial_tp"] = True
                trade.partial_closed  = True
                trade.status          = "PARTIAL"
                self.logger.info(f"Partial TP hit [{trade.trade_id}]")
            if current_price <= entry - be_dist:
                actions["move_to_be"] = True

        return actions

    # ─────────────────────────────────────────────
    # RESET KILL SWITCH (manual intervention)
    # ─────────────────────────────────────────────

    def reset_kill_switch(self):
        self.kill_switch = False
        self.logger.warning("Kill switch manually reset — trade with caution")

    # ─────────────────────────────────────────────
    # STATS SUMMARY
    # ─────────────────────────────────────────────

    def summary(self) -> Dict:
        closed = self.closed_trades
        if not closed:
            return {"message": "No closed trades yet"}

        wins   = [t for t in closed if t.pnl > 0]
        losses = [t for t in closed if t.pnl < 0]
        total  = len(closed)
        wr     = len(wins) / total if total > 0 else 0

        avg_win  = sum(t.pnl_r for t in wins)  / len(wins)  if wins   else 0
        avg_loss = sum(t.pnl_r for t in losses) / len(losses) if losses else 0
        pf       = (avg_win * len(wins)) / abs(avg_loss * len(losses)) \
                   if losses and avg_loss != 0 else float("inf")

        return {
            "account_size_initial": self.account_size,
            "current_equity":       round(self.equity, 2),
            "total_trades":         total,
            "wins":                 len(wins),
            "losses":               len(losses),
            "win_rate":             round(wr, 3),
            "avg_win_r":            round(avg_win, 2),
            "avg_loss_r":           round(avg_loss, 2),
            "profit_factor":        round(pf, 2),
            "daily_loss_pct":       round(self.daily_loss_pct, 4),
            "weekly_loss_pct":      round(self.weekly_loss_pct, 4),
            "open_trades":          self.open_trade_count,
            "kill_switch":          self.kill_switch,
        }