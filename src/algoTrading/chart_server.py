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

def _active_csv() -> Path:
    """Return live_data.csv when the MT5 feed is active, else sample_data.csv."""
    return LIVE_CSV if (_live["active"] and LIVE_CSV.exists()) else SAMPLE_CSV


def _load(limit: int = 0):
    """Load OHLCV data. limit=0 means all available bars."""
    csv_p = _active_csv()
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
            try:
                mtime = int(DASHBOARD_HTML.stat().st_mtime * 1000) if DASHBOARD_HTML.exists() else 0
            except Exception:
                mtime = 0
            self._json(200, {"hash": mtime})

        elif path == "/healthz":
            self._json(200, {"status": "ok", "live": dict(_live)})

        elif path == "/status":
            self._json(200, {"live": dict(_live)})

        elif path == "/ohlcv":
            try:
                limit = int(qs.get("limit", [0])[0])
                if limit < 0:
                    limit = 0   # 0 = all bars
                ohlcv, supertrend, markers = _load(limit)
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
