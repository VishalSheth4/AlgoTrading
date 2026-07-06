"""
nse_backtest.py — NSE / BSE backtest runner.

Re-uses the exact same strategies + BacktestEngine as the MT5 backtest.
Data is fetched from Yahoo Finance via fetch_nse.py.

Entry point:
    python -m algoTrading.nse_backtest

Or call programmatically:
    from algoTrading.nse_backtest import run_nse_backtest
    results = run_nse_backtest()

Config lives in config.yaml under the `nse_bse:` key.
Toggle enabled/disabled by setting  nse_bse.enabled: true/false
"""

import sys
import json
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).resolve().parent
DATA_DIR    = BASE / "data"
RESULT_FILE = DATA_DIR / "nse_results.json"
TRADE_FILE  = DATA_DIR / "nse_trade_data.csv"

# ── Config loader ──────────────────────────────────────────────────────────────

def _load_nse_config() -> dict:
    """Read nse_bse block from config.yaml. Returns defaults if missing."""
    defaults = {
        "enabled":         False,
        "exchange":        "NSE",
        "timeframe":       "1d",
        "symbols":         "RELIANCE,TCS,INFY,HDFCBANK,ICICIBANK",
        "start_date":      "2020-01-01",
        "end_date":        datetime.utcnow().strftime("%Y-%m-%d"),
        "initial_capital": 100000,
        "risk_per_trade":  1,        # percent
        "strategy":        "",       # blank = use active_preset from config.yaml
    }
    try:
        import yaml
        with open(BASE / "config.yaml") as f:
            data = yaml.safe_load(f) or {}
        cfg = data.get("nse_bse", {})
        return {**defaults, **cfg}
    except Exception:
        return defaults


def _resolve_strategy(cfg: dict) -> str:
    """Get strategy name: nse_bse.strategy override, else active_preset."""
    if cfg.get("strategy", "").strip():
        return cfg["strategy"].strip()
    try:
        import yaml
        with open(BASE / "config.yaml") as f:
            data = yaml.safe_load(f) or {}
        preset = data.get("active_preset", "").strip()
        if preset:
            return str(data.get("presets", {}).get(preset, preset))
    except Exception:
        pass
    return "SupertrendCounterFlip_X1"


# ── Strategy imports (same as main_backtest.py) ────────────────────────────────

def _get_strategy(name: str):
    from algoTrading.strategies.moving_average               import MovingAverageStrategy
    from algoTrading.strategies.supertrend_strategy          import SupertrendStrategy
    from algoTrading.strategies.engulfing_strategy           import EngulfingStrategy
    from algoTrading.strategies.engulfing_consolidation      import EngulfingConsolidationStrategy
    from algoTrading.strategies.SupertrendEngulfingReversalStrategy import Mark2Strategy, SupertrendEngulfingReversalStrategy
    from algoTrading.strategies.engulfing_reversal           import EngulfingReversalStrategy
    from algoTrading.strategies.mark_dollar_supertrend       import MarkDollarSuperTrendStrategy
    from algoTrading.strategies.mark5_supertrend             import Mark5SupertrendStrategy
    from algoTrading.strategies.SupertrendCounterFlip_X1     import SupertrendCounterFlipX1Strategy
    from algoTrading.strategies.EmaCrossoverRetestStrategy   import EmaCrossoverRetestStrategy
    from algoTrading.strategies.Ema200PullbackEngulfingStrategy import Ema200PullbackEngulfingStrategy
    from algoTrading.strategies.DojiStrategy                 import DojiStrategy
    from algoTrading.strategies.rsi_buy_sell_strategy        import RSIBuySellStrategy
    from algoTrading.strategies.RSIEMADoubleCrossStrategy    import RSIEMADoubleCrossStrategy
    from algoTrading.strategies.SupertrendTouchSellStrategy  import SupertrendTouchSellStrategy
    from algoTrading.strategies.SessionStrategy              import SessionStrategy

    MAP = {
        "mark2":                          Mark2Strategy,
        "supertrend_engulfing_reversal":  SupertrendEngulfingReversalStrategy,
        "mark_dollar_supertrend":         MarkDollarSuperTrendStrategy,
        "mark5_supertrend":               Mark5SupertrendStrategy,
        "SupertrendCounterFlip_X1":       SupertrendCounterFlipX1Strategy,
        "EmaCrossoverRetestStrategy":     EmaCrossoverRetestStrategy,
        "Ema200PullbackEngulfingStrategy":Ema200PullbackEngulfingStrategy,
        "DojiStrategy":                   DojiStrategy,
        "RSIBuySellStrategy":             RSIBuySellStrategy,
        "RSIEMADoubleCrossStrategy":      RSIEMADoubleCrossStrategy,
        "SupertrendTouchSell":            SupertrendTouchSellStrategy,
        "session_strategy":               SessionStrategy,
        "engulfing":                      EngulfingStrategy,
        "engulfing_consolidation":        EngulfingConsolidationStrategy,
        "engulfing_reversal":             EngulfingReversalStrategy,
        "supertrend":                     SupertrendStrategy,
        "ma":                             MovingAverageStrategy,
    }
    cls = MAP.get(name)
    if cls is None:
        raise ValueError(f"Unknown strategy: {name!r}. Available: {list(MAP)}")
    return cls()


# ── Per-symbol backtest ────────────────────────────────────────────────────────

def _run_symbol(symbol: str, df: pd.DataFrame, strategy_names: list, capital: float, risk_pct: float) -> list:
    from algoTrading.backtest.engine import BacktestEngine

    # Generate + merge signals (same logic as main_backtest._merge_signals)
    merged = df.copy()
    merged["signal"]         = 0
    merged["sl"]             = np.nan
    merged["tp"]             = np.nan
    merged["lot"]            = 0.0
    merged["risk_per_trade"] = np.nan
    merged["sl_exit_on_close"] = 1
    merged["reverse_exit"]   = 0
    merged["force_entry"]    = 0
    merged["_strategy"]      = ""
    merged["_tp_mode"]       = ""

    for name in strategy_names:
        try:
            strat  = _get_strategy(name)
            sig_df = strat.generate_signals(df.copy())
            free   = merged["signal"] == 0
            has_s  = sig_df["signal"] != 0
            mask   = free & has_s

            if mask.any():
                merged.loc[mask, "signal"]    = sig_df.loc[mask, "signal"]
                merged.loc[mask, "sl"]        = sig_df.loc[mask, "sl"]
                merged.loc[mask, "tp"]        = sig_df.loc[mask, "tp"]
                merged.loc[mask, "_strategy"] = name
                for col in ["lot", "risk_per_trade", "sl_exit_on_close",
                            "reverse_exit", "force_entry", "_tp_mode"]:
                    if col in sig_df.columns:
                        merged.loc[mask, col] = sig_df.loc[mask, col]

                n = int(mask.sum())
                print(f"    [{name}] {n} signals on {symbol}")
        except Exception as exc:
            print(f"    [{name}] error on {symbol}: {exc}")

    # NSE stocks: contract_size = 1 (shares, not lots)
    engine = BacktestEngine(
        capital           = capital,
        risk_per_trade    = risk_pct / 100.0,
        symbol            = symbol,
        min_sl_dist       = 0.01,
        max_position_size = 10000.0,
    )
    # Override contract size for equities
    engine.contract_size = 1.0

    engine.run(merged, save=False)
    return engine.trades


# ── Main entry ─────────────────────────────────────────────────────────────────

def run_nse_backtest(override_cfg: dict | None = None) -> dict:
    """
    Run NSE/BSE backtest. Returns a results dict that is also saved to
    data/nse_results.json and data/nse_trade_data.csv.
    """
    cfg = _load_nse_config()
    if override_cfg:
        cfg.update(override_cfg)

    if not cfg.get("enabled"):
        return {"error": "NSE/BSE module is disabled. Set nse_bse.enabled: true in config.yaml"}

    from algoTrading.data.fetch_nse import fetch_multiple

    symbols   = [s.strip() for s in str(cfg["symbols"]).split(",") if s.strip()]
    exchange  = str(cfg["exchange"]).upper()
    timeframe = str(cfg["timeframe"])
    start     = cfg.get("start_date")
    end       = cfg.get("end_date")
    capital   = float(cfg.get("initial_capital", 100000))
    risk_pct  = float(cfg.get("risk_per_trade",  1))
    strat_str = _resolve_strategy(cfg)
    strategies = [s.strip() for s in strat_str.split(",") if s.strip()]

    print(f"\n{'='*60}")
    print(f"  NSE/BSE BACKTEST")
    print(f"  Exchange   : {exchange}")
    print(f"  Symbols    : {', '.join(symbols)}")
    print(f"  Timeframe  : {timeframe}")
    print(f"  Date range : {start} → {end}")
    print(f"  Strategies : {', '.join(strategies)}")
    print(f"  Capital    : ₹{capital:,.0f}  |  Risk: {risk_pct}% per trade")
    print(f"{'='*60}\n")

    # Fetch data
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data_map = fetch_multiple(symbols, exchange, timeframe, start, end, save_dir=DATA_DIR)

    if not data_map:
        return {"error": "No data fetched for any symbol"}

    # Run backtest per symbol
    all_trades    = []
    symbol_results = []

    for sym, df in data_map.items():
        if df.empty:
            continue
        print(f"\n── {sym} ({len(df):,} bars) ─────────────────")
        trades = _run_symbol(sym, df, strategies, capital, risk_pct)
        all_trades.extend(trades)

        done  = [t for t in trades if t.get("type") in ("SELL", "COVER")]
        wins  = sum(1 for t in done if t.get("profit", 0) > 0)
        losses= sum(1 for t in done if t.get("profit", 0) <= 0)
        pnl   = sum(t.get("profit", 0) for t in done)
        wr    = round(wins / (wins + losses) * 100, 1) if (wins + losses) else 0

        symbol_results.append({
            "symbol":   sym,
            "exchange": exchange,
            "trades":   len(done),
            "wins":     wins,
            "losses":   losses,
            "win_rate": wr,
            "pnl":      round(pnl, 2),
        })
        print(f"    Trades: {len(done)}  W{wins}/L{losses}  WR={wr}%  P&L=₹{pnl:+.2f}")

    # Save trade CSV
    if all_trades:
        pd.DataFrame(all_trades).to_csv(TRADE_FILE, index=False)
        print(f"\n[nse] Trades saved → {TRADE_FILE.name}")

    # Build summary result
    total_pnl = sum(r["pnl"] for r in symbol_results)
    result = {
        "ok":        True,
        "exchange":  exchange,
        "timeframe": timeframe,
        "symbols":   symbols,
        "strategies":strategies,
        "start":     start,
        "end":       end,
        "capital":   capital,
        "symbol_results": symbol_results,
        "total_pnl": round(total_pnl, 2),
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    }

    RESULT_FILE.write_text(json.dumps(result, indent=2))
    print(f"[nse] Results saved → {RESULT_FILE.name}")
    return result


if __name__ == "__main__":
    result = run_nse_backtest()
    if result.get("error"):
        print(f"\nERROR: {result['error']}")
    else:
        print(f"\nTotal P&L: ₹{result['total_pnl']:+,.2f}")
