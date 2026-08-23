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

from .broker_time import get_broker_time_offset

# FX/gold markets are closed roughly Friday evening through Sunday evening
# (exact minute is broker-specific) -- some MT5 demo/prop-firm servers keep
# serving organic-looking-but-fabricated bars through that window anyway,
# which would otherwise feed strategies (and live auto-trading) phantom
# weekend signals. Drop anything whose CORRECTED UTC timestamp falls here.
# Mirrors algoTrading/data/fetch_mt5.py's identical filter -- adjust both
# together if your broker's published market hours differ.
MARKET_CLOSE_WEEKDAY = 4   # Friday (Monday=0 .. Sunday=6)
MARKET_CLOSE_HOUR_UTC = 21
MARKET_OPEN_WEEKDAY = 6    # Sunday
MARKET_OPEN_HOUR_UTC = 21


def _drop_weekend_bars(df: pd.DataFrame) -> pd.DataFrame:
    minutes = df["time"].dt.weekday * 24 * 60 + df["time"].dt.hour * 60 + df["time"].dt.minute
    close_minutes = MARKET_CLOSE_WEEKDAY * 24 * 60 + MARKET_CLOSE_HOUR_UTC * 60
    open_minutes = MARKET_OPEN_WEEKDAY * 24 * 60 + MARKET_OPEN_HOUR_UTC * 60
    closed = (minutes >= close_minutes) & (minutes < open_minutes)
    dropped = int(closed.sum())
    if dropped:
        print(f"⚠️ [weekend filter] Dropped {dropped} bar(s) inside market-closed hours")
    return df.loc[~closed].reset_index(drop=True)


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
    """The 6 timeframes requested: 1m, 5m, 15m, 30m, 1h, 4h."""
    return [
        TimeframeSpec("M1", mt5_module.TIMEFRAME_M1),
        TimeframeSpec("M5", mt5_module.TIMEFRAME_M5),
        TimeframeSpec("M15", mt5_module.TIMEFRAME_M15),
        TimeframeSpec("M30", mt5_module.TIMEFRAME_M30),
        TimeframeSpec("H1", mt5_module.TIMEFRAME_H1),
        TimeframeSpec("H4", mt5_module.TIMEFRAME_H4),
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
        # MT5's server clock is commonly UTC+2/UTC+3 (EET/EEST), not true
        # UTC -- correct every candle's timestamp here, at the source, so
        # every downstream consumer (indicators, strategies, the UI) works
        # with real UTC times. See broker_time.py.
        df["time"] = df["time"] - get_broker_time_offset(self._mt5, symbol).get_offset()
        df = _drop_weekend_bars(df)
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
