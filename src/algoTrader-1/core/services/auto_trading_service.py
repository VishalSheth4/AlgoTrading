"""
Automated trade execution for signal-producing strategies.

STRICT RULE, enforced structurally (not just documented in a comment
somewhere): a trade signal is only ever evaluated from the LAST FULLY
CLOSED candle for that timeframe. MT5's copy_rates_from_pos always
returns the current, still-forming candle as the most recent bar for
intraday timeframes -- so this deliberately reads index -2 of the
enriched DataFrame, never -1. This rule applies to EVERY auto-trading
signal source, not just Green Dollar.

OCP: a new auto-tradeable signal is added by appending a SignalRule to
the `rules` list passed into __init__ -- process() itself never changes.
A rule can optionally be restricted to a single timeframe (e.g. the
Engulfing+Consolidation sell pattern is only ever evaluated on H1) via
`only_timeframe`; `None` means "every timeframe", matching Green Dollar's
existing behavior.

Each rule also carries the originating strategy's name so a caller-supplied
StrategyToggleRegistry can mute auto-trading for a strategy the user has
disabled in the UI, independent of the global Auto Trade on/off switch.

If the enriched DataFrame has {rule.column}_sl / {rule.column}_tp columns
(a strategy's own computed stop/target PRICES for that signal -- see e.g.
strategies/mark2.py), those are converted to a price DISTANCE from the
closed candle's close and carried on the TradeSignal as sl_distance/
tp_distance, so TradeExecutor uses that strategy's own risk instead of one
fixed distance for every strategy. Missing/invalid columns fall back to
None, which TradeExecutor treats as "use my own default distance".

Disabled by default (AUTO_TRADE_ENABLED=false) -- once enabled this
places REAL orders on whatever MT5 account is connected. Opt in via the
environment variable, or toggle it at runtime with set_enabled() (used
by the header button in the UI), after confirming lot size, stop-loss,
and take-profit (see trading/mt5_executor.py) are what you want, and
that "Algo Trading" is enabled in the MT5 terminal itself (MT5 silently
rejects orders otherwise).
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass

import pandas as pd

from .strategies.registry import StrategyToggleRegistry
from .trading.base import TradeExecutor, TradeSignal
from .trading.closed_candle_gate import ClosedCandleTradeGate

logger = logging.getLogger(__name__)

AUTO_TRADE_ENABLED = os.environ.get("AUTO_TRADE_ENABLED", "false").strip().lower() == "true"


@dataclass(frozen=True)
class SignalRule:
    column: str  # boolean column on the enriched DataFrame
    direction: str  # "buy" | "sell"
    strategy_name: str  # matches a StrategyToggleRegistry entry
    only_timeframe: str | None = None  # None = every timeframe


def _price_distance(closed: pd.Series, column: str, entry: float) -> float | None:
    """abs(entry - closed[column]) if that column exists and is a valid,
    non-zero price; None otherwise (caller falls back to a default)."""
    if column not in closed.index:
        return None
    value = closed[column]
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(value):
        return None
    distance = abs(entry - value)
    return distance if distance > 0 else None


class AutoTradingService:
    def __init__(
        self,
        executor: TradeExecutor,
        enabled: bool = AUTO_TRADE_ENABLED,
        rules: list[SignalRule] | None = None,
        registry: StrategyToggleRegistry | None = None,
    ):
        self._gate = ClosedCandleTradeGate(executor)
        self._enabled = enabled
        self._lock = threading.Lock()
        self._rules: list[SignalRule] = rules or []
        self._registry = registry
        # Dedups the "signal seen but Auto Trade is OFF" log line the same
        # way ClosedCandleTradeGate dedups real executions, so a disabled
        # toggle doesn't spam one line per poll for the same closed candle.
        self._last_seen_while_disabled: dict[str, int] = {}

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def set_enabled(self, value: bool) -> bool:
        """Toggle at runtime (used by the header Auto Trade button).
        Returns the new state."""
        with self._lock:
            self._enabled = bool(value)
            return self._enabled

    def process(self, timeframe: str, enriched: pd.DataFrame) -> dict | None:
        """Call once per timeframe per refresh with the full indicator/
        strategy-enriched DataFrame (oldest -> newest, last row still
        forming). Returns the trade result dict if a trade was
        attempted, else None."""
        n = len(enriched)
        if n < 2:
            return None

        # STRICT: always the second-to-last row -- the most recently
        # CLOSED candle -- never iloc[-1], which is still forming.
        closed = enriched.iloc[-2]
        candle_time_unix = int(pd.Timestamp(closed["time"]).timestamp())

        for rule in self._rules:
            if rule.only_timeframe is not None and rule.only_timeframe != timeframe:
                continue
            if self._registry is not None and not self._registry.is_enabled(rule.strategy_name):
                continue
            if not bool(closed.get(rule.column, False)):
                continue

            if not self.enabled:
                key = f"{timeframe}:{rule.direction}:{rule.column}"
                if self._last_seen_while_disabled.get(key) != candle_time_unix:
                    self._last_seen_while_disabled[key] = candle_time_unix
                    logger.warning(
                        "%s %s signal on %s @ %s -- Auto Trade is OFF, skipped",
                        rule.column, rule.direction.upper(), timeframe, closed["close"],
                    )
                return None

            entry = float(closed["close"])
            sl_distance = _price_distance(closed, f"{rule.column}_sl", entry)
            tp_distance = _price_distance(closed, f"{rule.column}_tp", entry)
            signal = TradeSignal(
                timeframe, rule.direction, candle_time_unix, entry, source=rule.column,
                sl_distance=sl_distance, tp_distance=tp_distance,
            )
            return self._gate.maybe_trade(signal)

        return None
