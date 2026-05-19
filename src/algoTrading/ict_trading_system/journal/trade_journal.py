"""
journal/trade_journal.py
========================
Persistent trade journal.
Records every trade with ICT-specific fields for post-session review.
Supports CSV export, filtering, and simple performance queries.
"""

from __future__ import annotations
import csv
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import List, Optional

from data.models import Trade

logger = logging.getLogger(__name__)

JOURNAL_FIELDNAMES = [
    "trade_id", "strategy_id", "instrument", "direction",
    "open_time", "close_time", "entry_price", "stop_loss",
    "take_profit_1", "take_profit_2", "close_price",
    "risk_amount", "position_size", "pnl", "pnl_r",
    "rr_ratio", "confidence", "checklist_score", "checklist_total",
    "session", "status", "partial_closed", "notes",
    # Reflection fields (filled manually or via update_reflection)
    "mistakes", "emotions", "improvements", "grade",
]


@dataclass
class JournalEntry:
    trade_id:        str
    strategy_id:     str
    instrument:      str
    direction:       str
    open_time:       str
    close_time:      str
    entry_price:     float
    stop_loss:       float
    take_profit_1:   float
    take_profit_2:   float
    close_price:     float
    risk_amount:     float
    position_size:   float
    pnl:             float
    pnl_r:           float
    rr_ratio:        float
    confidence:      float
    checklist_score: int
    checklist_total: int
    session:         str
    status:          str
    partial_closed:  bool
    notes:           str
    # Post-trade reflection
    mistakes:        str = ""
    emotions:        str = ""
    improvements:    str = ""
    grade:           str = ""   # A+ / A / B / C / D

    @classmethod
    def from_trade(cls, trade: Trade) -> "JournalEntry":
        sig = trade.signal
        return cls(
            trade_id        = trade.trade_id,
            strategy_id     = sig.strategy_id.value,
            instrument      = sig.instrument.value,
            direction       = sig.direction.value,
            open_time       = str(trade.open_time),
            close_time      = str(trade.close_time) if trade.close_time else "",
            entry_price     = sig.entry_price,
            stop_loss       = sig.stop_loss,
            take_profit_1   = sig.take_profit_1,
            take_profit_2   = sig.take_profit_2,
            close_price     = trade.close_price,
            risk_amount     = trade.risk_amount,
            position_size   = trade.position_size,
            pnl             = round(trade.pnl, 4),
            pnl_r           = round(trade.pnl_r, 3),
            rr_ratio        = sig.rr_ratio,
            confidence      = sig.confidence,
            checklist_score = sig.checklist_score,
            checklist_total = sig.checklist_total,
            session         = sig.session.value,
            status          = trade.status,
            partial_closed  = trade.partial_closed,
            notes           = sig.notes,
        )

    def auto_grade(self) -> str:
        """Assign a trade grade based on execution quality."""
        score = self.checklist_score / self.checklist_total if self.checklist_total > 0 else 0
        if score >= 0.9 and self.rr_ratio >= 3:
            return "A+"
        elif score >= 0.8 and self.rr_ratio >= 2:
            return "A"
        elif score >= 0.7:
            return "B"
        elif score >= 0.6:
            return "C"
        return "D"


class TradeJournal:
    """
    Persistent trade journal backed by a CSV file.
    """

    def __init__(self, filepath: str = "journal/trades/journal.csv"):
        self.filepath = filepath
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        self._entries: List[JournalEntry] = []
        self._load()
        self.logger = logging.getLogger(self.__class__.__name__)

    # ─────────────────────────────────────────────
    # LOAD / SAVE
    # ─────────────────────────────────────────────

    def _load(self):
        if not os.path.exists(self.filepath):
            return
        with open(self.filepath, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    entry = JournalEntry(
                        trade_id        = row["trade_id"],
                        strategy_id     = row["strategy_id"],
                        instrument      = row["instrument"],
                        direction       = row["direction"],
                        open_time       = row["open_time"],
                        close_time      = row.get("close_time", ""),
                        entry_price     = float(row["entry_price"]),
                        stop_loss       = float(row["stop_loss"]),
                        take_profit_1   = float(row["take_profit_1"]),
                        take_profit_2   = float(row["take_profit_2"]),
                        close_price     = float(row["close_price"]),
                        risk_amount     = float(row["risk_amount"]),
                        position_size   = float(row["position_size"]),
                        pnl             = float(row["pnl"]),
                        pnl_r           = float(row["pnl_r"]),
                        rr_ratio        = float(row["rr_ratio"]),
                        confidence      = float(row["confidence"]),
                        checklist_score = int(row["checklist_score"]),
                        checklist_total = int(row["checklist_total"]),
                        session         = row["session"],
                        status          = row["status"],
                        partial_closed  = row["partial_closed"] == "True",
                        notes           = row.get("notes", ""),
                        mistakes        = row.get("mistakes", ""),
                        emotions        = row.get("emotions", ""),
                        improvements    = row.get("improvements", ""),
                        grade           = row.get("grade", ""),
                    )
                    self._entries.append(entry)
                except (KeyError, ValueError) as e:
                    logger.warning(f"Skipped bad journal row: {e}")

    def _save_all(self):
        with open(self.filepath, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=JOURNAL_FIELDNAMES)
            writer.writeheader()
            for entry in self._entries:
                writer.writerow(asdict(entry))

    # ─────────────────────────────────────────────
    # ADD TRADE
    # ─────────────────────────────────────────────

    def record(self, trade: Trade) -> JournalEntry:
        entry       = JournalEntry.from_trade(trade)
        entry.grade = entry.auto_grade()
        self._entries.append(entry)
        self._save_all()
        self.logger.info(
            f"Journaled trade [{entry.trade_id}] | "
            f"{entry.instrument} | PnL={entry.pnl:+.2f} | Grade={entry.grade}"
        )
        return entry

    def record_batch(self, trades: List[Trade]):
        for trade in trades:
            entry       = JournalEntry.from_trade(trade)
            entry.grade = entry.auto_grade()
            self._entries.append(entry)
        self._save_all()
        self.logger.info(f"Journaled {len(trades)} trades")

    # ─────────────────────────────────────────────
    # UPDATE REFLECTION
    # ─────────────────────────────────────────────

    def update_reflection(
        self,
        trade_id:     str,
        mistakes:     str = "",
        emotions:     str = "",
        improvements: str = "",
        grade:        Optional[str] = None,
    ):
        for entry in self._entries:
            if entry.trade_id == trade_id:
                entry.mistakes     = mistakes
                entry.emotions     = emotions
                entry.improvements = improvements
                if grade:
                    entry.grade = grade
                self._save_all()
                return
        logger.warning(f"Trade {trade_id} not found in journal")

    # ─────────────────────────────────────────────
    # QUERIES
    # ─────────────────────────────────────────────

    def get_all(self) -> List[JournalEntry]:
        return list(self._entries)

    def filter_by_instrument(self, instrument: str) -> List[JournalEntry]:
        return [e for e in self._entries if e.instrument == instrument]

    def filter_by_strategy(self, strategy_id: str) -> List[JournalEntry]:
        return [e for e in self._entries if e.strategy_id == strategy_id]

    def filter_by_grade(self, grade: str) -> List[JournalEntry]:
        return [e for e in self._entries if e.grade == grade]

    def performance_by_strategy(self) -> dict:
        result = {}
        for e in self._entries:
            sid = e.strategy_id
            if sid not in result:
                result[sid] = {"trades": 0, "wins": 0, "total_r": 0.0}
            result[sid]["trades"] += 1
            if e.pnl > 0:
                result[sid]["wins"] += 1
            result[sid]["total_r"] += e.pnl_r
        for sid, stats in result.items():
            t = stats["trades"]
            stats["win_rate"] = round(stats["wins"] / t, 3) if t > 0 else 0
            stats["avg_r"]    = round(stats["total_r"] / t, 3) if t > 0 else 0
        return result

    def performance_by_session(self) -> dict:
        result = {}
        for e in self._entries:
            sess = e.session
            if sess not in result:
                result[sess] = {"trades": 0, "wins": 0, "total_r": 0.0}
            result[sess]["trades"] += 1
            if e.pnl > 0:
                result[sess]["wins"] += 1
            result[sess]["total_r"] += e.pnl_r
        for sess, stats in result.items():
            t = stats["trades"]
            stats["win_rate"] = round(stats["wins"] / t, 3) if t > 0 else 0
            stats["avg_r"]    = round(stats["total_r"] / t, 3) if t > 0 else 0
        return result

    def print_summary(self):
        entries = self._entries
        if not entries:
            print("No journal entries.")
            return

        total  = len(entries)
        wins   = sum(1 for e in entries if e.pnl > 0)
        total_r = sum(e.pnl_r for e in entries)
        grades = {"A+": 0, "A": 0, "B": 0, "C": 0, "D": 0}
        for e in entries:
            if e.grade in grades:
                grades[e.grade] += 1

        print(f"\n{'='*50}")
        print(f"  TRADE JOURNAL SUMMARY")
        print(f"{'='*50}")
        print(f"  Total Trades : {total}")
        print(f"  Win Rate     : {wins/total:.1%}")
        print(f"  Total R      : {total_r:+.2f}R")
        print(f"  Grades       : {grades}")
        print(f"{'='*50}\n")