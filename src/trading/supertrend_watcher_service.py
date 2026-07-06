"""
supertrend_watcher_service.py  —  XAUUSD SuperTrend watcher using MT5 data only.

All 8 timeframes are checked IN PARALLEL using ThreadPoolExecutor.
A separate live-price thread polls the current XAUUSD bid/ask every 5 seconds via MT5.
A Telegram alert fires whenever SuperTrend direction flips on any timeframe.

Control via REST:
  POST /api/supertrend/start
  POST /api/supertrend/stop
  GET  /api/supertrend/status
  GET  /api/supertrend/log?n=100
"""

import sys
import threading
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests
import pandas as pd

# ── Path so we can import algoTrading ─────────────────────────────────────────
_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# ── Config ─────────────────────────────────────────────────────────────────────
BOT_TOKEN           = "8442885474:AAGIxFfSWJNnykAu--el6cjLwCfC4XnLH1k"
SYMBOL              = "XAUUSD"
BARS                = 500            # bars to fetch per timeframe
CHECK_INTERVAL      = 60             # seconds between SuperTrend polls
LIVE_PRICE_INTERVAL = 5              # seconds between live price refreshes
ST_PERIOD           = 10
ST_MULT             = 3.0
MAX_LOG_LINES       = 300

# MT5 timeframe map
MT5_TIMEFRAMES: dict[str, str] = {
    "1m" : "TIMEFRAME_M1",
    "5m" : "TIMEFRAME_M5",
    "10m": "TIMEFRAME_M10",
    "15m": "TIMEFRAME_M15",
    "30m": "TIMEFRAME_M30",
    "1H" : "TIMEFRAME_H1",
    "4H" : "TIMEFRAME_H4",
    "1D" : "TIMEFRAME_D1",
}

LOG_PATH = _SRC / "algoTrading" / "data" / "supertrend_watcher.log"

# ── Global state ───────────────────────────────────────────────────────────────
_state: dict = {
    "running":          False,
    "started":          None,
    "stopped":          None,
    "check_count":      0,
    "last_check":       None,
    "last_alert":       None,
    "live_price":       None,
    "live_price_time":  None,
    "chat_id":          None,
    "trends":           {},
    "prices":           {},
    "error":            None,
    "log_lines":        [],
}
_state_lock      = threading.Lock()
_stop_event      = threading.Event()
_previous_trend: dict[str, int] = {}
_watcher_thread: threading.Thread | None = None
_price_thread:   threading.Thread | None = None


# ── Logger ─────────────────────────────────────────────────────────────────────
class _MemHandler(logging.Handler):
    def emit(self, record):
        msg = self.format(record)
        with _state_lock:
            _state["log_lines"].append(msg)
            if len(_state["log_lines"]) > MAX_LOG_LINES:
                _state["log_lines"].pop(0)


def _make_logger(name: str) -> logging.Logger:
    LOG_PATH.parent.mkdir(exist_ok=True)
    fmt     = "%(asctime)s | %(levelname)-8s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setFormatter(logging.Formatter(fmt, datefmt))
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter(fmt, datefmt))
    logger.addHandler(ch)

    mh = _MemHandler()
    mh.setFormatter(logging.Formatter(fmt, datefmt))
    logger.addHandler(mh)

    return logger


# ── Telegram ───────────────────────────────────────────────────────────────────
def _get_chat_id() -> int | None:
    try:
        r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates", timeout=10)
        data = r.json()
        if data.get("ok") and data["result"]:
            return data["result"][-1]["message"]["chat"]["id"]
    except Exception:
        pass
    return None


def _send_telegram(chat_id: int, text: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception:
        pass


# ── MT5 data helpers ───────────────────────────────────────────────────────────
def _fetch_ohlcv_mt5(tf_attr: str) -> pd.DataFrame:
    import MetaTrader5 as mt5
    tf    = getattr(mt5, tf_attr)
    rates = mt5.copy_rates_from_pos(SYMBOL, tf, 0, BARS)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"MT5 returned no data for {SYMBOL} {tf_attr}: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("time")
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
    return df[["Open", "High", "Low", "Close"]]


def _fetch_live_price_mt5() -> float | None:
    import MetaTrader5 as mt5
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick:
        return round((tick.bid + tick.ask) / 2, 2)
    return None


# ── SuperTrend ─────────────────────────────────────────────────────────────────
def _supertrend(df: pd.DataFrame, period: int = 10, mult: float = 3.0) -> pd.Series:
    hl2 = (df["High"] + df["Low"]) / 2
    tr  = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift()).abs(),
        (df["Low"]  - df["Close"].shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()

    upper = hl2 + mult * atr
    lower = hl2 - mult * atr

    trend    = pd.Series(1, index=df.index)
    final_ub = upper.copy()
    final_lb = lower.copy()

    for i in range(1, len(df)):
        prev_close = df["Close"].iloc[i - 1]
        final_ub.iloc[i] = (
            upper.iloc[i] if upper.iloc[i] < final_ub.iloc[i-1] or prev_close > final_ub.iloc[i-1]
            else final_ub.iloc[i-1]
        )
        final_lb.iloc[i] = (
            lower.iloc[i] if lower.iloc[i] > final_lb.iloc[i-1] or prev_close < final_lb.iloc[i-1]
            else final_lb.iloc[i-1]
        )
        if trend.iloc[i-1] == -1 and df["Close"].iloc[i] > final_ub.iloc[i]:
            trend.iloc[i] = 1
        elif trend.iloc[i-1] == 1 and df["Close"].iloc[i] < final_lb.iloc[i]:
            trend.iloc[i] = -1
        else:
            trend.iloc[i] = trend.iloc[i-1]

    return trend


# ── Per-timeframe worker ───────────────────────────────────────────────────────
def _check_one_tf(tf_name: str, tf_attr: str, chat_id: int | None) -> str:
    df      = _fetch_ohlcv_mt5(tf_attr)
    if len(df) < ST_PERIOD + 5:
        return f"{tf_name}: not enough bars ({len(df)})"

    trend   = _supertrend(df, ST_PERIOD, ST_MULT)
    current = int(trend.iloc[-1])
    price   = round(float(df["Close"].iloc[-1]), 2)
    label   = "BULLISH" if current == 1 else "BEARISH"

    with _state_lock:
        _state["trends"][tf_name] = label
        _state["prices"][tf_name] = price
        prev = _previous_trend.get(tf_name)

    if prev is None:
        with _state_lock:
            _previous_trend[tf_name] = current
        return f"{tf_name:4s}  Init → {label:8s}  Price: {price}"

    if current != prev:
        with _state_lock:
            _previous_trend[tf_name] = current
        arrow = "⬆️" if current == 1 else "⬇️"
        emoji = "🟢" if current == 1 else "🔴"
        msg = (
            f"{arrow} <b>XAUUSD SuperTrend Flip</b>\n"
            f"Timeframe : <b>{tf_name}</b>\n"
            f"Direction : <b>{emoji} {label}</b>\n"
            f"Price     : <b>{price}</b>\n"
            f"Time      : {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        if chat_id:
            _send_telegram(chat_id, msg)
        with _state_lock:
            _state["last_alert"] = f"{tf_name} → {label} @ {price}"
        return f"{tf_name:4s}  *** ALERT *** → {label:8s}  Price: {price}"

    return f"{tf_name:4s}  No change  {label:8s}  Price: {price}"


# ── Live price thread (MT5 tick, every 5s) ─────────────────────────────────────
def _live_price_loop():
    while not _stop_event.is_set():
        try:
            price = _fetch_live_price_mt5()
            if price:
                with _state_lock:
                    _state["live_price"]      = price
                    _state["live_price_time"] = datetime.now().strftime("%H:%M:%S")
        except Exception:
            pass
        _stop_event.wait(LIVE_PRICE_INTERVAL)


# ── Main watcher loop ──────────────────────────────────────────────────────────
def _watcher_loop():
    global _previous_trend
    import MetaTrader5 as mt5
    from algoTrading.core.mt5_connector import connect, shutdown

    log = _make_logger("SupertrendWatcher")
    _previous_trend = {}

    log.info("=" * 55)
    log.info("  XAUUSD SuperTrend Watcher (MT5) — starting")
    log.info(f"  Symbol     : {SYMBOL}")
    log.info(f"  Timeframes : {', '.join(MT5_TIMEFRAMES)}  [ALL PARALLEL]")
    log.info(f"  SuperTrend : period={ST_PERIOD}  mult={ST_MULT}")
    log.info(f"  ST check   : every {CHECK_INTERVAL}s")
    log.info(f"  Live price : every {LIVE_PRICE_INTERVAL}s  (MT5 tick)")
    log.info("=" * 55)

    # Connect to MT5
    if not connect():
        err = "MT5 connect failed — is MetaTrader5 terminal open and logged in?"
        log.error(err)
        with _state_lock:
            _state.update({"running": False, "error": err,
                           "stopped": datetime.now().strftime("%H:%M:%S")})
        return

    chat_id = _get_chat_id()
    if not chat_id:
        log.warning("No Telegram chat_id — send /start to @xau_vraj_2026_bot first")
    with _state_lock:
        _state["chat_id"] = chat_id

    if chat_id:
        _send_telegram(
            chat_id,
            "🚀 <b>XAUUSD SuperTrend Watcher started (MT5)</b>\n"
            f"Time      : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Symbol    : {SYMBOL}\n"
            f"Timeframes: {' | '.join(MT5_TIMEFRAMES)}\n"
            "⏳ Fetching initial trends...",
        )

    _stop_event.clear()
    is_first_run = True

    try:
        while not _stop_event.is_set():
            ts = datetime.now().strftime("%H:%M:%S")
            log.info(f"── Parallel MT5 check [{ts}] ──")

            with ThreadPoolExecutor(max_workers=len(MT5_TIMEFRAMES), thread_name_prefix="st-tf") as ex:
                futures = {
                    ex.submit(_check_one_tf, tf_name, tf_attr, chat_id): tf_name
                    for tf_name, tf_attr in MT5_TIMEFRAMES.items()
                }
                for future in as_completed(futures):
                    tf_name = futures[future]
                    try:
                        log.info(f"  {future.result()}")
                    except Exception as exc:
                        log.error(f"  {tf_name}: {exc}")

            with _state_lock:
                _state["check_count"] += 1
                _state["last_check"]   = datetime.now().strftime("%H:%M:%S")
                trends_snapshot = dict(_state["trends"])
                prices_snapshot = dict(_state["prices"])

            # Send full summary after first check
            if is_first_run and chat_id and trends_snapshot:
                is_first_run = False
                lines = []
                for tf in MT5_TIMEFRAMES:
                    label = trends_snapshot.get(tf, "—")
                    price = prices_snapshot.get(tf, "—")
                    emoji = "🟢" if label == "BULLISH" else "🔴"
                    lines.append(f"{emoji} <b>{tf:4s}</b>  {label:8s}  @ {price}")
                summary = (
                    f"📊 <b>XAUUSD SuperTrend — Current Status</b>\n"
                    f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
                    + "\n".join(lines)
                    + "\n\n⚡ You will be alerted on every trend flip."
                )
                _send_telegram(chat_id, summary)
                log.info("Initial trend summary sent to Telegram.")

            log.info(f"Done (check #{_state['check_count']}). Next in {CHECK_INTERVAL}s ...")
            _stop_event.wait(CHECK_INTERVAL)

    except Exception as exc:
        log.error(f"Watcher crashed: {exc}", exc_info=True)
        with _state_lock:
            _state["error"] = str(exc)
    finally:
        try:
            shutdown()
        except Exception:
            pass
        with _state_lock:
            _state.update({"running": False, "stopped": datetime.now().strftime("%H:%M:%S")})
        log.info("SuperTrend watcher stopped.")


# ── Public API ─────────────────────────────────────────────────────────────────
def start_watcher() -> tuple[bool, str]:
    global _watcher_thread, _price_thread
    with _state_lock:
        if _state["running"]:
            return False, "already_running"
        _state.update({
            "running":         True,
            "started":         datetime.now().strftime("%H:%M:%S"),
            "stopped":         None,
            "check_count":     0,
            "last_check":      None,
            "last_alert":      None,
            "live_price":      None,
            "live_price_time": None,
            "error":           None,
            "log_lines":       [],
            "trends":          {},
            "prices":          {},
        })

    _stop_event.clear()
    _watcher_thread = threading.Thread(target=_watcher_loop, daemon=True, name="st-watcher")
    _watcher_thread.start()
    _price_thread   = threading.Thread(target=_live_price_loop, daemon=True, name="st-price")
    _price_thread.start()
    print("[st-watcher] Started — MT5 SuperTrend + live tick threads running")
    return True, "started"


def stop_watcher() -> tuple[bool, str]:
    with _state_lock:
        if not _state["running"]:
            return False, "not_running"
    _stop_event.set()
    print("[st-watcher] Stop signal sent")
    return True, "stopping"


def get_watcher_state() -> dict:
    with _state_lock:
        s = dict(_state)
        s.pop("log_lines", None)
    return s


def get_watcher_log(n: int = 100) -> list[str]:
    with _state_lock:
        return list(_state["log_lines"][-n:])
