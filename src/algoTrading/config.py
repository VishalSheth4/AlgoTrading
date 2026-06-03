# =============================
# config.py
# =============================
#
# Strategy selection:
#   The active_preset in config.yaml controls which strategies run.
#   Just change that one line — no editing needed here.
#   If active_preset is blank/missing, STRATEGY below is used as fallback.

from pathlib import Path


def _resolve_strategy() -> str:
    """Read active_preset from config.yaml; fall back to hardcoded default."""
    try:
        import yaml
        yaml_path = Path(__file__).resolve().parent / "config.yaml"
        with open(yaml_path) as f:
            data = yaml.safe_load(f) or {}
        preset = data.get("active_preset", "").strip()
        if preset:
            return str(data.get("presets", {}).get(preset, preset))
    except Exception:
        pass
    return "SupertrendCounterFlip_X1"


class Config:
    TIMEFRAME = "M5"
    INITIAL_CAPITAL = 1000
    RISK_PER_TRADE = 0.1
    # Resolved from config.yaml active_preset at import time.
    # Change active_preset in config.yaml instead of editing this line.
    STRATEGY = _resolve_strategy()
    SYMBOL = "XAUUSD"           # comma-separated for multi-symbol: "XAUUSD,EURUSD,GBPUSD,AUDUSD"
    # SYMBOL = "XAUUSD,EURUSD,GBPUSD,USDJPY,XAGUSD"
    LOT_SIZE = 0.01
    MODE = "mt5"   # "mt5" → fetch live from MetaTrader5 | "csv" → use local CSV (Kaggle)

    # MT5 account credentials — leave None to use whatever terminal is already open
    MT5_LOGIN    = 52879886
    MT5_PASSWORD = "zF!X0XEP1hvwWP"
    MT5_SERVER   = "ICMarketsSC-Demo"

    # Backtest date range — set both to filter bars, or None to use all data.
    # Format: "YYYY-MM-DD"
    START_DATE = "2026-01-01"       # earliest IC Markets M5 bar
    END_DATE   = "2026-06-03"       # up to latest available bar

    # -------------------------------------------------------
    # TP Settings  (fallback — each strategy overrides via config.yaml)
    # -------------------------------------------------------
    # RISK_PER_TRADE = 0.02   # decimal fallback (8%) — overridden per strategy in config.yaml

    RR         = 4
    TP_MODE    = "rr"   # "rr" | "st" | "both" | "fix_profit"
    FIX_PROFIT = 5

    MIN_TREND_CANDLES = 1
    MAX_CANDLE_SIZE   = 10
    MAX_DAILY_LOSSES  = 5

    RSI_PERIOD     = 14
    RSI_EMA_PERIOD = 14
    RSI_THRESHOLD  = 70
    EMA_PERIOD     = 200

    # Contract size per instrument (units per standard lot).
    CONTRACT_SIZES = {
        "XAUUSD": 100,
        "XAGUSD": 5000,
        "EURUSD": 100000,
        "GBPUSD": 100000,
        "USDJPY": 100000,
        "AUDUSD": 100000,
        "USDCAD": 100000,
        "USDCHF": 100000,
        "NZDUSD": 100000,
    }
    DEFAULT_CONTRACT_SIZE = 100000
