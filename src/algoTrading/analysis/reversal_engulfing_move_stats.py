"""
reversal_engulfing_move_stats.py
=================================

Standalone research script -- does NOT touch Mark2Strategy or the live
backtest pipeline. Answers one question:

    "When a trade is taken off a Supertrend reversal, and the engulfing
     confirmation candle is the 1st, 2nd, or 3rd candle after the flip,
     how big a move (price distance) does that trade end up making?"

Mark2Strategy itself only ever looks at the first 2 candles after a flip
(see mark2_strategy.py's "i - x_idx > 2" cutoff). This script reruns the
same Supertrend + engulfing detection logic with the window widened to 3
candles, tags every signal with WHICH candle after the flip it fired on,
runs it through the real BacktestEngine (same SL/TP/exit rules as
production), and buckets the resulting trades by that candle offset --
reporting count, win rate, and average / longest / smallest move for
each bucket.

Usage:
    python -m algoTrading.analysis.reversal_engulfing_move_stats
    python -m algoTrading.analysis.reversal_engulfing_move_stats --symbol XAUUSD --timeframe M15 --max-offset 3
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from algoTrading.config import Config
from algoTrading.data.loader import load_csv
from algoTrading.backtest.engine import BacktestEngine
from algoTrading.rr_matrix import get_rr

BASE = Path(__file__).resolve().parents[1]


# =============================================================
# Supertrend -- identical math to Mark2Strategy.calculate_supertrend
# (duplicated here on purpose so this analysis script has no dependency
# on, and never risks mutating, the live strategy).
# =============================================================
def _calculate_supertrend(df, period=10, multiplier=3):
    close = df['close'].values
    high  = df['high'].values
    low   = df['low'].values
    n     = len(df)

    hl2        = (high + low) / 2
    prev_close = np.concatenate([[close[0]], close[:-1]])

    tr = np.maximum.reduce([
        high - low,
        np.abs(high - prev_close),
        np.abs(low  - prev_close)
    ])

    atr = pd.Series(tr).ewm(alpha=1 / period, min_periods=period, adjust=False).mean().values

    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()

    for i in range(1, n):
        if np.isnan(basic_lower[i]):
            continue
        prev_lo = final_lower[i - 1]
        prev_hi = final_upper[i - 1]

        if np.isnan(prev_lo):
            final_lower[i] = basic_lower[i]
        elif basic_lower[i] > prev_lo or close[i - 1] < prev_lo:
            final_lower[i] = basic_lower[i]
        else:
            final_lower[i] = prev_lo

        if np.isnan(prev_hi):
            final_upper[i] = basic_upper[i]
        elif basic_upper[i] < prev_hi or close[i - 1] > prev_hi:
            final_upper[i] = basic_upper[i]
        else:
            final_upper[i] = prev_hi

    trend      = -np.ones(n, dtype=int)
    supertrend = np.full(n, np.nan)

    for i in range(1, n):
        lo = final_lower[i]
        hi = final_upper[i]
        if np.isnan(hi):
            trend[i] = trend[i - 1]
            continue
        if trend[i - 1] == 1:
            trend[i] = -1 if close[i] < lo else 1
        else:
            trend[i] = 1 if close[i] > hi else -1
        supertrend[i] = lo if trend[i] == 1 else hi

    df = df.copy()
    df['st_trend'] = trend
    df['st_lower'] = final_lower
    df['st_upper'] = final_upper
    return df


def _is_bearish_engulfing(prev, curr):
    return (
        prev['close'] > prev['open'] and
        curr['close'] < curr['open'] and
        curr['open']  >= prev['close'] and
        curr['close'] <= prev['open']
    )


def _is_bullish_engulfing(prev, curr):
    return (
        prev['close'] < prev['open'] and
        curr['close'] > curr['open'] and
        curr['open']  <= prev['close'] and
        curr['close'] >= prev['open']
    )


def generate_signals_with_offset(df, period=10, multiplier=3, rr=3.0, max_offset=3):
    """Same entry/SL/TP rules as Mark2Strategy, but the "watch window" after
    a Supertrend flip is widened to `max_offset` candles (default 3, vs the
    hardcoded 2 in the live strategy), and every signal is tagged with
    `candle_offset` = which candle after the flip triggered it (1, 2, or 3)."""
    df = _calculate_supertrend(df, period, multiplier)

    df['signal']        = 0
    df['sl']            = np.nan
    df['tp']            = np.nan
    df['candle_offset'] = 0   # 1 / 2 / 3 = which post-flip candle fired the entry

    trend  = df['st_trend'].values
    closes = df['close'].values
    highs  = df['high'].values
    lows   = df['low'].values
    n      = len(df)

    x_idx, active_trend, done = None, None, False
    running_ref = None
    trend_count = 0

    for i in range(1, n):
        if trend[i] != trend[i - 1]:
            prev_count  = trend_count
            trend_count = 1
            if prev_count >= Config.MIN_TREND_CANDLES:
                x_idx, active_trend, done = i, trend[i], False
                running_ref = highs[i] if active_trend == 1 else lows[i]
            else:
                x_idx, active_trend, done = None, trend[i], True
            continue

        trend_count += 1
        if x_idx is None or done:
            continue

        offset = i - x_idx
        if offset > max_offset:
            done = True
            continue

        prev_c, curr_c = df.iloc[i - 1], df.iloc[i]

        if active_trend == 1:                       # buy trend -> watch for SHORT
            if closes[i] > running_ref:
                done = True
            elif _is_bearish_engulfing(prev_c, curr_c):
                entry = closes[i]
                sl    = max(running_ref, highs[i])
                risk  = sl - entry
                if risk > 0:
                    st_line = df.iloc[i]['st_lower']
                    tp = float(st_line) if not np.isnan(st_line) else entry - rr * risk
                    df.at[i, 'signal']        = -1
                    df.at[i, 'sl']            = sl
                    df.at[i, 'tp']            = tp
                    df.at[i, 'candle_offset'] = offset
                done = True
            else:
                running_ref = max(running_ref, highs[i])

        elif active_trend == -1:                     # sell trend -> watch for LONG
            if closes[i] < running_ref:
                done = True
            elif _is_bullish_engulfing(prev_c, curr_c):
                entry = closes[i]
                sl    = min(running_ref, lows[i])
                risk  = entry - sl
                if risk > 0:
                    st_line = df.iloc[i]['st_upper']
                    tp = float(st_line) if not np.isnan(st_line) else entry + rr * risk
                    df.at[i, 'signal']        = 1
                    df.at[i, 'sl']            = sl
                    df.at[i, 'tp']            = tp
                    df.at[i, 'candle_offset'] = offset
                done = True
            else:
                running_ref = min(running_ref, lows[i])

    df['lot'] = Config.LOT_SIZE
    return df


def run_move_stats(symbol="XAUUSD", timeframe="M15", max_offset=3, period=10, multiplier=3):
    ohlcv_rel = f"data/ohlcv_{symbol}_{timeframe}.csv"
    csv_path  = BASE / ohlcv_rel
    if not csv_path.exists():
        raise FileNotFoundError(
            f"{csv_path} not found -- run a backtest for {symbol}/{timeframe} first "
            f"so its OHLCV snapshot exists."
        )

    df_base = load_csv(ohlcv_rel)
    rr = get_rr("mark2", timeframe)
    sig_df = generate_signals_with_offset(df_base, period=period, multiplier=multiplier, rr=rr, max_offset=max_offset)

    # candle_offset lives only on entry (signal) rows -- carry it forward so
    # it survives onto the matching exit row after BacktestEngine runs.
    offset_by_time = dict(zip(
        sig_df.loc[sig_df['candle_offset'] > 0, 'time'],
        sig_df.loc[sig_df['candle_offset'] > 0, 'candle_offset'],
    ))

    engine = BacktestEngine(capital=Config.INITIAL_CAPITAL, risk_per_trade=Config.RISK_PER_TRADE, symbol=symbol)
    engine.run(sig_df, save=False)

    # BacktestEngine only ever holds one open position at a time, so its
    # trade log strictly alternates entry, exit, entry, exit, ... -- the
    # n-th exit always belongs to the n-th entry.
    entries = [t for t in engine.trades if t['type'] in ("BUY", "SHORT")]
    exits   = [t for t in engine.trades if t['type'] in ("SELL", "COVER")]

    rows = []
    for entry_t, exit_t in zip(entries, exits):
        offset = offset_by_time.get(entry_t['time'])
        if offset is None:
            continue
        move = abs(exit_t['exit_price'] - exit_t['entry_price'])
        rows.append({
            "candle_offset": offset,
            "direction": "LONG" if exit_t['type'] == "SELL" else "SHORT",
            "entry_time": entry_t['time'],
            "entry_price": exit_t['entry_price'],
            "exit_price": exit_t['exit_price'],
            "move": move,
            "profit": exit_t['profit'],
            "win": exit_t['profit'] > 0,
        })

    result_df = pd.DataFrame(rows)
    if result_df.empty:
        print(f"No candle-offset-tagged trades found for {symbol}/{timeframe} (max_offset={max_offset}).")
        return result_df

    print(f"\n{'='*70}")
    print(f"  Reversal-Supertrend Engulfing -- move stats by entry candle")
    print(f"  {symbol}  |  {timeframe}  |  watch window: 1..{max_offset} candles after flip")
    print(f"{'='*70}")

    for offset in sorted(result_df['candle_offset'].unique()):
        grp = result_df[result_df['candle_offset'] == offset]
        wins = int(grp['win'].sum())
        label = {1: "1st", 2: "2nd", 3: "3rd"}.get(offset, f"{offset}th")
        print(f"\n  {label} candle after flip  --  {len(grp)} trades  ({wins} win / {len(grp) - wins} loss, {wins/len(grp)*100:.1f}% win rate)")
        print(f"    Move   avg: {grp['move'].mean():.2f}   longest: {grp['move'].max():.2f}   smallest: {grp['move'].min():.2f}")
        print(f"    Profit avg: {grp['profit'].mean():.2f}   best: {grp['profit'].max():.2f}   worst: {grp['profit'].min():.2f}")

    print(f"\n  {'-'*66}")
    print(f"  ALL candles combined  --  {len(result_df)} trades")
    print(f"    Move   avg: {result_df['move'].mean():.2f}   longest: {result_df['move'].max():.2f}   smallest: {result_df['move'].min():.2f}")
    print(f"{'='*70}\n")

    return result_df


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default=Config.SYMBOL.split(",")[0].strip())
    parser.add_argument("--timeframe", default=Config.TIMEFRAME)
    parser.add_argument("--max-offset", type=int, default=3, help="widen the post-flip watch window to N candles (default 3)")
    parser.add_argument("--period", type=int, default=10, help="Supertrend ATR period")
    parser.add_argument("--multiplier", type=float, default=3, help="Supertrend ATR multiplier")
    args = parser.parse_args()

    run_move_stats(
        symbol=args.symbol, timeframe=args.timeframe,
        max_offset=args.max_offset, period=args.period, multiplier=args.multiplier,
    )


if __name__ == "__main__":
    main()
