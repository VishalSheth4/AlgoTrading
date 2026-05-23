import json
import socket
import urllib.request
import webbrowser
import pandas as pd
import numpy as np
from pathlib import Path

from algoTrading.config import Config


def _get_chartjs():
    """Return Chart.js source — cached locally after first download."""
    cache = Path(__file__).resolve().parent / "data" / "_chartjs.min.js"
    if cache.exists() and cache.stat().st_size > 10_000:
        return cache.read_text(encoding='utf-8')
    url = "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"
    try:
        print("Downloading Chart.js (one-time)...")
        with urllib.request.urlopen(url, timeout=10) as r:
            src = r.read().decode('utf-8')
        cache.write_text(src, encoding='utf-8')
        print("Chart.js cached locally")
        return src
    except Exception:
        print("Chart.js download failed — charts will not render (no internet?)")
        return ""


def _get_lightweightcharts():
    """Return LightweightCharts v4 source — cached locally after first download."""
    cache = Path(__file__).resolve().parent / "data" / "_lwc.min.js"
    if cache.exists():
        return cache.read_text(encoding='utf-8')
    url = "https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"
    try:
        print("Downloading LightweightCharts (one-time)...")
        with urllib.request.urlopen(url, timeout=20) as r:
            src = r.read().decode('utf-8')
        cache.write_text(src, encoding='utf-8')
        print("LightweightCharts cached locally")
        return src
    except Exception:
        print("LightweightCharts download failed -- candle chart will not render (no internet?)")
        return ""


def _compute_supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0):
    """
    Wilder RMA Supertrend matching TradingView ta.supertrend().
    df must have: time (datetime64), high, low, close columns.
    Returns list of {"time": int (unix s), "value": float, "color": str}.
    """
    close = df['close'].values
    high  = df['high'].values
    low   = df['low'].values
    n     = len(df)

    hl2        = (high + low) / 2
    prev_close = np.concatenate([[close[0]], close[:-1]])
    tr = np.maximum.reduce([
        high - low,
        np.abs(high - prev_close),
        np.abs(low  - prev_close),
    ])
    atr = pd.Series(tr).ewm(
        alpha=1 / period, min_periods=period, adjust=False
    ).mean().values

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

    trend = -np.ones(n, dtype=int)
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

    times_unix = df['time'].apply(lambda x: int(x.timestamp())).values
    result = []
    for i in range(n):
        val = final_lower[i] if trend[i] == 1 else final_upper[i]
        if np.isnan(val):
            continue
        result.append({
            "time":  int(times_unix[i]),
            "value": round(float(val), 2),
            "color": "#26a69a" if trend[i] == 1 else "#ef5350",
        })
    return result


def _mark_engulfing(ohlcv: list) -> None:
    """
    Mutates ohlcv in-place, adding per-bar color overrides for engulfing candles.
    Bullish engulfing → white body/border/wick.
    Bearish engulfing → black body, red border and wick.
    """
    for i in range(1, len(ohlcv)):
        prev, curr = ohlcv[i - 1], ohlcv[i]
        po, pc = prev['open'], prev['close']
        co, cc = curr['open'], curr['close']

        prev_bear = pc < po
        prev_bull = pc > po
        curr_bull  = cc > co
        curr_bear  = cc < co

        if prev_bear and curr_bull and co <= pc and cc >= po:
            # Bullish engulfing — white candle
            curr['color']       = '#ffffff'
            curr['borderColor'] = '#ffffff'
            curr['wickColor']   = '#ffffff'
        elif prev_bull and curr_bear and co >= pc and cc <= po:
            # Bearish engulfing — black body, red border
            curr['color']       = '#000000'
            curr['borderColor'] = '#ef5350'
            curr['wickColor']   = '#ef5350'


def _load_trade_markers(ohlcv: list) -> list:
    """
    Read trade_data.csv, snap each trade timestamp to the nearest OHLCV bar,
    and return sorted LightweightCharts marker dicts.

    Entry markers : B (buy) / S (short sell)
    Exit markers  : T (target hit, circle) / SL (stop hit, circle)
    """
    import bisect
    csv_p = Path(__file__).resolve().parent / "data" / "trade_data.csv"
    if not csv_p.exists() or not ohlcv:
        return []
    try:
        df = pd.read_csv(csv_p)
    except Exception:
        return []
    df = df[df['type'].isin(['BUY', 'SHORT', 'SELL', 'COVER'])].copy()
    if df.empty:
        return []
    df['ts'] = pd.to_datetime(df['time']).apply(lambda x: int(x.timestamp()))

    bar_times = sorted(c['time'] for c in ohlcv)
    t_min, t_max = bar_times[0], bar_times[-1]

    def snap(ts):
        if ts < t_min or ts > t_max:
            return None   # outside visible window — skip
        idx = bisect.bisect_right(bar_times, ts) - 1
        return bar_times[max(idx, 0)]

    markers = []
    for _, row in df.iterrows():
        bt     = snap(int(row['ts']))
        if bt is None:
            continue
        t      = row['type']
        raw    = row.get('exit_reason', float('nan'))
        reason = str(raw) if pd.notna(raw) else ''

        if t == 'BUY':
            markers.append({"time": bt, "position": "belowBar",
                            "color": "#26a69a", "shape": "arrowUp",
                            "text": "B", "size": 1})
        elif t == 'SHORT':
            markers.append({"time": bt, "position": "aboveBar",
                            "color": "#ef5350", "shape": "arrowDown",
                            "text": "S", "size": 1})
        elif t == 'SELL':                       # long trade exit
            if reason == 'TP':
                markers.append({"time": bt, "position": "aboveBar",
                                "color": "#26a69a", "shape": "circle",
                                "text": "T", "size": 1})
            else:
                markers.append({"time": bt, "position": "belowBar",
                                "color": "#ef5350", "shape": "circle",
                                "text": "SL", "size": 1})
        elif t == 'COVER':                      # short trade exit
            if reason == 'TP':
                markers.append({"time": bt, "position": "belowBar",
                                "color": "#26a69a", "shape": "circle",
                                "text": "T", "size": 1})
            else:
                markers.append({"time": bt, "position": "aboveBar",
                                "color": "#ef5350", "shape": "circle",
                                "text": "SL", "size": 1})

    # ── SKIP markers from skip_data.csv ──────────────────────────────
    skip_p = Path(__file__).resolve().parent / "data" / "skip_data.csv"
    if skip_p.exists():
        try:
            sk = pd.read_csv(skip_p)
            sk['ts'] = pd.to_datetime(sk['time']).apply(lambda x: int(x.timestamp()))
            for _, row in sk.iterrows():
                bt = snap(int(row['ts']))
                if bt is not None:
                    markers.append({"time": bt, "position": "aboveBar",
                                    "color": "#94a3b8", "shape": "square",
                                    "text": "SKIP", "size": 1})
        except Exception:
            pass

    markers.sort(key=lambda m: m['time'])
    return markers


def _load_ohlcv_with_supertrend(n_bars: int = 2000):
    """
    Reads sample_data.csv, computes Supertrend on the FULL dataset for accuracy,
    then returns only the last n_bars as the embedded HTML snapshot.
    n_bars=0 means return everything (used by chart_server.py).
    """
    csv_p = Path(__file__).resolve().parent / "data" / "sample_data.csv"
    if not csv_p.exists():
        print("warning: sample_data.csv not found — candle chart will be empty")
        return [], []

    df = pd.read_csv(csv_p, usecols=['time', 'open', 'high', 'low', 'close'])
    df['time'] = pd.to_datetime(df['time'])
    df = df.sort_values('time').reset_index(drop=True)

    if df.empty:
        return [], []

    # Supertrend computed on full history — accurate even for the trimmed window
    st_all = _compute_supertrend(df)

    # Trim to the last n_bars for the HTML snapshot
    if n_bars > 0 and len(df) > n_bars:
        df = df.tail(n_bars).reset_index(drop=True)

    cutoff_unix = int(df['time'].iloc[0].timestamp())

    ohlcv = []
    for _, row in df.iterrows():
        ohlcv.append({
            "time":  int(row['time'].timestamp()),
            "open":  round(float(row['open']),  2),
            "high":  round(float(row['high']),  2),
            "low":   round(float(row['low']),   2),
            "close": round(float(row['close']), 2),
        })

    _mark_engulfing(ohlcv)

    st_trimmed = [p for p in st_all if p["time"] >= cutoff_unix]
    return ohlcv, st_trimmed


def calc_streaks(profits):
    max_win = max_loss = cur = 0
    cur_type = None
    for p in profits:
        if p > 0:
            cur = (cur + 1) if cur_type == 'win' else 1
            cur_type = 'win'
            max_win = max(max_win, cur)
        elif p < 0:
            cur = (cur + 1) if cur_type == 'loss' else 1
            cur_type = 'loss'
            max_loss = max(max_loss, cur)
    return max_win, max_loss, cur, cur_type or 'none'


def _find_streak_trades(done: pd.DataFrame, profits: list) -> tuple:
    """Return (win_streak_trades, loss_streak_trades) for the longest streaks."""
    def _best(is_win):
        best_len = best_start = 0
        cur_len = cur_start = 0
        for idx, p in enumerate(profits):
            hit = p > 0 if is_win else p < 0
            if hit:
                if cur_len == 0:
                    cur_start = idx
                cur_len += 1
                if cur_len > best_len:
                    best_len, best_start = cur_len, cur_start
            else:
                cur_len = 0
        return best_start, best_len

    def _extract(start, length):
        rows = []
        for _, r in done.iloc[start:start + length].iterrows():
            rows.append({
                "time":   str(r['time'])[:16],
                "symbol": str(r.get('symbol', '')),
                "dir":    "SHORT" if r['type'] == 'COVER' else "LONG",
                "entry":  round(float(r.get('entry_price', 0)), 2),
                "exit":   round(float(r.get('exit_price',  0)), 2),
                "profit": round(float(r['profit']), 2),
                "cap":    round(float(r['capital']), 2),
            })
        return rows

    ws, wl = _best(is_win=True)
    ls, ll = _best(is_win=False)
    return _extract(ws, wl), _extract(ls, ll)


def build_dashboard():
    base   = Path(__file__).resolve().parent
    csv_p  = base / "data" / "trade_data.csv"
    html_p = base / "data" / "dashboard.html"

    if not csv_p.exists() or csv_p.stat().st_size == 0:
        print("[dashboard] trade_data.csv not found or empty — run a backtest first")
        return

    df = pd.read_csv(csv_p)
    if df.empty:
        print("[dashboard] trade_data.csv is empty")
        return

    done = df[df['type'].isin(['SELL', 'COVER'])].copy()
    done['profit']  = pd.to_numeric(done['profit'],  errors='coerce')
    done['capital'] = pd.to_numeric(done['capital'], errors='coerce')
    done = done.dropna(subset=['profit', 'capital'])
    done['time'] = pd.to_datetime(done['time'], errors='coerce')
    done = done.sort_values('time').reset_index(drop=True)

    if done.empty:
        print("[dashboard] No completed trades to display")
        return

    profits = done['profit'].tolist()
    caps    = done['capital'].tolist()
    times   = done['time'].tolist()
    wins   = done[done['profit'] > 0]
    losses = done[done['profit'] < 0]
    nw, nl = len(wins), len(losses)
    n       = len(done)
    outcome_n = nw + nl

    n_long  = int((done['type'] == 'SELL').sum())    # closed LONG trades
    n_short = int((done['type'] == 'COVER').sum())   # closed SHORT trades

    start_cap    = caps[0] - profits[0]
    end_cap      = caps[-1]
    total_profit = round(end_cap - start_cap, 2)
    profit_pct   = round(total_profit / start_cap * 100, 2) if start_cap else 0

    overall_win_rate = round(nw / outcome_n * 100, 1) if outcome_n else 0
    avg_win   = round(wins['profit'].mean(),   2) if nw else 0
    avg_loss  = round(losses['profit'].mean(), 2) if nl else 0

    gross_win  = wins['profit'].sum()        if nw else 0
    gross_loss = abs(losses['profit'].sum()) if nl else 0
    pf_val     = round(gross_win / gross_loss, 2) if gross_loss > 0 else 999
    pf_display = str(pf_val) if pf_val != 999 else "∞"

    eq     = done['capital']
    dd     = (eq - eq.cummax()) / eq.cummax() * 100
    max_dd = round(dd.min(), 2)

    max_ws, max_ls, cur_s, cur_t = calc_streaks(profits)
    win_streak_trades, loss_streak_trades = _find_streak_trades(done, profits)

    date_from = str(times[0])[:16]
    date_to   = str(times[-1])[:16]

    # ── Biggest single win / loss ─────────────────────────────────
    def _best_trade(grp):
        if grp.empty:
            return {"profit": 0, "time": "—", "symbol": "—", "strategy": "—", "dir": "—"}
        row = grp.loc[grp['profit'].abs().idxmax()]
        return {
            "profit":   round(float(row['profit']), 2),
            "time":     str(row['time'])[:16],
            "symbol":   str(row.get('symbol',   '—')),
            "strategy": str(row.get('strategy', '—')),
            "dir":      "SHORT" if row['type'] == 'COVER' else "LONG",
        }

    biggest_win  = _best_trade(wins)
    biggest_loss = _best_trade(losses)

    # ── Monthly breakdown ─────────────────────────────────────────────
    done['_month'] = pd.to_datetime(done['time'], errors='coerce').dt.to_period('M')
    monthly_rows = []
    for period, grp in done.groupby('_month'):
        mw = (grp['profit'] > 0).sum()
        ml = (grp['profit'] < 0).sum()
        mt = len(grp)
        mp = round(grp['profit'].sum(), 2)
        first        = grp.iloc[0]
        month_start  = float(first['capital']) - float(first['profit'])
        profit_pct   = round(mp / month_start * 100, 2) if month_start else 0
        month_win_rate = round(float(mw) / mt * 100, 1) if mt else 0
        monthly_rows.append({
            "month":      str(period),
            "trades":     mt,
            "wins":       int(mw),
            "losses":     int(ml),
            "profit":     mp,
            "profit_pct": profit_pct,
            "win_rate":   month_win_rate,
        })

    # ── Weekly breakdown ──────────────────────────────────────────────
    done['_week'] = pd.to_datetime(done['time'], errors='coerce').dt.to_period('W')
    weekly_rows = []
    for period, grp in done.groupby('_week'):
        ww = (grp['profit'] > 0).sum()
        wl = (grp['profit'] < 0).sum()
        wt = len(grp)
        wp = round(grp['profit'].sum(), 2)
        first       = grp.iloc[0]
        week_start  = float(first['capital']) - float(first['profit'])
        wpct        = round(wp / week_start * 100, 2) if week_start else 0
        wwr         = round(float(ww) / wt * 100, 1) if wt else 0
        weekly_rows.append({
            "week":       str(period),
            "trades":     wt,
            "wins":       int(ww),
            "losses":     int(wl),
            "profit":     wp,
            "profit_pct": wpct,
            "win_rate":   wwr,
        })

    # ── Per-symbol breakdown ─────────────────────────────────────
    symbols_data = []
    if 'symbol' in done.columns:
        for sym, grp in done.groupby('symbol', sort=True):
            sw  = int((grp['profit'] > 0).sum())
            sl_ = int((grp['profit'] < 0).sum())
            st_ = len(grp)
            sp  = round(grp['profit'].sum(), 2)
            swr = round(sw / st_ * 100, 1) if st_ else 0
            sym_profs = grp['profit'].tolist()
            sym_caps  = grp['capital'].tolist()
            sym_start = sym_caps[0] - sym_profs[0] if sym_caps else 0
            sym_pct   = round(sp / sym_start * 100, 2) if sym_start else 0
            symbols_data.append({
                "symbol":     sym,
                "trades":     int(st_),
                "wins":       sw,
                "losses":     sl_,
                "profit":     sp,
                "win_rate":   swr,
                "profit_pct": sym_pct,
            })

    # ── Per-trade rows ────────────────────────────────────────────────
    rows = []
    for idx, (_, r) in enumerate(done.iterrows()):
        sl_val  = r.get('sl')
        tp_val  = r.get('tp')
        sl_str  = f"{float(sl_val):.2f}" if pd.notna(sl_val) else "—"
        tp_str  = f"{float(tp_val):.2f}" if pd.notna(tp_val) else "—"
        label   = str(r.get('exit_label', r.get('exit_reason', '')))
        lot_val = r.get('lot_size')
        lot_str = f"{float(lot_val):.4f}" if pd.notna(lot_val) else "—"
        rows.append({
            "num":      idx + 1,
            "time":     str(r['time'])[:16],
            "symbol":   str(r.get('symbol',   '')),
            "strategy": str(r.get('strategy', '')),
            "dir":      "SHORT" if r['type'] == 'COVER' else "LONG",
            "entry":  round(float(r.get('entry_price', 0)), 2),
            "sl":     sl_str,
            "target": tp_str,
            "exit":   round(float(r.get('exit_price',  0)), 2),
            "label":  label,
            "lot":    lot_str,
            "profit": round(float(r['profit']), 2),
            "cap":    round(float(r['capital']), 2),
        })

    eq_labels = ["Start"] + [str(t)[:16] for t in times]
    eq_data   = [round(start_cap, 2)] + [round(c, 2) for c in caps]

    chartjs            = _get_chartjs()
    lwc                = _get_lightweightcharts()
    ohlcv_raw, st_data = _load_ohlcv_with_supertrend()
    markers            = _load_trade_markers(ohlcv_raw)

    strategies_str = ", ".join(sorted(done['strategy'].dropna().unique())) if 'strategy' in done.columns else ""
    symbols_str    = ", ".join(sorted(done['symbol'].dropna().unique()))   if 'symbol'   in done.columns else ""

    html = _render(
        weekly_rows=weekly_rows,
        m={
            "start_cap":       round(start_cap, 2),
            "end_cap":         round(end_cap, 2),
            "total_profit":    total_profit,
            "profit_pct":      profit_pct,
            "n":               n,
            "nw":              nw,
            "nl":              nl,
            "n_long":          n_long,
            "n_short":         n_short,
            "win_rate":        overall_win_rate,
            "avg_win":         avg_win,
            "avg_loss":        avg_loss,
            "pf_display":      pf_display,
            "pf_val":          pf_val,
            "max_dd":          max_dd,
            "max_ws":          max_ws,
            "max_ls":          max_ls,
            "cur_s":           cur_s,
            "cur_t":           cur_t,
            "strategies_str":  strategies_str,
            "symbols_str":     symbols_str,
            "timeframe":       getattr(Config, "TIMEFRAME", ""),
            "biggest_win":     biggest_win,
            "biggest_loss":    biggest_loss,
        },
        rows=rows,
        monthly_rows=monthly_rows,
        eq_labels=eq_labels,
        eq_data=eq_data,
        profits=profits,
        date_from=date_from,
        date_to=date_to,
        chartjs=chartjs,
        lwc=lwc,
        ohlcv_json=json.dumps(ohlcv_raw),
        st_json=json.dumps(st_data),
        markers_json=json.dumps(markers),
        symbols_data=symbols_data,
        win_streak_trades=win_streak_trades,
        loss_streak_trades=loss_streak_trades,
    )

    html_p.write_text(html, encoding='utf-8')
    print(f"[dashboard] written -> {html_p}")

    # Try chart server first (reuses existing browser tab via JS auto-refresh).
    # Fall back to opening the file directly.
    server_url = "http://localhost:8765/"
    opened = False
    try:
        with socket.create_connection(("127.0.0.1", 8765), timeout=0.5):
            webbrowser.open(server_url, new=2)
            print(f"[dashboard] opened  -> {server_url}")
            opened = True
    except OSError:
        pass

    if not opened:
        try:
            import os, sys
            if sys.platform == "win32":
                os.startfile(str(html_p))
            else:
                webbrowser.open(html_p.as_uri(), new=2)
            print(f"[dashboard] opened  -> {html_p}")
        except Exception as e:
            print(f"[dashboard] could not open browser: {e}")


def _render(m, rows, monthly_rows, weekly_rows, eq_labels, eq_data, profits,
            date_from, date_to, chartjs="", lwc="", ohlcv_json="[]", st_json="[]", markers_json="[]",
            symbols_data=None, win_streak_trades=None, loss_streak_trades=None):

    profit_sign  = "+" if m['total_profit'] >= 0 else "-"
    profit_abs   = f"${abs(m['total_profit']):,.2f}"
    profit_color = "clr-green" if m['total_profit'] >= 0 else "clr-red"
    pct_arrow    = "&#9650;" if m['profit_pct'] >= 0 else "&#9660;"
    pct_abs      = abs(m['profit_pct'])

    end_cap_str   = f"${m['end_cap']:,.2f}"
    start_cap_str = f"${m['start_cap']:,.2f}"

    wr_color  = "clr-green" if m['win_rate'] >= 50 else "clr-red"
    pf_color  = "clr-green" if m['pf_val'] >= 1   else "clr-red"
    avg_w_str = f"+${m['avg_win']:.2f}"
    avg_l_str = f"${m['avg_loss']:.2f}"

    streak_hex   = "#22c55e" if m['cur_t'] == 'win' else "#ef4444"
    streak_label = f"{m['cur_s']} {m['cur_t'].upper()}"

    avg_all_val   = round(sum(profits) / len(profits), 2) if profits else 0
    avg_all_str   = (f"+${avg_all_val:.2f}" if avg_all_val >= 0 else f"-${abs(avg_all_val):.2f}")
    avg_all_color = "clr-green" if avg_all_val >= 0 else "clr-red"

    rows_j          = json.dumps(rows)
    monthly_rows_j  = json.dumps(monthly_rows)
    weekly_rows_j   = json.dumps(weekly_rows)
    eq_labels_j     = json.dumps(eq_labels)
    eq_data_j       = json.dumps(eq_data)
    profits_j       = json.dumps(profits)
    bar_clrs_j      = json.dumps(["#22c55e" if p > 0 else "#ef4444" for p in profits])
    symbols_data_j      = json.dumps(symbols_data or [])
    win_streak_trades_j  = json.dumps(win_streak_trades  or [])
    loss_streak_trades_j = json.dumps(loss_streak_trades or [])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Trading Dashboard</title>
<script>{chartjs}</script>
<script>{lwc}</script>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#090b0f;color:#c9d1d9;font-family:'Segoe UI',system-ui,-apple-system,sans-serif;min-height:100vh}}

/* ── Bloomberg-style terminal bar ── */
.terminal-bar{{background:#0d1117;border-bottom:2px solid #d29922;padding:0 20px;display:flex;align-items:center;height:34px;position:sticky;top:0;z-index:100;gap:0}}
.tb-brand{{font-size:11px;font-weight:900;letter-spacing:.18em;color:#d29922;margin-right:20px;font-family:'Courier New',monospace;white-space:nowrap}}
.tb-sep{{width:1px;height:14px;background:#1c2332;margin:0 14px;flex-shrink:0}}
.tb-pill{{font-size:10px;font-weight:600;letter-spacing:.08em;color:#6e7681;background:#161b22;border:1px solid #1c2332;padding:2px 8px;border-radius:2px;margin-right:5px;font-family:'Courier New',monospace;white-space:nowrap}}
.tb-pill.active{{color:#d29922;border-color:rgba(210,153,34,.4)}}
.tb-right{{margin-left:auto;display:flex;align-items:center;gap:10px}}
.tb-time{{font-size:10px;color:#3d4451;font-family:'Courier New',monospace}}
.tb-dot{{width:6px;height:6px;border-radius:50%;background:#3fb950;box-shadow:0 0 4px rgba(63,185,80,.5)}}

/* ── Main layout ── */
.page-wrap{{padding:18px 22px 40px;max-width:1900px;margin:0 auto}}
.page-title{{font-size:14px;font-weight:700;color:#e6edf3;letter-spacing:.02em}}
.page-sub{{font-size:10px;color:#3d4451;margin-top:3px;margin-bottom:18px;font-family:'Courier New',monospace}}
.clr-green{{color:#3fb950}}.clr-red{{color:#f85149}}.clr-blue{{color:#2f81f7}}

/* ── Tabs ── */
.tab-nav{{display:flex;margin-bottom:18px;border-bottom:1px solid #1c2332}}
.tab-btn{{padding:7px 18px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.12em;border:none;border-bottom:2px solid transparent;background:transparent;color:#3d4451;cursor:pointer;margin-bottom:-1px;transition:color .15s,border-color .15s}}
.tab-btn:hover{{color:#6e7681}}
.tab-btn.active{{color:#e6edf3;border-bottom-color:#d29922}}
.tab-pane{{display:none}}.tab-pane.active{{display:block}}

/* ── Section header ── */
.section-hdr{{display:flex;align-items:baseline;gap:12px;margin-bottom:10px}}
.section-lbl{{font-size:9px;text-transform:uppercase;letter-spacing:.12em;color:#3d4451;font-weight:700}}
.section-sub{{font-size:10px;color:#3d4451;font-family:'Courier New',monospace}}

/* ── Metric cards ── */
.cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(165px,1fr));gap:8px;margin-bottom:18px}}
.card{{background:#0d1117;border:1px solid #1c2332;border-left:3px solid #2f81f7;border-radius:2px;padding:14px 16px;transition:border-left-color .15s}}
.card:hover{{border-left-color:#d29922}}
.card-lbl{{font-size:9px;text-transform:uppercase;letter-spacing:.12em;color:#3d4451;margin-bottom:9px;font-weight:700}}
.card-val{{font-size:22px;font-weight:700;line-height:1;font-family:'Courier New',Courier,monospace;letter-spacing:-.01em}}
.card-sub{{font-size:10px;color:#3d4451;margin-top:6px;font-family:'Courier New',monospace}}
.bw-meta{{font-size:9px;line-height:1.5;word-break:break-word}}

/* ── Charts ── */
.charts{{display:grid;grid-template-columns:2.2fr 1fr;gap:8px;margin-bottom:18px}}
.chart-box{{background:#0d1117;border:1px solid #1c2332;border-radius:2px;padding:16px}}
.chart-lbl{{font-size:9px;text-transform:uppercase;letter-spacing:.12em;color:#3d4451;margin-bottom:12px;font-weight:700}}

/* ── Table boxes ── */
.tbl-box{{background:#0d1117;border:1px solid #1c2332;border-radius:2px;padding:14px 18px;overflow-x:auto;margin-bottom:18px}}
.tbl-lbl{{font-size:9px;text-transform:uppercase;letter-spacing:.12em;color:#3d4451;margin-bottom:10px;font-weight:700}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
thead th{{padding:6px 11px;text-align:left;font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:#3d4451;background:#090b0f;border-bottom:1px solid #1c2332;font-weight:700}}
tbody td{{padding:6px 11px;border-bottom:1px solid #0d1117;color:#6e7681;font-family:'Courier New',monospace;font-size:12px}}
tbody tr:nth-child(even) td{{background:rgba(255,255,255,.013)}}
tbody tr:hover td{{background:#161b22}}
tbody td.hl{{color:#c9d1d9}}
tbody td.muted{{color:#3d4451;font-size:11px;font-family:'Segoe UI',system-ui,sans-serif}}

/* ── Badges ── */
.badge{{display:inline-block;padding:1px 7px;border-radius:2px;font-size:10px;font-weight:700;letter-spacing:.06em}}
.b-long{{background:rgba(63,185,80,.1);color:#3fb950;border:1px solid rgba(63,185,80,.28)}}
.b-short{{background:rgba(248,81,73,.1);color:#f85149;border:1px solid rgba(248,81,73,.28)}}
.b-tp{{background:rgba(47,129,247,.1);color:#2f81f7;border:1px solid rgba(47,129,247,.28)}}
.b-sl{{background:rgba(248,81,73,.1);color:#f85149;border:1px solid rgba(248,81,73,.28)}}
.b-st{{background:rgba(188,140,255,.1);color:#bc8cff;border:1px solid rgba(188,140,255,.28)}}
.b-rr{{background:rgba(63,185,80,.1);color:#3fb950;border:1px solid rgba(63,185,80,.28)}}
.b-rev{{background:rgba(210,153,34,.1);color:#d29922;border:1px solid rgba(210,153,34,.28)}}
.win-txt{{color:#3fb950;font-weight:700}}.los-txt{{color:#f85149;font-weight:700}}

/* ── Filters ── */
.tbl-hdr{{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;flex-wrap:wrap;gap:6px}}
.filter-bar{{display:flex;align-items:center;gap:5px;flex-wrap:wrap}}
.month-select{{background:#090b0f;color:#6e7681;border:1px solid #1c2332;border-radius:2px;padding:3px 8px;font-size:10px;cursor:pointer;outline:none;font-family:'Courier New',monospace}}
.month-select:hover{{border-color:#d29922;color:#c9d1d9}}
.month-select option{{background:#090b0f}}

/* ── Monthly calendar ── */
.month-calendar{{display:grid;grid-template-columns:repeat(auto-fill,minmax(138px,1fr));gap:7px;padding:2px 0}}
.month-cell{{border-radius:2px;padding:12px 14px;border:1px solid;transition:transform .1s;cursor:default}}
.month-cell:hover{{transform:translateY(-1px)}}
.month-cell.mc-profit{{background:rgba(63,185,80,.07);border-color:rgba(63,185,80,.22)}}
.month-cell.mc-loss{{background:rgba(248,81,73,.07);border-color:rgba(248,81,73,.22)}}
.mc-name{{font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:#3d4451;margin-bottom:7px;font-weight:700}}
.mc-pnl{{font-size:18px;font-weight:700;line-height:1;margin-bottom:7px;font-family:'Courier New',monospace}}
.mc-pnl.mc-pos{{color:#3fb950}}.mc-pnl.mc-neg{{color:#f85149}}
.mc-divider{{border:none;border-top:1px solid #1c2332;margin:5px 0}}
.mc-row{{display:flex;justify-content:space-between;align-items:center;font-size:10px;color:#3d4451;margin-top:3px}}
.mc-lbl{{font-size:9px;text-transform:uppercase;letter-spacing:.06em}}
.mc-val{{font-weight:700;font-size:11px;font-family:'Courier New',monospace}}
.mc-val.c-trades{{color:#2f81f7}}.mc-val.c-wins{{color:#3fb950}}.mc-val.c-losses{{color:#f85149}}

/* ── Symbol section ── */
.sym-section-hdr{{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}}
.sym-section-lbl{{font-size:9px;text-transform:uppercase;letter-spacing:.12em;color:#3d4451;font-weight:700}}
.sym-cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(195px,1fr));gap:8px;margin-bottom:18px}}
.sym-card{{background:#0d1117;border:1px solid #1c2332;border-left:3px solid;border-radius:2px;padding:14px 16px;position:relative;overflow:hidden}}
.sym-card::before{{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,var(--sc),transparent);opacity:.3}}
.sym-name{{font-size:11px;font-weight:900;letter-spacing:.12em;color:var(--sc);margin-bottom:9px;font-family:'Courier New',monospace}}
.sym-pnl{{font-size:20px;font-weight:700;line-height:1;margin-bottom:3px;font-family:'Courier New',monospace}}
.sym-pct{{font-size:10px;color:#3d4451;margin-bottom:11px;font-family:'Courier New',monospace}}
.sym-stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;padding-top:9px;border-top:1px solid #1c2332}}
.sym-stat-item{{text-align:center}}
.sym-stat-val{{font-size:13px;font-weight:700;line-height:1;margin-bottom:2px;font-family:'Courier New',monospace}}
.sym-stat-lbl{{font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:#3d4451}}
.sym-badge{{display:inline-block;padding:1px 7px;border-radius:2px;font-size:10px;font-weight:800;letter-spacing:.08em;border:1px solid;font-family:'Courier New',monospace}}

/* ── Symbol table ── */
.sym-table-wrap{{background:#0d1117;border:1px solid #1c2332;border-radius:2px;padding:14px 18px;margin-bottom:18px;overflow-x:auto}}
.sym-table-lbl{{font-size:9px;text-transform:uppercase;letter-spacing:.12em;color:#3d4451;margin-bottom:10px;font-weight:700}}
.sym-tbl{{width:100%;border-collapse:collapse;font-size:12px}}
.sym-tbl thead th{{padding:6px 11px;text-align:left;font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:#3d4451;background:#090b0f;border-bottom:1px solid #1c2332;font-weight:700}}
.sym-tbl tbody td{{padding:6px 11px;border-bottom:1px solid #0d1117;color:#6e7681;font-family:'Courier New',monospace}}
.sym-tbl tbody tr:hover td{{background:#161b22}}
.sym-tbl tfoot td{{padding:7px 11px;border-top:1px solid #1c2332;font-weight:700;font-size:12px;color:#c9d1d9;background:#090b0f;font-family:'Courier New',monospace}}

/* ── Streak modal ── */
.modal-overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.82);z-index:999;align-items:center;justify-content:center}}
.modal-overlay.open{{display:flex}}
.modal-box{{background:#0d1117;border:1px solid #1c2332;border-top:2px solid #d29922;border-radius:2px;padding:22px 26px;width:min(820px,92vw);max-height:82vh;overflow-y:auto;position:relative}}
.modal-title{{font-size:13px;font-weight:700;color:#e6edf3;margin-bottom:3px;font-family:'Courier New',monospace}}
.modal-sub{{font-size:10px;color:#3d4451;margin-bottom:16px}}
.modal-close{{position:absolute;top:14px;right:16px;background:none;border:none;color:#3d4451;font-size:18px;cursor:pointer;line-height:1}}
.modal-close:hover{{color:#c9d1d9}}
.modal-tbl{{width:100%;border-collapse:collapse;font-size:12px}}
.modal-tbl thead th{{padding:6px 11px;text-align:left;font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:#3d4451;background:#090b0f;border-bottom:1px solid #1c2332}}
.modal-tbl tbody td{{padding:6px 11px;border-bottom:1px solid #0d1117;color:#6e7681;font-family:'Courier New',monospace}}
.modal-tbl tbody tr:hover td{{background:#161b22}}
.streak-card{{cursor:pointer}}
.streak-card:hover{{border-left-color:#d29922 !important}}

/* ── Candle chart ── */
.candle-wrap{{background:#0d1117;border:1px solid #1c2332;border-radius:2px;padding:16px;margin-bottom:18px}}
.candle-toolbar{{display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap}}
.candle-title{{font-size:9px;text-transform:uppercase;letter-spacing:.12em;color:#3d4451;flex:1;display:flex;align-items:center;gap:8px;font-weight:700}}
.status-dot{{width:6px;height:6px;border-radius:50%;background:#1c2332;display:inline-block;flex-shrink:0;transition:background .4s}}
.status-dot.live{{background:#3fb950;box-shadow:0 0 5px rgba(63,185,80,.5)}}
.btn-toggle{{padding:4px 12px;font-size:10px;font-weight:700;border-radius:2px;border:1px solid #1c2332;background:#090b0f;color:#3d4451;cursor:pointer;letter-spacing:.06em;transition:all .15s}}
.btn-toggle.on{{background:rgba(47,129,247,.1);border-color:#2f81f7;color:#2f81f7}}
#candleChart{{width:100%;height:520px;overflow:hidden}}

/* ── Collapsible sections ── */
.section-block{{margin-bottom:18px}}
.section-toggle{{display:flex;align-items:center;justify-content:space-between;padding:8px 16px;background:#0d1117;border:1px solid #1c2332;border-radius:2px;cursor:pointer;user-select:none;transition:border-color .15s}}
.section-toggle:hover{{border-color:#d29922}}
.section-block-lbl{{font-size:9px;text-transform:uppercase;letter-spacing:.12em;color:#6e7681;font-weight:700}}
.section-block-meta{{font-size:10px;color:#3d4451;font-family:'Courier New',monospace}}
.toggle-icon{{font-size:11px;color:#3d4451;transition:transform .2s;flex-shrink:0}}
.section-block.collapsed .toggle-icon{{transform:rotate(-90deg)}}
.section-block.collapsed .section-body{{display:none}}
.section-body{{padding-top:10px}}

/* ── Responsive ── */
@media(max-width:900px){{
  .charts{{grid-template-columns:1fr}}
  .cards{{grid-template-columns:repeat(auto-fill,minmax(140px,1fr))}}
  .sym-cards{{grid-template-columns:repeat(auto-fill,minmax(155px,1fr))}}
}}
@media(max-width:600px){{
  .page-wrap{{padding:10px 12px 28px}}
  .cards{{grid-template-columns:repeat(2,1fr)}}
  .card-val{{font-size:18px}}
  .tb-pill{{display:none}}
  .tb-sep{{display:none}}
  #candleChart{{height:380px}}
}}
</style>
</head>
<body>

<div class="terminal-bar">
  <span class="tb-brand">ALGO · TERMINAL</span>
  <div class="tb-sep"></div>
  <span class="tb-pill active">{m['strategies_str'] or 'Strategy'}</span>
  <span class="tb-pill">{m['symbols_str'] or 'Symbol'}</span>
  <span class="tb-pill">{m['timeframe'] or 'Timeframe'}</span>
  <span class="tb-pill">{m['n']} trades</span>
  <span class="tb-pill">{date_from[:10]} → {date_to[:10]}</span>
  <div class="tb-right">
    <span class="tb-time" id="tbTime"></span>
    <div class="tb-dot" title="System active"></div>
  </div>
</div>

<div class="page-wrap">

<!-- ── Tab navigation ── -->
<div class="tab-nav">
  <button class="tab-btn active" onclick="switchTab('performance', this)">Performance</button>
  <button class="tab-btn"        onclick="switchTab('candles',     this)">Candles</button>
</div>

<!-- ══════════════════ PERFORMANCE PANE ══════════════════ -->
<div id="pane-performance" class="tab-pane active">

<!-- ── Metric Cards ── -->
<div class="section-block">
<div class="section-toggle" onclick="toggleSection(this)">
  <div><span class="section-block-lbl">Overall Performance</span>&nbsp;&nbsp;<span class="section-block-meta">All symbols &nbsp;·&nbsp; {m['timeframe'] or 'Timeframe'} &nbsp;·&nbsp; {m['n']} trades &nbsp;·&nbsp; {date_from[:10]} → {date_to[:10]}</span></div>
  <span class="toggle-icon">▼</span>
</div>
<div class="section-body">
<div class="cards">

  <div class="card">
    <div class="card-lbl">Total Return</div>
    <div class="card-val {profit_color}">{profit_sign}{profit_abs}</div>
    <div class="card-sub">{pct_arrow} {pct_abs}%</div>
  </div>

  <div class="card">
    <div class="card-lbl">Final Capital</div>
    <div class="card-val clr-blue">{end_cap_str}</div>
    <div class="card-sub">Started {start_cap_str}</div>
  </div>

  <div class="card">
    <div class="card-lbl">Win Rate</div>
    <div class="card-val {wr_color}">{m['win_rate']}%</div>
    <div class="card-sub">{m['nw']}W &nbsp;·&nbsp; {m['nl']}L &nbsp;·&nbsp; {m['n']} total</div>
  </div>

  <div class="card">
    <div class="card-lbl">Total Buy (Long)</div>
    <div class="card-val clr-green">{m['n_long']}</div>
    <div class="card-sub">{round(m['n_long'] / m['n'] * 100, 1) if m['n'] else 0}% of all trades</div>
  </div>

  <div class="card">
    <div class="card-lbl">Total Sell (Short)</div>
    <div class="card-val clr-red">{m['n_short']}</div>
    <div class="card-sub">{round(m['n_short'] / m['n'] * 100, 1) if m['n'] else 0}% of all trades</div>
  </div>

  <div class="card">
    <div class="card-lbl">Profit Factor</div>
    <div class="card-val {pf_color}">{m['pf_display']}</div>
    <div class="card-sub">gross win ÷ gross loss</div>
  </div>

  <div class="card">
    <div class="card-lbl">Avg Profit / Win</div>
    <div class="card-val clr-green">{avg_w_str}</div>
    <div class="card-sub">over {m['nw']} winning trades</div>
  </div>

  <div class="card">
    <div class="card-lbl">Avg Loss / Loss</div>
    <div class="card-val clr-red">{avg_l_str}</div>
    <div class="card-sub">over {m['nl']} losing trades</div>
  </div>

  <div class="card">
    <div class="card-lbl">Avg P&amp;L / Trade</div>
    <div class="card-val {avg_all_color}">{avg_all_str}</div>
    <div class="card-sub">expectancy per trade</div>
  </div>

  <div class="card">
    <div class="card-lbl">Biggest Win</div>
    <div class="card-val clr-green">+${m['biggest_win']['profit']:.2f}</div>
    <div class="card-sub bw-meta">{m['biggest_win']['time'][:10]} &nbsp;·&nbsp; {m['biggest_win']['symbol']} &nbsp;·&nbsp; {m['biggest_win']['strategy']}</div>
  </div>

  <div class="card">
    <div class="card-lbl">Biggest Loss</div>
    <div class="card-val clr-red">${m['biggest_loss']['profit']:.2f}</div>
    <div class="card-sub bw-meta">{m['biggest_loss']['time'][:10]} &nbsp;·&nbsp; {m['biggest_loss']['symbol']} &nbsp;·&nbsp; {m['biggest_loss']['strategy']}</div>
  </div>

  <div class="card">
    <div class="card-lbl">Max Drawdown</div>
    <div class="card-val clr-red">{m['max_dd']}%</div>
    <div class="card-sub">Peak-to-trough</div>
  </div>

  <div class="card streak-card" onclick="openStreakModal('win')" title="Click to view trades">
    <div class="card-lbl">Max Win Streak</div>
    <div class="card-val clr-green">{m['max_ws']}</div>
    <div class="card-sub">consecutive wins &nbsp;↗</div>
  </div>

  <div class="card streak-card" onclick="openStreakModal('loss')" title="Click to view trades">
    <div class="card-lbl">Max Loss Streak</div>
    <div class="card-val clr-red">{m['max_ls']}</div>
    <div class="card-sub">consecutive losses &nbsp;↗</div>
  </div>

  <div class="card">
    <div class="card-lbl">Current Streak</div>
    <div class="card-val" style="color:{streak_hex}">{streak_label}</div>
    <div class="card-sub">most recent run</div>
  </div>

</div>
</div><!-- /section-body metrics -->
</div><!-- /section-block metrics -->

<!-- ── Symbol Breakdown ── -->
<div class="section-block" id="sym-section" style="display:none">
<div class="section-toggle" onclick="toggleSection(this)">
  <span class="section-block-lbl">Symbol Breakdown</span>
  <span class="toggle-icon">▼</span>
</div>
<div class="section-body">
  <div id="sym-cards" class="sym-cards"></div>
</div>
</div>

<!-- ── Charts ── -->
<div class="section-block">
<div class="section-toggle" onclick="toggleSection(this)">
  <span class="section-block-lbl">Equity &amp; P&amp;L Charts</span>
  <span class="toggle-icon">▼</span>
</div>
<div class="section-body">
<div class="charts">
  <div class="chart-box">
    <div class="chart-lbl" style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
      <span>Equity Curve <span style="font-size:9px;color:#2d3a4a;font-weight:400;margin-left:6px">scroll = zoom &nbsp;·&nbsp; drag = pan</span></span>
      <button id="eqResetBtn" style="padding:2px 10px;font-size:9px;font-weight:700;border-radius:2px;border:1px solid #1c2332;background:#090b0f;color:#3d4451;cursor:pointer;letter-spacing:.08em;transition:color .15s,border-color .15s" onmouseover="this.style.color='#e6edf3';this.style.borderColor='#d29922'" onmouseout="this.style.color='#3d4451';this.style.borderColor='#1c2332'">RESET</button>
    </div>
    <canvas id="eqChart" height="110" style="cursor:grab"></canvas>
  </div>
  <div class="chart-box">
    <div class="chart-lbl">Trade P&amp;L</div>
    <canvas id="plChart" height="110"></canvas>
  </div>
</div>
</div>
</div><!-- /section-block charts -->

<!-- ── Monthly Calendar ── -->
<div class="section-block">
<div class="section-toggle" onclick="toggleSection(this)">
  <span class="section-block-lbl">Monthly Performance</span>
  <span class="toggle-icon">▼</span>
</div>
<div class="section-body">
<div class="tbl-box" style="margin-bottom:0">
  <div id="month-calendar" class="month-calendar"></div>
</div>
</div>
</div><!-- /section-block monthly -->

<!-- ── Weekly Calendar ── -->
<div class="section-block">
<div class="section-toggle" onclick="toggleSection(this)">
  <span class="section-block-lbl">Weekly Performance</span>
  <span class="toggle-icon">▼</span>
</div>
<div class="section-body">
<div class="tbl-box" style="margin-bottom:0">
  <div id="week-calendar" class="month-calendar"></div>
</div>
</div>
</div><!-- /section-block weekly -->

<!-- ── Trade Log ── -->
<div class="section-block">
<div class="section-toggle" onclick="toggleSection(this)">
  <span class="section-block-lbl">Trade Log</span>
  <span class="toggle-icon">▼</span>
</div>
<div class="section-body">
<div class="tbl-box" style="margin-bottom:0">
  <div class="tbl-hdr">
    <div class="tbl-lbl" style="margin-bottom:0">Trade Log</div>
    <div class="filter-bar">
      <select id="stratFilter" class="month-select">
        <option value="all">All Strategies</option>
      </select>
      <select id="symFilter" class="month-select">
        <option value="all">All Symbols</option>
      </select>
      <select id="monthFilter" class="month-select">
        <option value="all">All Months</option>
      </select>
    </div>
  </div>
  <table>
    <thead>
      <tr>
        <th>#</th>
        <th>Exit Time</th>
        <th>Symbol</th>
        <th>Strategy</th>
        <th>Dir</th>
        <th>Entry</th>
        <th>Stop Loss</th>
        <th>Target</th>
        <th>Exit</th>
        <th>Closed By</th>
        <th>Lot Size</th>
        <th>P &amp; L</th>
        <th>Capital</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>
</div>
</div>
</div><!-- /section-block trade log -->

<!-- ── Symbol Summary Table ── -->
<div class="section-block" id="symTableWrap" style="display:none">
<div class="section-toggle" onclick="toggleSection(this)">
  <span class="section-block-lbl">Symbol Performance Summary</span>
  <span class="toggle-icon">▼</span>
</div>
<div class="section-body">
<div class="sym-table-wrap" style="margin-bottom:0">
  <table class="sym-tbl">
    <thead>
      <tr>
        <th>Symbol</th>
        <th>Trades</th>
        <th>Wins</th>
        <th>Losses</th>
        <th>Win Rate</th>
        <th>P &amp; L</th>
        <th>Return %</th>
      </tr>
    </thead>
    <tbody id="symTbody"></tbody>
    <tfoot id="symTfoot"></tfoot>
  </table>
</div>
</div>
</div><!-- /section-block symbol summary -->

</div><!-- /pane-performance -->

<!-- ══════════════════ STREAK MODAL ══════════════════ -->
<div class="modal-overlay" id="streakModal" onclick="closeStreakModal(event)">
  <div class="modal-box">
    <button class="modal-close" onclick="closeStreakModal(null)">✕</button>
    <div class="modal-title" id="modalTitle"></div>
    <div class="modal-sub"  id="modalSub"></div>
    <table class="modal-tbl">
      <thead>
        <tr>
          <th>#</th>
          <th>Exit Time</th>
          <th>Symbol</th>
          <th>Dir</th>
          <th>Entry</th>
          <th>Exit</th>
          <th>P &amp; L</th>
          <th>Capital</th>
        </tr>
      </thead>
      <tbody id="modalTbody"></tbody>
    </table>
  </div>
</div>

<!-- ══════════════════ CANDLES PANE ══════════════════ -->
<div id="pane-candles" class="tab-pane">
  <div class="candle-wrap">
    <div class="candle-toolbar">
      <div class="candle-title">
        {m['symbols_str'] or 'XAUUSD'} &nbsp;·&nbsp; Candlestick
        <span class="status-dot" id="liveDot" title="Green = live server connected"></span>
      </div>
      <button class="btn-toggle on" id="stToggle" onclick="toggleSupertrend()">Supertrend ON</button>
    </div>
    <div id="candleChart"></div>
  </div>
</div>

</div><!-- /page-wrap -->

<script>
var rows        = {rows_j};
var monthlyRows = {monthly_rows_j};
var weeklyRows  = {weekly_rows_j};
var eqLabels    = {eq_labels_j};
var eqData      = {eq_data_j};
var plData      = {profits_j};
var barClrs     = {bar_clrs_j};
var ohlcvData   = {ohlcv_json};
var stData      = {st_json};
var markersData = {markers_json};
var symbolsData      = {symbols_data_j};
var winStreakTrades  = {win_streak_trades_j};
var lossStreakTrades = {loss_streak_trades_j};

// ── Collapsible sections ──────────────────────────────────────
function toggleSection(el) {{
  el.closest('.section-block').classList.toggle('collapsed');
}}

// ── Tab switching ──────────────────────────────────────────────
function switchTab(name, btn) {{
  document.querySelectorAll('.tab-pane').forEach(function(p) {{
    p.classList.remove('active');
  }});
  document.querySelectorAll('.tab-btn').forEach(function(b) {{
    b.classList.remove('active');
  }});
  document.getElementById('pane-' + name).classList.add('active');
  btn.classList.add('active');
  location.hash = name;
  if (name === 'candles' && !candleChart) {{
    requestAnimationFrame(function() {{ initCandleChart(); }});
  }}
}}

// Restore tab from URL hash on page load
(function() {{
  if (location.hash === '#candles') {{
    var btns = document.querySelectorAll('.tab-btn');
    btns.forEach(function(b) {{ b.classList.remove('active'); }});
    btns[1].classList.add('active');
    document.querySelectorAll('.tab-pane').forEach(function(p) {{ p.classList.remove('active'); }});
    document.getElementById('pane-candles').classList.add('active');
    window.addEventListener('load', function() {{ initCandleChart(); }});
  }}
}})();

// ── Monthly Calendar ──────────────────────────
(function() {{
  var cal  = document.getElementById('month-calendar');
  var html = '';
  monthlyRows.forEach(function(r) {{
    var pos    = r.profit >= 0;
    var pnlStr = (pos ? '+$' : '-$') + Math.abs(r.profit).toFixed(2);
    var pctStr = (pos ? '&#9650; ' : '&#9660; ') + Math.abs(r.profit_pct).toFixed(2) + '%';
    var pctClr = pos ? '#3fb950' : '#f85149';
    var wrClr  = r.win_rate >= 50 ? '#3fb950' : '#f85149';
    html +=
      '<div class="month-cell ' + (pos ? 'mc-profit' : 'mc-loss') + '">' +
        '<div class="mc-name">' + r.month + '</div>' +
        '<div class="mc-pnl ' + (pos ? 'mc-pos' : 'mc-neg') + '">' + pnlStr + '</div>' +
        '<div style="font-size:11px;font-family:monospace;color:' + pctClr + ';margin-bottom:6px;font-weight:700">' + pctStr + '</div>' +
        '<hr class="mc-divider">' +
        '<div class="mc-row"><span class="mc-lbl">Trades</span>  <span class="mc-val c-trades">' + r.trades   + '</span></div>' +
        '<div class="mc-row"><span class="mc-lbl">Wins</span>    <span class="mc-val c-wins">'   + r.wins     + '</span></div>' +
        '<div class="mc-row"><span class="mc-lbl">Losses</span>  <span class="mc-val c-losses">' + r.losses   + '</span></div>' +
        '<div class="mc-row"><span class="mc-lbl">Win Rate</span><span class="mc-val" style="color:' + wrClr + '">' + r.win_rate + '%</span></div>' +
      '</div>';
  }});
  cal.innerHTML = html;
}})();

// ── Weekly Calendar ──────────────────────────────────────────
(function() {{
  var cal  = document.getElementById('week-calendar');
  var html = '';
  weeklyRows.forEach(function(r) {{
    var pos    = r.profit >= 0;
    var pnlStr = (pos ? '+$' : '-$') + Math.abs(r.profit).toFixed(2);
    var pctStr = (pos ? '&#9650; ' : '&#9660; ') + Math.abs(r.profit_pct).toFixed(2) + '%';
    var pctClr = pos ? '#3fb950' : '#f85149';
    var wrClr  = r.win_rate >= 50 ? '#3fb950' : '#f85149';
    html +=
      '<div class="month-cell ' + (pos ? 'mc-profit' : 'mc-loss') + '">' +
        '<div class="mc-name">' + r.week + '</div>' +
        '<div class="mc-pnl ' + (pos ? 'mc-pos' : 'mc-neg') + '">' + pnlStr + '</div>' +
        '<div style="font-size:11px;font-family:monospace;color:' + pctClr + ';margin-bottom:6px;font-weight:700">' + pctStr + '</div>' +
        '<hr class="mc-divider">' +
        '<div class="mc-row"><span class="mc-lbl">Trades</span>  <span class="mc-val c-trades">' + r.trades   + '</span></div>' +
        '<div class="mc-row"><span class="mc-lbl">Wins</span>    <span class="mc-val c-wins">'   + r.wins     + '</span></div>' +
        '<div class="mc-row"><span class="mc-lbl">Losses</span>  <span class="mc-val c-losses">' + r.losses   + '</span></div>' +
        '<div class="mc-row"><span class="mc-lbl">Win Rate</span><span class="mc-val" style="color:' + wrClr + '">' + r.win_rate + '%</span></div>' +
      '</div>';
  }});
  cal.innerHTML = html || '<div style="color:#3d4451;font-size:11px;padding:10px">No weekly data</div>';
}})();

// ── Symbol color palette (defined first — used by all blocks below) ──
var SYM_PALETTE = {{
  'XAUUSD': {{ color:'#f59e0b', bg:'rgba(245,158,11,.10)', border:'rgba(245,158,11,.30)' }},
  'EURUSD': {{ color:'#3b82f6', bg:'rgba(59,130,246,.10)',  border:'rgba(59,130,246,.30)'  }},
  'GBPUSD': {{ color:'#8b5cf6', bg:'rgba(139,92,246,.10)',  border:'rgba(139,92,246,.30)'  }},
  'USDJPY': {{ color:'#22c55e', bg:'rgba(34,197,94,.10)',   border:'rgba(34,197,94,.30)'   }},
  'USDCHF': {{ color:'#06b6d4', bg:'rgba(6,182,212,.10)',   border:'rgba(6,182,212,.30)'   }},
  'BTCUSD': {{ color:'#f97316', bg:'rgba(249,115,22,.10)',  border:'rgba(249,115,22,.30)'  }},
}};
var FALLBACK_COLORS = ['#a78bfa','#34d399','#fb7185','#38bdf8','#facc15','#e879f9'];
var _symColorCache = {{}};
var _symColorIdx = 0;
function symColor(sym) {{
  if (_symColorCache[sym]) return _symColorCache[sym];
  if (SYM_PALETTE[sym]) {{ _symColorCache[sym] = SYM_PALETTE[sym]; return _symColorCache[sym]; }}
  var c = FALLBACK_COLORS[_symColorIdx % FALLBACK_COLORS.length]; _symColorIdx++;
  _symColorCache[sym] = {{ color: c, bg: 'rgba(0,0,0,.1)', border: 'rgba(128,128,128,.3)' }};
  return _symColorCache[sym];
}}

// ── Symbol summary table ──────────────────────────────────────
(function() {{
  if (!symbolsData.length) return;
  document.getElementById('symTableWrap').style.display = 'block';
  var tbody = document.getElementById('symTbody');
  var tfoot = document.getElementById('symTfoot');

  var totTrades = 0, totWins = 0, totLosses = 0, totPnl = 0;

  var html = '';
  symbolsData.forEach(function(s) {{
    var pos    = s.profit >= 0;
    var pnlStr = (pos ? '+$' : '-$') + Math.abs(s.profit).toFixed(2);
    var pctStr = (pos ? '▲ ' : '▼ ') + Math.abs(s.profit_pct).toFixed(2) + '%';
    var wrClr  = s.win_rate >= 50 ? '#22c55e' : '#ef4444';
    var pnlClr = pos ? '#22c55e' : '#ef4444';
    var sc     = symColor(s.symbol);
    html +=
      '<tr>' +
        '<td><span class="sym-badge" style="color:' + sc.color + ';border-color:' + sc.border + ';background:' + sc.bg + '">' + s.symbol + '</span></td>' +
        '<td style="color:#60a5fa">' + s.trades + '</td>' +
        '<td style="color:#22c55e">' + s.wins   + '</td>' +
        '<td style="color:#ef4444">' + s.losses + '</td>' +
        '<td style="color:' + wrClr  + '">' + s.win_rate + '%</td>' +
        '<td style="color:' + pnlClr + '">' + pnlStr + '</td>' +
        '<td style="color:' + pnlClr + '">' + pctStr + '</td>' +
      '</tr>';
    totTrades += s.trades; totWins += s.wins; totLosses += s.losses; totPnl += s.profit;
  }});
  tbody.innerHTML = html;

  var totPos    = totPnl >= 0;
  var totPnlStr = (totPos ? '+$' : '-$') + Math.abs(totPnl).toFixed(2);
  var totWrStr  = totTrades ? (totWins / totTrades * 100).toFixed(1) + '%' : '—';
  var totClr    = totPos ? '#22c55e' : '#ef4444';
  tfoot.innerHTML =
    '<tr>' +
      '<td style="color:#94a3b8">TOTAL</td>' +
      '<td style="color:#60a5fa">' + totTrades + '</td>' +
      '<td style="color:#22c55e">' + totWins   + '</td>' +
      '<td style="color:#ef4444">' + totLosses + '</td>' +
      '<td style="color:' + (parseFloat(totWrStr) >= 50 ? '#22c55e' : '#ef4444') + '">' + totWrStr + '</td>' +
      '<td style="color:' + totClr + '">' + totPnlStr + '</td>' +
      '<td style="color:#475569">—</td>' +
    '</tr>';
}})();

// ── Streak modal ──────────────────────────────────────────────
function openStreakModal(type) {{
  var trades = type === 'win' ? winStreakTrades : lossStreakTrades;
  var title  = type === 'win' ? 'Max Win Streak' : 'Max Loss Streak';
  var clr    = type === 'win' ? '#22c55e' : '#ef4444';

  document.getElementById('modalTitle').textContent = title;
  document.getElementById('modalTitle').style.color = clr;
  document.getElementById('modalSub').textContent   = trades.length + ' consecutive ' + (type === 'win' ? 'winning' : 'losing') + ' trades';

  var html = '';
  trades.forEach(function(t, i) {{
    var win    = t.profit > 0;
    var pnlStr = (win ? '+$' : '-$') + Math.abs(t.profit).toFixed(2);
    var capStr = '$' + t.cap.toLocaleString();
    var sc     = symColor(t.symbol);
    var symBdg = t.symbol ? '<span class="sym-badge" style="color:' + sc.color + ';border-color:' + sc.border + ';background:' + sc.bg + '">' + t.symbol + '</span>' : '—';
    var dirBdg = t.dir === 'LONG'
      ? '<span class="badge b-long">LONG</span>'
      : '<span class="badge b-short">SHORT</span>';
    html +=
      '<tr>' +
        '<td class="muted">' + (i + 1) + '</td>' +
        '<td class="muted">' + t.time  + '</td>' +
        '<td>' + symBdg + '</td>' +
        '<td>' + dirBdg + '</td>' +
        '<td class="hl">' + t.entry + '</td>' +
        '<td class="hl">' + t.exit  + '</td>' +
        '<td class="' + (win ? 'win-txt' : 'los-txt') + '">' + pnlStr + '</td>' +
        '<td style="color:#60a5fa">' + capStr + '</td>' +
      '</tr>';
  }});
  document.getElementById('modalTbody').innerHTML = html || '<tr><td colspan="8" style="text-align:center;color:#334155;padding:20px">No trades</td></tr>';
  document.getElementById('streakModal').classList.add('open');
}}

function closeStreakModal(e) {{
  if (e && e.target !== document.getElementById('streakModal')) return;
  document.getElementById('streakModal').classList.remove('open');
}}

document.addEventListener('keydown', function(e) {{
  if (e.key === 'Escape') document.getElementById('streakModal').classList.remove('open');
}});

// ── Symbol summary cards ───────────────────────────────────────
(function() {{
  if (!symbolsData.length) return;
  document.getElementById('sym-section').style.display = 'block';
  var container = document.getElementById('sym-cards');
  var html = '';
  symbolsData.forEach(function(s) {{
    var sc     = symColor(s.symbol);
    var pos    = s.profit >= 0;
    var pnlStr = (pos ? '+$' : '-$') + Math.abs(s.profit).toFixed(2);
    var pctStr = (pos ? '▲' : '▼') + ' ' + Math.abs(s.profit_pct).toFixed(2) + '%';
    var pnlClr = pos ? '#22c55e' : '#ef4444';
    var wrClr  = s.win_rate >= 50 ? '#22c55e' : '#ef4444';
    html +=
      '<div class="sym-card" style="border-left-color:' + sc.color + ';--sc:' + sc.color + '">' +
        '<div class="sym-name">' + s.symbol + '</div>' +
        '<div class="sym-pnl" style="color:' + pnlClr + '">' + pnlStr + '</div>' +
        '<div class="sym-pct">' + pctStr + '</div>' +
        '<div class="sym-stats">' +
          '<div class="sym-stat-item">' +
            '<div class="sym-stat-val" style="color:#60a5fa">' + s.trades + '</div>' +
            '<div class="sym-stat-lbl">Trades</div>' +
          '</div>' +
          '<div class="sym-stat-item">' +
            '<div class="sym-stat-val" style="color:' + wrClr + '">' + s.win_rate + '%</div>' +
            '<div class="sym-stat-lbl">Win Rate</div>' +
          '</div>' +
          '<div class="sym-stat-item">' +
            '<div class="sym-stat-val"><span style="color:#22c55e">' + s.wins + '</span><span style="color:#475569"> / </span><span style="color:#ef4444">' + s.losses + '</span></div>' +
            '<div class="sym-stat-lbl">W / L</div>' +
          '</div>' +
        '</div>' +
      '</div>';
  }});
  container.innerHTML = html;
}})();

// ── Trade Table with month + symbol filter ─────────────────────
(function() {{
  var tbody    = document.getElementById('tbody');
  var monthSel = document.getElementById('monthFilter');
  var symSel   = document.getElementById('symFilter');
  var stratSel = document.getElementById('stratFilter');

  // Build month index
  var monthMap   = {{}};
  var monthOrder = [];
  rows.forEach(function(r) {{
    var m = r.time.substring(0, 7);
    if (!monthMap[m]) {{
      var d   = new Date(r.time);
      var lbl = d.toLocaleString('en-GB', {{month:'short', year:'numeric'}});
      monthMap[m] = {{label: lbl, rows: []}};
      monthOrder.push(m);
    }}
    monthMap[m].rows.push(r);
  }});

  // Populate month dropdown
  monthOrder.forEach(function(m) {{
    var opt = document.createElement('option');
    opt.value = m; opt.textContent = monthMap[m].label;
    monthSel.appendChild(opt);
  }});

  // Populate symbol dropdown
  var symSet = [];
  rows.forEach(function(r) {{
    if (r.symbol && symSet.indexOf(r.symbol) === -1) symSet.push(r.symbol);
  }});
  symSet.sort().forEach(function(s) {{
    var opt = document.createElement('option');
    opt.value = s; opt.textContent = s;
    symSel.appendChild(opt);
  }});

  // Populate strategy dropdown
  var stratSet = [];
  rows.forEach(function(r) {{
    if (r.strategy && stratSet.indexOf(r.strategy) === -1) stratSet.push(r.strategy);
  }});
  stratSet.sort().forEach(function(s) {{
    var opt = document.createElement('option');
    opt.value = s; opt.textContent = s;
    stratSel.appendChild(opt);
  }});

  function symBadge(sym) {{
    if (!sym) return '<span style="color:#475569">—</span>';
    var sc = symColor(sym);
    return '<span class="sym-badge" style="color:' + sc.color + ';border-color:' + sc.border + ';background:' + sc.bg + '">' + sym + '</span>';
  }}

  function rowHtml(r) {{
    var win        = r.profit > 0;
    var dirBadge   = r.dir === 'LONG'
      ? '<span class="badge b-long">LONG</span>'
      : '<span class="badge b-short">SHORT</span>';
    var lbl = r.label;
    var closedBadge;
    if      (lbl === 'ST')  closedBadge = '<span class="badge b-st">Supertrend</span>';
    else if (lbl === 'R:R') closedBadge = '<span class="badge b-rr">R:R</span>';
    else if (lbl === 'TP')  closedBadge = '<span class="badge b-tp">TP</span>';
    else if (lbl === 'REV') closedBadge = '<span class="badge b-rev">Rev Engulf</span>';
    else                    closedBadge = '<span class="badge b-sl">SL</span>';
    var pnlStr = (r.profit >= 0 ? '+$' : '-$') + Math.abs(r.profit).toFixed(2);
    var capStr = '$' + r.cap.toLocaleString();
    return '<tr>' +
      '<td class="muted">'   + r.num    + '</td>' +
      '<td class="muted">'   + r.time   + '</td>' +
      '<td>'                 + symBadge(r.symbol) + '</td>' +
      '<td class="muted" style="font-size:11px">' + (r.strategy || '—') + '</td>' +
      '<td>'                 + dirBadge + '</td>' +
      '<td class="hl">'      + r.entry  + '</td>' +
      '<td class="clr-red">' + r.sl     + '</td>' +
      '<td style="color:#a78bfa">' + r.target + '</td>' +
      '<td class="hl">'      + r.exit   + '</td>' +
      '<td>'                 + closedBadge + '</td>' +
      '<td class="muted">'   + (r.lot || '—') + '</td>' +
      '<td class="' + (win ? 'win-txt' : 'los-txt') + '">' + pnlStr + '</td>' +
      '<td style="color:#60a5fa">' + capStr + '</td>' +
      '</tr>';
  }}

  function render() {{
    var month = monthSel.value;
    var sym   = symSel.value;
    var strat = stratSel.value;
    var list  = month === 'all' ? rows : (monthMap[month] ? monthMap[month].rows : []);
    if (sym   !== 'all') list = list.filter(function(r) {{ return r.symbol   === sym;   }});
    if (strat !== 'all') list = list.filter(function(r) {{ return r.strategy === strat; }});
    tbody.innerHTML = list.map(rowHtml).join('');
  }}

  monthSel.addEventListener('change',  render);
  symSel.addEventListener('change',    render);
  stratSel.addEventListener('change',  render);
  render();
}})();

// ── Equity Curve with zoom + pan ──────────────
if (typeof Chart !== 'undefined') {{
  var _eqFull   = eqData.slice();
  var _eqLblFull = eqLabels.slice();
  var _eqS = 0, _eqE = _eqFull.length - 1;
  var _eqInst  = null;
  var _eqDragX = null, _eqDragS = null, _eqDragE = null;
  var _eqThrottle = 0;

  function _fmtEqLbl(lbl) {{
    if (lbl === 'Start') return 'Start';
    var d = new Date(lbl.replace(' ', 'T'));
    if (isNaN(d)) return lbl;
    return d.toLocaleDateString('en-GB', {{ day: '2-digit', month: 'short', year: '2-digit' }});
  }}

  function _drawEq() {{
    var lbls = _eqLblFull.slice(_eqS, _eqE + 1).map(_fmtEqLbl);
    var data = _eqFull.slice(_eqS, _eqE + 1);
    if (_eqInst) {{ _eqInst.destroy(); _eqInst = null; }}
    _eqInst = new Chart(document.getElementById('eqChart'), {{
      type: 'line',
      data: {{
        labels: lbls,
        datasets: [{{
          data: data,
          borderColor: '#3b82f6',
          backgroundColor: 'rgba(59,130,246,0.06)',
          borderWidth: 2,
          pointRadius: 0,
          fill: true,
          tension: 0.35
        }}]
      }},
      options: {{
        animation: false,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
          x: {{
            display: true,
            grid: {{ color: '#1a2540' }},
            ticks: {{
              color: '#334155',
              maxRotation: 40,
              autoSkip: true,
              maxTicksLimit: 8
            }}
          }},
          y: {{
            grid: {{ color: '#1a2540' }},
            ticks: {{
              color: '#334155',
              callback: function(v) {{ return '$' + v.toLocaleString(); }}
            }}
          }}
        }}
      }}
    }});
  }}

  _drawEq();

  var _eqCvs = document.getElementById('eqChart');

  _eqCvs.addEventListener('wheel', function(e) {{
    e.preventDefault();
    var total  = _eqFull.length - 1;
    var range  = _eqE - _eqS;
    var center = Math.round(_eqS + range / 2);
    var factor = e.deltaY < 0 ? 0.65 : 1.55;
    var nr = Math.max(4, Math.min(total, Math.round(range * factor)));
    var half = Math.round(nr / 2);
    _eqS = Math.max(0, center - half);
    _eqE = Math.min(total, _eqS + nr);
    if (_eqE === total) _eqS = Math.max(0, total - nr);
    _drawEq();
  }}, {{ passive: false }});

  _eqCvs.addEventListener('mousedown', function(e) {{
    _eqDragX = e.clientX; _eqDragS = _eqS; _eqDragE = _eqE;
    _eqCvs.style.cursor = 'grabbing';
  }});
  _eqCvs.addEventListener('mousemove', function(e) {{
    if (_eqDragX === null) return;
    var now = Date.now();
    if (now - _eqThrottle < 40) return;
    _eqThrottle = now;
    var total = _eqFull.length - 1;
    var range = _eqDragE - _eqDragS;
    var shift = -Math.round((e.clientX - _eqDragX) / (_eqCvs.clientWidth || 600) * range);
    _eqS = Math.max(0, Math.min(total - range, _eqDragS + shift));
    _eqE = Math.min(total, _eqS + range);
    _drawEq();
  }});
  function _eqStopDrag() {{ _eqDragX = null; _eqCvs.style.cursor = 'grab'; }}
  _eqCvs.addEventListener('mouseup',    _eqStopDrag);
  _eqCvs.addEventListener('mouseleave', _eqStopDrag);

  document.getElementById('eqResetBtn').addEventListener('click', function() {{
    _eqS = 0; _eqE = _eqFull.length - 1; _drawEq();
  }});

  // ── P&L Bars ─────────────────────────────────
  new Chart(document.getElementById('plChart'), {{
    type: 'bar',
    data: {{
      labels: plData.map(function(_, i) {{ return '#' + (i + 1); }}),
      datasets: [{{
        data: plData,
        backgroundColor: barClrs,
        borderRadius: 2
      }}]
    }},
    options: {{
      animation: false,
      plugins: {{ legend: {{ display: false }} }},
      scales: {{
        x: {{ display: false }},
        y: {{
          grid: {{ color: '#1a2540' }},
          ticks: {{
            color: '#334155',
            callback: function(v) {{ return '$' + v; }}
          }}
        }}
      }}
    }}
  }});
}} // end Chart guard

// ── Candlestick chart ─────────────────────────────────────────
var candleChart  = null;
var candleSeries = null;
var stSeries     = null;
var stVisible    = true;
var SERVER_URL   = 'http://localhost:8765';

function initCandleChart() {{
  if (candleChart) return;
  if (typeof LightweightCharts === 'undefined') {{
    document.getElementById('candleChart').innerHTML =
      '<div style="color:#3d4451;text-align:center;padding:40px;font-size:12px">LightweightCharts library not loaded</div>';
    return;
  }}
  var container = document.getElementById('candleChart');

  var TZ = 'Asia/Kolkata';
  function kolFmt(ts, opts) {{
    return new Intl.DateTimeFormat('en-GB', Object.assign({{ timeZone: TZ }}, opts)).format(new Date(ts * 1000));
  }}

  candleChart = LightweightCharts.createChart(container, {{
    width:  container.clientWidth || 900,
    height: 520,
    layout: {{
      background: {{ type: 'solid', color: '#07091a' }},
      textColor:  '#4a5568',
    }},
    grid: {{
      vertLines: {{ color: '#0d1526' }},
      horzLines: {{ color: '#0d1526' }},
    }},
    crosshair: {{
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: {{ color: '#3b82f6', labelBackgroundColor: '#1e3a8a' }},
      horzLine: {{ color: '#3b82f6', labelBackgroundColor: '#1e3a8a' }},
    }},
    localization: {{
      timeFormatter: function(ts) {{
        return kolFmt(ts, {{ day:'2-digit', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit', hour12:false }}) + ' IST';
      }},
    }},
    rightPriceScale: {{
      borderColor: '#1a2540',
      scaleMargins: {{ top: 0.08, bottom: 0.05 }},
    }},
    timeScale: {{
      borderColor:    '#1a2540',
      timeVisible:    true,
      secondsVisible: false,
      barSpacing:     8,
      tickMarkFormatter: function(ts, type) {{
        if (type === 0) return kolFmt(ts, {{ year:'numeric' }});
        if (type === 1) return kolFmt(ts, {{ month:'short' }});
        if (type === 2) return kolFmt(ts, {{ day:'2-digit', month:'short' }});
        return kolFmt(ts, {{ hour:'2-digit', minute:'2-digit', hour12:false }});
      }},
    }},
  }});

  new ResizeObserver(function() {{
    if (candleChart) {{
      candleChart.applyOptions({{ width: container.clientWidth }});
    }}
  }}).observe(container);

  candleSeries = candleChart.addCandlestickSeries({{
    upColor:         '#26a69a',
    downColor:       '#ef5350',
    borderUpColor:   '#26a69a',
    borderDownColor: '#ef5350',
    wickUpColor:     '#26a69a',
    wickDownColor:   '#ef5350',
  }});

  stSeries = candleChart.addLineSeries({{
    lineWidth:              2,
    priceLineVisible:       false,
    lastValueVisible:       false,
    crosshairMarkerVisible: false,
  }});

  if (ohlcvData.length) {{
    candleSeries.setData(ohlcvData);
    candleSeries.setMarkers(markersData);
    stSeries.setData(stData);
    candleChart.timeScale().fitContent();
  }}

  fetchAndUpdate();
  setInterval(fetchAndUpdate, 5000);
}}

function fetchAndUpdate() {{
  if (!candleChart) return;
  fetch(SERVER_URL + '/ohlcv?limit=0')
    .then(function(res) {{ return res.json(); }})
    .then(function(data) {{
      candleSeries.setData(data.ohlcv);
      candleSeries.setMarkers(data.markers || markersData);
      stSeries.setData(data.supertrend);
      var dot = document.getElementById('liveDot');
      var lv  = data.live || {{}};
      if (lv.active) {{
        dot.classList.add('live');
        var mkt = lv.market_open ? 'MARKET OPEN' : 'market closed';
        dot.title = (lv.symbol || '') + ' ' + (lv.timeframe || '') +
                    ' — ' + mkt + ' — updated ' + (lv.last_update || '');
      }} else {{
        dot.classList.remove('live');
        dot.title = lv.error ? 'MT5: ' + lv.error : 'historical data (server connected)';
      }}
    }})
    .catch(function() {{
      var dot = document.getElementById('liveDot');
      dot.classList.remove('live');
      dot.title = 'server offline — showing embedded snapshot';
    }});
}}

function toggleSupertrend() {{
  stVisible = !stVisible;
  if (stSeries) {{
    stSeries.applyOptions({{ visible: stVisible }});
  }}
  var btn = document.getElementById('stToggle');
  btn.textContent = stVisible ? 'Supertrend ON' : 'Supertrend OFF';
  btn.classList.toggle('on', stVisible);
}}

// ── Terminal bar clock ────────────────────────────────────────────
(function updateClock() {{
  var el = document.getElementById('tbTime');
  if (el) {{
    var now = new Date();
    el.textContent = now.toLocaleTimeString('en-GB', {{hour:'2-digit', minute:'2-digit', second:'2-digit'}});
  }}
  setTimeout(updateClock, 1000);
}})();

// ── Auto-refresh: reload when dashboard.html is rebuilt ───────────
(function() {{
  var lastHash = null;
  function checkHash() {{
    fetch('http://localhost:8765/dashboard_hash')
      .then(function(r) {{ return r.json(); }})
      .then(function(d) {{
        if (lastHash === null) {{ lastHash = d.hash; return; }}
        if (d.hash !== lastHash) {{ location.reload(); }}
      }})
      .catch(function() {{}});
  }}
  setInterval(checkHash, 3000);
  checkHash();
}})();
</script>
</body>
</html>"""


if __name__ == "__main__":
    build_dashboard()
