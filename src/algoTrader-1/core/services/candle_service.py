"""
Facade over MT5ConnectionManager + MultiTimeframeDataReader + indicators
for Django views.

Design (SOLID):
- SRP:  CandleService only orchestrates "connect, fetch, enrich, trim,
        format" -- it doesn't know about HTTP/templates, and the
        reader/indicator/flip-detector classes don't know about Django.
- OCP:  New indicators (engulfing, RSI, ...) are added by appending an
        Indicator to the pipeline list, new trading strategies (green
        dollar, ...) by appending a Strategy to the strategies list, and
        new Smart Money Concepts (order blocks, breaker blocks, and the
        100+ to follow) by appending an SMCConcept to the smc list --
        never by editing get_candles() itself.
- DIP:  Views depend on CandleService (an abstraction over MT5 +
        indicator details), never on MetaTrader5/pandas directly.

A single process-wide instance is reused across requests so every 15s
poll doesn't pay the cost of re-initializing the MT5 connection or
recreating the indicator pipeline.
"""

from __future__ import annotations

import os
import threading

import pandas as pd

from .auto_trading_service import AutoTradingService, SignalRule
from .indicators.base import Indicator
from .indicators.engulfing import EngulfingPatternIndicator
from .indicators.flip_detector import TrendFlipDetector
from .indicators.rsi import RsiIndicator
from .indicators.supertrend import SupertrendIndicator
from .mt5_connection import get_shared_connection
from .mt5_multi_timeframe_reader import MultiTimeframeDataReader, build_default_reader
from .smc.base import SMCConcept
from .smc.breaker_blocks import BreakerBlockConcept
from .smc.engine import SMCEngine
from .smc.order_blocks import OrderBlockConcept
from .strategies.base import Strategy
from .strategies.engulfing_consolidation import EngulfingConsolidationStrategy
from .strategies.engulfing_consolidation_sell import EngulfingConsolidationSellStrategy
from .strategies.engulfing_reversal import EngulfingReversalStrategy
from .strategies.engulfing_signal import EngulfingSignalStrategy
from .strategies.engulf_volume_fade import EngulfVolumeFadeStrategy
from .strategies.ema20 import Ema20Strategy
from .strategies.green_dollar import GreenDollarStrategy
from .strategies.green_dollar_clone import GreenDollarCloneStrategy
from .strategies.mark2 import Mark2Strategy
from .strategies.mark_dollar_supertrend import MarkDollarSupertrendStrategy
from .strategies.moving_average import MovingAverageStrategy
from .strategies.registry import StrategyToggleRegistry
from .strategies.runner import StrategyRunner
from .strategies.triple_wick_rejection import TripleWickRejectionStrategy
from .trading.mt5_executor import MT5TradeExecutor

# Alert type used for the (pre-existing) Supertrend-flip alert, which isn't
# a generic Strategy -- it reads the "trend" column already computed by
# SupertrendIndicator. Still registered in StrategyToggleRegistry so it
# gets the same UI enable/disable button as every other strategy.
SUPERTREND_FLIP_NAME = "supertrend_flip"
# RR for supertrend_flip's own auto-trade SL/TP (see get_candles() below) --
# defaults to 1:1 same as every other strategy's RR default.
SUPERTREND_FLIP_RR = 1.0

# Engulfing+Consolidation SELL pattern is deliberately evaluated ONLY on
# this timeframe (pattern shape is timeframe-dependent), then broadcast
# onto every other timeframe's chart -- see get_candles() below.
SELL_PATTERN_TIMEFRAME = "H1"

ZONE_KINDS = ("order_block_bullish", "order_block_bearish", "breaker_block_bullish", "breaker_block_bearish")

DEFAULT_SYMBOL = os.environ.get("MT5_SYMBOL", "XAUUSD")
DISPLAY_BARS = int(os.environ.get("MT5_DISPLAY_BARS", "200"))
# Extra history fetched so the Supertrend/ATR warm-up doesn't distort the
# indicator values shown for the last DISPLAY_BARS candles.
FETCH_BARS = int(os.environ.get("MT5_FETCH_BARS", "250"))
SUPERTREND_PERIOD = int(os.environ.get("SUPERTREND_PERIOD", "10"))
SUPERTREND_MULTIPLIER = float(os.environ.get("SUPERTREND_MULTIPLIER", "3"))


class CandleService:
    def __init__(
        self,
        symbol: str = DEFAULT_SYMBOL,
        fetch_bars: int = FETCH_BARS,
        display_bars: int = DISPLAY_BARS,
        indicators: list[Indicator] | None = None,
        strategies: list[Strategy] | None = None,
        smc_concepts: list[SMCConcept] | None = None,
        flip_detector: TrendFlipDetector | None = None,
        auto_trading: AutoTradingService | None = None,
    ):
        self._symbol = symbol
        self._fetch_bars = fetch_bars
        self._display_bars = display_bars
        self._connection = get_shared_connection()
        self._reader: MultiTimeframeDataReader = build_default_reader()
        self._indicators = indicators or [
            SupertrendIndicator(SUPERTREND_PERIOD, SUPERTREND_MULTIPLIER),
            EngulfingPatternIndicator(),
            RsiIndicator(),
        ]
        # Every generic per-timeframe strategy: each produces uniform
        # {name}_bull / {name}_bear / {name}_bull_price / {name}_bear_price
        # columns, which is what lets get_candles() build markers, alerts,
        # and auto-trade rules for all of them with one shared loop below
        # instead of one hardcoded block per strategy. Adding a new
        # strategy is just appending an instance here -- nothing else in
        # this class needs to change.
        self._generic_strategies: list[Strategy] = strategies or [
            GreenDollarStrategy(),
            GreenDollarCloneStrategy(),
            TripleWickRejectionStrategy(),
            MovingAverageStrategy(),
            EngulfingSignalStrategy(),
            EngulfingReversalStrategy(),
            EngulfingConsolidationStrategy(),
            Mark2Strategy(),
            MarkDollarSupertrendStrategy(),
            Ema20Strategy(),
            EngulfVolumeFadeStrategy(),
        ]
        # New SMC concepts (100+ to come) plug in here the same way.
        self._smc_engine = SMCEngine(smc_concepts or [OrderBlockConcept(), BreakerBlockConcept()])
        self._flip_detector = flip_detector or TrendFlipDetector()
        # Cross-timeframe SELL-only strategy -- evaluated manually against
        # a single timeframe (see get_candles()), not through the generic
        # per-timeframe StrategyRunner above.
        self._sell_pattern_strategy = EngulfingConsolidationSellStrategy()

        # One enabled/disabled flag per strategy (including the
        # non-Strategy-object Supertrend flip alert), backing the UI's
        # per-strategy toggle buttons. Read by StrategyRunner (skips
        # compute() when off), get_candles() (skips markers/alerts when
        # off), and AutoTradingService (skips auto-trading when off).
        self._registry = StrategyToggleRegistry(
            [s.name for s in self._generic_strategies]
            + [self._sell_pattern_strategy.name, SUPERTREND_FLIP_NAME]
        )
        self._strategy_runner = StrategyRunner(self._generic_strategies, registry=self._registry)

        auto_trade_rules = [
            SignalRule(f"{s.name}_bull", "buy", strategy_name=s.name) for s in self._generic_strategies
        ] + [
            SignalRule(f"{s.name}_bear", "sell", strategy_name=s.name) for s in self._generic_strategies
        ] + [
            SignalRule(
                self._sell_pattern_strategy.name, "sell",
                strategy_name=self._sell_pattern_strategy.name,
                only_timeframe=SELL_PATTERN_TIMEFRAME,
            ),
            # Supertrend flip has the same enable/disable button as every
            # other strategy in the UI -- that button must also gate
            # auto-trading, not just the alert. See the
            # {SUPERTREND_FLIP_NAME}_bull/_bear columns computed in
            # get_candles() below (alerting itself still goes through
            # TrendFlipDetector, unrelated to these columns).
            SignalRule(f"{SUPERTREND_FLIP_NAME}_bull", "buy", strategy_name=SUPERTREND_FLIP_NAME),
            SignalRule(f"{SUPERTREND_FLIP_NAME}_bear", "sell", strategy_name=SUPERTREND_FLIP_NAME),
        ]
        # Disabled by default (AUTO_TRADE_ENABLED env var) -- places REAL
        # orders once turned on. See auto_trading_service.py.
        self._auto_trading = auto_trading or AutoTradingService(
            MT5TradeExecutor(),
            rules=auto_trade_rules,
            registry=self._registry,
        )
        self._lock = threading.Lock()

    def _ensure_connected(self) -> bool:
        if self._connection.is_connected:
            return True
        return self._connection.connect()

    def get_candles(self) -> dict:
        """Returns {"symbol", "timeframes": {tf: {...}}, "alerts": [...]}."""
        with self._lock:
            if not self._ensure_connected():
                return {"symbol": self._symbol, "timeframes": {}, "alerts": []}
            raw = self._reader.read_all(self._symbol, self._fetch_bars)

        timeframes: dict[str, dict] = {}
        alerts: list[dict] = []
        trades: list[dict] = []

        # ---- Engulfing+Consolidation SELL pattern: H1-only evaluation,
        # broadcast to every chart -----------------------------------
        # Computed once, up front, on H1 data alone (not per-timeframe --
        # this pattern's shape is meaningless on other timeframes' bars).
        # `sell_points` holds every past CLOSED signal's (time, price) so
        # each timeframe's loop iteration below can place a marker on
        # whichever of ITS OWN bars was open at that moment. Alerting and
        # auto-trading both use the strict closed-candle rule (iloc[-2]),
        # same as every other signal in this file.
        sell_col = self._sell_pattern_strategy.name
        sell_points: list[tuple[int, float]] = []
        h1_raw = raw.get(SELL_PATTERN_TIMEFRAME)
        if self._registry.is_enabled(sell_col) and h1_raw is not None and not h1_raw.empty:
            h1_enriched = h1_raw
            for indicator in self._indicators:
                h1_enriched = indicator.compute(h1_enriched)
            h1_enriched = self._sell_pattern_strategy.compute(h1_enriched)

            trade_result = self._auto_trading.process(SELL_PATTERN_TIMEFRAME, h1_enriched)
            if trade_result is not None:
                trades.append({"timeframe": SELL_PATTERN_TIMEFRAME, **trade_result})

            if len(h1_enriched) >= 2 and bool(h1_enriched.iloc[-2][sell_col]):
                alerts.append({"timeframe": SELL_PATTERN_TIMEFRAME, "direction": "bearish", "type": sell_col})

            if len(h1_enriched) >= 1:
                epoch = pd.Timestamp("1970-01-01")
                h1_time_unix = (h1_enriched["time"] - epoch) // pd.Timedelta(seconds=1)
                # STRICT closed-candle rule: the still-forming last row is
                # dropped entirely, not just blanked -- it can never be a
                # confirmed signal.
                closed_mask = h1_enriched[sell_col].to_numpy()[:-1]
                closed_times = h1_time_unix.to_numpy()[:-1][closed_mask]
                closed_prices = h1_enriched["high"].to_numpy()[:-1][closed_mask]
                sell_points = list(zip(closed_times.tolist(), closed_prices.tolist()))

        for timeframe, df in raw.items():
            if df.empty:
                timeframes[timeframe] = {
                    "candles": [], "supertrend": [], "trend": None,
                    "flipped": False, "flip_direction": None,
                    "markers": {}, "zones": {kind: [] for kind in ZONE_KINDS},
                }
                continue

            enriched = df
            for indicator in self._indicators:
                enriched = indicator.compute(enriched)
            enriched = self._strategy_runner.run(enriched)

            # Auto-trade-only columns for the Supertrend flip "strategy"
            # (not a generic Strategy object -- see SUPERTREND_FLIP_NAME).
            # Deliberately separate from TrendFlipDetector, which still
            # drives the alert below unchanged -- this only feeds the
            # SignalRule pair registered in __init__ so the same
            # enable/disable button also controls auto-trading for it.
            prev_trend = enriched["trend"].shift(1)
            enriched[f"{SUPERTREND_FLIP_NAME}_bull"] = (prev_trend == -1) & (enriched["trend"] == 1)
            enriched[f"{SUPERTREND_FLIP_NAME}_bear"] = (prev_trend == 1) & (enriched["trend"] == -1)

            # Per-signal SL/TP: SL = the Supertrend line's own value (the
            # natural trailing stop for a Supertrend-based entry), TP =
            # RR-based off that same risk.
            entry = enriched["close"]
            st_line = enriched["supertrend"]
            bull_risk = entry - st_line
            bear_risk = st_line - entry
            enriched[f"{SUPERTREND_FLIP_NAME}_bull_sl"] = st_line.where(bull_risk > 0)
            enriched[f"{SUPERTREND_FLIP_NAME}_bull_tp"] = (entry + SUPERTREND_FLIP_RR * bull_risk).where(bull_risk > 0)
            enriched[f"{SUPERTREND_FLIP_NAME}_bear_sl"] = st_line.where(bear_risk > 0)
            enriched[f"{SUPERTREND_FLIP_NAME}_bear_tp"] = (entry - SUPERTREND_FLIP_RR * bear_risk).where(bear_risk > 0)

            # STRICT RULE: reads only the last FULLY CLOSED candle
            # (enriched.iloc[-2]) -- never the live/forming one. See
            # auto_trading_service.py. No-ops entirely when
            # AUTO_TRADE_ENABLED is false (the default).
            trade_result = self._auto_trading.process(timeframe, enriched)
            if trade_result is not None:
                trades.append({"timeframe": timeframe, **trade_result})

            # STRICT RULE: same closed-candle-only principle as Green Dollar
            # and auto-trading above -- detect the flip only between the two
            # most recently CLOSED candles (enriched.iloc[:-1] drops the
            # still-forming last row). Detecting on the live candle would
            # fire/clear the alert on every tick as price crosses the
            # Supertrend line mid-candle, before the flip is confirmed.
            flip_direction = self._flip_detector.detect(enriched.iloc[:-1]) if len(enriched) >= 3 else None
            latest_trend = int(enriched["trend"].iloc[-1])

            if flip_direction and self._registry.is_enabled(SUPERTREND_FLIP_NAME):
                alerts.append({"timeframe": timeframe, "direction": flip_direction, "type": SUPERTREND_FLIP_NAME})

            # SMC zones are detected over the FULL fetched history (more
            # context than the display window needs for swing-point/BOS
            # detection), then filtered down to whatever's still relevant
            # to what's actually shown.
            epoch = pd.Timestamp("1970-01-01")
            full_time_unix = ((enriched["time"] - epoch) // pd.Timedelta(seconds=1)).astype(int).to_numpy()
            zones_by_kind = self._smc_engine.run(enriched)

            display = enriched.tail(self._display_bars).copy()
            # Epoch-subtraction is resolution-agnostic (pandas may store
            # datetime64 in "s", "us", or "ns" units depending on version).
            display["time_unix"] = ((display["time"] - epoch) // pd.Timedelta(seconds=1)).astype(int)
            display["time"] = display["time"].astype(str)

            # STRICT RULE: every generic strategy's signal is only ever
            # confirmed on a CLOSED candle -- the live/forming candle
            # (always the last row) is blanked out here so no marker
            # appears for it. Its own OHLC keeps changing tick to tick, so
            # a signal reading taken mid-candle could flip on/off before
            # that candle actually closes. Mirrors the closed-candle rule
            # already applied to auto-trade execution above.
            if len(display) > 0:
                for strategy in self._generic_strategies:
                    bull_col, bear_col = f"{strategy.name}_bull", f"{strategy.name}_bear"
                    if bull_col not in display.columns:
                        continue
                    display.iloc[-1, display.columns.get_loc(bull_col)] = False
                    display.iloc[-1, display.columns.get_loc(bear_col)] = False

            # Broadcast the H1-only SELL pattern onto THIS timeframe's own
            # bars: each H1 signal is placed on whichever of this
            # timeframe's candles was the most recently opened one at that
            # signal's time (searchsorted finds the latest bar <= signal
            # time), so the marker lands on a real, existing bar in every
            # chart regardless of that timeframe's own boundary alignment.
            sell_pattern_markers = []
            if sell_points:
                bar_times = display["time_unix"]
                for sig_time, _sig_price in sell_points:
                    idx = int(bar_times.searchsorted(sig_time, side="right")) - 1
                    if idx < 0:
                        continue
                    sell_pattern_markers.append({
                        "time_unix": int(bar_times.iloc[idx]),
                        "price": float(display["high"].iloc[idx]),
                        "direction": "bear",
                    })

            display_start_unix = int(display["time_unix"].iloc[0])
            last_index = len(enriched) - 1
            zones_payload = {kind: [] for kind in ZONE_KINDS}
            for kind, zone_list in zones_by_kind.items():
                if kind not in zones_payload:
                    continue  # unknown kind -- ignore rather than guess
                for zone in zone_list:
                    payload = zone.to_payload(full_time_unix, last_index)
                    if payload["end_time_unix"] >= display_start_unix:
                        zones_payload[kind].append(payload)

            candles = display[["time", "time_unix", "open", "high", "low", "close", "tick_volume", "engulfing"]].to_dict(orient="records")
            # "trend" per point lets the frontend split the line into
            # green (uptrend) / red (downtrend) segments with shaded fill.
            supertrend_line = display[["time_unix", "supertrend", "trend"]].rename(columns={"supertrend": "value"}).to_dict(orient="records")

            # Strategy markers, keyed by strategy name so the frontend/future
            # strategies don't need special-casing -- each entry is just
            # {name: [{time_unix, price, direction}, ...]}. Skips any
            # strategy the user has disabled in the UI (no compute() ran
            # for it, so its columns are absent -- see StrategyRunner).
            #
            # Alerts fire only on a FRESH signal on the last FULLY CLOSED
            # candle (enriched.iloc[-2] -- same strict rule as auto-trading,
            # never the live/forming enriched.iloc[-1]), not every
            # historical marker already sitting on the chart. Same
            # {timeframe, direction, type} shape as Supertrend flips so the
            # frontend's existing alert pipeline (header flash, buzz sound,
            # "latest change" label, ON/OFF toggle) handles all of them
            # without special-casing.
            strategy_markers: dict[str, list[dict]] = {}
            closed_row = enriched.iloc[-2] if len(enriched) >= 2 else None

            for strategy in self._generic_strategies:
                name = strategy.name
                bull_col, bear_col = f"{name}_bull", f"{name}_bear"
                if bull_col not in display.columns:
                    continue

                bull_price_col, bear_price_col = f"{name}_bull_price", f"{name}_bear_price"
                strategy_markers[name] = (
                    [
                        {"time_unix": int(t), "price": float(p), "direction": "bull"}
                        for t, p in zip(
                            display.loc[display[bull_col], "time_unix"],
                            display.loc[display[bull_col], bull_price_col],
                        )
                    ]
                    + [
                        {"time_unix": int(t), "price": float(p), "direction": "bear"}
                        for t, p in zip(
                            display.loc[display[bear_col], "time_unix"],
                            display.loc[display[bear_col], bear_price_col],
                        )
                    ]
                )

                if closed_row is not None:
                    if bool(closed_row.get(bull_col, False)):
                        alerts.append({"timeframe": timeframe, "direction": "bullish", "type": name})
                    if bool(closed_row.get(bear_col, False)):
                        alerts.append({"timeframe": timeframe, "direction": "bearish", "type": name})

            timeframes[timeframe] = {
                "candles": candles,
                "supertrend": supertrend_line,
                "trend": "up" if latest_trend == 1 else "down",
                "flipped": flip_direction is not None,
                "flip_direction": flip_direction,
                "markers": {
                    **strategy_markers,
                    sell_col: sell_pattern_markers,
                },
                "zones": zones_payload,
            }

        return {"symbol": self._symbol, "timeframes": timeframes, "alerts": alerts, "trades": trades}

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def auto_trade_enabled(self) -> bool:
        return self._auto_trading.enabled

    def set_auto_trade_enabled(self, value: bool) -> bool:
        """Runtime toggle for the header Auto Trade button. Returns the
        new state. Places REAL orders once True -- see auto_trading_service.py."""
        return self._auto_trading.set_enabled(value)

    def list_strategy_states(self) -> dict[str, bool]:
        """{strategy_name: enabled} for every registered strategy, backing
        the UI's per-strategy toggle buttons."""
        return self._registry.list_states()

    def set_strategy_enabled(self, name: str, value: bool) -> bool:
        """Raises KeyError if `name` isn't a registered strategy."""
        return self._registry.set_enabled(name, value)

    def _rr_configurable_strategy(self, name: str) -> Strategy:
        """Look up a generic strategy that exposes set_rr()/rr (see
        Ema20Strategy). Raises KeyError if `name` isn't registered or
        doesn't support a runtime-adjustable RR."""
        for strategy in self._generic_strategies:
            if strategy.name == name and hasattr(strategy, "set_rr"):
                return strategy
        raise KeyError(name)

    def list_strategy_rr(self) -> dict[str, float]:
        """{strategy_name: rr} for every strategy whose RR is runtime-
        configurable, backing the UI's per-strategy RR input."""
        return {s.name: s.rr for s in self._generic_strategies if hasattr(s, "set_rr")}

    def set_strategy_rr(self, name: str, value: float) -> float:
        """Raises KeyError if `name` isn't a registered, RR-configurable
        strategy. Takes effect on the strategy's next compute() call --
        no restart needed."""
        strategy = self._rr_configurable_strategy(name)
        strategy.set_rr(value)
        return strategy.rr

    def _supertrend_filter_configurable_strategy(self, name: str) -> Strategy:
        """Look up a generic strategy that exposes set_supertrend_filter()/
        supertrend_filter (see Ema20Strategy). Raises KeyError if `name`
        isn't registered or doesn't support this filter."""
        for strategy in self._generic_strategies:
            if strategy.name == name and hasattr(strategy, "set_supertrend_filter"):
                return strategy
        raise KeyError(name)

    def list_strategy_supertrend_filter(self) -> dict[str, bool]:
        """{strategy_name: enabled} for every strategy that supports a
        Supertrend-direction confirmation filter, backing the Settings UI's
        per-strategy checkbox."""
        return {
            s.name: s.supertrend_filter
            for s in self._generic_strategies
            if hasattr(s, "set_supertrend_filter")
        }

    def set_strategy_supertrend_filter(self, name: str, value: bool) -> bool:
        """Raises KeyError if `name` isn't registered or doesn't support
        this filter. Takes effect on the strategy's next compute() call --
        no restart needed."""
        strategy = self._supertrend_filter_configurable_strategy(name)
        strategy.set_supertrend_filter(value)
        return strategy.supertrend_filter


_service: CandleService | None = None
_service_lock = threading.Lock()


def get_candle_service() -> CandleService:
    """Process-wide singleton so the MT5 connection is opened once."""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = CandleService()
    return _service
