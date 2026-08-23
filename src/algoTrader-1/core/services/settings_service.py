"""
Read/write algoTrading/config.py's tunable properties, backing algoTrader's
Settings tab. Edits are written to a persistent JSON overrides file
(algoTrading/config_overrides.json) that config.py itself reads at import
time (see its own docstring) -- so a save here takes effect on the NEXT
backtest run (a fresh `python -m algoTrading.main_backtest` subprocess
re-imports config.py from scratch) with no server restart needed, and
persists across restarts, unlike the BT_* per-run env var overrides.

Deliberately never touches config.py's own source text -- writing to a
sibling JSON file means a save here can never corrupt hand-written Python.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[3]  # .../src
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

import algoTrading.config as _config_module  # noqa: E402
from algoTrading.main_backtest import STRATEGY_MAP  # noqa: E402
from algoTrading.rr_matrix import DEFAULT_RR, load_rr_matrix, save_rr_matrix as _save_rr_matrix  # noqa: E402

_OVERRIDES_PATH = _SRC_ROOT / "algoTrading" / "config_overrides.json"
AVAILABLE_STRATEGIES = list(STRATEGY_MAP.keys())
AVAILABLE_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
AVAILABLE_TP_MODES = ["rr", "st", "both", "fix_profit"]

# (field name, input type, label, help text)
FIELD_SPECS = [
    ("SYMBOL", "str", "Symbol(s)", "Comma-separated for multi-symbol, e.g. XAUUSD,EURUSD"),
    ("STRATEGY", "str", "Strategy(ies)", "Comma-separated strategy keys, e.g. mark2,green_dollar"),
    ("TIMEFRAME", "select_tf", "Timeframe", "Default timeframe for a plain CLI run"),
    ("BARS", "int", "Bars to fetch", "How many candles to pull from MT5 per run"),
    ("INITIAL_CAPITAL", "float", "Initial capital ($)", "Starting balance for the backtest"),
    ("RISK_PER_TRADE", "float", "Risk per trade", "Fraction of capital risked per trade, e.g. 0.03 = 3% (used when Risk mode = percent)"),
    ("RISK_MODE", "select_risk_mode", "Risk mode", "percent = risk a % of current capital; fixed = risk a flat $ amount every trade"),
    ("RISK_AMOUNT", "float", "Risk amount ($)", "Dollars risked per trade when Risk mode = fixed, e.g. 25"),
    ("CONTRACT_SIZE", "float", "Contract size", "Units per 1.0 lot for the traded symbol (100 for XAUUSD's standard lot) -- used when Risk mode = fixed"),
    ("MAX_LOT_SIZE", "float", "Max lot size", "Safety ceiling on lot size in BOTH risk modes -- protects against a near-zero-width stoploss otherwise sizing an absurdly large trade"),
    ("LOT_SIZE", "float", "Lot size", "Fixed lot size used by strategies without their own sizing"),
    ("STOP_LOSS", "float", "Stop loss (price units)", ""),
    ("TAKE_PROFIT", "float", "Take profit (price units)", ""),
    ("RR", "float", "Risk:Reward ratio", "Used when TP mode is 'rr' or 'both'"),
    ("TP_MODE", "select_tp", "TP mode", "rr | st | both | fix_profit"),
    ("FIX_PROFIT", "float", "Fixed profit target", "Used when TP mode = fix_profit"),
    ("MIN_TREND_CANDLES", "int", "Min trend candles", "Filters micro Supertrend flips (Mark2 / Mark Dollar ST)"),
    ("MAX_CANDLE_SIZE", "optional_int", "Max candle size", "Skip signal candles bigger than this (blank = disabled)"),
    ("EMA20_SUPERTREND_FILTER", "bool", "20 EMA: Supertrend confirmation",
     "20ema only: BUY while Supertrend is bullish (green), SELL while it's bearish (red)"),
]
_KNOWN_FIELDS = {name for name, *_ in FIELD_SPECS}


def _reload_config() -> None:
    """config.py resolves its overrides at import time; reload it after a
    save/reset so get_settings() immediately reflects what was just written
    instead of the values captured at server startup."""
    importlib.reload(_config_module)


def get_settings() -> dict:
    values = {name: getattr(_config_module.Config, name) for name, *_ in FIELD_SPECS}
    return {
        "values": values,
        "fields": [{"name": n, "type": t, "label": lbl, "help": h} for n, t, lbl, h in FIELD_SPECS],
        "available_strategies": AVAILABLE_STRATEGIES,
        "available_timeframes": AVAILABLE_TIMEFRAMES,
        "available_tp_modes": AVAILABLE_TP_MODES,
        "overrides_active": list(_load_overrides().keys()),
    }


def _load_overrides() -> dict:
    if not _OVERRIDES_PATH.exists():
        return {}
    try:
        return json.loads(_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(values: dict) -> dict:
    unknown = [k for k in values if k not in _KNOWN_FIELDS]
    if unknown:
        raise ValueError(f"Unknown setting(s): {', '.join(unknown)}")

    previous = _load_overrides()
    overrides = dict(previous)
    overrides.update(values)
    _OVERRIDES_PATH.write_text(json.dumps(overrides, indent=2), encoding="utf-8")
    try:
        # config.py casts each field (int(...)/float(...)) at reload time --
        # an invalid value (e.g. "abc" for BARS) blows up HERE.
        _reload_config()
    except Exception:
        # Roll back before re-raising: config_overrides.json must never be
        # left holding a value that breaks every future backtest run.
        _OVERRIDES_PATH.write_text(json.dumps(previous, indent=2), encoding="utf-8")
        _reload_config()
        raise
    return get_settings()


def reset_settings() -> dict:
    """Deletes config_overrides.json entirely -- every field falls back to
    config.py's own hardcoded defaults."""
    if _OVERRIDES_PATH.exists():
        _OVERRIDES_PATH.unlink()
    _reload_config()
    return get_settings()


# ---------------------------------------------------------------------------
# Per-(strategy, timeframe) risk:reward matrix -- see algoTrading/rr_matrix.py
# ---------------------------------------------------------------------------

def get_rr_matrix() -> dict:
    """Full grid, every (strategy, timeframe) cell filled in (DEFAULT_RR --
    1:1 -- for anything never explicitly set), so the UI never has to
    special-case a missing cell."""
    stored = load_rr_matrix()
    grid = {
        strategy: {
            tf: float(stored.get(strategy, {}).get(tf, DEFAULT_RR))
            for tf in AVAILABLE_TIMEFRAMES
        }
        for strategy in AVAILABLE_STRATEGIES
    }
    return {
        "matrix": grid,
        "default_rr": DEFAULT_RR,
        "available_strategies": AVAILABLE_STRATEGIES,
        "available_timeframes": AVAILABLE_TIMEFRAMES,
    }


def save_rr_matrix_cells(updates: dict) -> dict:
    """`updates` is {strategy: {timeframe: rr}} -- only the cells present
    get changed; everything else keeps its current (or default) value.
    Merges onto the existing stored matrix rather than replacing it
    wholesale, so saving one edited cell doesn't need the whole 56-cell
    grid round-tripped."""
    unknown_strategies = [s for s in updates if s not in AVAILABLE_STRATEGIES]
    if unknown_strategies:
        raise ValueError(f"Unknown strategy(ies): {', '.join(unknown_strategies)}")

    stored = load_rr_matrix()
    for strategy, tf_values in updates.items():
        unknown_tf = [tf for tf in tf_values if tf not in AVAILABLE_TIMEFRAMES]
        if unknown_tf:
            raise ValueError(f"Unknown timeframe(s) for {strategy}: {', '.join(unknown_tf)}")
        row = dict(stored.get(strategy, {}))
        for tf, rr in tf_values.items():
            row[tf] = float(rr)  # raises ValueError/TypeError on a bad value -- caught by the view
        stored[strategy] = row

    _save_rr_matrix(stored)
    return get_rr_matrix()


def reset_rr_matrix() -> dict:
    """Deletes rr_matrix.json entirely -- every cell falls back to 1:1."""
    _save_rr_matrix({})
    return get_rr_matrix()
