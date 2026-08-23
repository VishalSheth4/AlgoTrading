"""
Facade over the NSE data sources + signal engine.

Design (SOLID):
- SRP:  this class only orchestrates "cycle through symbols/timeframes,
        detect signals, keep a running table" -- it doesn't know about
        HTTP/templates, and the data-source/strategy classes don't know
        about Django.
- OCP:  new timeframes/symbols are constructor parameters; new signal
        strategies plug into NseSignalEngine's list; new data sources
        plug into RoutedOhlcDataSource's mapping -- none of that requires
        editing this class.
- DIP:  the Django view depends on this facade, never on nsepython/
        yfinance/pandas directly.

Runs its own background thread rather than fetching on each HTTP request,
because NSE/Yahoo scraping is slow and must be rate-limited (one fetch
every `fetch_delay_seconds`, cycling through the whole symbol x timeframe
grid continuously) -- blocking a web request on that would be far too
slow and would hammer the external sources in lockstep with page loads.
The view just reads whatever the background thread has already computed.

The table is a growing LOG of distinct flip events (deduplicated by
(symbol, timeframe, strategy, candle time) -- see NseSignal.row_key()),
not a live-updating snapshot per stock, so the UI's "new row" highlight/
buzz has something meaningful to react to.
"""

from __future__ import annotations

import threading
import time

from .data_sources.base import OhlcDataSource
from .signals.base import NseSignal
from .signals.engine import NseSignalEngine

MAX_STORED_SIGNALS = 500


class NseDashboardService:
    def __init__(
        self,
        symbols: list[str],
        timeframes: list[str],
        data_source: OhlcDataSource,
        engine: NseSignalEngine,
        fetch_delay_seconds: float = 1.5,
    ):
        self._symbols = symbols
        self._timeframes = timeframes
        self._data_source = data_source
        self._engine = engine
        self._fetch_delay_seconds = fetch_delay_seconds

        self._signals_by_key: dict[tuple, dict] = {}
        self._lock = threading.Lock()
        self._started = False
        self._last_cycle_completed_at: float = 0.0

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        thread = threading.Thread(target=self._run_loop, daemon=True, name="nse-signal-refresh")
        thread.start()

    def _run_loop(self) -> None:
        while True:
            for symbol in self._symbols:
                for timeframe in self._timeframes:
                    try:
                        df = self._data_source.fetch(symbol, timeframe)
                        if not df.empty:
                            new_signals = self._engine.compute_for(symbol, timeframe, df)
                            if new_signals:
                                self._merge_signals(new_signals)
                    except Exception:
                        pass  # one bad symbol/timeframe must never stop the whole cycle
                    time.sleep(self._fetch_delay_seconds)
            with self._lock:
                self._last_cycle_completed_at = time.time()

    def _merge_signals(self, new_signals: list[NseSignal]) -> None:
        with self._lock:
            appended = False
            for signal in new_signals:
                key = signal.row_key()
                if key in self._signals_by_key:
                    continue
                self._signals_by_key[key] = signal.to_dict()
                appended = True

            if appended and len(self._signals_by_key) > MAX_STORED_SIGNALS:
                newest = sorted(
                    self._signals_by_key.items(),
                    key=lambda kv: kv[1]["detected_at_unix"],
                    reverse=True,
                )[:MAX_STORED_SIGNALS]
                self._signals_by_key = dict(newest)

    def get_signals(self) -> list[dict]:
        with self._lock:
            rows = list(self._signals_by_key.values())
        rows.sort(key=lambda r: r["detected_at_unix"], reverse=True)
        return rows

    @property
    def is_first_cycle_done(self) -> bool:
        with self._lock:
            return self._last_cycle_completed_at > 0.0


_service: NseDashboardService | None = None
_service_lock = threading.Lock()


def get_nse_dashboard_service() -> NseDashboardService:
    """Process-wide singleton; starts the background refresh thread on
    first access."""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                from .data_sources.cached_source import CachedOhlcDataSource
                from .data_sources.nsepython_source import NsepythonDataSource
                from .data_sources.routed_source import RoutedOhlcDataSource
                from .data_sources.yfinance_source import YFinanceDataSource
                from .signals.supertrend_flip import SupertrendFlipSignalStrategy
                from .symbols import NIFTY_50

                routed = RoutedOhlcDataSource({
                    "15m": YFinanceDataSource(),
                    "1h": YFinanceDataSource(),
                    "4h": YFinanceDataSource(),
                    "1d": NsepythonDataSource(),
                    "1w": NsepythonDataSource(),
                })
                cached = CachedOhlcDataSource(routed)
                engine = NseSignalEngine([SupertrendFlipSignalStrategy()])

                _service = NseDashboardService(
                    symbols=NIFTY_50,
                    timeframes=["15m", "1h", "4h", "1d", "1w"],
                    data_source=cached,
                    engine=engine,
                )
                _service.start()
    return _service
