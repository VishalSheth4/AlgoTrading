"""
fetch_nse.py — NSE / BSE stock data fetcher via yfinance.

Supports all yfinance intervals:
  Intraday : 1m, 2m, 5m, 15m, 30m, 60m, 90m
             (limited history: 1m→7d, others→60d max)
  Daily+   : 1d, 5d, 1wk, 1mo, 3mo  (full history available)

Symbol mapping:
  NSE symbol  →  append ".NS"  e.g. RELIANCE → RELIANCE.NS
  BSE symbol  →  append ".BO"  e.g. RELIANCE → RELIANCE.BO

Output DataFrame columns: time, open, high, low, close, volume
  (matches the same OHLCV schema used by MT5 strategies)
"""

import time
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

# ── yfinance interval → max lookback days ─────────────────────────────────────
_MAX_DAYS = {
    "1m":  7,
    "2m":  60,
    "5m":  60,
    "15m": 60,
    "30m": 60,
    "60m": 730,
    "90m": 60,
    "1h":  730,
    "1d":  None,   # unlimited
    "5d":  None,
    "1wk": None,
    "1mo": None,
    "3mo": None,
}

# ── Exchange suffix ────────────────────────────────────────────────────────────
_SUFFIX = {"NSE": ".NS", "BSE": ".BO"}

# Popular NSE indices (use ^ prefix for yfinance)
_INDEX_MAP = {
    "NIFTY50":    "^NSEI",
    "NIFTY":      "^NSEI",
    "SENSEX":     "^BSESN",
    "BANKNIFTY":  "^NSEBANK",
    "NIFTYMIDCAP":"^NSEMDCP50",
}


def _yf_symbol(symbol: str, exchange: str) -> str:
    sym = symbol.strip().upper()
    if sym in _INDEX_MAP:
        return _INDEX_MAP[sym]
    suffix = _SUFFIX.get(exchange.upper(), ".NS")
    return f"{sym}{suffix}"


def fetch_stock(
    symbol:     str,
    exchange:   str   = "NSE",
    timeframe:  str   = "1d",
    start_date: str | None = None,
    end_date:   str | None = None,
) -> pd.DataFrame:
    """
    Fetch OHLCV data for one NSE/BSE stock from Yahoo Finance.

    Parameters
    ----------
    symbol    : NSE/BSE ticker, e.g. "RELIANCE", "TCS", "NIFTY50"
    exchange  : "NSE" or "BSE"
    timeframe : yfinance interval string — "1m","5m","15m","1h","1d","1wk" etc.
    start_date: "YYYY-MM-DD" — None = use max allowed lookback for the timeframe
    end_date  : "YYYY-MM-DD" — None = today

    Returns
    -------
    pd.DataFrame with columns: time, open, high, low, close, volume
    Raises RuntimeError if no data is returned.
    """
    try:
        import yfinance as yf
    except ImportError:
        raise ImportError("yfinance not installed — run: pip install yfinance")

    yf_sym = _yf_symbol(symbol, exchange)
    max_days = _MAX_DAYS.get(timeframe)

    # Clamp start_date to the allowed window for intraday data
    if start_date and max_days:
        earliest = (datetime.utcnow() - timedelta(days=max_days)).strftime("%Y-%m-%d")
        if start_date < earliest:
            print(f"  [nse] '{timeframe}' allows max {max_days}d lookback — "
                  f"clamping start to {earliest}")
            start_date = earliest
    elif not start_date and max_days:
        start_date = (datetime.utcnow() - timedelta(days=max_days)).strftime("%Y-%m-%d")

    print(f"  [nse] Fetching {yf_sym} | {timeframe} | {start_date} → {end_date or 'today'}")

    ticker = yf.Ticker(yf_sym)
    df = ticker.history(
        start    = start_date,
        end      = end_date,
        interval = timeframe,
        auto_adjust = True,
        prepost  = False,
    )

    if df.empty:
        raise RuntimeError(
            f"No data returned for {yf_sym} ({timeframe}). "
            "Check symbol name, exchange, and date range."
        )

    # Normalise to standard OHLCV schema
    df = df.rename(columns={
        "Open":   "open",
        "High":   "high",
        "Low":    "low",
        "Close":  "close",
        "Volume": "volume",
    })
    df.index.name = "time"
    df = df.reset_index()

    # Strip timezone info so downstream code doesn't break
    df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(None)

    # Keep only standard columns
    keep = [c for c in ["time", "open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[keep].sort_values("time").reset_index(drop=True)

    print(f"  [nse] {len(df):,} bars  ({df['time'].iloc[0].date()} → {df['time'].iloc[-1].date()})")
    return df


def fetch_multiple(
    symbols:    list[str],
    exchange:   str   = "NSE",
    timeframe:  str   = "1d",
    start_date: str | None = None,
    end_date:   str | None = None,
    save_dir:   Path | None = None,
) -> dict[str, pd.DataFrame]:
    """
    Fetch data for multiple symbols.
    Optionally save each to save_dir/nse_{symbol}.csv.

    Returns dict: {symbol: DataFrame}
    """
    results = {}
    for sym in symbols:
        try:
            df = fetch_stock(sym, exchange, timeframe, start_date, end_date)
            results[sym] = df
            if save_dir:
                path = Path(save_dir) / f"nse_{sym}_{timeframe}.csv"
                df.to_csv(path, index=False)
                print(f"  [nse] Saved → {path.name}")
            time.sleep(0.3)   # polite rate-limiting
        except Exception as exc:
            print(f"  [nse] {sym} failed: {exc}")
    return results
