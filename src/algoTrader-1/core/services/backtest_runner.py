"""
Launches algoTrading's real backtest pipeline (MT5 fetch -> strategy signal
generation -> BacktestEngine -> trade_data.csv) from the "Run Backtest"
button in this Django UI -- equivalent to running
`python -m algoTrading.main_backtest` from a terminal in src/algoTrading/,
once per selected timeframe.

Each timeframe runs as a SEPARATE PROCESS, not an in-process function call,
on purpose: MetaTrader5's Python module holds one connection per OS process
(see mt5_connection.py's docstring -- CandleService's live dashboard
depends on that single shared connection staying up). main_backtest.py
calls mt5.shutdown() after fetching; doing that in-process would silently
kill the live dashboard's own MT5 connection out from under it. A
subprocess gets its own independent MT5 session, so a backtest run can
freely connect/fetch/shutdown without touching the live connection at all.

Multiple selected timeframes run IN PARALLEL (up to MAX_PARALLEL_TIMEFRAMES
at once, via a thread pool where each worker thread owns one subprocess),
not sequentially -- each timeframe is an independent MT5 session doing its
own fetch + backtest, and MT5 comfortably supports several simultaneous
client connections (this is exactly what already happens between this
tool and the live dashboard's own persistent connection). Running N
timeframes in parallel is roughly N times faster wall-clock than one at a
time, bounded by CPU/MT5-throughput rather than being purely sequential.

Parallel writes to shared files needed a real fix, not just a note: two
timeframes fetching the same symbol at once both used to funnel through
ohlcv_{symbol}.csv (and trade_data.csv) as an intermediate/alias path --
concurrent writers to the same filename is a genuine corruption risk. See
main_backtest.py / fetch_mt5.py: every shared alias is now written via a
temp-file-then-os.replace() pattern (atomic on both Windows and POSIX), and
the per-timeframe files (ohlcv_{symbol}_{TF}.csv, trade_data_{TF}.csv) have
inherently unique filenames so they were never at risk.

Strategy/symbol/timeframe/bars are overridable per-run via BT_* env vars
(see algoTrading/config.py) so the UI's timeframe checkboxes and strategy
checkboxes can launch a one-off backtest without editing that file --
omitting an override just falls back to config.py's own default.

The child process's own stdout is forced to UTF-8 (PYTHONIOENCODING/
PYTHONUTF8 env vars): Windows' default console codepage (cp1252) can't
encode the emoji main_backtest.py/mt5_connector.py print (checkmarks,
crosses), which crashed the child itself with a UnicodeEncodeError before
it ever reached MT5 -- not just a decoding issue on our reading end.
"""

from __future__ import annotations

import concurrent.futures
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[3]  # .../src
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

import algoTrading.config as _config_module  # noqa: E402
from algoTrading.main_backtest import STRATEGY_MAP  # noqa: E402

# NOTE: imported as a MODULE, not `from algoTrading.config import Config` --
# settings_service.py reloads algoTrading.config in-place after a Settings
# save, and only a live module reference (Config always re-read as
# _config_module.Config below) picks that up. A `from ... import Config`
# binding would freeze on the class object that existed at import time.

AVAILABLE_STRATEGIES = list(STRATEGY_MAP.keys())
AVAILABLE_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]

# How many timeframe subprocesses may run at once. Each one fetches tens of
# thousands of bars over its own MT5 session and runs the full strategy
# signal-generation loop -- uncapped parallelism across all 7 timeframes at
# once would be needlessly heavy on both the MT5 terminal and CPU for
# limited extra speedup once you're past a handful running concurrently.
MAX_PARALLEL_TIMEFRAMES = int(os.environ.get("BT_MAX_PARALLEL", "4"))

_lock = threading.Lock()
_state: dict = {
    "status": "idle",  # idle | running | done | error | cancelled
    "log": [],
    "started_at": None,
    "finished_at": None,
    "returncode": None,
    "timeframes": [],            # the full list requested for this run
    "current_timeframes": [],    # which ones are actively running right now
    "timeframe_results": {},     # {timeframe: returncode} as each one finishes
}
_current_processes: dict[str, subprocess.Popen] = {}
_cancel_requested = False


def get_current_config() -> dict:
    """Whatever's currently in algoTrading/config.py (i.e. what a run
    launches with if the UI doesn't override it), plus the pick-lists the
    UI renders its timeframe checkboxes / strategy checkboxes from."""
    cfg = _config_module.Config
    return {
        "symbol": cfg.SYMBOL,
        "strategy": cfg.STRATEGY,
        "timeframe": cfg.TIMEFRAME,
        "bars": cfg.BARS,
        "date_from": cfg.DATE_FROM,
        "date_to": cfg.DATE_TO,
        "rr": cfg.RR,
        "tp_mode": cfg.TP_MODE,
        "risk_per_trade": cfg.RISK_PER_TRADE,
        "risk_mode": cfg.RISK_MODE,
        "risk_amount": cfg.RISK_AMOUNT,
        "contract_size": cfg.CONTRACT_SIZE,
        "max_lot_size": cfg.MAX_LOT_SIZE,
        "initial_capital": cfg.INITIAL_CAPITAL,
        "available_strategies": AVAILABLE_STRATEGIES,
        "available_timeframes": AVAILABLE_TIMEFRAMES,
        "max_parallel_timeframes": MAX_PARALLEL_TIMEFRAMES,
    }


def _append_log(line: str) -> None:
    line = line.rstrip("\n")
    if not line:
        return
    with _lock:
        _state["log"].append(line)
        # Bounded -- this is a live progress feed for the UI, not a permanent record.
        if len(_state["log"]) > 800:
            del _state["log"][: len(_state["log"]) - 800]


# Sentinel returncode meaning "never started" -- a queued timeframe whose
# turn came up after cancel had already been requested. Distinct from any
# real process exit code (which are always >= 0 on success/failure, or
# negative on POSIX signal termination -- but this whole tool only targets
# Windows subprocesses, so -1 is unambiguous here).
_SKIPPED = -1


def _run_one_timeframe(
    timeframe: str,
    strategies: list[str] | None,
    symbol: str | None,
    bars: int | None,
    date_from: str | None,
    date_to: str | None,
    risk_mode: str | None = None,
    risk_amount: float | None = None,
    max_lot_size: float | None = None,
) -> int:
    with _lock:
        if _cancel_requested:
            return _SKIPPED

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["BT_TIMEFRAME"] = timeframe
    if strategies:
        env["BT_STRATEGY"] = ",".join(strategies)
    if symbol:
        env["BT_SYMBOL"] = symbol
    if risk_mode:
        env["BT_RISK_MODE"] = risk_mode
    if risk_amount is not None:
        env["BT_RISK_AMOUNT"] = str(risk_amount)
    if max_lot_size is not None:
        env["BT_MAX_LOT_SIZE"] = str(max_lot_size)
    if date_from:
        # A date range takes over from BARS entirely for this run (see
        # fetch_mt5.py) -- BT_BARS is irrelevant when BT_DATE_FROM is set,
        # so it's deliberately not sent alongside it.
        env["BT_DATE_FROM"] = date_from
        if date_to:
            env["BT_DATE_TO"] = date_to
    elif bars:
        env["BT_BARS"] = str(bars)

    process = subprocess.Popen(
        [sys.executable, "-m", "algoTrading.main_backtest"],
        cwd=str(_SRC_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
    )
    with _lock:
        _current_processes[timeframe] = process
        _state["current_timeframes"] = sorted(_current_processes.keys())

    assert process.stdout is not None
    try:
        for line in process.stdout:
            stripped = line.rstrip("\n")
            # Prefixed so several timeframes' interleaved output stays
            # attributable -- without this, four processes' logs arriving
            # concurrently would read as one scrambled stream.
            _append_log(f"[{timeframe}] {stripped}" if stripped else "")
    except Exception as exc:  # noqa: BLE001 -- must not leave the run stuck
        _append_log(f"[{timeframe}] [log stream error: {exc}]")
    returncode = process.wait()

    with _lock:
        _current_processes.pop(timeframe, None)
        _state["current_timeframes"] = sorted(_current_processes.keys())
    return returncode


def _run_parallel(
    timeframes: list[str],
    strategies: list[str] | None,
    symbol: str | None,
    bars: int | None,
    date_from: str | None,
    date_to: str | None,
    risk_mode: str | None = None,
    risk_amount: float | None = None,
    max_lot_size: float | None = None,
) -> None:
    max_workers = max(1, min(len(timeframes), MAX_PARALLEL_TIMEFRAMES))
    range_note = f", range {date_from} -> {date_to or 'now'}" if date_from else ""
    _append_log(
        f"\n===== Running {len(timeframes)} timeframe(s), up to {max_workers} at once: "
        f"{', '.join(timeframes)}{range_note} ====="
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_tf = {
            pool.submit(_run_one_timeframe, tf, strategies, symbol, bars, date_from, date_to, risk_mode, risk_amount, max_lot_size): tf
            for tf in timeframes
        }
        for future in concurrent.futures.as_completed(future_to_tf):
            tf = future_to_tf[future]
            try:
                returncode = future.result()
            except Exception as exc:  # noqa: BLE001 -- a worker thread must not crash the batch
                _append_log(f"[{tf}] [worker error: {exc}]")
                returncode = 1

            if returncode == _SKIPPED:
                _append_log(f"[{tf}] skipped (cancelled before it started).")
                continue

            with _lock:
                _state["timeframe_results"][tf] = returncode
            _append_log(f"[{tf}] finished ({'OK' if returncode == 0 else 'FAILED'}, exit code {returncode})")

    with _lock:
        was_cancelled = _cancel_requested
        results = _state["timeframe_results"]
        all_ok = len(results) == len(timeframes) and all(rc == 0 for rc in results.values())
        _state["current_timeframes"] = []
        _state["status"] = "cancelled" if was_cancelled else ("done" if all_ok else "error")
        _state["returncode"] = 0 if all_ok and not was_cancelled else 1
        _state["finished_at"] = time.time()


def cancel_backtest() -> dict:
    """Stops the in-flight run: kills every currently-running subprocess
    and prevents any not-yet-started (queued) timeframe from launching.
    Already-completed timeframes in this run keep their saved
    trade_data_{TF}.csv results -- cancelling only stops what hasn't
    finished yet."""
    global _cancel_requested
    with _lock:
        if _state["status"] != "running":
            return {**_state, "config": get_current_config()}
        _cancel_requested = True
        procs = list(_current_processes.values())

    for proc in procs:
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception as exc:  # noqa: BLE001 -- best-effort; process may have just exited
                _append_log(f"[cancel: terminate failed: {exc}]")

    return {**_state, "config": get_current_config()}


def _parse_date(value: str | None, field: str) -> str | None:
    if not value:
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"{field} must be YYYY-MM-DD, got {value!r}")
    return value


def start_backtest(
    strategies: list[str] | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    timeframes: list[str] | None = None,
    bars: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    risk_mode: str | None = None,
    risk_amount: float | None = None,
    max_lot_size: float | None = None,
) -> dict:
    """Starts a background run (up to MAX_PARALLEL_TIMEFRAMES subprocesses
    at once) if one isn't already in flight. Returns the current state
    either way -- the caller (the API view) doesn't need to special-case
    "already running".

    Pass either `timeframes` (a list, for multi-timeframe runs) or the
    older single `timeframe` -- both optional; omitting both falls back to
    config.py's default. `strategies`/`symbol`/`bars` override config.py
    for every timeframe in this run. `date_from`/`date_to` (YYYY-MM-DD,
    UTC) fetch that exact calendar range instead of the last `bars`
    candles -- `date_to` defaults to now when `date_from` is given alone.
    `risk_mode` ("percent" | "fixed") / `risk_amount` (dollars, only used
    when risk_mode="fixed") / `max_lot_size` (real lots, safety ceiling on
    either mode) override config.py's position-sizing for every strategy
    in this run -- see Config.RISK_MODE's / Config.MAX_LOT_SIZE's docstrings."""
    global _cancel_requested
    tf_list = timeframes if timeframes else ([timeframe] if timeframe else [])

    with _lock:
        if _state["status"] == "running":
            return {**_state, "config": get_current_config()}

        if strategies:
            unknown = [s for s in strategies if s not in AVAILABLE_STRATEGIES]
            if unknown:
                _state.update(status="error", log=[f"Unknown strategy: {', '.join(unknown)}"], finished_at=time.time())
                return {**_state, "config": get_current_config()}

        if risk_mode and risk_mode not in ("percent", "fixed"):
            _state.update(status="error", log=[f"Unknown risk_mode: {risk_mode!r} (must be 'percent' or 'fixed')"], finished_at=time.time())
            return {**_state, "config": get_current_config()}

        if max_lot_size is not None and max_lot_size <= 0:
            _state.update(status="error", log=[f"max_lot_size must be positive, got {max_lot_size!r}"], finished_at=time.time())
            return {**_state, "config": get_current_config()}

        unknown_tf = [tf for tf in tf_list if tf not in AVAILABLE_TIMEFRAMES]
        if unknown_tf:
            _state.update(status="error", log=[f"Unknown timeframe: {', '.join(unknown_tf)}"], finished_at=time.time())
            return {**_state, "config": get_current_config()}

        try:
            date_from = _parse_date(date_from, "date_from")
            date_to = _parse_date(date_to, "date_to")
        except ValueError as exc:
            _state.update(status="error", log=[str(exc)], finished_at=time.time())
            return {**_state, "config": get_current_config()}

        if date_to and not date_from:
            _state.update(status="error", log=["date_to given without date_from"], finished_at=time.time())
            return {**_state, "config": get_current_config()}

        if date_from and date_to and date_to < date_from:
            _state.update(status="error", log=["date_to must not be before date_from"], finished_at=time.time())
            return {**_state, "config": get_current_config()}

        effective_timeframes = tf_list or [_config_module.Config.TIMEFRAME]
        _cancel_requested = False
        _state.update(
            status="running", log=[], started_at=time.time(), finished_at=None, returncode=None,
            timeframes=effective_timeframes, current_timeframes=[], timeframe_results={},
        )

    threading.Thread(
        target=_run_parallel,
        args=(effective_timeframes, strategies, symbol, bars, date_from, date_to, risk_mode, risk_amount, max_lot_size),
        daemon=True,
    ).start()
    return {**_state, "config": get_current_config()}


def get_status() -> dict:
    with _lock:
        return {**_state, "config": get_current_config()}
