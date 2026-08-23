# =============================
# config.py
# =============================
#
# Every field below resolves in this order:
#   1. an environment variable BT_<FIELD>       (a one-off override for a
#      single run -- used by algoTrader's "Run Backtest" UI to launch with
#      a different strategy/symbol/timeframe from a dropdown without
#      touching this file)
#   2. config_overrides.json in this same folder (a PERSISTENT override --
#      written by algoTrader's Settings tab; survives across runs/restarts,
#      same as if you'd edited the literal default below)
#   3. the hardcoded default in this file
#
# config_overrides.json is deliberately NOT edited by hand -- it's managed
# by algoTrader/core/services/settings_service.py. Delete it (or clear a
# field from it) to fall back to this file's hardcoded defaults again.

import json
import os
from pathlib import Path

_OVERRIDES_PATH = Path(__file__).resolve().parent / "config_overrides.json"


def _load_overrides() -> dict:
    if not _OVERRIDES_PATH.exists():
        return {}
    try:
        return json.loads(_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


_overrides = _load_overrides()


def _get(name: str, default, cast=str):
    env_val = os.environ.get(f"BT_{name}")
    if env_val is not None:
        return cast(env_val) if env_val != "" else default
    if name in _overrides and _overrides[name] is not None:
        return cast(_overrides[name])
    return default


def _get_optional_int(name: str, default):
    """Like _get, but an explicit null/empty override means "disabled"
    (None), not "fall back to default" -- used by MAX_CANDLE_SIZE."""
    env_val = os.environ.get(f"BT_{name}")
    if env_val is not None:
        return None if env_val.strip().lower() in ("", "none", "null") else int(env_val)
    if name in _overrides:
        val = _overrides[name]
        return None if val in (None, "", "none", "null") else int(val)
    return default


def _get_bool(name: str, default: bool) -> bool:
    """Like _get, but for booleans -- accepts real bools (from the Settings
    UI's JSON POST) as well as string forms ("true"/"1"/"yes") from an env
    var override."""
    env_val = os.environ.get(f"BT_{name}")
    if env_val is not None:
        return env_val.strip().lower() in ("1", "true", "yes", "on")
    if name in _overrides and _overrides[name] is not None:
        val = _overrides[name]
        if isinstance(val, bool):
            return val
        return str(val).strip().lower() in ("1", "true", "yes", "on")
    return default


def _get_optional_str(name: str, default=None):
    """Like _get_optional_int but for plain strings -- used by
    DATE_FROM/DATE_TO, where "not set" (None) means "use BARS instead"."""
    env_val = os.environ.get(f"BT_{name}")
    if env_val is not None:
        return None if env_val.strip() == "" else env_val.strip()
    if name in _overrides:
        val = _overrides[name]
        return None if val in (None, "") else str(val)
    return default


class Config:
    TIMEFRAME = _get("TIMEFRAME", "M5")
    BARS = _get("BARS", 90000, int)
    # Optional date range (YYYY-MM-DD, true UTC) -- when DATE_FROM is set,
    # fetch_mt5.py pulls every candle in [DATE_FROM, DATE_TO] instead of
    # the last BARS candles (DATE_TO defaults to now if left blank). Unset
    # by default, so a plain CLI run behaves exactly as before.
    DATE_FROM = _get_optional_str("DATE_FROM")
    DATE_TO = _get_optional_str("DATE_TO")
    INITIAL_CAPITAL = _get("INITIAL_CAPITAL", 100, float)
    DATA_PATH = "data/sample_data.csv"
    STRATEGY = _get("STRATEGY", "mark2,mark_dollar_supertrend")  # comma-separated for multi-strategy
    RISK_PER_TRADE = _get("RISK_PER_TRADE", 0.03, float)

    # -------------------------------------------------------
    # Position sizing -- applies to EVERY strategy's entry (BacktestEngine
    # is shared across whichever strategies a run selects, so this isn't
    # per-strategy config -- see backtest/engine.py).
    #
    # RISK_MODE  : "percent" -> position_size = (capital * RISK_PER_TRADE) /
    #                           stoploss_distance -- the original/default
    #                           behavior, math unchanged from before this
    #                           feature existed.
    #              "fixed"   -> position_size = RISK_AMOUNT dollars /
    #                           (stoploss_distance * CONTRACT_SIZE) -- a
    #                           real broker-style lot size, the same dollar
    #                           risk on every trade regardless of capital.
    #                           e.g. $25 risk, a $5-wide stop, CONTRACT_SIZE
    #                           =100 (XAUUSD's 100oz standard lot) ->
    #                           25 / (5 * 100) = 0.05 lots.
    # -------------------------------------------------------
    RISK_MODE = _get("RISK_MODE", "percent")  # "percent" | "fixed"
    RISK_AMOUNT = _get("RISK_AMOUNT", 25, float)  # dollars risked per trade when RISK_MODE == "fixed"
    CONTRACT_SIZE = _get("CONTRACT_SIZE", 100, float)  # units per 1.0 lot for the traded symbol
    # Safety ceiling on BOTH modes above -- either sizing formula divides by
    # the stoploss distance, so an unusually tight stop (a near-doji signal
    # candle) can otherwise produce an absurd lot size: e.g. $15 risk on a
    # $0.05-wide stop = 3.0 lots, and any close-price overshoot past that
    # paper-thin stop then multiplies into a wildly oversized loss. Caps
    # position_size at this value regardless of what the formula computes.
    #
    # 0.1 is deliberately conservative, not a universal "correct" number --
    # a lot's $ impact scales with CONTRACT_SIZE (100 for XAUUSD), so even
    # a "small-looking" 1.0 lot cap can be 1-2x a small backtest account's
    # entire capital in a single move (1.0 lot x 100 x an $8 move = $800).
    # Tune this relative to INITIAL_CAPITAL: lot x CONTRACT_SIZE x a
    # plausible worst-case price move should stay a small fraction of it.
    MAX_LOT_SIZE = _get("MAX_LOT_SIZE", 0.1, float)
    SYMBOL = _get("SYMBOL", "XAUUSD")  # comma-separated for multi-symbol: "XAUUSD,EURUSD,GBPUSD,AUDUSD"
    LOT_SIZE = _get("LOT_SIZE", 0.01, float)
    STOP_LOSS = _get("STOP_LOSS", 50, float)
    TAKE_PROFIT = _get("TAKE_PROFIT", 100, float)
    MODE = "mt5"

    # -------------------------------------------------------
    # Mark2 / MarkDollarSuperTrend TP Settings
    #
    # TP_MODE   : which target to use
    #   "rr"         → R:R ratio  (entry ± RR × risk)
    #   "st"         → Supertrend line
    #   "both"       → whichever is hit first (closer of the two)
    #   "fix_profit" → fixed price distance regardless of risk
    #                  LONG  TP = entry + FIX_PROFIT
    #                  SHORT TP = entry - FIX_PROFIT
    #
    # RR         : multiplier used when TP_MODE = "rr" or "both"
    # FIX_PROFIT : price units of profit when TP_MODE = "fix_profit"
    #              e.g. 5 → $5 for XAUUSD, 50 pips for EURUSD (0.0050)
    # -------------------------------------------------------
    RR = _get("RR", 3, float)
    TP_MODE = _get("TP_MODE", "rr")  # "rr" | "st" | "both" | "fix_profit"
    FIX_PROFIT = _get("FIX_PROFIT", 5, float)  # price-unit target when TP_MODE = "fix_profit"

    # Minimum candles the PREVIOUS trend must have lasted before the flip
    # counts as a valid X/Y candle.  Filters 1-2 candle micro-flips caused
    # by MT5 vs TradingView data discrepancies.  Set to 1 to disable.
    MIN_TREND_CANDLES = _get("MIN_TREND_CANDLES", 1, int)

    # Maximum candle size (high - low) allowed for a signal candle.
    # Candles larger than this are skipped to avoid chasing volatile spikes.
    # Set to None to disable.
    MAX_CANDLE_SIZE = _get_optional_int("MAX_CANDLE_SIZE", 13)

    # 20ema-only: when True, BUY only fires while Supertrend is bullish
    # (green) and SELL only fires while it's bearish (red). See
    # main_backtest.get_strategy() and strategies/ema20.py. Off by default.
    EMA20_SUPERTREND_FILTER = _get_bool("EMA20_SUPERTREND_FILTER", False)
