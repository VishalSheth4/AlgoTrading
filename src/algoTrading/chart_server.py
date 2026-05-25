"""
chart_server.py — HTTP server for live OHLCV + Supertrend data.

Usage:
    cd src/algoTrading
    python chart_server.py          # port 8765 (default)
    python chart_server.py 9000     # custom port

Endpoints:
    GET /              → serves data/dashboard.html
    GET /ohlcv?limit=N → JSON {"ohlcv": [...], "supertrend": [...], "live": {...}}
    GET /status        → JSON {"live": {...}}
    GET /healthz       → JSON {"status": "ok"}

Live feed:
    If MetaTrader5 is installed and the terminal is running, a background thread
    fetches fresh bars every FETCH_INTERVAL seconds and writes live_data.csv.
    /ohlcv reads from live_data.csv when the feed is active, otherwise falls
    back to sample_data.csv.
"""

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd

BASE           = Path(__file__).resolve().parent
SAMPLE_CSV     = BASE / "data" / "sample_data.csv"
LIVE_CSV       = BASE / "data" / "live_data.csv"
DASHBOARD_HTML = BASE / "data" / "dashboard.html"
TRADE_CSV      = BASE / "data" / "trade_data.csv"

# ── Static JS library cache ───────────────────────────────────────────────────
STATIC_LIBS: dict[str, tuple[str, Path]] = {
    "preact.js":       ("https://unpkg.com/preact@10/dist/preact.umd.js",
                        BASE / "data" / "_preact.min.js"),
    "preact-hooks.js": ("https://unpkg.com/preact@10/hooks/dist/hooks.umd.js",
                        BASE / "data" / "_preact-hooks.min.js"),
    "htm.js":          ("https://unpkg.com/htm@3/dist/htm.umd.js",
                        BASE / "data" / "_htm.min.js"),
    "chartjs.js":      ("https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js",
                        BASE / "data" / "_chartjs.min.js"),
    "lwc.js":          ("https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js",
                        BASE / "data" / "_lwc.min.js"),
}


def _serve_lib(name: str) -> bytes | None:
    """Return cached JS library bytes, downloading on first call."""
    if name not in STATIC_LIBS:
        return None
    url, cache_path = STATIC_LIBS[name]
    if cache_path.exists() and cache_path.stat().st_size > 1000:
        return cache_path.read_bytes()
    try:
        import urllib.request
        print(f"[static] downloading {name} …")
        with urllib.request.urlopen(url, timeout=20) as r:
            data = r.read()
        cache_path.write_bytes(data)
        print(f"[static] cached {name} ({len(data)//1024}KB)")
        return data
    except Exception as exc:
        print(f"[static] failed to download {name}: {exc}")
        return None

FETCH_INTERVAL = 15   # seconds between MT5 polls

# ── Live feed state ───────────────────────────────────────────────────────────

_live = {
    "active":      False,
    "symbol":      None,
    "timeframe":   None,
    "last_update": None,
    "error":       None,
}

# ── Config (try to read from project Config, fall back to safe defaults) ──────

try:
    _src = str(Path(__file__).resolve().parents[1])
    if _src not in sys.path:
        sys.path.insert(0, _src)
    from algoTrading.config import Config
    _SYMBOL    = Config.SYMBOL
    _TIMEFRAME = Config.TIMEFRAME
    _BARS      = min(getattr(Config, "BARS", 2000), 5000)
except Exception:
    _SYMBOL    = "XAUUSD"
    _TIMEFRAME = "M15"
    _BARS      = 2000


# ── MT5 live feed (background thread) ─────────────────────────────────────────

def _mt5_live_feed():
    """
    Background daemon thread.
    Connects to MetaTrader5, polls for fresh OHLCV bars every FETCH_INTERVAL
    seconds, and writes them to live_data.csv.
    Falls back to waiting if MT5 is unreachable.
    """
    try:
        import MetaTrader5 as mt5
    except ImportError:
        _live["error"] = "MetaTrader5 package not installed — using sample_data.csv"
        print(f"[live] {_live['error']}")
        return

    TF_MAP = {
        "M1":  mt5.TIMEFRAME_M1,
        "M5":  mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1":  mt5.TIMEFRAME_H1,
        "H4":  mt5.TIMEFRAME_H4,
        "D1":  mt5.TIMEFRAME_D1,
    }
    tf = TF_MAP.get(_TIMEFRAME)
    if tf is None:
        _live["error"] = f"Unknown timeframe '{_TIMEFRAME}'"
        print(f"[live] {_live['error']}")
        return

    _live["symbol"]    = _SYMBOL
    _live["timeframe"] = _TIMEFRAME

    while True:
        try:
            # ── Connect ───────────────────────────────────────────────────
            if not mt5.initialize():
                err = mt5.last_error()
                _live["active"] = False
                _live["error"]  = f"MT5 not running ({err})"
                print(f"[live] {_live['error']} — retrying in {FETCH_INTERVAL}s")
                time.sleep(FETCH_INTERVAL)
                continue

            # ── Make symbol visible ───────────────────────────────────────
            info = mt5.symbol_info(_SYMBOL)
            if info is None:
                _live["active"] = False
                _live["error"]  = f"Symbol '{_SYMBOL}' not found in MT5"
                print(f"[live] {_live['error']}")
                mt5.shutdown()
                time.sleep(FETCH_INTERVAL)
                continue

            if not info.visible:
                mt5.symbol_select(_SYMBOL, True)

            # ── Check market open via latest tick ─────────────────────────
            tick = mt5.symbol_info_tick(_SYMBOL)
            market_open = (
                tick is not None
                and (time.time() - tick.time) < 600   # tick within last 10 min
            )

            # ── Fetch bars ────────────────────────────────────────────────
            rates = mt5.copy_rates_from_pos(_SYMBOL, tf, 0, _BARS)
            if rates is None or len(rates) == 0:
                _live["active"] = False
                _live["error"]  = "No data returned from MT5"
                print(f"[live] {_live['error']}")
                mt5.shutdown()
                time.sleep(FETCH_INTERVAL)
                continue

            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            df.to_csv(LIVE_CSV, index=False)

            _live["active"]      = True
            _live["error"]       = None
            _live["last_update"] = time.strftime("%H:%M:%S")
            _live["market_open"] = market_open

            status = "MARKET OPEN" if market_open else "market closed"
            print(f"[live] {_SYMBOL} {_TIMEFRAME} — {len(df)} bars — {status} — {_live['last_update']}")

            mt5.shutdown()

        except Exception as exc:
            _live["active"] = False
            _live["error"]  = str(exc)
            print(f"[live] error: {exc}")

        time.sleep(FETCH_INTERVAL)


# ── Supertrend ────────────────────────────────────────────────────────────────

def _supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0):
    close = df["close"].values
    high  = df["high"].values
    low   = df["low"].values
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

    times_unix = df["time"].apply(lambda x: int(x.timestamp())).values
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


# ── Trade markers ─────────────────────────────────────────────────────────────

def _load_trade_markers(ohlcv: list) -> list:
    import bisect
    csv_p = BASE / "data" / "trade_data.csv"
    if not csv_p.exists() or not ohlcv:
        return []
    try:
        df = pd.read_csv(csv_p)
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
            return None   # outside visible window — skip
        idx = bisect.bisect_right(bar_times, ts) - 1
        return bar_times[max(idx, 0)]

    markers = []
    for _, row in df.iterrows():
        bt     = snap(int(row["ts"]))
        if bt is None:
            continue
        t      = row["type"]
        raw    = row.get("exit_reason", float("nan"))
        reason = str(raw) if pd.notna(raw) else ""

        if t == "BUY":
            markers.append({"time": bt, "position": "belowBar",
                            "color": "#26a69a", "shape": "arrowUp",
                            "text": "B", "size": 1})
        elif t == "SHORT":
            markers.append({"time": bt, "position": "aboveBar",
                            "color": "#ef5350", "shape": "arrowDown",
                            "text": "S", "size": 1})
        elif t == "SELL":
            if reason == "TP":
                markers.append({"time": bt, "position": "aboveBar",
                                "color": "#26a69a", "shape": "circle",
                                "text": "T", "size": 1})
            else:
                markers.append({"time": bt, "position": "belowBar",
                                "color": "#ef5350", "shape": "circle",
                                "text": "SL", "size": 1})
        elif t == "COVER":
            if reason == "TP":
                markers.append({"time": bt, "position": "belowBar",
                                "color": "#26a69a", "shape": "circle",
                                "text": "T", "size": 1})
            else:
                markers.append({"time": bt, "position": "aboveBar",
                                "color": "#ef5350", "shape": "circle",
                                "text": "SL", "size": 1})

    markers.sort(key=lambda m: m["time"])
    return markers


# ── Engulfing candle colors ───────────────────────────────────────────────────

def _mark_engulfing(ohlcv: list) -> None:
    for i in range(1, len(ohlcv)):
        prev, curr = ohlcv[i - 1], ohlcv[i]
        po, pc = prev["open"], prev["close"]
        co, cc = curr["open"], curr["close"]
        if pc < po and cc > co and co <= pc and cc >= po:
            curr["color"]       = "#ffffff"
            curr["borderColor"] = "#ffffff"
            curr["wickColor"]   = "#ffffff"
        elif pc > po and cc < co and co >= pc and cc <= po:
            curr["color"]       = "#000000"
            curr["borderColor"] = "#ef5350"
            curr["wickColor"]   = "#ef5350"


# ── Data loader ───────────────────────────────────────────────────────────────

def _available_symbols() -> dict:
    """Scan data dir for ohlcv_*.csv; return {SYMBOL: [tf, ...]} ordered M1→D1."""
    TF_ORDER = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN"]
    result: dict = {}
    for p in sorted((BASE / "data").glob("ohlcv_*.csv")):
        parts = p.stem.split("_")
        if len(parts) == 2:          # ohlcv_XAUUSD
            sym = parts[1]
            if sym not in result:
                result[sym] = []
            meta = p.with_suffix(".meta")
            if meta.exists():
                try:
                    import json as _j
                    m = _j.loads(meta.read_text(encoding="utf-8"))
                    base_tf = m.get("timeframe", "D1")
                    if base_tf not in result[sym]:
                        result[sym].insert(0, base_tf)
                except Exception:
                    pass
        elif len(parts) == 3:        # ohlcv_XAUUSD_M15
            sym, t = parts[1], parts[2]
            if sym not in result:
                result[sym] = []
            if t not in result[sym]:
                result[sym].append(t)
    for sym in result:
        result[sym].sort(key=lambda t: TF_ORDER.index(t) if t in TF_ORDER else 99)
    return result


def _active_csv(symbol: str = "", tf: str = "") -> Path:
    """Prefer ohlcv_{sym}_{tf}.csv → ohlcv_{sym}.csv → live → sample."""
    sym = (symbol or _SYMBOL).upper().strip()
    tf  = (tf or "").upper().strip()
    if tf:
        p = BASE / "data" / f"ohlcv_{sym}_{tf}.csv"
        if p.exists():
            return p
    p = BASE / "data" / f"ohlcv_{sym}.csv"
    if p.exists():
        return p
    return LIVE_CSV if (_live["active"] and LIVE_CSV.exists()) else SAMPLE_CSV


def _load(limit: int = 0, symbol: str = "", tf: str = ""):
    """Load OHLCV data. limit=0 means all available bars."""
    csv_p = _active_csv(symbol, tf)
    df = pd.read_csv(csv_p, usecols=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time").reset_index(drop=True)

    if limit > 0:
        df = df.tail(limit).reset_index(drop=True)

    st_all = _supertrend(df)
    cutoff = int(df["time"].iloc[0].timestamp())

    ohlcv = [
        {
            "time":  int(row["time"].timestamp()),
            "open":  round(float(row["open"]),  2),
            "high":  round(float(row["high"]),  2),
            "low":   round(float(row["low"]),   2),
            "close": round(float(row["close"]), 2),
        }
        for _, row in df.iterrows()
    ]
    _mark_engulfing(ohlcv)
    supertrend = [p for p in st_all if p["time"] >= cutoff]
    markers    = _load_trade_markers(ohlcv)
    return ohlcv, supertrend, markers


# ── Trade analytics ───────────────────────────────────────────────────────────

def _current_timeframe() -> str:
    """Re-read Config.TIMEFRAME each call so dashboard reflects config changes."""
    try:
        from algoTrading.config import Config as _C
        return _C.TIMEFRAME
    except Exception:
        return _TIMEFRAME

def _calc_streaks(profits: list) -> tuple:
    max_win = max_loss = cur = 0
    cur_type = None
    for p in profits:
        if p > 0:
            cur = (cur + 1) if cur_type == "win" else 1
            cur_type = "win"
            max_win = max(max_win, cur)
        elif p < 0:
            cur = (cur + 1) if cur_type == "loss" else 1
            cur_type = "loss"
            max_loss = max(max_loss, cur)
    return max_win, max_loss, cur, cur_type or "none"


def _streak_trades(done: pd.DataFrame, profits: list, is_win: bool) -> list:
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
    rows = []
    for _, r in done.iloc[best_start:best_start + best_len].iterrows():
        lot_v = r.get("lot_size")
        rows.append({
            "time":     str(r["time"])[:16],
            "symbol":   str(r.get("symbol",   "")),
            "strategy": str(r.get("strategy", "—")),
            "dir":      "SHORT" if r["type"] == "COVER" else "LONG",
            "entry":    round(float(r.get("entry_price", 0)), 2),
            "exit":     round(float(r.get("exit_price",  0)), 2),
            "lot":      f"{float(lot_v):.2f}" if pd.notna(lot_v) else "—",
            "profit":   round(float(r["profit"]), 2),
            "cap":      round(float(r["capital"]), 2),
        })
    return rows


def _compute_trade_analytics() -> dict:
    """Read trade_data.csv and return a complete analytics dict for /trades."""
    if not TRADE_CSV.exists():
        return {"error": "trade_data.csv not found"}

    df = pd.read_csv(TRADE_CSV)
    if df.empty:
        return {"error": "trade_data.csv is empty"}

    done = df[df["type"].isin(["SELL", "COVER"])].copy()
    done["profit"]  = pd.to_numeric(done["profit"],  errors="coerce")
    done["capital"] = pd.to_numeric(done["capital"], errors="coerce")
    done = done.dropna(subset=["profit", "capital"])
    done["time"] = pd.to_datetime(done["time"], errors="coerce")
    done = done.sort_values("time").reset_index(drop=True)

    if done.empty:
        return {"error": "No completed trades"}

    profits = done["profit"].tolist()
    caps    = done["capital"].tolist()
    times   = done["time"].tolist()

    wins   = done[done["profit"] > 0]
    losses = done[done["profit"] < 0]
    nw, nl = len(wins), len(losses)
    n      = len(done)
    outcome_n = nw + nl

    n_long  = int((done["type"] == "SELL").sum())
    n_short = int((done["type"] == "COVER").sum())

    long_done  = done[done["type"] == "SELL"]
    short_done = done[done["type"] == "COVER"]
    long_wins    = int((long_done["profit"] > 0).sum())
    long_losses  = int((long_done["profit"] < 0).sum())
    short_wins   = int((short_done["profit"] > 0).sum())
    short_losses = int((short_done["profit"] < 0).sum())

    start_cap    = caps[0] - profits[0]
    end_cap      = caps[-1]
    peak_cap     = round(float(done["capital"].max()), 2)
    total_profit = round(end_cap - start_cap, 2)
    profit_pct   = round(total_profit / start_cap * 100, 2) if start_cap else 0

    overall_win_rate = round(nw / outcome_n * 100, 1) if outcome_n else 0
    avg_win  = round(wins["profit"].mean(),   2) if nw else 0
    avg_loss = round(losses["profit"].mean(), 2) if nl else 0
    avg_all  = round(sum(profits) / len(profits), 2) if profits else 0

    gross_win  = float(wins["profit"].sum())        if nw else 0
    gross_loss = float(abs(losses["profit"].sum())) if nl else 0
    pf_val     = round(gross_win / gross_loss, 2) if gross_loss > 0 else 999
    pf_display = str(pf_val) if pf_val != 999 else "∞"

    eq     = done["capital"]
    dd     = (eq - eq.cummax()) / eq.cummax() * 100
    max_dd = round(float(dd.min()), 2)

    max_ws, max_ls, cur_s, cur_t = _calc_streaks(profits)
    win_streak_trades  = _streak_trades(done, profits, True)
    loss_streak_trades = _streak_trades(done, profits, False)

    date_from = str(times[0])[:16]
    date_to   = str(times[-1])[:16]

    def _best(grp):
        if grp.empty:
            return {"profit": 0, "time": "—", "symbol": "—", "strategy": "—", "dir": "—"}
        row = grp.loc[grp["profit"].abs().idxmax()]
        return {
            "profit":   round(float(row["profit"]), 2),
            "time":     str(row["time"])[:16],
            "symbol":   str(row.get("symbol",   "—")),
            "strategy": str(row.get("strategy", "—")),
            "dir":      "SHORT" if row["type"] == "COVER" else "LONG",
        }

    biggest_win  = _best(wins)
    biggest_loss = _best(losses)

    # Monthly breakdown (win_rate uses wins+losses denominator — consistent with overall)
    done["_month"] = done["time"].dt.to_period("M")
    monthly_rows = []
    for period, grp in done.groupby("_month"):
        mw  = int((grp["profit"] > 0).sum())
        ml_ = int((grp["profit"] < 0).sum())
        mt  = len(grp)
        mp  = round(float(grp["profit"].sum()), 2)
        first = grp.iloc[0]
        ms  = float(first["capital"]) - float(first["profit"])
        mpct = round(mp / ms * 100, 2) if ms else 0
        mwr  = round(mw / (mw + ml_) * 100, 1) if (mw + ml_) else 0

        trade_list = []
        for _, r in grp.iterrows():
            lot_v = r.get("lot_size")
            trade_list.append({
                "time":     str(r["time"])[:16],
                "symbol":   str(r.get("symbol",   "")),
                "strategy": str(r.get("strategy", "")),
                "dir":      "SHORT" if r["type"] == "COVER" else "LONG",
                "lot":      f"{float(lot_v):.2f}" if pd.notna(lot_v) else "—",
                "profit":   round(float(r["profit"]), 2),
                "label":    str(r.get("exit_label", r.get("exit_reason", ""))),
            })

        monthly_rows.append({
            "month":      str(period),
            "trades":     mt,
            "wins":       mw,
            "losses":     ml_,
            "profit":     mp,
            "profit_pct": mpct,
            "win_rate":   mwr,
            "trade_list": trade_list,
        })

    # Weekly breakdown
    done["_week"] = done["time"].dt.to_period("W")
    weekly_rows = []
    for period, grp in done.groupby("_week"):
        ww  = int((grp["profit"] > 0).sum())
        wl_ = int((grp["profit"] < 0).sum())
        wt  = len(grp)
        wp  = round(float(grp["profit"].sum()), 2)
        first = grp.iloc[0]
        ws  = float(first["capital"]) - float(first["profit"])
        wpct = round(wp / ws * 100, 2) if ws else 0
        wwr  = round(ww / (ww + wl_) * 100, 1) if (ww + wl_) else 0
        weekly_rows.append({
            "week":       str(period),
            "trades":     wt,
            "wins":       ww,
            "losses":     wl_,
            "profit":     wp,
            "profit_pct": wpct,
            "win_rate":   wwr,
        })

    # Per-symbol
    symbols_data = []
    if "symbol" in done.columns:
        for sym, grp in done.groupby("symbol", sort=True):
            sw  = int((grp["profit"] > 0).sum())
            sl_ = int((grp["profit"] < 0).sum())
            st_ = len(grp)
            sp  = round(float(grp["profit"].sum()), 2)
            swr = round(sw / (sw + sl_) * 100, 1) if (sw + sl_) else 0
            sc  = grp["capital"].tolist()
            sp_ = grp["profit"].tolist()
            ss  = sc[0] - sp_[0] if sc else 0
            spct = round(sp / ss * 100, 2) if ss else 0
            symbols_data.append({
                "symbol":     sym,
                "trades":     st_,
                "wins":       sw,
                "losses":     sl_,
                "profit":     sp,
                "win_rate":   swr,
                "profit_pct": spct,
            })

    # All closed trade rows
    rows = []
    for idx, (_, r) in enumerate(done.iterrows()):
        sl_v  = r.get("sl");  sl_s  = f"{float(sl_v):.2f}"  if pd.notna(sl_v)  else "—"
        tp_v  = r.get("tp");  tp_s  = f"{float(tp_v):.2f}"  if pd.notna(tp_v)  else "—"
        lot_v = r.get("lot_size"); lot_s = f"{float(lot_v):.2f}" if pd.notna(lot_v) else "—"
        rows.append({
            "num":      idx + 1,
            "time":     str(r["time"])[:16],
            "symbol":   str(r.get("symbol",   "")),
            "strategy": str(r.get("strategy", "")),
            "dir":      "SHORT" if r["type"] == "COVER" else "LONG",
            "entry":    round(float(r.get("entry_price", 0)), 2),
            "sl":       sl_s,
            "target":   tp_s,
            "exit":     round(float(r.get("exit_price",  0)), 2),
            "label":    str(r.get("exit_label", r.get("exit_reason", ""))),
            "lot":      lot_s,
            "profit":   round(float(r["profit"]), 2),
            "cap":      round(float(r["capital"]), 2),
        })

    eq_labels = ["Start"] + [str(t)[:16] for t in times]
    eq_data   = [round(start_cap, 2)] + [round(c, 2) for c in caps]

    strategies_str = ", ".join(sorted(done["strategy"].dropna().unique())) if "strategy" in done.columns else ""
    symbols_str    = ", ".join(sorted(done["symbol"].dropna().unique()))   if "symbol"   in done.columns else ""

    return {
        "meta": {
            "strategies": strategies_str,
            "symbols":    symbols_str,
            "timeframe":  _current_timeframe(),
            "n":          n,
            "date_from":  date_from[:10],
            "date_to":    date_to[:10],
        },
        "metrics": {
            "start_cap":    round(start_cap, 2),
            "end_cap":      round(end_cap, 2),
            "peak_cap":     peak_cap,
            "total_profit": total_profit,
            "profit_pct":   profit_pct,
            "win_rate":     overall_win_rate,
            "nw":           nw,
            "nl":           nl,
            "n_long":       n_long,
            "n_short":      n_short,
            "long_wins":    long_wins,
            "long_losses":  long_losses,
            "short_wins":   short_wins,
            "short_losses": short_losses,
            "avg_win":      avg_win,
            "avg_loss":     avg_loss,
            "avg_all":      avg_all,
            "pf_val":       pf_val,
            "pf_display":   pf_display,
            "max_dd":       max_dd,
            "max_ws":       max_ws,
            "max_ls":       max_ls,
            "cur_s":        cur_s,
            "cur_t":        cur_t,
            "biggest_win":  biggest_win,
            "biggest_loss": biggest_loss,
        },
        "monthly_rows":       monthly_rows,
        "weekly_rows":        weekly_rows,
        "symbols_data":       symbols_data,
        "rows":               rows,
        "eq_labels":          eq_labels,
        "eq_data":            eq_data,
        "profits":            profits,
        "win_streak_trades":  win_streak_trades,
        "loss_streak_trades": loss_streak_trades,
    }


# ── HTTP handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        qs     = parse_qs(parsed.query)

        if path == "/dashboard_hash":
            # Watch trade_data.csv mtime so the browser reloads after each backtest
            try:
                mtime = int(TRADE_CSV.stat().st_mtime * 1000) if TRADE_CSV.exists() else 0
            except Exception:
                mtime = 0
            self._json(200, {"hash": mtime})

        elif path.startswith("/static/"):
            name = path[8:]
            data = _serve_lib(name)
            if data:
                self.send_response(200)
                self.send_header("Content-Type",   "application/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control",  "max-age=86400")
                self._cors()
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404)
                self.end_headers()

        elif path == "/trades":
            try:
                self._json(200, _compute_trade_analytics())
            except Exception as exc:
                self._json(500, {"error": str(exc)})

        elif path == "/healthz":
            self._json(200, {"status": "ok", "live": dict(_live)})

        elif path == "/status":
            self._json(200, {"live": dict(_live)})

        elif path == "/symbols":
            try:
                self._json(200, _available_symbols())
            except Exception as exc:
                self._json(500, {"error": str(exc)})

        elif path == "/ohlcv":
            try:
                limit  = int(qs.get("limit", [0])[0])
                sym    = qs.get("sym", [""])[0].strip()
                tf_req = qs.get("tf",  [""])[0].strip()
                if limit < 0:
                    limit = 0
                ohlcv, supertrend, markers = _load(limit, sym, tf_req)
                self._json(200, {
                    "ohlcv":      ohlcv,
                    "supertrend": supertrend,
                    "markers":    markers,
                    "live":       dict(_live),
                })
            except Exception as exc:
                self._json(500, {"error": str(exc)})

        elif path in ("/", "/index.html"):
            if DASHBOARD_HTML.exists():
                body = DASHBOARD_HTML.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type",   "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                body = b"<h1>404 &mdash; run build_dashboard() first</h1>"
                self.send_response(404)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)

        else:
            self.send_response(404)
            self.end_headers()

    def _json(self, code: int, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control",  "no-cache")
        self._cors()
        self.end_headers()
        self.wfile.write(body)


# ── Entry point ───────────────────────────────────────────────────────────────

def run(port: int = 8765):
    # Start MT5 live feed in background daemon thread
    t = threading.Thread(target=_mt5_live_feed, daemon=True)
    t.start()
    print(f"[live] Starting feed for {_SYMBOL} {_TIMEFRAME} (poll every {FETCH_INTERVAL}s)")

    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"chart_server running on http://127.0.0.1:{port}")
    print(f"  Dashboard : http://127.0.0.1:{port}/")
    print(f"  OHLCV API : http://127.0.0.1:{port}/ohlcv?limit=1000")
    print(f"  Live status: http://127.0.0.1:{port}/status")
    print("  Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    run(port)
