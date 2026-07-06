"""
watcher.py — Trade alert watcher.

Polls trade_data.csv every 2 seconds.
Fires an alert for every NEW row that appears:
  - BUY / SHORT  → Entry alert  (green)
  - SELL / COVER → Exit alert   (TP=green / SL=red / SESSION=yellow)

Also watches live MT5 tick for significant price moves (optional).

Started as a daemon thread from trading/apps.py.
"""

import time
import threading
import pandas as pd
from pathlib import Path

from algoTrading.alerts.notifier import broadcast

_BASE      = Path(__file__).resolve().parents[1]
_TRADE_CSV = _BASE / "data" / "trade_data.csv"
_POLL_S    = 2     # seconds between checks

_started = False
_lock    = threading.Lock()


# ── Message builders ───────────────────────────────────────────────────────────

def _entry_msg(row: pd.Series) -> tuple[str, str]:
    sym  = str(row.get("symbol",   "?"))
    dir_ = "BUY" if row.get("type") == "BUY" else "SELL SHORT"
    ep   = row.get("entry_price", row.get("close", "?"))
    sl   = row.get("sl",  "?")
    tp   = row.get("tp",  "?")
    st   = str(row.get("strategy", "")).replace("_", " ").title()
    t    = str(row.get("time", ""))[:16]

    emoji = "🟢" if row.get("type") == "BUY" else "🔴"
    title = f"{emoji} {dir_} {sym} @ {ep}"
    body  = (
        f"Symbol   : {sym}\n"
        f"Direction: {dir_}\n"
        f"Entry    : {ep}\n"
        f"Stop Loss: {sl}\n"
        f"Target   : {tp}\n"
        f"Strategy : {st}\n"
        f"Time     : {t}"
    )
    return title, body


def _exit_msg(row: pd.Series) -> tuple[str, str]:
    sym    = str(row.get("symbol", "?"))
    reason = str(row.get("exit_label", row.get("exit_reason", "EXIT"))).upper()
    ep     = row.get("entry_price", "?")
    ex     = row.get("exit_price",  "?")
    pnl    = row.get("profit", 0)
    t      = str(row.get("time", ""))[:16]
    st     = str(row.get("strategy", "")).replace("_", " ").title()

    if reason in ("TP", "R:R", "ST"):
        emoji = "✅"
    elif reason == "SL":
        emoji = "❌"
    elif reason == "SESSION":
        emoji = "⏱"
    else:
        emoji = "📤"

    pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
    title = f"{emoji} {reason} {sym} | {pnl_str}"
    body  = (
        f"Symbol   : {sym}\n"
        f"Result   : {reason}\n"
        f"Entry    : {ep}\n"
        f"Exit     : {ex}\n"
        f"P&L      : {pnl_str}\n"
        f"Strategy : {st}\n"
        f"Time     : {t}"
    )
    return title, body


# ── Watcher loop ───────────────────────────────────────────────────────────────

def _watch_loop() -> None:
    last_ids: set = set()   # track seen rows by (time, type, symbol)
    initialized   = False

    while True:
        time.sleep(_POLL_S)
        try:
            if not _TRADE_CSV.exists():
                continue

            df = pd.read_csv(_TRADE_CSV)
            if df.empty:
                continue

            # Build a unique key per row
            df["_key"] = (
                df["time"].astype(str) + "|" +
                df["type"].astype(str) + "|" +
                df.get("symbol", pd.Series(["?"] * len(df))).astype(str)
            )
            current_ids = set(df["_key"].tolist())

            if not initialized:
                # On first load: remember existing rows without alerting
                last_ids    = current_ids
                initialized = True
                print(f"[alert-watcher] Loaded {len(last_ids)} existing trades — watching for new ones")
                continue

            new_keys = current_ids - last_ids
            if not new_keys:
                continue

            # Alert for each new row
            new_rows = df[df["_key"].isin(new_keys)].copy()
            for _, row in new_rows.iterrows():
                t = str(row.get("type", ""))
                if t in ("BUY", "SHORT"):
                    title, body = _entry_msg(row)
                elif t in ("SELL", "COVER"):
                    title, body = _exit_msg(row)
                else:
                    continue
                broadcast(title, body)

            last_ids = current_ids

        except Exception as exc:
            print(f"[alert-watcher] Error: {exc}")


def start() -> None:
    """Start the trade alert watcher daemon thread. Safe to call multiple times."""
    global _started
    with _lock:
        if _started:
            return
        _started = True

    t = threading.Thread(target=_watch_loop, daemon=True, name="alert-watcher")
    t.start()
    print("[alert-watcher] Started — watching trade_data.csv for new signals")
