import os
import shutil
import uuid
from datetime import datetime
import numpy as np
import pandas as pd
from pathlib import Path

from algoTrading.core.fs_utils import replace_with_retry
from algoTrading.core.mt5_connector import connect, shutdown
from algoTrading.dashboard import build_dashboard
from algoTrading.data.fetch_mt5 import fetch_and_store
from algoTrading.data.loader import load_csv
from algoTrading.backtest.engine import BacktestEngine
from algoTrading.backtest.metrics import analyze_trades
from algoTrading.config import Config
from algoTrading.rr_matrix import get_rr

from algoTrading.strategies.moving_average import MovingAverageStrategy
from algoTrading.strategies.supertrend_strategy import SupertrendStrategy
from algoTrading.strategies.engulfing_strategy import EngulfingStrategy
from algoTrading.strategies.green_dollar import GreenDollarStrategy
from algoTrading.strategies.green_dollar_clone import GreenDollarCloneStrategy
from algoTrading.strategies.engulfing_consolidation import EngulfingConsolidationStrategy
from algoTrading.strategies.mark2_strategy import Mark2Strategy
from algoTrading.strategies.engulfing_reversal import EngulfingReversalStrategy
from algoTrading.strategies.mark_dollar_supertrend import MarkDollarSuperTrendStrategy
from algoTrading.strategies.ema20 import Ema20Strategy
from algoTrading.strategies.engulf_volume_fade import EngulfVolumeFadeStrategy

BASE = Path(__file__).resolve().parent

STRATEGY_MAP = {
    "engulfing":               EngulfingStrategy,
    "green_dollar":            GreenDollarStrategy,
    # Bull-only variant (keeps just the source Bullvolumecheck $ marker,
    # drops the mirrored SHORT side) -- own STRATEGY_MAP entry gives it its
    # own RR-matrix row and its own checkbox in the Backtest UI, separate
    # from "green_dollar" itself.
    "greenDollar":             GreenDollarCloneStrategy,
    "ma":                      MovingAverageStrategy,
    "supertrend":              SupertrendStrategy,
    "engulfing_consolidation": EngulfingConsolidationStrategy,
    "engulfing_reversal":      EngulfingReversalStrategy,
    "mark2":                   Mark2Strategy,
    "mark_dollar_supertrend":  MarkDollarSuperTrendStrategy,
    "20ema":                   Ema20Strategy,
    "engulf_volume_fade":      EngulfVolumeFadeStrategy,
}


def get_strategy(name: str):
    cls = STRATEGY_MAP.get(name)
    if cls is None:
        raise ValueError(f"Unknown strategy: {name!r}. Available: {list(STRATEGY_MAP)}")
    # Per-(strategy, timeframe) RR override -- see rr_matrix.py. Defaults to
    # 1:1 for any (strategy, timeframe) pair that hasn't been explicitly
    # set via algoTrader's Settings tab.
    rr = get_rr(name, Config.TIMEFRAME)
    if name == "20ema":
        # Only this strategy takes a Supertrend-confirmation switch (see
        # Config.EMA20_SUPERTREND_FILTER / the Settings UI) -- every other
        # strategy class's __init__ only accepts rr, so this stays a
        # special case here rather than a kwarg every strategy must accept.
        return cls(rr=rr, supertrend_filter=Config.EMA20_SUPERTREND_FILTER)
    return cls(rr=rr)


def _atomic_copy(src: str, dst: str) -> None:
    """Copy via a same-directory temp file + os.replace() -- unlike
    shutil.copy2 straight to `dst`, this never leaves `dst` half-written if
    another process (a different timeframe running in parallel, see
    backtest_runner.py) is reading it at the same moment. Both `dst`
    candidates here (ohlcv_{symbol}.csv, sample_data.csv) are shared
    "latest fetch" aliases that every parallel timeframe run writes to."""
    tmp = f"{dst}.{uuid.uuid4().hex}.tmp"
    shutil.copy2(src, tmp)
    replace_with_retry(tmp, dst)


def fetch_symbol(symbol: str, is_first: bool):
    """Fetch OHLCV from MT5 straight into ohlcv_{symbol}_{TIMEFRAME}.csv --
    the per-timeframe snapshot (same idea as trade_data_{TIMEFRAME}.csv) a
    candle chart can be rebuilt from for any previously-backtested
    timeframe. That filename is unique per Config.TIMEFRAME, so it's
    inherently race-free even when several timeframes fetch the same
    symbol in parallel. It's then atomically copied into the shared
    "latest fetch" aliases (ohlcv_{symbol}.csv, and sample_data.csv for the
    first symbol) that non-timeframe-aware code still reads."""
    ohlcv_abs    = str(BASE / f"data/ohlcv_{symbol}.csv")
    ohlcv_per_tf = str(BASE / f"data/ohlcv_{symbol}_{Config.TIMEFRAME}.csv")
    sample_csv   = str(BASE / "data" / "sample_data.csv")

    date_from = datetime.strptime(Config.DATE_FROM, "%Y-%m-%d") if Config.DATE_FROM else None
    date_to = datetime.strptime(Config.DATE_TO, "%Y-%m-%d") if Config.DATE_TO else None

    fetch_and_store(
        symbol=symbol,
        timeframe=Config.TIMEFRAME,
        bars=Config.BARS,
        save_path=ohlcv_per_tf,
        date_from=date_from,
        date_to=date_to,
    )
    _atomic_copy(ohlcv_per_tf, ohlcv_abs)
    if is_first:
        _atomic_copy(ohlcv_per_tf, sample_csv)
        print(f"  Chart data → sample_data.csv")


def _merge_signals(df_base: pd.DataFrame, sig_dfs: list, strategy_names: list) -> pd.DataFrame:
    """
    Merge signal DataFrames from multiple strategies into a single timeline.

    Rules:
      - OHLCV columns come from df_base (authoritative).
      - For each bar, the first strategy (in config order) with a non-zero
        signal wins — later strategies are skipped for that bar.
      - The winning strategy's sl, tp, lot, and reverse_exit are used.
      - A '_strategy' column records which strategy owns each signal bar.
    """
    merged = df_base.copy()
    merged['signal']       = 0
    merged['sl']           = np.nan
    merged['tp']           = np.nan
    merged['lot']          = 0.0
    merged['reverse_exit'] = 0
    merged['_strategy']    = ''

    for sig_df, name in zip(sig_dfs, strategy_names):
        # Only fill bars that haven't been claimed yet
        free = merged['signal'] == 0
        has_signal = sig_df['signal'] != 0
        mask = free & has_signal

        merged.loc[mask, 'signal']    = sig_df.loc[mask, 'signal']
        merged.loc[mask, 'sl']        = sig_df.loc[mask, 'sl']
        merged.loc[mask, 'tp']        = sig_df.loc[mask, 'tp']
        merged.loc[mask, 'lot']       = sig_df.loc[mask, 'lot']
        merged.loc[mask, '_strategy'] = name

        if 'reverse_exit' in sig_df.columns:
            # Only copy reverse_exit flags that belong to this strategy's signals
            rev_mask = mask & (sig_df['reverse_exit'] == 1)
            merged.loc[rev_mask, 'reverse_exit'] = 1

    return merged


def run_combined_symbol(symbol: str, strategy_names: list) -> list:
    """
    Run all strategies on one symbol with a single shared capital pool.
    Signals are merged chronologically — first strategy to fire on a bar wins.
    Returns trade rows, each tagged with the originating strategy name.
    """
    # MUST read the per-TIMEFRAME snapshot, not the generic ohlcv_{symbol}.csv
    # alias: when several timeframes run in parallel (see algoTrader's
    # backtest_runner.py), that alias gets overwritten by whichever
    # timeframe's fetch finishes last -- reading it here would silently
    # backtest this run against a DIFFERENT timeframe's candles.
    ohlcv_rel = f"data/ohlcv_{symbol}_{Config.TIMEFRAME}.csv"
    df_base   = load_csv(ohlcv_rel)

    # Generate signals from every strategy independently
    sig_dfs = []
    total_signals = 0
    for name in strategy_names:
        sig_df = get_strategy(name).generate_signals(df_base)
        n = int((sig_df['signal'] != 0).sum())
        total_signals += n
        print(f"    [{name}] signals: {n}")
        sig_dfs.append(sig_df)

    # Merge into one timeline
    merged = _merge_signals(df_base, sig_dfs, strategy_names)
    combined_signals = int((merged['signal'] != 0).sum())
    print(f"    Combined unique signals : {combined_signals}  (out of {total_signals} raw)")

    # Single engine — one capital account for all strategies
    engine = BacktestEngine(
        capital=Config.INITIAL_CAPITAL,
        risk_per_trade=Config.RISK_PER_TRADE,
        symbol=symbol,
    )
    result = engine.run(merged, save=False)

    wins   = sum(1 for t in engine.trades if t.get("type") in ("SELL", "COVER") and t.get("profit", 0) > 0)
    losses = sum(1 for t in engine.trades if t.get("type") in ("SELL", "COVER") and t.get("profit", 0) <= 0)
    pnl    = result["final_capital"] - Config.INITIAL_CAPITAL

    print(f"    Trades  : {result['total_trades']}  |  W {wins}  L {losses}")
    print(f"    P&L     : {'+' if pnl >= 0 else ''}{pnl:.2f}  ({result['return (%)']:+.2f}%)")
    print(f"    Capital : {Config.INITIAL_CAPITAL} → {result['final_capital']}")

    return engine.trades


def main():
    symbols    = [s.strip() for s in Config.SYMBOL.split(",")   if s.strip()]
    strategies = [s.strip() for s in Config.STRATEGY.split(",") if s.strip()]

    print(f"\n{'='*60}")
    print(f"  BACKTEST  —  merged strategy execution")
    print(f"  Strategies : {', '.join(strategies)}")
    print(f"  Symbols    : {', '.join(symbols)}")
    if Config.DATE_FROM:
        print(f"  Timeframe  : {Config.TIMEFRAME}  |  Range : {Config.DATE_FROM} -> {Config.DATE_TO or 'now'} (UTC)")
    else:
        print(f"  Timeframe  : {Config.TIMEFRAME}  |  Bars : {Config.BARS}")
    print(f"  Capital    : ${Config.INITIAL_CAPITAL} per symbol  (shared across strategies)")
    print(f"{'='*60}")

    # ── Connect MT5 ────────────────────────────────────────────────
    if not connect():
        return

    # ── Fetch all symbols once ─────────────────────────────────────
    print(f"\n── Fetching data ──────────────────────────────────────────")
    for i, symbol in enumerate(symbols):
        print(f"  {symbol}")
        fetch_symbol(symbol, is_first=(i == 0))

    shutdown()

    # ── Run each symbol with all strategies merged ─────────────────
    all_trades = []
    for symbol in symbols:
        print(f"\n── {symbol}  |  {Config.TIMEFRAME}  {'─'*40}")
        trades = run_combined_symbol(symbol, strategies)
        all_trades.extend(trades)

    # ── Save combined trade log (sorted by time) ───────────────────
    # trade_data.csv is the "latest run" alias analyze_trades()/dashboard.py
    # always read. trade_data_{TIMEFRAME}.csv is a per-timeframe snapshot so
    # running several timeframes back to back (e.g. from algoTrader's
    # Backtest tab) doesn't overwrite each other -- each timeframe's results
    # stay queryable afterward.
    trade_path        = BASE / "data" / "trade_data.csv"
    trade_path_per_tf = BASE / "data" / f"trade_data_{Config.TIMEFRAME}.csv"
    if not all_trades:
        print("\nNo trades executed.")
        return

    trade_df = pd.DataFrame(all_trades)
    trade_df['time'] = pd.to_datetime(trade_df['time'])
    trade_df = trade_df.sort_values('time').reset_index(drop=True)

    # trade_path_per_tf's filename is unique per Config.TIMEFRAME (race-free
    # under parallel timeframe runs); trade_path is a shared "latest run"
    # alias every parallel run writes to, so it goes through the same
    # temp-file + os.replace() pattern as fetch_symbol()'s shared aliases --
    # atomic, so a reader never sees a half-written CSV.
    trade_df.to_csv(trade_path_per_tf, index=False)
    tmp_path = f"{trade_path}.{uuid.uuid4().hex}.tmp"
    trade_df.to_csv(tmp_path, index=False)
    replace_with_retry(tmp_path, trade_path)

    print(f"\nSaved {len(all_trades)} trade rows → trade_data.csv, trade_data_{Config.TIMEFRAME}.csv  (sorted by time)")

    # ── Overall metrics ────────────────────────────────────────────
    metrics = analyze_trades()
    print("\n===== COMBINED PERFORMANCE METRICS =====")
    for key, value in metrics.items():
        print(f"  {key}: {value}")

    # ── Dashboard ──────────────────────────────────────────────────
    build_dashboard()


if __name__ == "__main__":
    main()
