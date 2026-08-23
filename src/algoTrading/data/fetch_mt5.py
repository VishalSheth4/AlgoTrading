from datetime import datetime

import MetaTrader5 as mt5
import pandas as pd
from pathlib import Path
import os
import uuid

from algoTrading.core.broker_time import get_broker_time_offset
from algoTrading.core.fs_utils import replace_with_retry

TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

# FX/gold markets are closed roughly Friday evening through Sunday evening
# (exact minute is broker-specific) -- some MT5 demo/prop-firm servers keep
# serving organic-looking-but-fabricated bars through that window anyway,
# which silently feeds strategies (and backtests) phantom weekend trades.
# Drop anything whose CORRECTED UTC timestamp falls here. Adjust these two
# pairs if your broker's published market hours differ.
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
        print(f"⚠️ Dropped {dropped} weekend bar(s) (market closed Fri {MARKET_CLOSE_HOUR_UTC}:00 UTC -> Sun {MARKET_OPEN_HOUR_UTC}:00 UTC)")
    return df.loc[~closed].reset_index(drop=True)

def fetch_and_store(symbol, timeframe, bars, save_path, date_from: datetime | None = None, date_to: datetime | None = None):
    """Fetch either the last `bars` candles (default), or every candle
    inside [date_from, date_to] when date_from is given -- date_to defaults
    to now. date_from/date_to are naive datetimes in true UTC (same
    convention as every other timestamp in this app); they're converted to
    the broker's own server time before querying MT5, since
    copy_rates_range filters using the terminal's own (uncorrected) bar
    timestamps, not the corrected ones this function returns.
    """

    tf = TIMEFRAME_MAP.get(timeframe)

    if tf is None:
        raise ValueError("❌ Invalid timeframe")

    offset = get_broker_time_offset(mt5, symbol).get_offset()

    if date_from is not None:
        server_from = date_from + offset
        server_to = (date_to or datetime.utcnow()) + offset
        print(f"Fetching {symbol} from {date_from} to {date_to or 'now'} (UTC)...")
        rates = mt5.copy_rates_range(symbol, tf, server_from, server_to)
    else:
        print(f"Fetching {bars} bars for {symbol}...")
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)

    if rates is None:
        print("❌ Error:", mt5.last_error())
        raise Exception("Failed to fetch MT5 data")

    if len(rates) == 0:
        raise Exception("❌ No data returned")

    print(f"✅ Received {len(rates)} bars")

    # ----------------------------
    # CONVERT TO DATAFRAME (ALL COLUMNS)
    # ----------------------------
    df = pd.DataFrame(rates)

    # Convert time properly
    df['time'] = pd.to_datetime(df['time'], unit='s')

    # MT5's server clock is commonly UTC+2/UTC+3 (EET/EEST), not true UTC --
    # correct it here, at the source, exactly like algoTrader's live reader
    # does. Without this, backtested trade timestamps are off by the
    # broker's offset (an XAUUSD broker running EEST made every backtest
    # trade land ~3 hours later than the live dashboard's alert for the
    # SAME candle), which makes comparing a live signal against the
    # backtest impossible.
    df['time'] = df['time'] - get_broker_time_offset(mt5, symbol).get_offset()

    df = _drop_weekend_bars(df)

    # 🔥 KEEP ALL COLUMNS (NO DROP)
    # ['time','open','high','low','close','tick_volume','spread','real_volume']

    # ----------------------------
    # SAVE CSV -- write to a unique temp file in the same directory, then
    # atomically replace the target. Running several timeframes in
    # PARALLEL (see backtest_runner.py) means multiple processes can fetch
    # the same symbol at once; the old delete-then-write approach was a
    # race (one process's delete could clobber another's in-progress
    # write). os.replace() is atomic on both Windows and POSIX as long as
    # source and destination are on the same filesystem, which the temp
    # file (written right next to save_path) guarantees.
    # ----------------------------
    save_path = str(save_path)
    tmp_path = f"{save_path}.{uuid.uuid4().hex}.tmp"
    df.to_csv(tmp_path, index=False)
    replace_with_retry(tmp_path, save_path)

    print(f"✅ Data saved to {save_path}")