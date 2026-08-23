"""
Runtime enable/disable registry for trading strategies.

SRP: this class only tracks which strategy names are currently enabled --
it knows nothing about HTTP, MT5, or how any given strategy computes its
signal.

OCP: CandleService seeds this once at startup with every registered
strategy's name. Three call sites then read it -- StrategyRunner (skip
compute() for a disabled strategy), the alert/marker loop in
CandleService.get_candles() (skip building markers/alerts for it), and
AutoTradingService (skip its auto-trade rule) -- so a new strategy gets
enable/disable, alert-muting, and auto-trade-muting for free, with zero
per-strategy special-casing anywhere.

Every strategy starts DISABLED: opening the dashboard for the first time
(or after a restart) should be quiet -- no alerts, no markers, no
auto-trading -- until you explicitly turn a strategy on, rather than
every strategy on every timeframe firing at once.
"""

from __future__ import annotations

import threading


class StrategyToggleRegistry:
    def __init__(self, names: list[str]):
        self._enabled: dict[str, bool] = {name: False for name in names}
        self._lock = threading.Lock()

    def is_enabled(self, name: str) -> bool:
        with self._lock:
            return self._enabled.get(name, False)

    def set_enabled(self, name: str, value: bool) -> bool:
        """Raises KeyError for an unregistered name -- callers (the API
        view) turn that into a 400 rather than silently no-op-ing."""
        with self._lock:
            if name not in self._enabled:
                raise KeyError(name)
            self._enabled[name] = bool(value)
            return self._enabled[name]

    def list_states(self) -> dict[str, bool]:
        with self._lock:
            return dict(self._enabled)
