"""
File watcher — auto-reruns the backtest whenever watched files change.

Watched paths:
  src/algoTrading/config.yaml   ← strategy presets, lot sizes, filters
  src/algoTrading/config.py     ← symbol, timeframe, capital, dates
  src/algoTrading/strategies/   ← all strategy .py files

On any change:
  1. 2-second debounce (avoids double-trigger on fast saves)
  2. Runs: python -m algoTrading.main_backtest  (inside src/)
  3. trade_data.csv is updated
  4. TradesConsumer WebSocket detects the new mtime → pushes to dashboard
  5. Dashboard refreshes automatically

Django auto-reload also restarts the server when config.yaml changes
(wired up in apps.py via autoreload_started signal).
"""

import os
import sys
import time
import threading
import subprocess
from pathlib import Path

_SRC  = Path(__file__).resolve().parent.parent          # .../src/
_ALGO = _SRC / "algoTrading"

# ── Files / directories to watch ──────────────────────────────────────────────
_WATCH_FILES = [
    _ALGO / "config.yaml",
    _ALGO / "config.py",
]
_WATCH_DIRS = [
    _ALGO / "strategies",       # any *.py inside
    _ALGO / "backtest",         # engine.py, metrics.py
]

_lock       = threading.Lock()
_last_run   = 0.0
_DEBOUNCE   = 2.5   # seconds — ignore repeated saves within this window
_running_bt = False


def _collect_mtimes() -> dict[str, float]:
    """Return {filepath: mtime} for every watched file."""
    mtimes: dict[str, float] = {}

    for f in _WATCH_FILES:
        if f.exists():
            mtimes[str(f)] = f.stat().st_mtime

    for d in _WATCH_DIRS:
        if d.is_dir():
            for f in d.glob("*.py"):
                mtimes[str(f)] = f.stat().st_mtime

    return mtimes


def _trigger_backtest(changed_names: list[str]) -> None:
    global _last_run, _running_bt

    with _lock:
        now = time.time()
        if now - _last_run < _DEBOUNCE:
            return
        if _running_bt:
            print("[watcher] Backtest already running, skipping trigger")
            return
        _last_run   = now
        _running_bt = True

    print(f"\n[watcher] Change detected: {', '.join(changed_names)}")
    print("[watcher] Re-running backtest…\n")

    try:
        result = subprocess.run(
            [sys.executable, "-m", "algoTrading.main_backtest"],
            cwd=_SRC,
            timeout=600,          # 10-minute hard timeout
        )
        if result.returncode == 0:
            print("\n[watcher] Backtest complete — dashboard will refresh")
        else:
            print(f"\n[watcher] Backtest exited with code {result.returncode}")
    except subprocess.TimeoutExpired:
        print("\n[watcher] Backtest timed out after 10 minutes")
    except Exception as exc:
        print(f"\n[watcher] Error running backtest: {exc}")
    finally:
        with _lock:
            _running_bt = False


def _watch_loop() -> None:
    prev = _collect_mtimes()

    while True:
        time.sleep(1)
        try:
            curr    = _collect_mtimes()
            changed = [
                Path(k).name
                for k, v in curr.items()
                if prev.get(k) != v
            ]
            # Also detect newly created files
            new = [Path(k).name for k in curr if k not in prev]
            changed += new

            if changed:
                prev = curr
                threading.Thread(
                    target=_trigger_backtest,
                    args=(changed,),
                    daemon=True,
                    name="auto-backtest",
                ).start()
            else:
                # Update prev for any deleted files too
                prev = curr

        except Exception as exc:
            print(f"[watcher] poll error: {exc}")


def start() -> None:
    """Start the file-watcher daemon thread. Safe to call multiple times."""
    t = threading.Thread(target=_watch_loop, daemon=True, name="file-watcher")
    t.start()
    watched = [f.name for f in _WATCH_FILES] + [f"{d.name}/*.py" for d in _WATCH_DIRS]
    print(f"[watcher] Started — watching: {', '.join(watched)}")
