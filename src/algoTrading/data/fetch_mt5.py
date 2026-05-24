import json
import time
import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime
from pathlib import Path

TIMEFRAME_MAP = {
    "M1":  mt5.TIMEFRAME_M1,
    "M5":  mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1":  mt5.TIMEFRAME_H1,
    "H4":  mt5.TIMEFRAME_H4,
    "D1":  mt5.TIMEFRAME_D1,
}

CACHE_TTL = 900  # 15 minutes in seconds

MULTI_TIMEFRAMES = ["M5", "M15", "M30", "H1", "H4"]


def _meta_path(save_path: str) -> Path:
    return Path(save_path).with_suffix(".meta")


def _cache_valid(save_path: str, timeframe: str, from_date: datetime | None) -> bool:
    """Return True if cached CSV is < 1 hour old, same timeframe, and same from_date."""
    csv_p  = Path(save_path)
    meta_p = _meta_path(save_path)

    if not csv_p.exists() or not meta_p.exists():
        return False

    try:
        meta = json.loads(meta_p.read_text())
    except Exception:
        return False

    if meta.get("timeframe") != timeframe:
        return False

    # Invalidate if from_date changed
    cached_from = meta.get("from_date")
    new_from    = from_date.strftime("%Y-%m-%d") if from_date else None
    if cached_from != new_from:
        return False

    age_seconds = time.time() - meta.get("cached_at", 0)
    return age_seconds < CACHE_TTL


def fetch_and_store(symbol, timeframe, bars, save_path, from_date: datetime | None = None):

    # ── Cache hit: skip MT5 fetch ─────────────────────────────────
    if _cache_valid(save_path, timeframe, from_date):
        meta      = json.loads(_meta_path(save_path).read_text())
        age_min   = int((time.time() - meta["cached_at"]) / 60)
        remaining = int((CACHE_TTL - (time.time() - meta["cached_at"])) / 60)
        print(f"✅ Cache hit — {symbol} {timeframe} | cached {age_min}m ago | refreshes in ~{remaining}m")
        return

    tf = TIMEFRAME_MAP.get(timeframe)
    if tf is None:
        raise ValueError(f"❌ Invalid timeframe: {timeframe!r}")

    # ── Calculate bar count from start date (if given) ────────────
    # Use copy_rates_from_pos — most reliable across all MT5 brokers.
    # Bar count is calculated from START_YEAR so we always cover the full range.
    if from_date is not None:
        # M5 = 12 bars/h × 24h × 5 trading days/week × 52 weeks ≈ 74,880/year
        # Use 80,000/year + 20% buffer to be safe
        years     = max(1, (datetime.utcnow() - from_date).days / 365.25)
        bar_count = min(int(years * 80_000 * 1.2), 3_000_000)
        print(f"🔄 Fetching {symbol} {timeframe} | "
              f"START_DATE={from_date.date()} | ~{bar_count:,} bars ...")
    else:
        bar_count = bars
        print(f"🔄 Fetching {bar_count:,} bars for {symbol} {timeframe} from MT5...")

    # Try requested count, then fall back to smaller counts if broker rejects
    rates = None
    for attempt_count in sorted({bar_count, 99_999, 50_000, 10_000}, reverse=True):
        if attempt_count > bar_count:
            continue
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, attempt_count)
        if rates is not None and len(rates) > 0:
            if attempt_count < bar_count:
                print(f"  Broker limit hit — fetched {attempt_count:,} bars instead of {bar_count:,}")
            break
        print(f"  Retrying with {attempt_count:,} bars ...")

    if rates is None or len(rates) == 0:
        print("❌ Error:", mt5.last_error())
        raise Exception("Failed to fetch MT5 data")

    print(f"✅ Received {len(rates):,} bars")

    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')

    # Overwrite CSV
    csv_p = Path(save_path)
    if csv_p.exists():
        csv_p.unlink()

    df.to_csv(save_path, index=False)
    print(f"✅ Data saved to {save_path}  ({df['time'].min().date()} → {df['time'].max().date()})")

    # Write / update sidecar metadata
    _meta_path(save_path).write_text(json.dumps({
        "symbol":    symbol,
        "timeframe": timeframe,
        "bars":      len(df),
        "from_date": from_date.strftime("%Y-%m-%d") if from_date else None,
        "cached_at": time.time(),
    }))
    print(f"📦 Cache written — next refresh in 15min")


def fetch_all_timeframes(
    symbol: str,
    save_dir,
    from_date: datetime | None = None,
    bars: int = 200_000,
) -> None:
    """Fetch M5, M15, M30, H1, H4 for one symbol concurrently with 15-min cache."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    save_dir = Path(save_dir)

    def _fetch_one(tf: str):
        path = str(save_dir / f"ohlcv_{symbol}_{tf}.csv")
        fetch_and_store(symbol, tf, bars, path, from_date)
        return tf

    print(f"\n── Multi-TF fetch: {symbol} ({', '.join(MULTI_TIMEFRAMES)}) ─────")
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(_fetch_one, tf): tf for tf in MULTI_TIMEFRAMES}
        for fut in as_completed(futures):
            tf = futures[fut]
            try:
                fut.result()
            except Exception as exc:
                print(f"  ⚠️  {symbol} {tf} failed: {exc}")
