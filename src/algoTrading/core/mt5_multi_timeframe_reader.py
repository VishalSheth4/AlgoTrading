"""
Parallel multi-timeframe MT5 data reader.

Design (SOLID):
- SRP:  connection handling, single-timeframe fetching, and orchestration
        each live in their own class.
- OCP:  new timeframes/data sources are added by registering another
        ITimeframeDataFetcher -- MultiTimeframeDataReader itself never
        changes.
- LSP:  any ITimeframeDataFetcher implementation is interchangeable
        wherever the interface is expected.
- ISP:  ITimeframeDataFetcher exposes exactly one method (fetch).
- DIP:  MultiTimeframeDataReader depends on the ITimeframeDataFetcher
        abstraction, not on MetaTrader5 directly -- the mt5 module is
        injected, so it can be swapped/mocked in tests.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import MetaTrader5 as mt5
import pandas as pd


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

class MT5ConnectionManager:
    """Owns the MT5 terminal connection lifecycle. Nothing else should
    call mt5.initialize()/mt5.shutdown() directly."""

    def __init__(self, mt5_module=mt5):
        self._mt5 = mt5_module
        self._connected = False

    def connect(self) -> bool:
        if not self._mt5.initialize():
            print("❌ MT5 Initialization Failed:", self._mt5.last_error())
            return False
        self._connected = True
        print("✅ MT5 Connected")
        return True

    def shutdown(self) -> None:
        if self._connected:
            self._mt5.shutdown()
            self._connected = False
            print("🔌 MT5 Disconnected")

    @property
    def is_connected(self) -> bool:
        return self._connected

    def __enter__(self) -> "MT5ConnectionManager":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.shutdown()


# ---------------------------------------------------------------------------
# Timeframe definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TimeframeSpec:
    name: str          # e.g. "M1"
    constant: int       # mt5.TIMEFRAME_M1 etc.


def default_timeframes(mt5_module=mt5) -> list[TimeframeSpec]:
    """The 5 timeframes requested: 1m, 5m, 15m, 30m, 1h."""
    return [
        TimeframeSpec("M1", mt5_module.TIMEFRAME_M1),
        TimeframeSpec("M5", mt5_module.TIMEFRAME_M5),
        TimeframeSpec("M15", mt5_module.TIMEFRAME_M15),
        TimeframeSpec("M30", mt5_module.TIMEFRAME_M30),
        TimeframeSpec("H1", mt5_module.TIMEFRAME_H1),
    ]


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

class ITimeframeDataFetcher(ABC):
    """Narrow interface: fetch one symbol's candles for one timeframe."""

    @abstractmethod
    def fetch(self, symbol: str, bars: int) -> pd.DataFrame:
        ...


class MT5TimeframeDataFetcher(ITimeframeDataFetcher):
    """Fetches candles for a single timeframe from MT5."""

    def __init__(self, mt5_module, timeframe: TimeframeSpec):
        self._mt5 = mt5_module
        self._timeframe = timeframe

    def fetch(self, symbol: str, bars: int) -> pd.DataFrame:
        rates = self._mt5.copy_rates_from_pos(symbol, self._timeframe.constant, 0, bars)

        if rates is None or len(rates) == 0:
            print(f"❌ [{self._timeframe.name}] No data for {symbol}: {self._mt5.last_error()}")
            return pd.DataFrame()

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df["timeframe"] = self._timeframe.name
        return df


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class MultiTimeframeDataReader:
    """Reads every registered timeframe for a symbol concurrently."""

    def __init__(self, fetchers: dict[str, ITimeframeDataFetcher], max_workers: int | None = None):
        self._fetchers = fetchers
        self._max_workers = max_workers or len(fetchers)

    def read_all(self, symbol: str, bars: int) -> dict[str, pd.DataFrame]:
        results: dict[str, pd.DataFrame] = {}

        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            future_to_name = {
                pool.submit(fetcher.fetch, symbol, bars): name
                for name, fetcher in self._fetchers.items()
            }

            for future in as_completed(future_to_name):
                name = future_to_name[future]
                try:
                    results[name] = future.result()
                except Exception as exc:
                    print(f"❌ [{name}] fetch raised: {exc}")
                    results[name] = pd.DataFrame()

        return results


# ---------------------------------------------------------------------------
# Factory (wires everything above together)
# ---------------------------------------------------------------------------

def build_default_reader(mt5_module=mt5) -> MultiTimeframeDataReader:
    fetchers = {
        tf.name: MT5TimeframeDataFetcher(mt5_module, tf)
        for tf in default_timeframes(mt5_module)
    }
    return MultiTimeframeDataReader(fetchers)


if __name__ == "__main__":
    SYMBOL = "XAUUSD"
    BARS = 500

    with MT5ConnectionManager() as conn:
        if not conn.is_connected:
            raise SystemExit(1)

        reader = build_default_reader()
        data = reader.read_all(SYMBOL, BARS)

        for tf_name, df in data.items():
            print(f"{tf_name}: {len(df)} candles" + (f" | last close={df.iloc[-1]['close']}" if not df.empty else ""))
