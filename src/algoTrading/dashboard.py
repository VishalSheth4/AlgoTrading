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
    """Write the static React dashboard shell and open the browser.
    All trade analytics are served live by chart_server.py via /trades.
    """
    base   = Path(__file__).resolve().parent
    html_p = base / "data" / "dashboard.html"

    html = _render()
    html_p.write_text(html, encoding='utf-8')
    print(f"[dashboard] written -> {html_p}")

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



def _render() -> str:
    """Return the static React dashboard shell. All analytics come from /trades."""
    return r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Trading Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;900&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{background:#090b0f;color:#c9d1d9;font-family:'Inter',system-ui,-apple-system,sans-serif;min-height:100vh}
@keyframes spin{to{transform:rotate(360deg)}}
.terminal-bar{background:#0d1117;border-bottom:2px solid #d29922;padding:0 20px;display:flex;align-items:center;height:34px;position:sticky;top:0;z-index:100;gap:0}
.tb-brand{font-size:11px;font-weight:900;letter-spacing:.18em;color:#d29922;margin-right:20px;font-family:'JetBrains Mono',monospace;white-space:nowrap}
.tb-sep{width:1px;height:14px;background:#1c2332;margin:0 14px;flex-shrink:0}
.tb-pill{font-size:10px;font-weight:600;letter-spacing:.08em;color:#6e7681;background:#161b22;border:1px solid #1c2332;padding:2px 8px;border-radius:2px;margin-right:5px;font-family:'JetBrains Mono',monospace;white-space:nowrap}
.tb-pill.active{color:#d29922;border-color:rgba(210,153,34,.4)}
.tb-right{margin-left:auto;display:flex;align-items:center;gap:10px}
.tb-time{font-size:10px;color:#3d4451;font-family:'JetBrains Mono',monospace}
.tb-dot{width:6px;height:6px;border-radius:50%;background:#3fb950;box-shadow:0 0 4px rgba(63,185,80,.5)}
.page-wrap{padding:18px 22px 40px;max-width:1900px;margin:0 auto}
.clr-green{color:#3fb950}.clr-red{color:#f85149}.clr-blue{color:#2f81f7}
.tab-nav{display:flex;margin-bottom:18px;border-bottom:1px solid #1c2332}
.tab-btn{padding:7px 18px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.12em;border:none;border-bottom:2px solid transparent;background:transparent;color:#3d4451;cursor:pointer;margin-bottom:-1px;transition:color .15s,border-color .15s;font-family:'Inter',sans-serif}
.tab-btn:hover{color:#6e7681}
.tab-btn.active{color:#e6edf3;border-bottom-color:#d29922}
.section-block{margin-bottom:18px}
.section-toggle{display:flex;align-items:center;justify-content:space-between;padding:8px 16px;background:#0d1117;border:1px solid #1c2332;border-radius:2px;cursor:pointer;user-select:none;transition:border-color .15s}
.section-toggle:hover{border-color:#d29922}
.section-block-lbl{font-size:9px;text-transform:uppercase;letter-spacing:.12em;color:#6e7681;font-weight:700;font-family:'Inter',sans-serif}
.toggle-icon{font-size:11px;color:#3d4451;transition:transform .2s;flex-shrink:0}
.section-block.collapsed .toggle-icon{transform:rotate(-90deg)}
.section-body{padding-top:10px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(168px,1fr));gap:8px;margin-bottom:18px}
.card{background:#0d1117;border:1px solid #1c2332;border-left:3px solid #2f81f7;border-radius:2px;padding:14px 16px;transition:border-left-color .15s}
.card:hover{border-left-color:#d29922}
.card-lbl{font-size:9px;text-transform:uppercase;letter-spacing:.12em;color:#3d4451;margin-bottom:9px;font-weight:700;font-family:'Inter',sans-serif}
.card-val{font-size:22px;font-weight:700;line-height:1;font-family:'JetBrains Mono',monospace;letter-spacing:-.01em}
.card-sub{font-size:10px;color:#3d4451;margin-top:6px;font-family:'JetBrains Mono',monospace}
.streak-card{cursor:pointer}.streak-card:hover{border-left-color:#d29922 !important}
.charts{display:grid;grid-template-columns:2.2fr 1fr;gap:8px;margin-bottom:18px}
.chart-box{background:#0d1117;border:1px solid #1c2332;border-radius:2px;padding:16px}
.chart-lbl{font-size:9px;text-transform:uppercase;letter-spacing:.12em;color:#3d4451;margin-bottom:12px;font-weight:700;font-family:'Inter',sans-serif}
.btn-sm{padding:2px 10px;font-size:9px;font-weight:700;border-radius:2px;border:1px solid #1c2332;background:#090b0f;color:#3d4451;cursor:pointer;letter-spacing:.08em;font-family:'Inter',sans-serif}
.btn-sm:hover{color:#e6edf3;border-color:#d29922}
.tbl-box{background:#0d1117;border:1px solid #1c2332;border-radius:2px;padding:14px 18px;overflow-x:auto;margin-bottom:18px}
.tbl-lbl{font-size:9px;text-transform:uppercase;letter-spacing:.12em;color:#3d4451;margin-bottom:10px;font-weight:700;font-family:'Inter',sans-serif}
.tbl-hdr{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;flex-wrap:wrap;gap:6px}
.filter-bar{display:flex;align-items:center;gap:5px;flex-wrap:wrap}
table{width:100%;border-collapse:collapse;font-size:12px}
thead th{padding:6px 11px;text-align:left;font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:#3d4451;background:#090b0f;border-bottom:1px solid #1c2332;font-weight:700;font-family:'Inter',sans-serif}
tbody td{padding:6px 11px;border-bottom:1px solid #0d1117;color:#6e7681;font-family:'JetBrains Mono',monospace;font-size:12px}
tbody tr:nth-child(even) td{background:rgba(255,255,255,.013)}
tbody tr:hover td{background:#161b22}
tbody td.hl{color:#c9d1d9}
tbody td.muted{color:#3d4451;font-size:11px;font-family:'Inter',sans-serif}
.win-txt{color:#3fb950;font-weight:700}.los-txt{color:#f85149;font-weight:700}
.mono{font-family:'JetBrains Mono',monospace}
.strat-cell{max-width:90px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:10px;font-family:'Inter',sans-serif;color:#6e7681}
.month-calendar{display:grid;grid-template-columns:repeat(auto-fill,minmax(138px,1fr));gap:7px;padding:2px 0}
.month-cell{border-radius:2px;padding:12px 14px;border:1px solid;transition:transform .1s,box-shadow .1s;cursor:pointer;position:relative}
.month-cell:hover{transform:translateY(-1px)}
.month-cell.mc-profit{background:rgba(63,185,80,.07);border-color:rgba(63,185,80,.22)}
.month-cell.mc-loss{background:rgba(248,81,73,.07);border-color:rgba(248,81,73,.22)}
.month-cell.mc-active{box-shadow:0 0 0 2px #d29922;transform:translateY(-1px)}
.mc-name{font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:#3d4451;margin-bottom:7px;font-weight:700;font-family:'Inter',sans-serif}
.mc-pnl{font-size:18px;font-weight:700;line-height:1;margin-bottom:7px;font-family:'JetBrains Mono',monospace}
.mc-pnl.mc-pos{color:#3fb950}.mc-pnl.mc-neg{color:#f85149}
.mc-pct{font-size:11px;font-family:'JetBrains Mono',monospace;margin-bottom:6px;font-weight:700}
.mc-divider{border:none;border-top:1px solid #1c2332;margin:5px 0}
.mc-row{display:flex;justify-content:space-between;align-items:center;font-size:10px;color:#3d4451;margin-top:3px}
.mc-lbl{font-size:9px;text-transform:uppercase;letter-spacing:.06em;font-family:'Inter',sans-serif}
.mc-val{font-weight:700;font-size:11px;font-family:'JetBrains Mono',monospace}
.mc-val.c-trades{color:#2f81f7}.mc-val.c-wins{color:#3fb950}.mc-val.c-losses{color:#f85149}
.cal-tooltip{position:fixed;background:#0d1117;border:1px solid #d29922;border-radius:4px;padding:12px 14px;min-width:360px;max-height:320px;overflow-y:auto;pointer-events:none;z-index:1000;box-shadow:0 8px 32px rgba(0,0,0,.7)}
.ct-title{font-size:10px;font-weight:700;color:#d29922;letter-spacing:.1em;text-transform:uppercase;margin-bottom:8px;font-family:'JetBrains Mono',monospace}
.ct-tbl{width:100%;border-collapse:collapse;font-size:11px}
.ct-tbl thead th{text-align:left;color:#3d4451;font-weight:700;padding-bottom:4px;font-size:9px;letter-spacing:.08em;text-transform:uppercase;font-family:'Inter',sans-serif}
.ct-tbl tbody td{padding:2px 6px 2px 0;border:none;color:#6e7681}
.sym-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(195px,1fr));gap:8px;margin-bottom:18px}
.sym-card{background:#0d1117;border:1px solid #1c2332;border-left:3px solid;border-radius:2px;padding:14px 16px;position:relative;overflow:hidden}
.sym-name{font-size:11px;font-weight:900;letter-spacing:.12em;margin-bottom:9px;font-family:'JetBrains Mono',monospace}
.sym-pnl{font-size:20px;font-weight:700;line-height:1;margin-bottom:3px;font-family:'JetBrains Mono',monospace}
.sym-pct{font-size:10px;color:#3d4451;margin-bottom:11px;font-family:'JetBrains Mono',monospace}
.sym-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;padding-top:9px;border-top:1px solid #1c2332}
.sym-stat-item{text-align:center}
.sym-stat-val{font-size:13px;font-weight:700;line-height:1;margin-bottom:2px;font-family:'JetBrains Mono',monospace}
.sym-stat-lbl{font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:#3d4451;font-family:'Inter',sans-serif}
.sym-badge{display:inline-block;padding:1px 7px;border-radius:2px;font-size:10px;font-weight:800;letter-spacing:.08em;border:1px solid;font-family:'JetBrains Mono',monospace}
.sym-table-wrap{background:#0d1117;border:1px solid #1c2332;border-radius:2px;padding:14px 18px;margin-bottom:18px;overflow-x:auto}
.sym-tbl{width:100%;border-collapse:collapse;font-size:12px}
.sym-tbl thead th{padding:6px 11px;text-align:left;font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:#3d4451;background:#090b0f;border-bottom:1px solid #1c2332;font-weight:700;font-family:'Inter',sans-serif}
.sym-tbl tbody td{padding:6px 11px;border-bottom:1px solid #0d1117;color:#6e7681;font-family:'JetBrains Mono',monospace}
.sym-tbl tbody tr:hover td{background:#161b22}
.sym-tbl tfoot td{padding:7px 11px;border-top:1px solid #1c2332;font-weight:700;font-size:12px;color:#c9d1d9;background:#090b0f;font-family:'JetBrains Mono',monospace}
.modal-overlay{display:flex;position:fixed;inset:0;background:rgba(0,0,0,.82);z-index:999;align-items:center;justify-content:center}
.modal-box{background:#0d1117;border:1px solid #1c2332;border-top:2px solid #d29922;border-radius:2px;padding:22px 26px;width:min(820px,92vw);max-height:82vh;overflow-y:auto;position:relative}
.modal-title{font-size:13px;font-weight:700;margin-bottom:3px;font-family:'JetBrains Mono',monospace}
.modal-sub{font-size:10px;color:#3d4451;margin-bottom:16px;font-family:'Inter',sans-serif}
.modal-close{position:absolute;top:14px;right:16px;background:none;border:none;color:#3d4451;font-size:18px;cursor:pointer;line-height:1}
.modal-close:hover{color:#c9d1d9}
.modal-tbl{width:100%;border-collapse:collapse;font-size:12px}
.modal-tbl thead th{padding:6px 11px;text-align:left;font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:#3d4451;background:#090b0f;border-bottom:1px solid #1c2332;font-family:'Inter',sans-serif}
.modal-tbl tbody td{padding:6px 11px;border-bottom:1px solid #0d1117;color:#6e7681;font-family:'JetBrains Mono',monospace}
.modal-tbl tbody tr:hover td{background:#161b22}
.candle-wrap{background:#0d1117;border:1px solid #1c2332;border-radius:2px;padding:16px;margin-bottom:18px}
.candle-toolbar{display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap}
.candle-title{font-size:9px;text-transform:uppercase;letter-spacing:.12em;color:#3d4451;flex:1;display:flex;align-items:center;gap:8px;font-weight:700;font-family:'Inter',sans-serif}
.status-dot{width:6px;height:6px;border-radius:50%;background:#1c2332;display:inline-block;flex-shrink:0;transition:background .4s}
.status-dot.live{background:#3fb950;box-shadow:0 0 5px rgba(63,185,80,.5)}
.btn-toggle{padding:4px 12px;font-size:10px;font-weight:700;border-radius:2px;border:1px solid #1c2332;background:#090b0f;color:#3d4451;cursor:pointer;letter-spacing:.06em;transition:all .15s;font-family:'Inter',sans-serif}
.btn-toggle.on{background:rgba(47,129,247,.1);border-color:#2f81f7;color:#2f81f7}
@media(max-width:900px){.charts{grid-template-columns:1fr}.cards{grid-template-columns:repeat(auto-fill,minmax(140px,1fr))}}
@media(max-width:600px){.page-wrap{padding:10px 12px 28px}.cards{grid-template-columns:repeat(2,1fr)}.card-val{font-size:18px}.tb-pill{display:none}.tb-sep{display:none}}
</style>
</head>
<body>
<div id="root">
  <div style="display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column;gap:12px">
    <div style="width:28px;height:28px;border:2px solid #1c2332;border-top:2px solid #d29922;border-radius:50%;animation:spin 1s linear infinite"></div>
    <div style="font-size:11px;color:#3d4451;letter-spacing:.12em;text-transform:uppercase;font-family:'JetBrains Mono',monospace">Loading…</div>
  </div>
</div>
<script>
const SERVER='http://localhost:8765';
function loadScript(src,fb){return new Promise((res,rej)=>{const s=document.createElement('script');s.src=src;s.onload=res;s.onerror=fb?()=>{const s2=document.createElement('script');s2.src=fb;s2.onload=res;s2.onerror=rej;document.head.appendChild(s2);}:rej;document.head.appendChild(s);});}
Promise.all([
  loadScript(SERVER+'/static/preact.js','https://unpkg.com/preact@10/dist/preact.umd.js'),
  loadScript(SERVER+'/static/preact-hooks.js','https://unpkg.com/preact@10/hooks/dist/hooks.umd.js'),
  loadScript(SERVER+'/static/htm.js','https://unpkg.com/htm@3/dist/htm.umd.js'),
  loadScript(SERVER+'/static/chartjs.js','https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js'),
]).then(()=>initApp()).catch(()=>{document.getElementById('root').innerHTML='<div style="display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column;gap:12px;color:#f85149;font-family:Inter,sans-serif"><div style="font-size:28px">⚡</div><div style="font-size:14px">Could not load libraries. Is chart_server.py running?</div></div>';});
function initApp(){
const {h,render,Fragment}=preact;
const {useState,useEffect,useRef,useMemo}=preactHooks;
const html=htm.bind(h);
const SC={XAUUSD:'#f59e0b',EURUSD:'#3b82f6',GBPUSD:'#8b5cf6',USDJPY:'#22c55e',XAGUSD:'#06b6d4',USDCHF:'#f97316',BTCUSD:'#f97316'};
const FB=['#a78bfa','#34d399','#fb7185','#38bdf8','#facc15','#e879f9'];
let _si=0;const _cc={};
function sClr(s){return _cc[s]=_cc[s]||SC[s]||FB[_si++%FB.length];}
function fP(v){return(v>=0?'+$':'-$')+Math.abs(v).toFixed(2);}
function fC(v){return'$'+v.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});}
function Chip({sym}){const c=sClr(sym);return html`<span style=${{display:'inline-block',padding:'1px 7px',borderRadius:'2px',fontSize:'10px',fontWeight:700,letterSpacing:'.06em',border:'1px solid',color:c,borderColor:c+'55',background:c+'18',fontFamily:"'JetBrains Mono',monospace"}}>${sym}</span>`;}
function Badge({label}){
  const M={LONG:{bg:'rgba(63,185,80,.12)',clr:'#3fb950',b:'rgba(63,185,80,.3)'},SHORT:{bg:'rgba(248,81,73,.12)',clr:'#f85149',b:'rgba(248,81,73,.3)'},TP:{bg:'rgba(47,129,247,.12)',clr:'#2f81f7',b:'rgba(47,129,247,.3)'},SL:{bg:'rgba(248,81,73,.12)',clr:'#f85149',b:'rgba(248,81,73,.3)'},'R:R':{bg:'rgba(63,185,80,.12)',clr:'#3fb950',b:'rgba(63,185,80,.3)'},ST:{bg:'rgba(188,140,255,.12)',clr:'#bc8cff',b:'rgba(188,140,255,.3)'},REV:{bg:'rgba(210,153,34,.12)',clr:'#d29922',b:'rgba(210,153,34,.3)'}};
  const s=M[label]||M.SL;const d=label==='R:R'?'R:R':label==='ST'?'Supertrend':label==='REV'?'Rev Engulf':label;
  return html`<span style=${{display:'inline-block',padding:'1px 7px',borderRadius:'2px',fontSize:'10px',fontWeight:700,letterSpacing:'.06em',border:'1px solid',color:s.clr,borderColor:s.b,background:s.bg,fontFamily:"'JetBrains Mono',monospace"}}>${d}</span>`;}
function Card({lbl,val,valStyle,sub,onClick,className}){return html`<div class=${'card'+(className?' '+className:'')} onclick=${onClick} style=${onClick?{cursor:'pointer'}:{}}><div class="card-lbl">${lbl}</div><div class="card-val" style=${valStyle||{}}>${val}</div>${sub?html`<div class="card-sub">${sub}</div>`:null}</div>`;}
function SBlock({title,children,open:initOpen=true}){const [o,setO]=useState(initOpen);return html`<div class=${'section-block'+(o?'':' collapsed')}><div class="section-toggle" onclick=${()=>setO(x=>!x)}><span class="section-block-lbl">${title}</span><span class="toggle-icon">▼</span></div><div class="section-body">${o?children:null}</div></div>`;}
function EqChart({labels,data}){
  const r=useRef(null);const inst=useRef(null);const s=useRef(0);const e=useRef(data.length-1);const dg=useRef({});
  function draw(s0,e0){if(!r.current||typeof Chart==='undefined')return;if(inst.current){inst.current.destroy();inst.current=null;}
    const lb=labels.slice(s0,e0+1).map(l=>{if(l==='Start')return'Start';const d=new Date(l.replace(' ','T'));return isNaN(d)?l:d.toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'2-digit'});});
    inst.current=new Chart(r.current,{type:'line',data:{labels:lb,datasets:[{data:data.slice(s0,e0+1),borderColor:'#3b82f6',backgroundColor:'rgba(59,130,246,.06)',borderWidth:2,pointRadius:0,fill:true,tension:0.35}]},options:{animation:false,plugins:{legend:{display:false}},scales:{x:{display:true,grid:{color:'#1a2540'},ticks:{color:'#334155',maxRotation:40,autoSkip:true,maxTicksLimit:8}},y:{grid:{color:'#1a2540'},ticks:{color:'#334155',callback:v=>'$'+v.toLocaleString()}}}}});}
  useEffect(()=>{draw(0,data.length-1);return()=>{if(inst.current)inst.current.destroy();};},[]);
  function wh(ev){ev.preventDefault();const tot=data.length-1;const rng=e.current-s.current;const ctr=Math.round(s.current+rng/2);const f=ev.deltaY<0?0.65:1.55;const nr=Math.max(4,Math.min(tot,Math.round(rng*f)));const half=Math.round(nr/2);let ns=Math.max(0,ctr-half);let ne=Math.min(tot,ns+nr);if(ne===tot)ns=Math.max(0,tot-nr);s.current=ns;e.current=ne;draw(ns,ne);}
  function md(ev){dg.current={x:ev.clientX,s:s.current,e:e.current};}
  function mm(ev){if(!dg.current.x)return;const tot=data.length-1;const rng=dg.current.e-dg.current.s;const sh=-Math.round((ev.clientX-dg.current.x)/(r.current?.clientWidth||600)*rng);const ns=Math.max(0,Math.min(tot-rng,dg.current.s+sh));const ne=Math.min(tot,ns+rng);s.current=ns;e.current=ne;draw(ns,ne);}
  function mu(){dg.current={};}
  return html`<div><div class="chart-lbl" style=${{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:'8px'}}><span>Equity Curve <span style=${{fontSize:'9px',color:'#2d3a4a',fontWeight:400,marginLeft:'6px'}}>scroll=zoom · drag=pan</span></span><button class="btn-sm" onclick=${()=>{s.current=0;e.current=data.length-1;draw(0,data.length-1);}}>RESET</button></div><canvas ref=${r} height="110" style=${{cursor:'grab'}} onWheel=${wh} onMouseDown=${md} onMouseMove=${mm} onMouseUp=${mu} onMouseLeave=${mu}/></div>`;}
function PlChart({profits}){const r=useRef(null);useEffect(()=>{if(!r.current||typeof Chart==='undefined')return;const c=new Chart(r.current,{type:'bar',data:{labels:profits.map((_,i)=>'#'+(i+1)),datasets:[{data:profits,backgroundColor:profits.map(p=>p>0?'#22c55e':'#ef4444'),borderRadius:2}]},options:{animation:false,plugins:{legend:{display:false}},scales:{x:{display:false},y:{grid:{color:'#1a2540'},ticks:{color:'#334155',callback:v=>'$'+v}}}}});return()=>c.destroy();},[]);return html`<canvas ref=${r} height="110"/>`;}
function CalTooltip({row,pos,onClose}){
  if(!pos)return null;
  const left=Math.min(pos.x+12,window.innerWidth-430);const top=Math.min(pos.y+12,window.innerHeight-400);
  const trades=row.trade_list||[];
  return html`<div class="cal-tooltip" style=${{left:left+'px',top:top+'px'}} onClick=${e=>e.stopPropagation()}><div class="ct-title" style=${{display:'flex',alignItems:'center',justifyContent:'space-between'}}><span>${row.month||row.week} — ${trades.length} trades</span><button onClick=${onClose} style=${{background:'none',border:'none',color:'#6e7681',cursor:'pointer',fontSize:'14px',lineHeight:1,padding:'0 2px',marginLeft:'12px'}} title="Close">✕</button></div><table class="ct-tbl"><thead><tr><th>Date</th><th>Instrument</th><th>Strategy</th><th>Dir</th><th>Lot</th><th style=${{textAlign:'right'}}>P&L</th></tr></thead><tbody>${trades.map((t,i)=>html`<tr key=${i}><td class="mono" style=${{color:'#6e7681',paddingRight:'8px'}}>${t.time.slice(0,10)}</td><td style=${{paddingRight:'8px'}}><${Chip} sym=${t.symbol}/></td><td class="strat-cell" style=${{paddingRight:'8px'}}>${t.strategy}</td><td style=${{paddingRight:'8px'}}><${Badge} label=${t.dir}/></td><td class="mono" style=${{paddingRight:'8px',color:'#94a3b8'}}>${t.lot||'—'}</td><td class="mono" style=${{textAlign:'right',color:t.profit>=0?'#3fb950':'#f85149',fontWeight:600}}>${fP(t.profit)}</td></tr>`)}</tbody></table></div>`;}
function CalCell({row,field}){
  const [pos,setPos]=useState(null);const p=row.profit>=0;
  useEffect(()=>{if(!pos)return;const close=()=>setPos(null);document.addEventListener('click',close);return()=>document.removeEventListener('click',close);},[pos]);
  return html`<${Fragment}><div class=${'month-cell '+(p?'mc-profit':'mc-loss')+(pos?' mc-active':'')} style=${{cursor:'pointer'}} onClick=${e=>{e.stopPropagation();setPos(v=>v?null:{x:e.clientX,y:e.clientY});}}><div class="mc-name">${row[field]}</div><div class=${'mc-pnl '+(p?'mc-pos':'mc-neg')}>${fP(row.profit)}</div><div class="mc-pct" style=${{color:p?'#3fb950':'#f85149'}}>${(p?'▲ ':'▼ ')+Math.abs(row.profit_pct).toFixed(2)+'%'}</div><hr class="mc-divider"/><div class="mc-row"><span class="mc-lbl">Trades</span><span class="mc-val c-trades">${row.trades}</span></div><div class="mc-row"><span class="mc-lbl">Wins</span><span class="mc-val c-wins">${row.wins}</span></div><div class="mc-row"><span class="mc-lbl">Losses</span><span class="mc-val c-losses">${row.losses}</span></div><div class="mc-row"><span class="mc-lbl">Win Rate</span><span class="mc-val" style=${{color:row.win_rate>=50?'#3fb950':'#f85149'}}>${row.win_rate}%</span></div></div>${pos?html`<${CalTooltip} row=${row} pos=${pos} onClose=${()=>setPos(null)}/>`:null}</${Fragment}>`;}
function SymCard({s}){const c=sClr(s.symbol);const p=s.profit>=0;return html`<div class="sym-card" style=${{borderLeftColor:c}}><div class="sym-name" style=${{color:c}}>${s.symbol}</div><div class="sym-pnl" style=${{color:p?'#22c55e':'#ef4444'}}>${fP(s.profit)}</div><div class="sym-pct">${(p?'▲':'▼')+' '+Math.abs(s.profit_pct).toFixed(2)+'%'}</div><div class="sym-stats"><div class="sym-stat-item"><div class="sym-stat-val" style=${{color:'#60a5fa'}}>${s.trades}</div><div class="sym-stat-lbl">Trades</div></div><div class="sym-stat-item"><div class="sym-stat-val" style=${{color:s.win_rate>=50?'#22c55e':'#ef4444'}}>${s.win_rate}%</div><div class="sym-stat-lbl">Win Rate</div></div><div class="sym-stat-item"><div class="sym-stat-val"><span style=${{color:'#22c55e'}}>${s.wins}</span><span style=${{color:'#475569'}}> / </span><span style=${{color:'#ef4444'}}>${s.losses}</span></div><div class="sym-stat-lbl">W / L</div></div></div></div>`;}
function TradeLog({rows}){
  const [mo,setMo]=useState('all');const [sy,setSy]=useState('all');const [st,setSt]=useState('all');
  const months=useMemo(()=>{const seen={},ord=[];rows.forEach(r=>{const m=r.time.substring(0,7);if(!seen[m]){seen[m]=new Date(r.time).toLocaleString('en-GB',{month:'short',year:'numeric'});ord.push(m);}});return ord.map(m=>({val:m,lbl:seen[m]}));},[rows]);
  const syms=useMemo(()=>[...new Set(rows.map(r=>r.symbol).filter(Boolean))].sort(),[rows]);
  const strats=useMemo(()=>[...new Set(rows.map(r=>r.strategy).filter(Boolean))].sort(),[rows]);
  const fl=useMemo(()=>{let l=rows;if(mo!=='all')l=l.filter(r=>r.time.startsWith(mo));if(sy!=='all')l=l.filter(r=>r.symbol===sy);if(st!=='all')l=l.filter(r=>r.strategy===st);return l;},[rows,mo,sy,st]);
  const sel={background:'#090b0f',color:'#6e7681',border:'1px solid #1c2332',borderRadius:'2px',padding:'3px 8px',fontSize:'10px',cursor:'pointer',outline:'none',fontFamily:"'JetBrains Mono',monospace"};
  return html`<div><div class="tbl-hdr"><div class="tbl-lbl" style=${{marginBottom:0}}>Trade Log <span style=${{color:'#3d4451',fontWeight:400}}>(${fl.length})</span></div><div class="filter-bar"><select value=${st} onChange=${e=>setSt(e.target.value)} style=${sel}><option value="all">All Strategies</option>${strats.map(s=>html`<option key=${s} value=${s}>${s}</option>`)}</select><select value=${sy} onChange=${e=>setSy(e.target.value)} style=${sel}><option value="all">All Symbols</option>${syms.map(s=>html`<option key=${s} value=${s}>${s}</option>`)}</select><select value=${mo} onChange=${e=>setMo(e.target.value)} style=${sel}><option value="all">All Months</option>${months.map(m=>html`<option key=${m.val} value=${m.val}>${m.lbl}</option>`)}</select></div></div><table><thead><tr><th>#</th><th>Exit Time</th><th>Symbol</th><th>Strategy</th><th>Dir</th><th>Entry</th><th>SL</th><th>Target</th><th>Exit</th><th>Closed By</th><th>Lot</th><th>P&L</th><th>Capital</th></tr></thead><tbody>${fl.map(r=>html`<tr key=${r.num}><td class="muted">${r.num}</td><td class="muted">${r.time}</td><td><${Chip} sym=${r.symbol}/></td><td class="muted" style=${{fontSize:'11px'}}>${r.strategy||'—'}</td><td><${Badge} label=${r.dir}/></td><td class="hl">${r.entry}</td><td class="clr-red">${r.sl}</td><td style=${{color:'#a78bfa'}}>${r.target}</td><td class="hl">${r.exit}</td><td><${Badge} label=${r.label||'SL'}/></td><td class="muted">${r.lot}</td><td class=${r.profit>=0?'win-txt':'los-txt'}>${fP(r.profit)}</td><td style=${{color:'#60a5fa'}}>${fC(r.cap)}</td></tr>`)}</tbody></table></div>`;}
function StreakModal({type,trades,onClose}){if(!type)return null;const clr=type==='win'?'#22c55e':'#ef4444';return html`<div class="modal-overlay" onclick=${e=>e.target===e.currentTarget&&onClose()}><div class="modal-box"><button class="modal-close" onclick=${onClose}>✕</button><div class="modal-title" style=${{color:clr}}>${type==='win'?'Max Win Streak':'Max Loss Streak'}</div><div class="modal-sub">${trades.length} consecutive ${type==='win'?'winning':'losing'} trades</div><table class="modal-tbl"><thead><tr><th>#</th><th>Exit Time</th><th>Symbol</th><th>Dir</th><th>Entry</th><th>Exit</th><th>P&L</th><th>Capital</th></tr></thead><tbody>${trades.map((t,i)=>html`<tr key=${i}><td class="muted">${i+1}</td><td class="muted">${t.time}</td><td><${Chip} sym=${t.symbol}/></td><td><${Badge} label=${t.dir}/></td><td class="hl">${t.entry}</td><td class="hl">${t.exit}</td><td class=${t.profit>0?'win-txt':'los-txt'}>${fP(t.profit)}</td><td style=${{color:'#60a5fa'}}>${fC(t.cap)}</td></tr>`)}</tbody></table></div></div>`;}
function CandlesPane({visible}){
  const cR=useRef(null);const chR=useRef(null);const csR=useRef(null);const stR=useRef(null);
  const stV=useRef(true);const [stL,setStL]=useState('Supertrend ON');const [dl,setDl]=useState(false);const [dt,setDt]=useState('');const ini=useRef(false);const flR=useRef(false);
  useEffect(()=>{if(!visible||ini.current)return;ini.current=true;if(typeof LightweightCharts!=='undefined'){initChart();return;}const s=document.createElement('script');s.src=SERVER+'/static/lwc.js';s.onerror=()=>{s.src='https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js';};s.onload=()=>{if(cR.current)initChart();};document.head.appendChild(s);},[visible]);
  function initChart(){if(!cR.current||typeof LightweightCharts==='undefined')return;
    const TZ='Asia/Kolkata';function kF(ts,o){return new Intl.DateTimeFormat('en-GB',Object.assign({timeZone:TZ},o)).format(new Date(ts*1000));}
    const ch=LightweightCharts.createChart(cR.current,{width:cR.current.clientWidth||900,height:520,layout:{background:{type:'solid',color:'#07091a'},textColor:'#4a5568'},grid:{vertLines:{color:'#0d1526'},horzLines:{color:'#0d1526'}},crosshair:{mode:LightweightCharts.CrosshairMode.Normal,vertLine:{color:'#3b82f6',labelBackgroundColor:'#1e3a8a'},horzLine:{color:'#3b82f6',labelBackgroundColor:'#1e3a8a'}},localization:{timeFormatter:ts=>kF(ts,{day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit',hour12:false})+' IST'},rightPriceScale:{borderColor:'#1a2540',scaleMargins:{top:0.08,bottom:0.05}},timeScale:{borderColor:'#1a2540',timeVisible:true,secondsVisible:false,barSpacing:8,tickMarkFormatter:(ts,t)=>{if(t===0)return kF(ts,{year:'numeric'});if(t===1)return kF(ts,{month:'short'});if(t===2)return kF(ts,{day:'2-digit',month:'short'});return kF(ts,{hour:'2-digit',minute:'2-digit',hour12:false});}}});
    new ResizeObserver(()=>ch.applyOptions({width:cR.current.clientWidth})).observe(cR.current);
    const cs=ch.addCandlestickSeries({upColor:'#26a69a',downColor:'#ef5350',borderUpColor:'#26a69a',borderDownColor:'#ef5350',wickUpColor:'#26a69a',wickDownColor:'#ef5350'});
    const ss=ch.addLineSeries({lineWidth:2,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false});
    chR.current=ch;csR.current=cs;stR.current=ss;fetch2();setInterval(fetch2,5000);}
  function fetch2(){fetch(SERVER+'/ohlcv?limit=0').then(r=>r.json()).then(d=>{if(csR.current){csR.current.setData(d.ohlcv);csR.current.setMarkers(d.markers||[]);}if(stR.current)stR.current.setData(d.supertrend);if(chR.current&&!flR.current){flR.current=true;chR.current.timeScale().scrollToRealTime();}const lv=d.live||{};if(lv.active){setDl(true);setDt((lv.symbol||'')+' '+(lv.timeframe||'')+' — '+(lv.market_open?'MARKET OPEN':'market closed'));}else{setDl(false);setDt(lv.error?'MT5: '+lv.error:'historical data');}}).catch(()=>{setDl(false);setDt('server offline');});}
  function tST(){stV.current=!stV.current;if(stR.current)stR.current.applyOptions({visible:stV.current});setStL(stV.current?'Supertrend ON':'Supertrend OFF');}
  return html`<div class="candle-wrap"><div class="candle-toolbar"><div class="candle-title">Candlestick — All Bars <span class=${'status-dot'+(dl?' live':'')} title=${dt}></span></div><button class=${'btn-toggle'+(stV.current?' on':'')} onclick=${tST}>${stL}</button></div><div ref=${cR} style=${{width:'100%',height:'520px',overflow:'hidden'}}></div></div>`;}
function Clock(){const [t,setT]=useState('');useEffect(()=>{const tick=()=>setT(new Date().toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',second:'2-digit'}));tick();const id=setInterval(tick,1000);return()=>clearInterval(id);},[]);return html`<span class="tb-time">${t}</span>`;}
function App(){
  const [data,setData]=useState(null);const [loading,setLoading]=useState(true);const [err,setErr]=useState(false);
  const [tab,setTab]=useState(location.hash==='#candles'?'candles':'performance');const [streak,setStreak]=useState(null);
  useEffect(()=>{fetch(SERVER+'/trades').then(r=>r.json()).then(d=>{setData(d);setLoading(false);}).catch(()=>{setErr(true);setLoading(false);});},[]);
  useEffect(()=>{let h=null;const id=setInterval(()=>{fetch(SERVER+'/dashboard_hash').then(r=>r.json()).then(d=>{if(h===null){h=d.hash;return;}if(d.hash!==h)location.reload();}).catch(()=>{});},3000);return()=>clearInterval(id);},[]);
  useEffect(()=>{location.hash=tab;},[tab]);
  useEffect(()=>{const fn=e=>{if(e.key==='Escape')setStreak(null);};document.addEventListener('keydown',fn);return()=>document.removeEventListener('keydown',fn);},[]);
  if(loading)return html`<div style=${{display:'flex',alignItems:'center',justifyContent:'center',height:'100vh',flexDirection:'column',gap:'12px'}}><div style=${{width:'28px',height:'28px',border:'2px solid #1c2332',borderTop:'2px solid #d29922',borderRadius:'50%',animation:'spin 1s linear infinite'}}></div><div style=${{fontSize:'11px',color:'#3d4451',letterSpacing:'.12em',textTransform:'uppercase',fontFamily:"'JetBrains Mono',monospace"}}>Loading trade data…</div></div>`;
  if(err)return html`<div style=${{display:'flex',alignItems:'center',justifyContent:'center',height:'100vh',flexDirection:'column',gap:'16px',padding:'40px'}}><div style=${{fontSize:'28px'}}>⚡</div><div style=${{fontSize:'16px',fontWeight:700,color:'#c9d1d9',fontFamily:"'Inter',sans-serif"}}>Server Offline</div><div style=${{fontSize:'13px',color:'#6e7681',textAlign:'center',lineHeight:'1.6',fontFamily:"'Inter',sans-serif"}}>Start chart server: <code style=${{background:'#161b22',padding:'2px 8px',borderRadius:'4px',color:'#d29922',fontFamily:"'JetBrains Mono',monospace"}}>python chart_server.py</code></div></div>`;
  const {meta,metrics:m,monthly_rows,weekly_rows,symbols_data,rows,eq_labels,eq_data,profits,win_streak_trades,loss_streak_trades}=data;
  const pPos=m.total_profit>=0;const wrC=m.win_rate>=50?'#3fb950':'#f85149';const pfC=m.pf_val>=1?'#3fb950':'#f85149';const stH=m.cur_t==='win'?'#22c55e':'#ef4444';
  const lWR=m.n_long?(m.long_wins/m.n_long*100).toFixed(1):0;const sWR=m.n_short?(m.short_wins/m.n_short*100).toFixed(1):0;
  return html`<div>
    <div class="terminal-bar"><span class="tb-brand">ALGO · TERMINAL</span><div class="tb-sep"></div><span class="tb-pill active">${meta.strategies||'Strategy'}</span><span class="tb-pill">${meta.symbols||'Symbols'}</span><span class="tb-pill">${meta.timeframe||'—'}</span><span class="tb-pill">${meta.n} trades</span><span class="tb-pill">${meta.date_from} → ${meta.date_to}</span><div class="tb-right"><${Clock}/><div class="tb-dot" title="System active"></div></div></div>
    <div class="page-wrap">
      <div class="tab-nav"><button class=${'tab-btn'+(tab==='performance'?' active':'')} onclick=${()=>setTab('performance')}>Performance</button><button class=${'tab-btn'+(tab==='candles'?' active':'')} onclick=${()=>setTab('candles')}>Candles</button></div>
      <div style=${{display:tab==='performance'?'block':'none'}}>
        <${SBlock} title=${'Overall Performance · '+meta.timeframe+' · '+meta.n+' trades · '+meta.date_from+' → '+meta.date_to}>
          <div class="cards">
            <${Card} lbl="Total Return" val=${(pPos?'+':'')+fP(m.total_profit)} valStyle=${{color:pPos?'#3fb950':'#f85149'}} sub=${(pPos?'▲ ':'▼ ')+Math.abs(m.profit_pct)+'%'}/>
            <${Card} lbl="Final Capital" val=${fC(m.end_cap)} valStyle=${{color:'#2f81f7'}} sub=${'Started '+fC(m.start_cap)}/>
            <${Card} lbl="Peak Capital" val=${fC(m.peak_cap)} valStyle=${{color:'#d29922'}} sub="Max capital ever reached"/>
            <${Card} lbl="Win Rate" val=${m.win_rate+'%'} valStyle=${{color:wrC}} sub=${m.nw+'W · '+m.nl+'L · '+meta.n+' total'}/>
            <${Card} lbl="Buy (Long) Trades" val=${m.n_long} valStyle=${{color:'#3fb950'}} sub=${m.long_wins+'W · '+m.long_losses+'L · '+lWR+'% win rate'}/>
            <${Card} lbl="Sell (Short) Trades" val=${m.n_short} valStyle=${{color:'#f85149'}} sub=${m.short_wins+'W · '+m.short_losses+'L · '+sWR+'% win rate'}/>
            <${Card} lbl="Profit Factor" val=${m.pf_display} valStyle=${{color:pfC}} sub="Gross win ÷ gross loss"/>
            <${Card} lbl="Avg Profit / Win" val=${'+$'+m.avg_win.toFixed(2)} valStyle=${{color:'#3fb950'}} sub=${'Over '+m.nw+' winning trades'}/>
            <${Card} lbl="Avg Loss / Loss" val=${'$'+m.avg_loss.toFixed(2)} valStyle=${{color:'#f85149'}} sub=${'Over '+m.nl+' losing trades'}/>
            <${Card} lbl="Avg P&L / Trade" val=${(m.avg_all>=0?'+$':'-$')+Math.abs(m.avg_all).toFixed(2)} valStyle=${{color:m.avg_all>=0?'#3fb950':'#f85149'}} sub="Expectancy per trade"/>
            <${Card} lbl="Biggest Win" val=${'+$'+m.biggest_win.profit.toFixed(2)} valStyle=${{color:'#3fb950'}} sub=${m.biggest_win.time.slice(0,10)+' · '+m.biggest_win.symbol}/>
            <${Card} lbl="Biggest Loss" val=${'$'+m.biggest_loss.profit.toFixed(2)} valStyle=${{color:'#f85149'}} sub=${m.biggest_loss.time.slice(0,10)+' · '+m.biggest_loss.symbol}/>
            <${Card} lbl="Max Drawdown" val=${m.max_dd+'%'} valStyle=${{color:'#f85149'}} sub="Peak-to-trough"/>
            <${Card} lbl="Max Win Streak" val=${m.max_ws} valStyle=${{color:'#3fb950'}} sub="Consecutive wins ↗" className="streak-card" onClick=${()=>setStreak({type:'win',trades:win_streak_trades})}/>
            <${Card} lbl="Max Loss Streak" val=${m.max_ls} valStyle=${{color:'#f85149'}} sub="Consecutive losses ↘" className="streak-card" onClick=${()=>setStreak({type:'loss',trades:loss_streak_trades})}/>
            <${Card} lbl="Current Streak" val=${m.cur_s+' '+m.cur_t.toUpperCase()} valStyle=${{color:stH}} sub="Most recent run"/>
          </div>
        </${SBlock}>
        ${symbols_data.length?html`<${SBlock} title="Symbol Breakdown"><div class="sym-cards">${symbols_data.map(s=>html`<${SymCard} key=${s.symbol} s=${s}/>`)} </div></${SBlock}>`:null}
        <${SBlock} title="Equity & P&L Charts"><div class="charts"><div class="chart-box"><${EqChart} labels=${eq_labels} data=${eq_data}/></div><div class="chart-box"><div class="chart-lbl">Trade P&L</div><${PlChart} profits=${profits}/></div></div></${SBlock}>
        <${SBlock} title="Monthly Performance"><div class="tbl-box" style=${{marginBottom:0}}><div class="month-calendar">${monthly_rows.map(r=>html`<${CalCell} key=${r.month} row=${r} field="month"/>`)}</div></div></${SBlock}>
        <${SBlock} title="Weekly Performance"><div class="tbl-box" style=${{marginBottom:0}}><div class="month-calendar">${weekly_rows.map(r=>html`<${CalCell} key=${r.week} row=${r} field="week"/>`)}</div></div></${SBlock}>
        ${symbols_data.length?html`<${SBlock} title="Symbol Performance Summary"><div class="sym-table-wrap" style=${{marginBottom:0}}><table class="sym-tbl"><thead><tr><th>Symbol</th><th>Trades</th><th>Wins</th><th>Losses</th><th>Win Rate</th><th>P&L</th><th>Return %</th></tr></thead><tbody>${symbols_data.map(s=>{const p=s.profit>=0;const wc=s.win_rate>=50?'#22c55e':'#ef4444';const c=sClr(s.symbol);return html`<tr key=${s.symbol}><td><span class="sym-badge" style=${{color:c,borderColor:c+'55',background:c+'18'}}>${s.symbol}</span></td><td style=${{color:'#60a5fa'}}>${s.trades}</td><td style=${{color:'#22c55e'}}>${s.wins}</td><td style=${{color:'#ef4444'}}>${s.losses}</td><td style=${{color:wc}}>${s.win_rate}%</td><td style=${{color:p?'#22c55e':'#ef4444'}}>${fP(s.profit)}</td><td style=${{color:p?'#22c55e':'#ef4444'}}>${(p?'▲ ':'▼ ')+Math.abs(s.profit_pct).toFixed(2)+'%'}</td></tr>`; })}</tbody><tfoot><tr><td style=${{color:'#94a3b8'}}>TOTAL</td><td style=${{color:'#60a5fa'}}>${symbols_data.reduce((a,s)=>a+s.trades,0)}</td><td style=${{color:'#22c55e'}}>${symbols_data.reduce((a,s)=>a+s.wins,0)}</td><td style=${{color:'#ef4444'}}>${symbols_data.reduce((a,s)=>a+s.losses,0)}</td><td style=${{color:wrC}}>${m.win_rate}%</td><td style=${{color:pPos?'#22c55e':'#ef4444'}}>${fP(m.total_profit)}</td><td style=${{color:'#475569'}}>—</td></tr></tfoot></table></div></${SBlock}>`:null}
        <${SBlock} title="Trade Log"><div class="tbl-box" style=${{marginBottom:0}}><${TradeLog} rows=${rows}/></div></${SBlock}>
      </div>
      <div style=${{display:tab==='candles'?'block':'none'}}><${CandlesPane} visible=${tab==='candles'}/></div>
    </div>
    ${streak?html`<${StreakModal} type=${streak.type} trades=${streak.trades} onClose=${()=>setStreak(null)}/>`:null}
  </div>`;
}
render(html`<${App}/>`,document.getElementById('root'));
}
</script>
</body>
</html>"""



if __name__ == "__main__":
    build_dashboard()
