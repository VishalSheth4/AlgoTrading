"""
main.py — Backtest entry point for the algoTrading framework.
=============================================================

WHAT THIS MODULE DOES
---------------------
Orchestrates the full backtest pipeline end-to-end:

    1. CSV cleanup    — deletes ALL .csv files in <project_root>/data/ before any
                        new data is fetched, preventing stale-cache contamination.

    2. Data fetch     — MT5 mode: connects to MetaTrader 5, pulls fresh OHLCV bars
                        per symbol and saves them to ohlcv_{symbol}.csv.
                      — CSV / Kaggle mode: skips the MT5 connection and reads
                        pre-existing CSV files from disk.

    3. Signal gen.    — Every configured strategy independently generates signal
                        columns (signal, sl, tp, reverse_exit) for the full bar range.

    4. Signal merge   — Signals from multiple strategies are merged into one timeline.
                        First strategy in Config.STRATEGY to fire on a bar wins.
                        Ties never occur because the merge is sequential and exclusive.

    5. Backtest       — BacktestEngine replays the merged signal DataFrame bar-by-bar
                        with risk-based position sizing using a single shared capital pool
                        per symbol.

    6. Reporting      — Trade rows are collected, sorted by time, saved to trade_data.csv,
                        and passed through analyze_trades() + build_dashboard().

KEY CONFIG VALUES USED (algoTrading/config.py)
----------------------------------------------
    Config.SYMBOL           : comma-separated symbols, e.g. "XAUUSD" or "XAUUSD,EURUSD"
    Config.STRATEGY         : comma-separated strategy keys, e.g. "mark_dollar_supertrend"
    Config.TIMEFRAME        : MT5 timeframe string, e.g. "M5"
    Config.MODE             : "mt5" (live fetch) | "csv" / "kaggle" (offline)
    Config.START_DATE       : "YYYY-MM-DD" — bars before this date are dropped (or None)
    Config.END_DATE         : "YYYY-MM-DD" — bars after this date are dropped (or None)
    Config.INITIAL_CAPITAL  : starting account balance per symbol, e.g. 100
    Config.RISK_PER_TRADE   : fraction of capital risked per trade, e.g. 0.01 (= 1 %)
    Config.BARS             : maximum number of bars to fetch from MT5 (default 200 000)
    Config.DATA_PATH        : override CSV path for csv/kaggle mode (optional)

SIGNAL MERGE RULES
------------------
    - OHLCV columns always come from the authoritative df_base load.
    - For each bar, the first strategy (left-to-right in Config.STRATEGY) with
      a non-zero signal wins; that bar is marked and skipped by all later strategies.
    - The winning strategy's sl, tp, lot, and reverse_exit values are used.
    - A '_strategy' column in the merged DataFrame records the originating strategy.

OUTPUT FILES  (all written to <project_root>/data/)
---------------------------------------------------
    ohlcv_{symbol}.csv   — raw OHLCV bars fetched from MT5 (one file per symbol)
    sample_data.csv      — copy of the first symbol's OHLCV (used by the dashboard)
    skip_data.csv        — bars flagged SKIP by any strategy (used by the dashboard)
    trade_data.csv       — full trade log (entries + exits), sorted by time

USAGE
-----
    python main.py
    # Configure everything in algoTrading/config.py before running.
"""

import shutil
import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path

from algoTrading.strategies.calendar import ForexFactoryCalendar
from algoTrading.core.mt5_connector import connect, shutdown
from algoTrading.dashboard import build_dashboard
from algoTrading.data.fetch_mt5 import fetch_and_store
from algoTrading.data.loader import load_csv
from algoTrading.backtest.engine import BacktestEngine
from algoTrading.backtest.metrics import analyze_trades
from algoTrading.config import Config

from algoTrading.strategies.moving_average import MovingAverageStrategy
from algoTrading.strategies.supertrend_strategy import SupertrendStrategy
from algoTrading.strategies.engulfing_strategy import EngulfingStrategy
from algoTrading.strategies.green_dollar import GreenDollarStrategy
from algoTrading.strategies.engulfing_consolidation import EngulfingConsolidationStrategy
from algoTrading.strategies.mark2_strategy import Mark2Strategy
from algoTrading.strategies.engulfing_reversal import EngulfingReversalStrategy
from algoTrading.strategies.mark_dollar_supertrend import MarkDollarSuperTrendStrategy
from algoTrading.strategies.rsi_engulfing_strategy import RSIEngulfingStrategy
from algoTrading.strategies.mark5_supertrend import Mark5SupertrendStrategy
from algoTrading.strategies.SupertrendCounterFlip_X1 import SupertrendCounterFlipX1Strategy

# Absolute path to the package root (directory that contains main.py).
BASE = Path(__file__).resolve().parent

# Registry of all available strategy keys → classes.
# Add new strategies here; Config.STRATEGY selects which ones run.
STRATEGY_MAP = {
    "engulfing":               EngulfingStrategy,
    "green_dollar":            GreenDollarStrategy,
    "ma":                      MovingAverageStrategy,
    "supertrend":              SupertrendStrategy,
    "engulfing_consolidation": EngulfingConsolidationStrategy,
    "engulfing_reversal":      EngulfingReversalStrategy,
    "mark2":                   Mark2Strategy,
    "mark_dollar_supertrend":  MarkDollarSuperTrendStrategy,
    "rsi_engulfing":           RSIEngulfingStrategy,
    "mark5_supertrend":        Mark5SupertrendStrategy,
    "SupertrendCounterFlip_X1": SupertrendCounterFlipX1Strategy,
}


# ────────────────────────────────────────────────────────────────────────────────
# CACHE CLEANUP
# ────────────────────────────────────────────────────────────────────────────────

def purge_csv_cache() -> None:
    """
    Delete ALL .csv files inside <project_root>/data/ before the run starts.

    This guarantees that every backtest works on freshly fetched data with no
    stale signal, OHLCV, or trade files left over from a previous run.

    Files removed (if they exist):
        data/ohlcv_*.csv
        data/sample_data.csv
        data/skip_data.csv
        data/trade_data.csv
        data/<any other *.csv>

    Prints a summary of what was deleted.  Safe to call even if the directory
    is empty — missing files are silently skipped.
    """
    data_dir = BASE / "data"
    data_dir.mkdir(parents=True, exist_ok=True)     # create if it doesn't exist yet

    csv_files = list(data_dir.glob("*.csv"))

    if not csv_files:
        print("  🗑️  No CSV files to purge — data/ is already clean.")
        return

    deleted = []
    failed  = []
    for f in csv_files:
        try:
            f.unlink()
            deleted.append(f.name)
        except OSError as exc:
            failed.append((f.name, str(exc)))

    print(f"  🗑️  Purged {len(deleted)} CSV file(s) from data/:")
    for name in deleted:
        print(f"       — {name}")

    if failed:
        print(f"  ⚠️  Could not delete {len(failed)} file(s):")
        for name, err in failed:
            print(f"       — {name}: {err}")
    
    calendar_file = data_dir / "fx_calendar.csv"
    if calendar_file.exists():
        calendar_file.unlink()
        print("       — fx_calendar.csv")


def fetch_fx_calendar():

    print("\n── Fetching FX Calendar ─────────────────────────")

    try:

        calendar = ForexFactoryCalendar()

        df = calendar.fetch_calendar()

        if df.empty:

            print("  ❌ No FX calendar data fetched")
            return

        file_path = BASE / "data" / "fx_calendar.csv"

        df.to_csv(file_path, index=False)

        print(f"  ✅ FX calendar saved → {file_path.name}")

    except Exception as e:

        print(f"  ❌ Calendar fetch failed: {e}")
# ────────────────────────────────────────────────────────────────────────────────
# STRATEGY FACTORY
# ────────────────────────────────────────────────────────────────────────────────

def get_strategy(name: str):
    """
    Instantiate a strategy by its Config.STRATEGY key.

    Parameters
    ----------
    name : str — one of the keys in STRATEGY_MAP (e.g. "mark_dollar_supertrend")

    Returns
    -------
    An instantiated strategy object with a .generate_signals(df) method.

    Raises
    ------
    ValueError if the key is not registered in STRATEGY_MAP.
    """
    cls = STRATEGY_MAP.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown strategy: {name!r}. "
            f"Available: {list(STRATEGY_MAP)}"
        )
    return cls()


# ────────────────────────────────────────────────────────────────────────────────
# DATA FETCHING
# ────────────────────────────────────────────────────────────────────────────────

def fetch_symbol(symbol: str, is_first: bool) -> None:
    """
    Fetch fresh OHLCV bars for one symbol from MetaTrader 5 and save to CSV.

    Files written
    -------------
    data/ohlcv_{symbol}.csv  — full bar history for this symbol.
    data/sample_data.csv     — copy of the first symbol's file (dashboard uses this).

    Parameters
    ----------
    symbol   : str  — MT5 symbol name, e.g. "XAUUSD"  (from Config.SYMBOL)
    is_first : bool — True for the first symbol; triggers the sample_data.csv copy.
    """
    ohlcv_abs  = str(BASE / f"data/ohlcv_{symbol}.csv")
    sample_csv = BASE / "data" / "sample_data.csv"

    # Config.START_DATE: "YYYY-MM-DD" | None — restrict the fetch window start
    start_date = getattr(Config, "START_DATE", None)
    from_date  = datetime.strptime(start_date, "%Y-%m-%d") if start_date else None

    fetch_and_store(
        symbol=symbol,
        timeframe=Config.TIMEFRAME,         # e.g. "M5"
        bars=getattr(Config, "BARS", 200000),  # Config.BARS — max bars per fetch
        save_path=ohlcv_abs,
        from_date=from_date,
    )

    if is_first:
        shutil.copy2(ohlcv_abs, str(sample_csv))
        print(f"  Chart data → sample_data.csv")


# ────────────────────────────────────────────────────────────────────────────────
# SIGNAL MERGE
# ────────────────────────────────────────────────────────────────────────────────

def _merge_signals(
    df_base: pd.DataFrame,
    sig_dfs: list,
    strategy_names: list,
) -> pd.DataFrame:
    """
    Merge signal DataFrames from multiple strategies into a single timeline.

    Each bar can be owned by at most one strategy. The first strategy in
    Config.STRATEGY order to emit a non-zero signal on a bar claims it;
    all subsequent strategies are skipped for that bar.

    Parameters
    ----------
    df_base        : pd.DataFrame — authoritative OHLCV base (provides time, OHLCV cols)
    sig_dfs        : list[pd.DataFrame] — signal outputs from each strategy, same index
    strategy_names : list[str]          — strategy keys matching Config.STRATEGY order

    Returns
    -------
    pd.DataFrame — df_base columns plus: signal, sl, tp, lot, reverse_exit, _strategy
    """
    merged = df_base.copy()
    merged["signal"]       = 0
    merged["sl"]           = np.nan
    merged["tp"]           = np.nan
    merged["lot"]          = 0.0
    merged["reverse_exit"] = 0
    merged["_strategy"]    = ""

    for sig_df, name in zip(sig_dfs, strategy_names):
        # Only touch bars not yet claimed by a higher-priority strategy.
        free       = merged["signal"] == 0
        has_signal = sig_df["signal"] != 0
        mask       = free & has_signal

        merged.loc[mask, "signal"]    = sig_df.loc[mask, "signal"]
        merged.loc[mask, "sl"]        = sig_df.loc[mask, "sl"]
        merged.loc[mask, "tp"]        = sig_df.loc[mask, "tp"]
        merged.loc[mask, "lot"]       = sig_df.loc[mask, "lot"]
        merged.loc[mask, "_strategy"] = name

        # Copy reverse_exit flag only when the strategy provides it.
        if "reverse_exit" in sig_df.columns:
            rev_mask = mask & (sig_df["reverse_exit"] == 1)
            merged.loc[rev_mask, "reverse_exit"] = 1

    return merged


# ────────────────────────────────────────────────────────────────────────────────
# PER-SYMBOL BACKTEST
# ────────────────────────────────────────────────────────────────────────────────

def run_combined_symbol(symbol: str, strategy_names: list) -> list:
    """
    Run all strategies on one symbol using a single shared capital pool.

    Workflow
    --------
    1. Load OHLCV bars from CSV; apply START_DATE / END_DATE filters.
    2. Generate independent signal DataFrames from every strategy.
    3. Save skip markers (if any) to data/skip_data.csv for the dashboard.
    4. Merge all signal DataFrames into one timeline (first-wins rule).
    5. Run BacktestEngine on the merged DataFrame.
    6. Print a per-symbol summary (trades, W/L, P&L, capital change).

    Parameters
    ----------
    symbol         : str       — instrument, e.g. "XAUUSD"
    strategy_names : list[str] — strategy keys from Config.STRATEGY

    Returns
    -------
    list[dict] — raw trade rows from engine.trades (entries + exits combined)
    """
    # ── Load bars ────────────────────────────────────────────────────────────
    data_path = getattr(Config, "DATA_PATH", None)
    ohlcv_rel = data_path if data_path else f"data/ohlcv_{symbol}.csv"
    df_base   = load_csv(ohlcv_rel)

    # Config.START_DATE / END_DATE — trim bars outside the backtest window
    start_date = getattr(Config, "START_DATE", None)  # "YYYY-MM-DD" | None
    end_date   = getattr(Config, "END_DATE",   None)  # "YYYY-MM-DD" | None

    if start_date is not None:
        cutoff  = pd.Timestamp(start_date)
        df_base = df_base[df_base["time"] >= cutoff].reset_index(drop=True)
        print(f"    START_DATE={start_date} → {len(df_base):,} bars from {cutoff.date()} onwards")

    if end_date is not None:
        end_ts  = pd.Timestamp(end_date)
        df_base = df_base[df_base["time"] <= end_ts].reset_index(drop=True)
        print(f"    END_DATE={end_date}   → {len(df_base):,} bars up to {end_ts.date()}")

    # ── Generate signals from each strategy independently ────────────────────
    sig_dfs       = []
    total_signals = 0
    all_skips     = []

    for name in strategy_names:
        sig_df = get_strategy(name).generate_signals(df_base)
        n      = int((sig_df["signal"] != 0).sum())
        total_signals += n
        print(f"    [{name}] signals: {n}")
        sig_dfs.append(sig_df)

        # Collect any bars that the strategy explicitly skipped.
        if "skip" in sig_df.columns:
            skipped = sig_df[sig_df["skip"] == "SKIP"][["time"]].copy()
            n_skip  = len(skipped)
            if n_skip:
                print(f"    [{name}] skipped : {n_skip}")
            all_skips.append(skipped)

    # Save combined skip markers so the dashboard can overlay them on the chart.
    if all_skips:
        skip_df   = pd.concat(all_skips, ignore_index=True)
        skip_path = BASE / "data" / "skip_data.csv"
        skip_df.to_csv(skip_path, index=False)

    # ── Merge signals into one authoritative timeline ────────────────────────
    merged           = _merge_signals(df_base, sig_dfs, strategy_names)
    combined_signals = int((merged["signal"] != 0).sum())
    print(f"    Combined unique signals : {combined_signals}  (out of {total_signals} raw)")

    # ── Run the backtest engine ───────────────────────────────────────────────
    # One engine = one capital account shared across all strategies for this symbol.
    # Config.INITIAL_CAPITAL and Config.RISK_PER_TRADE drive sizing.
    engine = BacktestEngine(
        capital=Config.INITIAL_CAPITAL,         # e.g. 100
        risk_per_trade=Config.RISK_PER_TRADE,   # e.g. 0.01 → 1 % per trade
        symbol=symbol,
    )
    result = engine.run(merged, save=False)     # save=False — we save centrally below

    # ── Print per-symbol summary ─────────────────────────────────────────────
    wins   = sum(1 for t in engine.trades if t.get("type") in ("SELL", "COVER") and t.get("profit", 0) >  0)
    losses = sum(1 for t in engine.trades if t.get("type") in ("SELL", "COVER") and t.get("profit", 0) <= 0)
    pnl    = result["final_capital"] - Config.INITIAL_CAPITAL

    print(f"    Trades  : {result['total_trades']}  |  W {wins}  L {losses}")
    print(f"    P&L     : {'+' if pnl >= 0 else ''}{pnl:.2f}  ({result['return (%)']:+.2f}%)")
    print(f"    Capital : {Config.INITIAL_CAPITAL} → {result['final_capital']}")

    return engine.trades


# ────────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ────────────────────────────────────────────────────────────────────────────────

def main() -> None:
    purge_csv_cache()
    fetch_fx_calendar()
    """
    Full pipeline: purge cache → fetch data → generate signals → backtest → report.

    Reads all settings from Config (algoTrading/config.py).  No CLI args needed.
    """
    # Parse comma-separated symbol and strategy lists from Config.
    symbols    = [s.strip() for s in Config.SYMBOL.split(",")   if s.strip()]
    strategies = [s.strip() for s in Config.STRATEGY.split(",") if s.strip()]

    # Config.MODE: "mt5" | "csv" | "kaggle"
    mode     = getattr(Config, "MODE", "mt5").lower()
    csv_mode = mode in ("csv", "kaggle")

    # ── Banner ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  BACKTEST  —  merged strategy execution")
    print(f"  Mode       : {'CSV / Kaggle (offline)' if csv_mode else 'MT5 (live fetch)'}")
    print(f"  Strategies : {', '.join(strategies)}")
    print(f"  Symbols    : {', '.join(symbols)}")
    print(f"  Timeframe  : {Config.TIMEFRAME}  |  Bars : {getattr(Config, 'BARS', 'N/A')}")
    print(f"  Capital    : ${Config.INITIAL_CAPITAL} per symbol  (shared across strategies)")
    print(f"{'='*60}")

    # ── Step 1: Purge stale CSV cache ────────────────────────────────────────
    # Deletes every *.csv in data/ so we always work from fresh data.
    print(f"\n── Purging CSV cache ──────────────────────────────────────")
    purge_csv_cache()

    # ── Step 2: Fetch / validate data ────────────────────────────────────────
    if csv_mode:
        # Offline mode — validate that required CSV files exist on disk.
        data_path = getattr(Config, "DATA_PATH", None)
        print(f"\n── CSV mode — using {'DATA_PATH' if data_path else 'ohlcv_{symbol}.csv'} ──")
        for symbol in symbols:
            csv_p = BASE / data_path if data_path else BASE / "data" / f"ohlcv_{symbol}.csv"
            if not csv_p.exists():
                hint = "check Config.DATA_PATH" if data_path else "run fetch_kaggle.py first"
                print(f"  ❌ {csv_p} not found — {hint}")
                return
            print(f"  ✅ {symbol} — {csv_p.name}")
    else:
        # MT5 mode — connect to the terminal and pull fresh bars for every symbol.
        if not connect():
            return
        print(f"\n── Fetching data ──────────────────────────────────────────")
        for i, symbol in enumerate(symbols):
            print(f"  {symbol}")
            fetch_symbol(symbol, is_first=(i == 0))
        shutdown()

    # ── Step 3: Run each symbol ───────────────────────────────────────────────
    all_trades: list = []
    for symbol in symbols:
        print(f"\n── {symbol}  |  {Config.TIMEFRAME}  {'─'*40}")
        trades = run_combined_symbol(symbol, strategies)
        all_trades.extend(trades)

    # ── Step 4: Save combined trade log sorted by time ────────────────────────
    trade_path = BASE / "data" / "trade_data.csv"

    if not all_trades:
        print("\n⚠️  No trades executed — nothing to save.")
        return

    trade_df           = pd.DataFrame(all_trades)
    trade_df["time"]   = pd.to_datetime(trade_df["time"])
    trade_df           = trade_df.sort_values("time").reset_index(drop=True)
    trade_df.to_csv(trade_path, index=False)
    print(f"\nSaved {len(all_trades)} trade rows → trade_data.csv  (sorted by time)")

    # ── Step 5: Performance metrics ───────────────────────────────────────────
    metrics = analyze_trades()
    print("\n===== COMBINED PERFORMANCE METRICS =====")
    for key, value in metrics.items():
        print(f"  {key}: {value}")

    # ── Step 6: Dashboard ─────────────────────────────────────────────────────
    build_dashboard()


if __name__ == "__main__":
    main()