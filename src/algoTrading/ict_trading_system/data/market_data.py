"""
data/market_data.py
===================
Handles all OHLCV data ingestion:
  - CSV / Parquet loading
  - Multi-timeframe resampling
  - Live feed simulation (tick → candle aggregation)
  - Session tagging on every candle
"""

from __future__ import annotations
import os
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional
import pandas as pd
import numpy as np

from config.settings import (
    Instrument, Timeframe, Session,
    SessionConfig, DATA_DIR
)
from data.models import Candle

logger = logging.getLogger(__name__)
_session_cfg = SessionConfig()


# ─────────────────────────────────────────────
# SESSION DETECTION
# ─────────────────────────────────────────────

def detect_session(dt: datetime) -> Session:
    """
    Classify a UTC datetime into a trading session.
    Dead sessions are gaps between London and NY, and overnight.
    """
    hour = dt.hour
    cfg  = _session_cfg

    if cfg.london_open_prime[0] <= hour < cfg.london_open_prime[1]:
        return Session.LONDON
    if cfg.new_york_window[0] <= hour < cfg.new_york_window[1]:
        return Session.NEW_YORK
    if cfg.asian_window[0] <= hour < cfg.asian_window[1]:
        return Session.ASIAN
    return Session.DEAD


def tag_sessions(df: pd.DataFrame) -> pd.DataFrame:
    """Add a 'session' column to an OHLCV DataFrame."""
    df = df.copy()
    df["session"] = df.index.map(
        lambda ts: detect_session(ts.to_pydatetime()).value
    )
    return df


# ─────────────────────────────────────────────
# CSV LOADER
# ─────────────────────────────────────────────

def load_csv(
    filepath: str,
    instrument: Instrument,
    timeframe: Timeframe,
    date_col: str = "timestamp",
) -> pd.DataFrame:
    """
    Load OHLCV data from a CSV file.
    Expected columns: timestamp, open, high, low, close, volume
    Returns a DataFrame with a DatetimeIndex (UTC).
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Data file not found: {filepath}")

    df = pd.read_csv(filepath, parse_dates=[date_col])
    df.columns = [c.lower().strip() for c in df.columns]

    # Rename common variants
    col_map = {"time": "timestamp", "date": "timestamp",
               "vol": "volume", "tick_volume": "volume"}
    df.rename(columns=col_map, inplace=True)

    required = ["timestamp", "open", "high", "low", "close"]
    missing  = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns in {filepath}: {missing}")

    if "volume" not in df.columns:
        df["volume"] = 0.0

    df.set_index("timestamp", inplace=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df.sort_index(inplace=True)

    df["instrument"] = instrument.value
    df["timeframe"]  = timeframe.value

    df = tag_sessions(df)
    logger.info(f"Loaded {len(df)} candles  {instrument.value} {timeframe.value}")
    return df


# ─────────────────────────────────────────────
# RESAMPLE TO HIGHER TIMEFRAME
# ─────────────────────────────────────────────

PANDAS_FREQ: Dict[Timeframe, str] = {
    Timeframe.M1:  "1min",
    Timeframe.M5:  "5min",
    Timeframe.M15: "15min",
    Timeframe.H1:  "1H",
    Timeframe.H4:  "4H",
    Timeframe.D1:  "1D",
    Timeframe.W1:  "1W",
}

def resample(df: pd.DataFrame, target_tf: Timeframe) -> pd.DataFrame:
    """
    Resample a lower-timeframe OHLCV DataFrame to a higher timeframe.
    """
    freq = PANDAS_FREQ[target_tf]
    agg  = {
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }
    resampled = df.resample(freq).agg(agg).dropna()
    resampled["instrument"] = df["instrument"].iloc[0]
    resampled["timeframe"]  = target_tf.value
    resampled = tag_sessions(resampled)
    return resampled


# ─────────────────────────────────────────────
# MULTI-TIMEFRAME BUILDER
# ─────────────────────────────────────────────

class MultiTimeframeData:
    """
    Holds OHLCV DataFrames for multiple timeframes of a single instrument.
    Auto-builds higher TFs via resampling from a base TF.
    """

    def __init__(self, instrument: Instrument, base_tf: Timeframe, df_base: pd.DataFrame):
        self.instrument = instrument
        self.base_tf    = base_tf
        self._frames: Dict[Timeframe, pd.DataFrame] = {base_tf: df_base}

    def build_higher_tfs(self, targets: List[Timeframe]) -> None:
        for tf in targets:
            if tf not in self._frames:
                self._frames[tf] = resample(self._frames[self.base_tf], tf)
                logger.debug(f"Built {tf.value} from {self.base_tf.value}")

    def get(self, tf: Timeframe) -> pd.DataFrame:
        if tf not in self._frames:
            raise KeyError(f"Timeframe {tf} not available. Call build_higher_tfs() first.")
        return self._frames[tf]

    def candles(self, tf: Timeframe) -> List[Candle]:
        """Convert DataFrame slice to list of Candle objects."""
        df  = self.get(tf)
        out = []
        for ts, row in df.iterrows():
            out.append(Candle(
                timestamp  = ts.to_pydatetime(),
                open       = float(row["open"]),
                high       = float(row["high"]),
                low        = float(row["low"]),
                close      = float(row["close"]),
                volume     = float(row.get("volume", 0)),
                instrument = self.instrument,
                timeframe  = tf,
            ))
        return out

    def latest_candles(self, tf: Timeframe, n: int = 100) -> List[Candle]:
        df = self.get(tf).tail(n)
        return self._df_to_candles(df, tf)

    def _df_to_candles(self, df: pd.DataFrame, tf: Timeframe) -> List[Candle]:
        out = []
        for ts, row in df.iterrows():
            out.append(Candle(
                timestamp  = ts.to_pydatetime(),
                open       = float(row["open"]),
                high       = float(row["high"]),
                low        = float(row["low"]),
                close      = float(row["close"]),
                volume     = float(row.get("volume", 0)),
                instrument = self.instrument,
                timeframe  = tf,
            ))
        return out


# ─────────────────────────────────────────────
# ASIAN RANGE EXTRACTOR
# ─────────────────────────────────────────────

def get_asian_range(df: pd.DataFrame, date: Optional[str] = None) -> Dict[str, float]:
    """
    Extract Asian session high and low for a given date.
    If date is None, uses the most recent complete Asian session.
    """
    asian_df = df[df["session"] == Session.ASIAN.value]
    if date:
        asian_df = asian_df[asian_df.index.date == pd.Timestamp(date).date()]
    else:
        last_date = asian_df.index.date[-1] if len(asian_df) > 0 else None
        if last_date:
            asian_df = asian_df[asian_df.index.date == last_date]

    if asian_df.empty:
        return {"high": None, "low": None}

    return {
        "high": float(asian_df["high"].max()),
        "low":  float(asian_df["low"].min()),
        "date": str(asian_df.index[0].date()),
    }


# ─────────────────────────────────────────────
# PREVIOUS DAY HIGH / LOW
# ─────────────────────────────────────────────

def get_previous_day_levels(df_d1: pd.DataFrame) -> Dict[str, float]:
    """Return previous day's high and low from a daily DataFrame."""
    if len(df_d1) < 2:
        return {"pdh": None, "pdl": None}
    prev = df_d1.iloc[-2]
    return {
        "pdh": float(prev["high"]),
        "pdl": float(prev["low"]),
        "date": str(df_d1.index[-2].date()),
    }


# ─────────────────────────────────────────────
# SYNTHETIC DATA GENERATOR (for testing/backtesting)
# ─────────────────────────────────────────────

def generate_synthetic_data(
    instrument: Instrument = Instrument.EURUSD,
    timeframe:  Timeframe  = Timeframe.M5,
    n_candles:  int        = 5000,
    start:      str        = "2024-01-01",
    seed:       int        = 42,
) -> pd.DataFrame:
    """
    Generate realistic synthetic OHLCV data for testing.
    Uses geometric Brownian motion with session-based volatility.
    """
    np.random.seed(seed)
    freq = PANDAS_FREQ[timeframe]
    idx  = pd.date_range(start=start, periods=n_candles, freq=freq, tz="UTC")

    base_price = {
        Instrument.EURUSD: 1.0800,
        Instrument.GBPUSD: 1.2700,
        Instrument.XAUUSD: 2000.0,
        Instrument.NAS100: 17000.0,
        Instrument.US500:  4800.0,
    }.get(instrument, 1.0)

    vol_map = {
        Session.ASIAN.value:    0.0002,
        Session.LONDON.value:   0.0005,
        Session.NEW_YORK.value: 0.0006,
        Session.DEAD.value:     0.0001,
    }

    prices  = [base_price]
    sessions = [detect_session(ts.to_pydatetime()).value for ts in idx]

    for i in range(1, n_candles):
        vol   = vol_map[sessions[i]] * base_price
        drift = 0.00001
        chg   = np.random.normal(drift, vol)
        prices.append(max(prices[-1] + chg, base_price * 0.5))

    opens, highs, lows, closes, volumes = [], [], [], [], []
    for i, close in enumerate(prices):
        o = prices[i - 1] if i > 0 else close
        noise_h = abs(np.random.normal(0, abs(close - o) * 0.5 + 0.00001))
        noise_l = abs(np.random.normal(0, abs(close - o) * 0.5 + 0.00001))
        h = max(o, close) + noise_h
        l = min(o, close) - noise_l
        opens.append(o); highs.append(h)
        lows.append(l);  closes.append(close)
        volumes.append(np.random.randint(100, 5000))

    df = pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
        "instrument": instrument.value,
        "timeframe":  timeframe.value,
        "session":    sessions,
    }, index=idx)

    return df