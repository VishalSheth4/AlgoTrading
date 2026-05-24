"""
strategy/sessions.py
====================
Kill Zones, Session Windows, and Judas Swing detection.

ICT is highly time-based. The best setups form during specific session windows.

Kill Zones (UTC):
  Asian         : 00:00 – 06:00  (consolidation, range building)
  London Open   : 07:00 – 10:00  (most powerful for FX + Gold)
  London Close  : 10:00 – 12:00
  New York Open : 13:00 – 16:00  (Gold volatility, continuation)
  New York PM   : 16:00 – 20:00

IST offsets: add +5:30 to UTC
  London Open   : 12:30 – 15:30 IST
  New York Open : 18:30 – 21:30 IST

XAUUSD (Gold) trades best 12:00–20:00 IST, which aligns:
  London Kill Zone + NY Open Kill Zone

Judas Swing:
  A deliberate false breakout at a session open designed to:
    1. Trap breakout traders
    2. Take out stop-loss clusters (liquidity sweep)
    3. Reverse in the opposite direction (the real move)

  Detection pattern:
    - Price sweeps outside the Asian range (or prior session high/low)
      within the first 30–60 minutes of London or NY open
    - Candle that swept closes BACK inside the range (wick rejection)
    - Followed by displacement candle in the opposite direction
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd

from algoTrading.ict_trading_system.settings import (
    Bias, Instrument, Session, Timeframe,
)
from algoTrading.ict_trading_system.data.models import Candle
from algoTrading.ict_trading_system.strategy.ict_concepts import is_displacement_candle

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# SESSION WINDOWS (UTC hours)
# ─────────────────────────────────────────────

SESSION_WINDOWS: Dict[str, Tuple[int, int]] = {
    "asian":          (0,  6),
    "london_open":    (7,  10),
    "london_close":   (10, 12),
    "ny_open":        (13, 16),
    "ny_pm":          (16, 20),
    "dead_london_ny": (12, 13),   # lunch dead zone
    "dead_overnight": (20, 24),
}

# Kill Zone windows — highest probability trade windows
KILL_ZONES: Dict[str, Tuple[int, int]] = {
    "london_kill_zone": (7,  10),
    "ny_kill_zone":     (13, 16),
    "asian_kill_zone":  (2,  5),   # Tokyo session active
}

# IST offset: UTC + 5:30
_IST_OFFSET_HOURS   = 5
_IST_OFFSET_MINUTES = 30


# ─────────────────────────────────────────────
# SESSION DETECTION
# ─────────────────────────────────────────────

def get_session(dt: datetime) -> Session:
    """Return the current ICT session for a UTC datetime."""
    h = dt.hour
    if 7 <= h < 12:
        return Session.LONDON
    if 13 <= h < 17:
        return Session.NEW_YORK
    if 0 <= h < 7:
        return Session.ASIAN
    return Session.DEAD


def is_in_kill_zone(dt: datetime, zone: str = "any") -> bool:
    """
    Return True if the UTC datetime falls inside the specified kill zone.
    zone: "london_kill_zone" | "ny_kill_zone" | "asian_kill_zone" | "any"
    """
    h = dt.hour
    if zone == "any":
        return any(start <= h < end for start, end in KILL_ZONES.values())
    kz = KILL_ZONES.get(zone)
    if not kz:
        return False
    return kz[0] <= h < kz[1]


def to_ist(dt: datetime) -> datetime:
    """Convert a UTC datetime to IST (UTC+5:30)."""
    return dt + timedelta(hours=_IST_OFFSET_HOURS, minutes=_IST_OFFSET_MINUTES)


def is_xauusd_prime_window(dt: datetime) -> bool:
    """
    Gold (XAUUSD) prime trading window.
    12:00–20:00 IST = 06:30–14:30 UTC.
    Covers London open + NY open kill zones.
    """
    utc_hour  = dt.hour
    utc_min   = dt.minute
    utc_total = utc_hour * 60 + utc_min
    return 390 <= utc_total <= 870   # 06:30=390, 14:30=870


# ─────────────────────────────────────────────
# ASIAN RANGE
# ─────────────────────────────────────────────

@dataclass
class AsianRange:
    """The high/low range formed during the Asian session (UTC 00:00–06:00)."""
    date:        datetime
    high:        float
    low:         float
    instrument:  Instrument

    @property
    def size(self) -> float:
        return self.high - self.low

    @property
    def midpoint(self) -> float:
        return (self.high + self.low) / 2

    def is_above_range(self, price: float) -> bool:
        return price > self.high

    def is_below_range(self, price: float) -> bool:
        return price < self.low

    def is_inside_range(self, price: float) -> bool:
        return self.low <= price <= self.high


def compute_asian_range(
    candles: List[Candle],
    date: Optional[datetime] = None,
) -> Optional[AsianRange]:
    """
    Extract the Asian session high/low from the provided candle list.
    If date is None, uses the most recent Asian session.
    """
    if not candles:
        return None

    target_date = (date or candles[-1].timestamp).date()

    asian = [
        c for c in candles
        if c.timestamp.date() == target_date
        and SESSION_WINDOWS["asian"][0] <= c.timestamp.hour < SESSION_WINDOWS["asian"][1]
    ]

    if not asian:
        return None

    instrument = candles[-1].instrument
    return AsianRange(
        date       = datetime.combine(target_date, time(0, 0)),
        high       = max(c.high for c in asian),
        low        = min(c.low  for c in asian),
        instrument = instrument,
    )


def compute_asian_range_from_df(
    df: pd.DataFrame,
    instrument: Instrument,
    date: Optional[str] = None,
) -> Optional[AsianRange]:
    """
    Compute Asian range from an OHLCV DataFrame.

    Expected columns: open, high, low, close, time (or timestamp)
    date: 'YYYY-MM-DD' string; if None uses the last available date
    """
    ts_col = next(
        (c for c in ("time", "timestamp", "datetime", "date") if c in df.columns),
        None
    )
    if ts_col is None or df.empty:
        return None

    df = df.copy()
    df[ts_col] = pd.to_datetime(df[ts_col], utc=True)

    if date:
        target = pd.Timestamp(date).date()
    else:
        target = df[ts_col].dt.date.max()

    mask = (
        (df[ts_col].dt.date == target) &
        (df[ts_col].dt.hour >= SESSION_WINDOWS["asian"][0]) &
        (df[ts_col].dt.hour <  SESSION_WINDOWS["asian"][1])
    )
    asian_df = df[mask]

    if asian_df.empty:
        return None

    return AsianRange(
        date       = datetime.combine(target, time(0, 0)),
        high       = float(asian_df["high"].max()),
        low        = float(asian_df["low"].min()),
        instrument = instrument,
    )


# ─────────────────────────────────────────────
# JUDAS SWING
# ─────────────────────────────────────────────

@dataclass
class JudasSwing:
    """
    A detected Judas Swing (fake-out at session open).

    sweep_direction : which side was swept
      BULLISH → price swept ABOVE a level then reversed DOWN
      BEARISH → price swept BELOW a level then reversed UP

    trade_direction : the real move AFTER the sweep (opposite of sweep)
    """
    instrument:      Instrument
    timeframe:       Timeframe
    sweep_direction: Bias
    trade_direction: Bias
    sweep_candle:    Candle
    swept_level:     float            # the level that was wicked past
    swept_high:      float            # extreme of the wick
    swept_low:       float
    session:         Session
    timestamp:       datetime
    displacement_confirmed: bool = False
    displacement_candle:    Optional[Candle] = None

    @property
    def sl_price(self) -> float:
        """SL beyond the sweep wick extreme."""
        if self.trade_direction == Bias.BULLISH:
            return self.swept_low   # stop below the wick low
        return self.swept_high       # stop above the wick high

    @property
    def entry_price(self) -> float:
        """Enter on the displacement candle close (or sweep candle close)."""
        if self.displacement_candle:
            return self.displacement_candle.close
        return self.sweep_candle.close


def detect_judas_swing(
    candles: List[Candle],
    asian_range: Optional[AsianRange] = None,
    prior_high: Optional[float] = None,
    prior_low:  Optional[float] = None,
    session_window: str = "london_open",
    max_candles_to_confirm: int = 5,
) -> Optional[JudasSwing]:
    """
    Detect a Judas Swing during a session open.

    Logic:
      1. During `session_window`, find a candle that wicks OUTSIDE the
         reference range (asian_range or prior_high/low).
      2. The candle must CLOSE back INSIDE the range (wick rejection).
      3. Within `max_candles_to_confirm` candles, a displacement candle
         must confirm the reversal direction.

    Args:
        candles:               ordered Candle list
        asian_range:           Asian session H/L (preferred reference)
        prior_high/low:        fallback reference levels if no Asian range
        session_window:        which session to look in ("london_open"/"ny_open")
        max_candles_to_confirm: look this many candles after sweep for displacement
    """
    if not candles:
        return None

    sw = SESSION_WINDOWS.get(session_window, (7, 10))
    ref_high = (asian_range.high if asian_range else prior_high) or 0.0
    ref_low  = (asian_range.low  if asian_range else prior_low)  or float("inf")

    if ref_high == 0.0 or ref_low == float("inf"):
        logger.debug("Judas Swing: no reference range — skipping")
        return None

    instrument = candles[-1].instrument
    timeframe  = candles[-1].timeframe
    session    = Session.LONDON if "london" in session_window else Session.NEW_YORK

    # Find candles inside the session window
    session_candles = [
        (i, c) for i, c in enumerate(candles)
        if sw[0] <= c.timestamp.hour < sw[1]
    ]

    for idx, (i, c) in enumerate(session_candles):
        swept_up   = c.high > ref_high and c.close < ref_high   # wick above, close inside
        swept_down = c.low  < ref_low  and c.close > ref_low    # wick below, close inside

        if not swept_up and not swept_down:
            continue

        sweep_dir  = Bias.BULLISH if swept_up   else Bias.BEARISH
        trade_dir  = Bias.BEARISH if swept_up   else Bias.BULLISH
        swept_lvl  = ref_high     if swept_up   else ref_low

        # Look ahead for displacement confirmation
        disp_candle = None
        future_slice = candles[i + 1: i + 1 + max_candles_to_confirm]

        for fc in future_slice:
            if trade_dir == Bias.BEARISH and fc.is_bearish and is_displacement_candle(fc):
                disp_candle = fc
                break
            elif trade_dir == Bias.BULLISH and fc.is_bullish and is_displacement_candle(fc):
                disp_candle = fc
                break

        swing = JudasSwing(
            instrument             = instrument,
            timeframe              = timeframe,
            sweep_direction        = sweep_dir,
            trade_direction        = trade_dir,
            sweep_candle           = c,
            swept_level            = swept_lvl,
            swept_high             = c.high,
            swept_low              = c.low,
            session                = session,
            timestamp              = c.timestamp,
            displacement_confirmed = disp_candle is not None,
            displacement_candle    = disp_candle,
        )

        logger.info(
            f"Judas Swing detected: {trade_dir.value} on {instrument.value} "
            f"@ {c.timestamp} | swept {swept_lvl:.5f} | disp={disp_candle is not None}"
        )
        return swing   # return first (most recent) valid Judas swing

    return None


# ─────────────────────────────────────────────
# STANDALONE DATAFRAME INTERFACE
# ─────────────────────────────────────────────

def scan_judas_swings(
    df: pd.DataFrame,
    instrument: Instrument,
    timeframe: Timeframe,
    asian_window: Tuple[int, int] = (0, 6),
    london_window: Tuple[int, int] = (7, 10),
    ny_window: Tuple[int, int] = (13, 16),
) -> pd.DataFrame:
    """
    Scan an OHLCV DataFrame for Judas Swing patterns.

    For each trading day:
      1. Build Asian range from asian_window candles
      2. Look for sweep + close-back in london_window or ny_window
      3. Flag as Judas Swing

    Returns DataFrame with:
        date, session, candle_index, timestamp, sweep_direction,
        trade_direction, swept_level, sweep_high, sweep_low,
        displacement_confirmed, entry_price
    """
    ts_col = next(
        (c for c in ("time", "timestamp", "datetime", "date") if c in df.columns),
        None
    )
    if ts_col is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    df[ts_col] = pd.to_datetime(df[ts_col], utc=True)
    df["_date"] = df[ts_col].dt.date
    df["_hour"] = df[ts_col].dt.hour

    rows = []

    for date, day_df in df.groupby("_date"):
        asian_mask = (day_df["_hour"] >= asian_window[0]) & (day_df["_hour"] < asian_window[1])
        asian_df   = day_df[asian_mask]

        if asian_df.empty:
            continue

        ref_high = float(asian_df["high"].max())
        ref_low  = float(asian_df["low"].min())

        for sess_label, sw in [("LONDON", london_window), ("NY", ny_window)]:
            sess_mask = (day_df["_hour"] >= sw[0]) & (day_df["_hour"] < sw[1])
            sess_df   = day_df[sess_mask].reset_index()

            for si in range(len(sess_df)):
                c = sess_df.iloc[si]
                swept_up   = c["high"] > ref_high and c["close"] < ref_high
                swept_down = c["low"]  < ref_low  and c["close"] > ref_low

                if not swept_up and not swept_down:
                    continue

                trade_dir = "BEARISH" if swept_up else "BULLISH"
                swept_lvl = ref_high  if swept_up else ref_low

                # Look ahead up to 5 candles for displacement confirmation
                disp_confirmed = False
                for dj in range(si + 1, min(si + 6, len(sess_df))):
                    fc = sess_df.iloc[dj]
                    fc_rng  = fc["high"] - fc["low"]
                    fc_body = abs(fc["close"] - fc["open"])
                    fc_ratio = fc_body / fc_rng if fc_rng > 0 else 0
                    is_disp  = fc_ratio >= MIN_DISPLACEMENT_BODY_RATIO

                    if trade_dir == "BEARISH" and fc["close"] < fc["open"] and is_disp:
                        disp_confirmed = True
                        break
                    if trade_dir == "BULLISH" and fc["close"] > fc["open"] and is_disp:
                        disp_confirmed = True
                        break

                rows.append({
                    "date":                  str(date),
                    "session":               sess_label,
                    "candle_index":          sess_df.at[si, "index"] if "index" in sess_df.columns else si,
                    "timestamp":             c[ts_col],
                    "sweep_direction":       "BULLISH" if swept_up else "BEARISH",
                    "trade_direction":       trade_dir,
                    "swept_level":           round(swept_lvl, 6),
                    "sweep_high":            round(float(c["high"]), 6),
                    "sweep_low":             round(float(c["low"]),  6),
                    "displacement_confirmed": disp_confirmed,
                    "entry_close":           round(float(c["close"]), 6),
                    "asian_high":            round(ref_high, 6),
                    "asian_low":             round(ref_low,  6),
                })
                break   # one Judas swing per session per day

    if not rows:
        return pd.DataFrame(columns=[
            "date", "session", "candle_index", "timestamp",
            "sweep_direction", "trade_direction", "swept_level",
            "sweep_high", "sweep_low", "displacement_confirmed",
            "entry_close", "asian_high", "asian_low",
        ])

    return pd.DataFrame(rows)


# ─────────────────────────────────────────────
# KILL ZONE FILTER (DataFrame)
# ─────────────────────────────────────────────

def add_session_labels(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add session and kill-zone columns to a OHLCV DataFrame.

    Adds columns:
        session       — ASIAN / LONDON / NEW_YORK / DEAD
        kill_zone     — london_kill_zone / ny_kill_zone / asian_kill_zone / None
        is_prime_gold — True if inside XAUUSD prime window (06:30–14:30 UTC)
        ist_hour      — IST hour for reference
    """
    ts_col = next(
        (c for c in ("time", "timestamp", "datetime", "date") if c in df.columns),
        None
    )
    if ts_col is None:
        return df

    df = df.copy()
    df[ts_col] = pd.to_datetime(df[ts_col], utc=True)
    hour = df[ts_col].dt.hour
    minute = df[ts_col].dt.minute

    def _session(h):
        if 7 <= h < 12:
            return "LONDON"
        if 13 <= h < 17:
            return "NEW_YORK"
        if 0 <= h < 7:
            return "ASIAN"
        return "DEAD"

    def _kill_zone(h):
        if 7 <= h < 10:
            return "london_kill_zone"
        if 13 <= h < 16:
            return "ny_kill_zone"
        if 2 <= h < 5:
            return "asian_kill_zone"
        return None

    df["session"]       = hour.apply(_session)
    df["kill_zone"]     = hour.apply(_kill_zone)
    df["ist_hour"]      = (hour + _IST_OFFSET_HOURS + (minute + _IST_OFFSET_MINUTES) // 60) % 24
    df["is_prime_gold"] = (hour * 60 + minute).between(390, 870)  # 06:30–14:30 UTC

    return df


# Import guard for scan_judas_swings which uses MIN_DISPLACEMENT_BODY_RATIO
from algoTrading.ict_trading_system.settings import MIN_DISPLACEMENT_BODY_RATIO
