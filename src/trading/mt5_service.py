"""
mt5_service.py  —  shared MT5 live-feed state and analytics used by Django views.

This module is imported by trading/views.py and started via trading/apps.py.
It mirrors the logic in algoTrading/chart_server.py so Django can serve
all dashboard endpoints without running a second HTTP server.
"""

import sys
import time
import threading
from pathlib import Path

import numpy as np
import pandas as pd

# ── Path resolution ────────────────────────────────────────────────────────────
# Running from src/  →  BASE = src/algoTrading/
_HERE = Path(__file__).resolve().parent          # src/trading/
_SRC  = _HERE.parent                             # src/
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

BASE           = _SRC / "algoTrading"
SAMPLE_CSV     = BASE / "data" / "sample_data.csv"
LIVE_CSV       = BASE / "data" / "live_data.csv"
DASHBOARD_HTML = BASE / "data" / "dashboard.html"
TRADE_CSV      = BASE / "data" / "trade_data.csv"

# ── Config (safe defaults if algoTrading not yet importable) ───────────────────
try:
    from algoTrading.config import Config
    _SYMBOL    = Config.SYMBOL
    _TIMEFRAME = Config.TIMEFRAME
    _BARS      = min(getattr(Config, "BARS", 2000), 5000)
except Exception:
    _SYMBOL    = "XAUUSD"
    _TIMEFRAME = "M15"
    _BARS      = 2000

# ── JS library cache ───────────────────────────────────────────────────────────
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


def serve_lib(name: str) -> bytes | None:
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


FETCH_INTERVAL = 5   # bar refresh every 5 s when MT5 is live

# ── Live feed state ────────────────────────────────────────────────────────────
_live: dict = {
    "active":       False,
    "symbol":       None,
    "timeframe":    None,
    "last_update":  None,
    "error":        None,
    "price":        None,   # latest mid-price from MT5 tick
    "bid":          None,
    "ask":          None,
    "market_open":  False,
}
_feed_started = False
_feed_lock    = threading.Lock()


def _mt5_connect():
    """
    Initialize MT5 using credentials from Config.
    Returns True on success, False on failure.
    """
    try:
        import MetaTrader5 as mt5
        # Try with credentials first (works even when terminal is not logged in)
        try:
            from algoTrading.config import Config
            login    = int(Config.MT5_LOGIN)    if Config.MT5_LOGIN    else None
            password = str(Config.MT5_PASSWORD) if Config.MT5_PASSWORD else None
            server   = str(Config.MT5_SERVER)   if Config.MT5_SERVER   else None

            if login and password and server:
                ok = mt5.initialize(login=login, password=password, server=server)
            else:
                ok = mt5.initialize()
        except Exception:
            ok = mt5.initialize()

        if not ok:
            print(f"[mt5] initialize failed: {mt5.last_error()}")
        return ok
    except Exception as exc:
        print(f"[mt5] connect error: {exc}")
        return False


def _mt5_live_feed():
    try:
        import MetaTrader5 as mt5
    except ImportError:
        _live["error"] = "MetaTrader5 package not installed"
        print(f"[live] {_live['error']}")
        return

    TF_MAP = {
        "M1":  mt5.TIMEFRAME_M1,  "M5":  mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
        "H1":  mt5.TIMEFRAME_H1,  "H4":  mt5.TIMEFRAME_H4,
        "D1":  mt5.TIMEFRAME_D1,
    }
    tf = TF_MAP.get(_TIMEFRAME)
    if tf is None:
        _live["error"] = f"Unknown timeframe '{_TIMEFRAME}'"
        return

    _live["symbol"]    = _SYMBOL
    _live["timeframe"] = _TIMEFRAME

    while True:
        try:
            if not _mt5_connect():
                _live["active"] = False
                _live["error"]  = f"MT5 login failed — open MetaTrader5 terminal"
                time.sleep(FETCH_INTERVAL)
                continue

            # ── Ensure symbol is visible ─────────────────────────────────────
            info = mt5.symbol_info(_SYMBOL)
            if info is None:
                _live["active"] = False
                _live["error"]  = f"Symbol '{_SYMBOL}' not found in MT5"
                mt5.shutdown()
                time.sleep(FETCH_INTERVAL)
                continue
            if not info.visible:
                mt5.symbol_select(_SYMBOL, True)

            # ── Get latest tick price ────────────────────────────────────────
            tick = mt5.symbol_info_tick(_SYMBOL)
            if tick and tick.bid > 0:
                _live["price"]       = round((tick.bid + tick.ask) / 2, 2)
                _live["bid"]         = round(tick.bid, 2)
                _live["ask"]         = round(tick.ask, 2)
                _live["market_open"] = (time.time() - tick.time) < 600
            else:
                _live["market_open"] = False

            # ── Fetch latest OHLCV bars and save to live_data.csv ────────────
            rates = mt5.copy_rates_from_pos(_SYMBOL, tf, 0, _BARS)
            if rates is None or len(rates) == 0:
                _live["active"] = False
                _live["error"]  = "MT5 returned no bars"
                mt5.shutdown()
                time.sleep(FETCH_INTERVAL)
                continue

            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            df.to_csv(LIVE_CSV, index=False)

            _live["active"]      = True
            _live["error"]       = None
            _live["last_update"] = time.strftime("%H:%M:%S")

            status = "OPEN" if _live["market_open"] else "CLOSED"
            print(f"[mt5] {_SYMBOL} {_TIMEFRAME} | {len(df)} bars | "
                  f"price={_live['price']} | market {status}")
            mt5.shutdown()

        except Exception as exc:
            _live["active"] = False
            _live["error"]  = str(exc)
            print(f"[mt5] feed error: {exc}")
            try:
                mt5.shutdown()
            except Exception:
                pass

        time.sleep(FETCH_INTERVAL)


def start_live_feed():
    """Start the MT5 feed thread once (safe to call from AppConfig.ready)."""
    global _feed_started
    with _feed_lock:
        if _feed_started:
            return
        _feed_started = True
    t = threading.Thread(target=_mt5_live_feed, daemon=True, name="mt5-live-feed")
    t.start()
    print("[live] MT5 feed thread started")


# ── Supertrend ─────────────────────────────────────────────────────────────────
def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0):
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
    atr = pd.Series(tr).ewm(alpha=1/period, min_periods=period, adjust=False).mean().values

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


def mark_engulfing(ohlcv: list) -> None:
    for i in range(1, len(ohlcv)):
        prev, curr = ohlcv[i - 1], ohlcv[i]
        po, pc = prev["open"], prev["close"]
        co, cc = curr["open"], curr["close"]
        if pc < po and cc > co and co <= pc and cc >= po:
            curr.update({"color": "#ffffff", "borderColor": "#ffffff", "wickColor": "#ffffff"})
        elif pc > po and cc < co and co >= pc and cc <= po:
            curr.update({"color": "#000000", "borderColor": "#ef5350", "wickColor": "#ef5350"})


def load_trade_markers(ohlcv: list) -> list:
    import bisect
    if not TRADE_CSV.exists() or not ohlcv:
        return []
    try:
        df = pd.read_csv(TRADE_CSV)
    except Exception:
        return []
    df = df[df["type"].isin(["BUY", "SHORT", "SELL", "COVER"])].copy()
    if df.empty:
        return []
    df["ts"]   = pd.to_datetime(df["time"]).apply(lambda x: int(x.timestamp()))
    bar_times  = sorted(c["time"] for c in ohlcv)
    t_min, t_max = bar_times[0], bar_times[-1]

    def snap(ts):
        if ts < t_min or ts > t_max:
            return None
        idx = bisect.bisect_right(bar_times, ts) - 1
        return bar_times[max(idx, 0)]

    markers = []
    for _, row in df.iterrows():
        bt = snap(int(row["ts"]))
        if bt is None:
            continue
        t      = row["type"]
        reason = str(row.get("exit_reason", "")) if pd.notna(row.get("exit_reason")) else ""
        label  = str(row.get("exit_label",  "")) if pd.notna(row.get("exit_label"))  else reason
        ep     = row.get("entry_price")
        ep_s   = f" {float(ep):.1f}" if pd.notna(ep) else ""

        if t == "BUY":
            markers.append({
                "time": bt, "position": "belowBar",
                "color": "#26a69a", "shape": "arrowUp",
                "text": f"BUY{ep_s}", "size": 2,
            })
        elif t == "SHORT":
            markers.append({
                "time": bt, "position": "aboveBar",
                "color": "#ef5350", "shape": "arrowDown",
                "text": f"SELL{ep_s}", "size": 2,
            })
        elif t == "SELL":
            is_tp = label in ("TP", "R:R", "ST")
            markers.append({
                "time": bt, "position": "aboveBar",
                "color": "#26a69a" if is_tp else "#ef5350",
                "shape": "circle",
                "text": label if label else ("TP" if is_tp else "SL"),
                "size": 1,
            })
        elif t == "COVER":
            is_tp = label in ("TP", "R:R", "ST")
            markers.append({
                "time": bt, "position": "belowBar",
                "color": "#26a69a" if is_tp else "#ef5350",
                "shape": "circle",
                "text": label if label else ("TP" if is_tp else "SL"),
                "size": 1,
            })
    markers.sort(key=lambda m: m["time"])
    return markers


def active_csv() -> Path:
    return LIVE_CSV if (_live["active"] and LIVE_CSV.exists()) else SAMPLE_CSV


def load_ohlcv(limit: int = 0):
    csv_p = active_csv()
    df = pd.read_csv(csv_p, usecols=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time").reset_index(drop=True)
    if limit > 0:
        df = df.tail(limit).reset_index(drop=True)

    st_all = supertrend(df)
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
    mark_engulfing(ohlcv)
    st_filtered = [p for p in st_all if p["time"] >= cutoff]
    markers     = load_trade_markers(ohlcv)
    return ohlcv, st_filtered, markers


# ── Trade analytics ────────────────────────────────────────────────────────────
def _calc_streaks(profits):
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


def _streak_trades(done, profits, is_win):
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
        rows.append({
            "time":   str(r["time"])[:16],
            "symbol": str(r.get("symbol", "")),
            "dir":    "SHORT" if r["type"] == "COVER" else "LONG",
            "entry":  round(float(r.get("entry_price", 0)), 2),
            "exit":   round(float(r.get("exit_price",  0)), 2),
            "profit": round(float(r["profit"]), 2),
            "cap":    round(float(r["capital"]), 2),
        })
    return rows


def compute_trade_analytics() -> dict:
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

    profits   = done["profit"].tolist()
    caps      = done["capital"].tolist()
    times     = done["time"].tolist()

    wins   = done[done["profit"] > 0]
    losses = done[done["profit"] < 0]
    nw, nl = len(wins), len(losses)
    n      = len(done)
    outcome_n = nw + nl

    long_done  = done[done["type"] == "SELL"]
    short_done = done[done["type"] == "COVER"]
    n_long     = len(long_done)
    n_short    = len(short_done)
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

    eq    = done["capital"]
    dd    = (eq - eq.cummax()) / eq.cummax() * 100
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

    # Monthly (win_rate = wins / (wins + losses))
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
                "lot":      f"{float(lot_v):.4f}" if pd.notna(lot_v) else "—",
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

    # Weekly
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
                "symbol": sym, "trades": st_, "wins": sw,
                "losses": sl_, "profit": sp, "win_rate": swr, "profit_pct": spct,
            })

    # Build entry_time lookup: pair BUY/SHORT rows with SELL/COVER rows sequentially
    all_df = pd.read_csv(TRADE_CSV)
    entries_df = all_df[all_df["type"].isin(["BUY", "SHORT"])].copy()
    entries_df["time"] = pd.to_datetime(entries_df["time"], errors="coerce")
    entries_df = entries_df.sort_values("time").reset_index(drop=True)
    entry_times = entries_df["time"].tolist()

    # All trade rows
    rows = []
    for idx, (_, r) in enumerate(done.iterrows()):
        sl_v  = r.get("sl");  sl_s  = f"{float(sl_v):.2f}"  if pd.notna(sl_v)  else "—"
        tp_v  = r.get("tp");  tp_s  = f"{float(tp_v):.2f}"  if pd.notna(tp_v)  else "—"
        lot_v = r.get("lot_size"); lot_s = f"{float(lot_v):.4f}" if pd.notna(lot_v) else "—"
        entry_t = str(entry_times[idx])[:16] if idx < len(entry_times) else "—"
        rows.append({
            "num":        idx + 1,
            "entry_time": entry_t,
            "time":       str(r["time"])[:16],
            "symbol":     str(r.get("symbol",   "")),
            "strategy":   str(r.get("strategy", "")),
            "dir":        "SHORT" if r["type"] == "COVER" else "LONG",
            "entry":      round(float(r.get("entry_price", 0)), 2),
            "sl":         sl_s,
            "target":     tp_s,
            "exit":       round(float(r.get("exit_price",  0)), 2),
            "label":      str(r.get("exit_label", r.get("exit_reason", ""))),
            "lot":        lot_s,
            "profit":     round(float(r["profit"]), 2),
            "cap":        round(float(r["capital"]), 2),
        })

    eq_labels = ["Start"] + [str(t)[:16] for t in times]
    eq_data   = [round(start_cap, 2)] + [round(c, 2) for c in caps]

    strategies_str = ", ".join(sorted(done["strategy"].dropna().unique())) if "strategy" in done.columns else ""
    symbols_str    = ", ".join(sorted(done["symbol"].dropna().unique()))   if "symbol"   in done.columns else ""

    return {
        "meta": {
            "strategies": strategies_str,
            "symbols":    symbols_str,
            "timeframe":  _TIMEFRAME,
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
            "biggest_win":  _best(wins),
            "biggest_loss": _best(losses),
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
