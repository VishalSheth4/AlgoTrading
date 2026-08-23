"""
Reads algoTrading's trade_data*.csv files (produced by running
src/algoTrading/main_backtest.py) and computes the same performance
metrics algoTrading/dashboard.py's build_dashboard() renders into its
standalone dashboard.html -- exposed here as JSON for the "Backtest" tab
in this Django app instead.

A run against multiple timeframes (see backtest_runner.py) writes one
trade_data_{TIMEFRAME}.csv per timeframe plus a trade_data.csv "latest run"
alias, so results for each timeframe stay independently browsable instead
of the last one silently overwriting the others.

Deliberately reuses algoTrading.dashboard's calc_streaks/_find_streak_trades
helpers (via a sys.path insert to the sibling src/ directory) rather than
re-implementing the same streak math a second time -- algoTrading and
algoTrader are sibling packages under src/, so this is a one-directory-up
import, not a network/package dependency.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_SRC_ROOT = Path(__file__).resolve().parents[3]  # .../src
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from algoTrading.dashboard import (  # noqa: E402
    _compute_supertrend,
    _find_streak_trades,
    _mark_engulfing,
    calc_streaks,
)

_DATA_DIR = _SRC_ROOT / "algoTrading" / "data"
TRADE_CSV = _DATA_DIR / "trade_data.csv"
_ALL_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]

# Every stored timestamp (trade_data*.csv, ohlcv_*.csv) is true UTC (see
# fetch_mt5.py's broker-offset correction). The whole project displays IST
# instead -- this is display-only, added at the boundary where a value
# becomes a string/label for a human, never applied to timestamps still
# used for sorting/matching/market-hours logic.
IST_OFFSET = pd.Timedelta(hours=5, minutes=30)


def list_available_timeframe_results() -> list[str]:
    """Which trade_data_{TIMEFRAME}.csv files currently exist on disk --
    backs the Backtest tab's "viewing results for" timeframe selector."""
    return [tf for tf in _ALL_TIMEFRAMES if (_DATA_DIR / f"trade_data_{tf}.csv").exists()]


def list_available_candle_timeframes(symbol: str) -> list[str]:
    """Which ohlcv_{symbol}_{TIMEFRAME}.csv snapshots currently exist --
    backs the Backtest tab's Candles-pane timeframe buttons."""
    return [tf for tf in _ALL_TIMEFRAMES if (_DATA_DIR / f"ohlcv_{symbol}_{tf}.csv").exists()]


def _trade_csv_path(timeframe: str | None) -> Path:
    if timeframe:
        return _DATA_DIR / f"trade_data_{timeframe}.csv"
    return TRADE_CSV


def _load_trade_markers(ohlcv: list[dict], trade_csv_path: Path) -> list[dict]:
    """Snap each trade's entry/exit timestamp to the nearest OHLCV bar and
    return LightweightCharts marker dicts, each tagged with its originating
    `strategy` (from the trade log's own `strategy` column) so the frontend
    can filter markers by the enabled-strategy buttons without another
    round trip. Adapted from algoTrading.dashboard._load_trade_markers,
    which doesn't carry the strategy tag and reads a fixed trade_data.csv
    path rather than a specific timeframe's snapshot."""
    import bisect

    if not trade_csv_path.exists() or not ohlcv:
        return []
    try:
        df = pd.read_csv(trade_csv_path)
    except Exception:
        return []
    df = df[df["type"].isin(["BUY", "SHORT", "SELL", "COVER"])].copy()
    if df.empty:
        return []
    df["ts"] = pd.to_datetime(df["time"]).apply(lambda x: int(x.timestamp()))

    bar_times = sorted(c["time"] for c in ohlcv)
    t_min, t_max = bar_times[0], bar_times[-1]

    def snap(ts):
        if ts < t_min or ts > t_max:
            return None
        idx = max(bisect.bisect_right(bar_times, ts) - 1, 0)
        floor_t = bar_times[idx]
        # Genuinely pick whichever neighboring bar `ts` is closer to --
        # the previous version always floored (picked `floor_t` even when
        # `ts` was closer to the NEXT bar), which silently shifted a
        # trade's marker one candle earlier than it belonged whenever its
        # timestamp didn't land exactly on a bar (e.g. the trade CSV and
        # the currently-loaded ohlcv CSV came from slightly different
        # fetches). Exact matches (the common case) are unaffected.
        if idx + 1 < len(bar_times):
            ceil_t = bar_times[idx + 1]
            if ceil_t - ts < ts - floor_t:
                return ceil_t
        return floor_t

    markers = []
    for _, row in df.iterrows():
        bt = snap(int(row["ts"]))
        if bt is None:
            continue
        t = row["type"]
        raw = row.get("exit_reason", float("nan"))
        reason = str(raw) if pd.notna(raw) else ""
        strategy = str(row.get("strategy", "")) or "unknown"

        if t == "BUY":
            markers.append({"time_unix": bt, "position": "belowBar", "color": "#26a69a", "shape": "arrowUp", "text": "B", "strategy": strategy})
        elif t == "SHORT":
            markers.append({"time_unix": bt, "position": "aboveBar", "color": "#ef5350", "shape": "arrowDown", "text": "S", "strategy": strategy})
        elif t == "SELL":
            if reason == "TP":
                markers.append({"time_unix": bt, "position": "aboveBar", "color": "#26a69a", "shape": "circle", "text": "T", "strategy": strategy})
            else:
                markers.append({"time_unix": bt, "position": "belowBar", "color": "#ef5350", "shape": "circle", "text": "SL", "strategy": strategy})
        elif t == "COVER":
            if reason == "TP":
                markers.append({"time_unix": bt, "position": "belowBar", "color": "#26a69a", "shape": "circle", "text": "T", "strategy": strategy})
            else:
                markers.append({"time_unix": bt, "position": "aboveBar", "color": "#ef5350", "shape": "circle", "text": "SL", "strategy": strategy})

    markers.sort(key=lambda m: m["time_unix"])
    return markers


def load_candles_with_markers(symbol: str, timeframe: str, max_bars: int = 5000) -> dict | None:
    """OHLCV + Supertrend + trade markers for one symbol/timeframe, for the
    Backtest tab's Candles pane. Returns None if that timeframe was never
    backtested (no ohlcv_{symbol}_{timeframe}.csv on disk)."""
    csv_path = _DATA_DIR / f"ohlcv_{symbol}_{timeframe}.csv"
    if not csv_path.exists():
        return None

    df = pd.read_csv(csv_path, usecols=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time").reset_index(drop=True)
    if df.empty:
        return None

    # Supertrend computed on the FULL history for accuracy, same as
    # algoTrading.dashboard -- trimming first would distort its ATR warm-up.
    st_all = _compute_supertrend(df)

    if len(df) > max_bars:
        df = df.tail(max_bars).reset_index(drop=True)
    cutoff_unix = int(df["time"].iloc[0].timestamp())

    ohlcv = []
    for _, row in df.iterrows():
        ohlcv.append({
            "time": int(row["time"].timestamp()),
            "open": round(float(row["open"]), 2),
            "high": round(float(row["high"]), 2),
            "low": round(float(row["low"]), 2),
            "close": round(float(row["close"]), 2),
        })
    _mark_engulfing(ohlcv)  # adds color/borderColor/wickColor in-place for engulfing candles

    st_trimmed = [p for p in st_all if p["time"] >= cutoff_unix]
    markers = _load_trade_markers(ohlcv, _trade_csv_path(timeframe))

    return {
        "candles": [
            {
                "time_unix": c["time"], "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"],
                "color": c.get("color"), "borderColor": c.get("borderColor"), "wickColor": c.get("wickColor"),
            }
            for c in ohlcv
        ],
        "supertrend": [{"time_unix": p["time"], "value": p["value"], "color": p["color"]} for p in st_trimmed],
        "markers": markers,
    }


def _best_trade(grp: pd.DataFrame) -> dict:
    if grp.empty:
        return {"profit": 0, "time": "—", "symbol": "—", "strategy": "—", "dir": "—"}
    row = grp.loc[grp["profit"].abs().idxmax()]
    return {
        "profit": round(float(row["profit"]), 2),
        "time": str(row["time"])[:16],
        "symbol": str(row.get("symbol", "—")),
        "strategy": str(row.get("strategy", "—")),
        "dir": "SHORT" if row["type"] == "COVER" else "LONG",
    }


def load_backtest_results(timeframe: str | None = None) -> dict | None:
    """Returns None if no backtest has been run yet for this timeframe (no
    matching trade_data*.csv, or it's empty / has no completed trades).
    `timeframe=None` reads the "latest run" alias, trade_data.csv."""
    csv_path = _trade_csv_path(timeframe)
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return None

    df = pd.read_csv(csv_path)
    if df.empty:
        return None

    done = df[df["type"].isin(["SELL", "COVER"])].copy()
    done["profit"] = pd.to_numeric(done["profit"], errors="coerce")
    done["capital"] = pd.to_numeric(done["capital"], errors="coerce")
    done = done.dropna(subset=["profit", "capital"])

    # A trade in the last ~2.5h before Friday's 21:00 UTC close (still
    # genuinely inside market hours) rolls forward into Saturday's
    # calendar date once shifted to IST -- looks exactly like a weekend
    # trade even though it isn't one. Flag it (using the RAW UTC value,
    # before shifting) so the UI can tag it instead of leaving it looking
    # like a market-hours bug.
    def _is_fri_session_crossing(utc_series: pd.Series) -> pd.Series:
        return (utc_series.dt.weekday == 4) & ((utc_series + IST_OFFSET).dt.date != utc_series.dt.date)

    time_utc = pd.to_datetime(done["time"], errors="coerce")
    done["time_fri_session"] = _is_fri_session_crossing(time_utc)
    # +IST_OFFSET right after parsing -- a constant shift doesn't change
    # relative order, so sorting/streak-matching below still work exactly
    # as before; every string built from these columns from here on is IST.
    done["time"] = time_utc + IST_OFFSET
    # entry_time was only added to the trade CSVs after this fix -- older
    # runs' CSVs won't have the column, so fall back to "" (rendered as
    # "—") rather than KeyError-ing on a stale trade_data*.csv.
    if "entry_time" in done.columns:
        entry_time_utc = pd.to_datetime(done["entry_time"], errors="coerce")
        done["entry_fri_session"] = _is_fri_session_crossing(entry_time_utc)
        done["entry_time"] = entry_time_utc + IST_OFFSET
    else:
        done["entry_time"] = pd.NaT
        done["entry_fri_session"] = False
    done = done.sort_values("time").reset_index(drop=True)

    if done.empty:
        return None

    profits = done["profit"].tolist()
    caps = done["capital"].tolist()
    times = done["time"].tolist()
    n = len(done)

    wins = done[done["profit"] > 0]
    losses = done[done["profit"] < 0]
    nw, nl = len(wins), len(losses)

    start_cap = caps[0] - profits[0]
    end_cap = caps[-1]
    total_profit = round(end_cap - start_cap, 2)
    profit_pct = round(total_profit / start_cap * 100, 2) if start_cap else 0

    win_rate = round(nw / n * 100, 2)
    avg_win = round(wins["profit"].mean(), 2) if nw else 0
    avg_loss = round(losses["profit"].mean(), 2) if nl else 0

    gross_win = wins["profit"].sum() if nw else 0
    gross_loss = abs(losses["profit"].sum()) if nl else 0
    pf_val = round(gross_win / gross_loss, 2) if gross_loss > 0 else 999
    pf_display = str(pf_val) if pf_val != 999 else "∞"

    eq = done["capital"]
    dd = (eq - eq.cummax()) / eq.cummax() * 100
    max_dd = round(dd.min(), 2)

    max_ws, max_ls, cur_s, cur_t = calc_streaks(profits)
    win_streak_trades, loss_streak_trades = _find_streak_trades(done, profits)

    date_from = str(times[0])[:16]
    date_to = str(times[-1])[:16]

    biggest_win = _best_trade(wins)
    biggest_loss = _best_trade(losses)

    done["_month"] = pd.to_datetime(done["time"], errors="coerce").dt.to_period("M")
    monthly_rows = []
    for period, grp in done.groupby("_month"):
        mw = (grp["profit"] > 0).sum()
        ml = (grp["profit"] < 0).sum()
        mt = len(grp)
        mp = round(grp["profit"].sum(), 2)
        first = grp.iloc[0]
        month_start = float(first["capital"]) - float(first["profit"])
        m_profit_pct = round(mp / month_start * 100, 2) if month_start else 0
        m_win_rate = round(float(mw) / mt * 100, 1) if mt else 0
        monthly_rows.append({
            "month": str(period), "trades": int(mt), "wins": int(mw), "losses": int(ml),
            "profit": mp, "profit_pct": m_profit_pct, "win_rate": m_win_rate,
        })

    symbols_data = []
    if "symbol" in done.columns:
        for sym, grp in done.groupby("symbol", sort=True):
            sw = int((grp["profit"] > 0).sum())
            sl_ = int((grp["profit"] < 0).sum())
            st_ = len(grp)
            sp = round(grp["profit"].sum(), 2)
            swr = round(sw / st_ * 100, 1) if st_ else 0
            sym_profs = grp["profit"].tolist()
            sym_caps = grp["capital"].tolist()
            sym_start = sym_caps[0] - sym_profs[0] if sym_caps else 0
            sym_pct = round(sp / sym_start * 100, 2) if sym_start else 0
            symbols_data.append({
                "symbol": sym, "trades": int(st_), "wins": sw, "losses": sl_,
                "profit": sp, "win_rate": swr, "profit_pct": sym_pct,
            })

    rows = []
    for idx, (_, r) in enumerate(done.iterrows()):
        sl_val = r.get("sl")
        tp_val = r.get("tp")
        lot_val = r.get("lot_size")
        candle_size_val = r.get("candle_size")
        sl_str = f"{float(sl_val):.2f}" if pd.notna(sl_val) else "—"
        tp_str = f"{float(tp_val):.2f}" if pd.notna(tp_val) else "—"
        lot_str = f"{float(lot_val):.2f}" if pd.notna(lot_val) else "—"
        candle_size_str = f"{float(candle_size_val):.2f}" if pd.notna(candle_size_val) else "—"
        label = str(r.get("exit_label", r.get("exit_reason", "")))
        entry_time_val = r.get("entry_time")
        entry_time_str = str(entry_time_val)[:16] if pd.notna(entry_time_val) else "—"
        rows.append({
            "num": idx + 1, "entry_time": entry_time_str, "time": str(r["time"])[:16], "symbol": str(r.get("symbol", "")),
            "entry_fri_session": bool(r.get("entry_fri_session", False)), "time_fri_session": bool(r.get("time_fri_session", False)),
            "strategy": str(r.get("strategy", "")), "dir": "SHORT" if r["type"] == "COVER" else "LONG",
            "entry": round(float(r.get("entry_price", 0)), 2), "sl": sl_str, "target": tp_str,
            "exit": round(float(r.get("exit_price", 0)), 2), "label": label,
            "lot_size": lot_str, "candle_size": candle_size_str,
            "profit": round(float(r["profit"]), 2), "cap": round(float(r["capital"]), 2),
        })

    eq_labels = ["Start"] + [str(t)[:16] for t in times]
    eq_data = [round(start_cap, 2)] + [round(c, 2) for c in caps]

    strategies_str = ", ".join(sorted(done["strategy"].dropna().unique())) if "strategy" in done.columns else ""
    symbols_str = ", ".join(sorted(done["symbol"].dropna().unique())) if "symbol" in done.columns else ""

    return {
        "metrics": {
            "start_cap": round(start_cap, 2), "end_cap": round(end_cap, 2),
            "total_profit": total_profit, "profit_pct": profit_pct,
            "n": n, "nw": nw, "nl": nl, "win_rate": win_rate,
            "avg_win": avg_win, "avg_loss": avg_loss,
            "pf_display": pf_display, "pf_val": pf_val, "max_dd": max_dd,
            "max_ws": max_ws, "max_ls": max_ls, "cur_s": cur_s, "cur_t": cur_t,
            "strategies_str": strategies_str, "symbols_str": symbols_str,
            "biggest_win": biggest_win, "biggest_loss": biggest_loss,
            "date_from": date_from, "date_to": date_to,
        },
        "rows": rows,
        "monthly_rows": monthly_rows,
        "eq_labels": eq_labels,
        "eq_data": eq_data,
        "profits": profits,
        "symbols_data": symbols_data,
        "win_streak_trades": win_streak_trades,
        "loss_streak_trades": loss_streak_trades,
    }
