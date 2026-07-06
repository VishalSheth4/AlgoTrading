"""
live_bot_service.py  —  Django-managed live trading bot.

Runs the same trading logic as algoTrading/main.py but as a Django
background thread so you don't need a separate process.  Control it
via the /api/livebot/* REST endpoints from the dashboard.

  start_live_bot()   → launch bot thread
  stop_live_bot()    → signal clean shutdown
  get_bot_state()    → current status dict
  get_bot_log(n)     → last n log lines
"""

import math
import sys
import threading
import time
import logging
from datetime import datetime, date, timedelta
from pathlib import Path

import pandas as pd

# ── Path resolution ────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent   # src/trading/
_SRC  = _HERE.parent                      # src/
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

LOG_PATH      = _SRC / "algoTrading" / "data" / "live_trading.log"
MAGIC         = 234567
POLL_INTERVAL = 60      # seconds between signal scans
BARS          = 500     # bars to fetch per symbol
MIN_SL_DIST   = 0.10    # minimum SL distance in price units
MAX_LOT       = 10.0    # absolute lot cap
MAX_LOG_LINES = 300     # in-memory log buffer size

TIMEFRAME_MAP_STR = {
    "M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15", "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
}

# ── Global state ───────────────────────────────────────────────────────────────
_bot_state: dict = {
    "running":     False,
    "started":     None,
    "stopped":     None,
    "scan_count":  0,
    "last_scan":   None,
    "last_signal": None,
    "symbols":     [],
    "strategy":    None,
    "timeframe":   None,
    "balance":     None,
    "equity":      None,
    "error":       None,
    "log_lines":   [],
}
_state_lock  = threading.Lock()
_stop_event  = threading.Event()
_bot_thread: threading.Thread | None = None


# ── In-memory log handler ──────────────────────────────────────────────────────
class _MemHandler(logging.Handler):
    def emit(self, record):
        msg = self.format(record)
        with _state_lock:
            _bot_state["log_lines"].append(msg)
            if len(_bot_state["log_lines"]) > MAX_LOG_LINES:
                _bot_state["log_lines"].pop(0)


def _make_logger() -> logging.Logger:
    LOG_PATH.parent.mkdir(exist_ok=True)
    fmt     = "%(asctime)s | %(levelname)-8s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    logger = logging.getLogger("LiveBot")
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


# ── Strategy registry (mirrors main.py) ───────────────────────────────────────
def _load_strategy_map() -> dict:
    from algoTrading.strategies.moving_average import MovingAverageStrategy
    from algoTrading.strategies.supertrend_strategy import SupertrendStrategy
    from algoTrading.strategies.engulfing_strategy import EngulfingStrategy
    from algoTrading.strategies.green_dollar import GreenDollarStrategy
    from algoTrading.strategies.engulfing_consolidation import EngulfingConsolidationStrategy
    from algoTrading.strategies.SupertrendEngulfingReversalStrategy import (
        Mark2Strategy, SupertrendEngulfingReversalStrategy,
    )
    from algoTrading.strategies.engulfing_reversal import EngulfingReversalStrategy
    from algoTrading.strategies.mark_dollar_supertrend import MarkDollarSuperTrendStrategy
    from algoTrading.strategies.rsi_engulfing_strategy import RSIEngulfingStrategy
    from algoTrading.strategies.SupertrendCounterFlip_X1 import SupertrendCounterFlipX1Strategy
    from algoTrading.strategies.SimpleICT1H5mFVGStrategy import SimpleICT1H5mFVGStrategy
    from algoTrading.strategies.RSIBuySellStrategy import RSIBuySellStrategy
    from algoTrading.strategies.RSIEMADoubleCrossStrategy import RSIEMADoubleCrossStrategy
    from algoTrading.strategies.SupertrendTouchSellStrategy import SupertrendTouchSellStrategy
    from algoTrading.strategies.SessionStrategy import SessionStrategy

    return {
        "engulfing":                     EngulfingStrategy,
        "green_dollar":                  GreenDollarStrategy,
        "ma":                            MovingAverageStrategy,
        "supertrend":                    SupertrendStrategy,
        "engulfing_consolidation":       EngulfingConsolidationStrategy,
        "engulfing_reversal":            EngulfingReversalStrategy,
        "mark2":                         Mark2Strategy,
        "supertrend_engulfing_reversal": SupertrendEngulfingReversalStrategy,
        "mark_dollar_supertrend":        MarkDollarSuperTrendStrategy,
        "rsi_engulfing":                 RSIEngulfingStrategy,
        "SupertrendCounterFlip_X1":      SupertrendCounterFlipX1Strategy,
        "ict_simple_1h5m_fvg":           SimpleICT1H5mFVGStrategy,
        "RSIBuySellStrategy":            RSIBuySellStrategy,
        "RSIEMADoubleCrossStrategy":     RSIEMADoubleCrossStrategy,
        "SupertrendTouchSell":           SupertrendTouchSellStrategy,
        "session_strategy":              SessionStrategy,
    }


# ── Bot main loop ──────────────────────────────────────────────────────────────
def _bot_loop():
    import MetaTrader5 as mt5
    from algoTrading.config import Config
    from algoTrading.core.mt5_connector import connect, shutdown

    log = _make_logger()

    symbols     = [s.strip() for s in Config.SYMBOL.split(",")   if s.strip()]
    strat_names = [s.strip() for s in Config.STRATEGY.split(",") if s.strip()]
    tf_str      = Config.TIMEFRAME

    with _state_lock:
        _bot_state.update({
            "symbols":   symbols,
            "strategy":  Config.STRATEGY,
            "timeframe": tf_str,
            "error":     None,
        })

    log.info("=" * 60)
    log.info("  LIVE BOT  —  starting (Django-managed)")
    log.info(f"  Strategy  : {Config.STRATEGY}")
    log.info(f"  Symbols   : {', '.join(symbols)}")
    log.info(f"  Timeframe : {tf_str}")
    log.info(f"  Poll      : {POLL_INTERVAL}s")
    log.info(f"  Magic #   : {MAGIC}")
    log.info("=" * 60)

    if not connect():
        err = "MT5 connect failed — is MetaTrader5 terminal open and logged in?"
        log.error(err)
        with _state_lock:
            _bot_state.update({"running": False, "error": err,
                                "stopped": datetime.now().strftime("%H:%M:%S")})
        return

    STRATEGY_MAP = _load_strategy_map()

    tf_attr = TIMEFRAME_MAP_STR.get(tf_str)
    if tf_attr is None:
        err = f"Unknown timeframe '{tf_str}'"
        log.error(err)
        shutdown()
        with _state_lock:
            _bot_state.update({"running": False, "error": err,
                                "stopped": datetime.now().strftime("%H:%M:%S")})
        return
    tf = getattr(mt5, tf_attr)

    strategies = {}
    for name in strat_names:
        cls = STRATEGY_MAP.get(name)
        if cls is None:
            log.warning(f"Unknown strategy '{name}' — skipping")
            continue
        try:
            strategies[name] = cls()
            log.info(f"  Loaded strategy: {name}")
        except Exception as exc:
            log.error(f"  Failed to load '{name}': {exc}")

    if not strategies:
        err = "No valid strategies loaded — check config.yaml"
        log.error(err)
        shutdown()
        with _state_lock:
            _bot_state.update({"running": False, "error": err,
                                "stopped": datetime.now().strftime("%H:%M:%S")})
        return

    log.info(f"Bot ready — {len(strategies)} strategies on {len(symbols)} symbols")
    _stop_event.clear()

    last_bar_times: dict = {}

    try:
        while not _stop_event.is_set():
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log.info(f"── Scan [{now}] " + "─" * 36)

            # Account info
            info = mt5.account_info()
            if info:
                log.info(f"Balance: ${info.balance:,.2f} | Equity: ${info.equity:,.2f} | Profit: ${info.profit:+,.2f}")
                balance = float(info.balance)
                with _state_lock:
                    _bot_state["balance"] = round(info.balance, 2)
                    _bot_state["equity"]  = round(info.equity, 2)
            else:
                balance = 0.0

            # Daily loss cap
            max_daily = getattr(Config, "MAX_DAILY_LOSSES", None)
            if max_daily:
                from_dt = datetime.combine(date.today(), datetime.min.time())
                to_dt   = datetime.now() + timedelta(hours=1)
                deals   = mt5.history_deals_get(from_dt, to_dt) or []
                losses  = sum(
                    1 for d in deals
                    if d.magic == MAGIC and d.profit < 0
                    and d.entry == mt5.DEAL_ENTRY_OUT
                )
                if losses >= max_daily:
                    log.info(f"Daily loss cap hit ({losses}/{max_daily}) — skipping scan")
                    _stop_event.wait(POLL_INTERVAL)
                    continue

            # Log open positions from this bot
            positions = mt5.positions_get() or []
            bot_pos = [p for p in positions if p.magic == MAGIC]
            if bot_pos:
                for p in bot_pos:
                    d = "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL"
                    log.info(f"  Open | {p.symbol} {d} lot={p.volume:.2f} "
                             f"P&L=${p.profit:+.2f} ticket={p.ticket}")
            else:
                log.info("  Positions: none open from this bot")

            # Scan symbols
            for symbol in symbols:
                if _stop_event.is_set():
                    break

                # Skip symbol if position already open
                sym_positions = mt5.positions_get(symbol=symbol) or []
                if any(p.magic == MAGIC for p in sym_positions):
                    log.debug(f"  {symbol}: position open — skipping entry scan")
                    continue

                rates = mt5.copy_rates_from_pos(symbol, tf, 0, BARS)
                if rates is None or len(rates) == 0:
                    log.warning(f"  {symbol}: no bars from MT5")
                    continue

                df = pd.DataFrame(rates)
                df["time"] = pd.to_datetime(df["time"], unit="s")

                if len(df) < 10:
                    log.warning(f"  {symbol}: insufficient bars ({len(df)})")
                    continue

                last_closed_time = df.iloc[-2]["time"]

                if last_bar_times.get(symbol) == last_closed_time:
                    log.debug(f"  {symbol}: no new closed candle — skipping")
                    continue

                signal_taken = False
                for strat_name, strategy in strategies.items():
                    try:
                        sig_df = strategy.generate_signals(df)
                    except Exception as exc:
                        log.error(f"  {symbol} [{strat_name}] generate_signals error: {exc}")
                        continue

                    last   = sig_df.iloc[-2]
                    signal = int(last.get("signal", 0))
                    if signal == 0:
                        continue

                    sl    = float(last["sl"])
                    tp    = float(last["tp"])
                    entry = float(df.iloc[-2]["close"])

                    sl_dist = abs(entry - sl)
                    if sl_dist < MIN_SL_DIST:
                        log.warning(f"  {symbol} [{strat_name}]: SL too tight (dist={sl_dist:.5f})")
                        continue

                    # Lot sizing from live balance
                    sig_risk = last.get("risk_per_trade", float("nan"))
                    if not (pd.isna(sig_risk) or sig_risk == 0):
                        risk = float(sig_risk)
                    else:
                        risk = float(getattr(Config, "RISK_PER_TRADE", 0.01))

                    cs_map   = getattr(Config, "CONTRACT_SIZES", {})
                    cs_def   = getattr(Config, "DEFAULT_CONTRACT_SIZE", 100000)
                    contract = float(cs_map.get(symbol, cs_def))

                    raw_lot = (balance * risk) / (sl_dist * contract)
                    lot     = min(max(0.01, math.floor(raw_lot / 0.01) * 0.01), MAX_LOT)

                    dir_str = "BUY" if signal == 1 else "SELL"
                    log.info(
                        f"  SIGNAL | {symbol} [{strat_name}] {dir_str} | "
                        f"candle={str(last_closed_time)[:16]} | "
                        f"entry={entry:.5f} | SL={sl:.5f} | TP={tp:.5f} | lot={lot:.2f}"
                    )

                    # Place order
                    tick     = mt5.symbol_info_tick(symbol)
                    sym_info = mt5.symbol_info(symbol)
                    if not tick or not sym_info:
                        log.error(f"  {symbol}: cannot get tick/symbol info")
                        continue

                    digits     = sym_info.digits
                    order_type = mt5.ORDER_TYPE_BUY if signal == 1 else mt5.ORDER_TYPE_SELL
                    price      = tick.ask if signal == 1 else tick.bid

                    if sym_info.filling_mode & mt5.SYMBOL_FILLING_FOK:
                        filling = mt5.ORDER_FILLING_FOK
                    elif sym_info.filling_mode & mt5.SYMBOL_FILLING_IOC:
                        filling = mt5.ORDER_FILLING_IOC
                    else:
                        filling = mt5.ORDER_FILLING_RETURN

                    req = {
                        "action":       mt5.TRADE_ACTION_DEAL,
                        "symbol":       symbol,
                        "volume":       round(float(lot), 2),
                        "type":         order_type,
                        "price":        price,
                        "sl":           round(sl, digits),
                        "tp":           round(tp, digits),
                        "deviation":    20,
                        "magic":        MAGIC,
                        "comment":      f"AlgoBot:{Config.STRATEGY}",
                        "type_time":    mt5.ORDER_TIME_GTC,
                        "type_filling": filling,
                    }
                    result = mt5.order_send(req)

                    if result.retcode == mt5.TRADE_RETCODE_DONE:
                        log.info(
                            f"  ORDER PLACED | {symbol} {dir_str} | "
                            f"lot={lot:.2f} | price={price:.{digits}f} | "
                            f"SL={sl:.{digits}f} | TP={tp:.{digits}f} | "
                            f"ticket={result.order}"
                        )
                        signal_taken = True
                        with _state_lock:
                            _bot_state["last_signal"] = (
                                f"{symbol} {dir_str} @ {price:.{digits}f}"
                            )
                    else:
                        log.error(
                            f"  ORDER FAILED | {symbol} | "
                            f"retcode={result.retcode} | {result.comment}"
                        )
                    break  # first-wins: one strategy per symbol per scan

                last_bar_times[symbol] = last_closed_time

                if not signal_taken:
                    log.info(f"  {symbol}: no signal on {str(last_closed_time)[:16]}")

            with _state_lock:
                _bot_state["scan_count"] += 1
                _bot_state["last_scan"]   = datetime.now().strftime("%H:%M:%S")

            log.info(f"Scan done (#{_bot_state['scan_count']}). Sleeping {POLL_INTERVAL}s ...")
            _stop_event.wait(POLL_INTERVAL)

    except Exception as exc:
        log.error(f"Bot crashed: {exc}", exc_info=True)
        with _state_lock:
            _bot_state["error"] = str(exc)
    finally:
        try:
            shutdown()
        except Exception:
            pass
        with _state_lock:
            _bot_state.update({
                "running": False,
                "stopped": datetime.now().strftime("%H:%M:%S"),
            })
        log.info("Live bot stopped cleanly.")


# ── Public API ─────────────────────────────────────────────────────────────────

def start_live_bot() -> tuple[bool, str]:
    """
    Launch the live bot in a daemon thread.
    Returns (True, "started") or (False, reason).
    """
    global _bot_thread
    with _state_lock:
        if _bot_state["running"]:
            return False, "already_running"
        _bot_state.update({
            "running":    True,
            "started":    datetime.now().strftime("%H:%M:%S"),
            "stopped":    None,
            "scan_count": 0,
            "last_scan":  None,
            "last_signal": None,
            "error":      None,
            "log_lines":  [],
        })

    _stop_event.clear()
    _bot_thread = threading.Thread(target=_bot_loop, daemon=True, name="live-bot")
    _bot_thread.start()
    print("[live-bot] Started")
    return True, "started"


def stop_live_bot() -> tuple[bool, str]:
    """Signal the bot to stop. Returns immediately — shutdown may take up to POLL_INTERVAL s."""
    with _state_lock:
        if not _bot_state["running"]:
            return False, "not_running"
    _stop_event.set()
    print("[live-bot] Stop signal sent")
    return True, "stopping"


def get_bot_state() -> dict:
    with _state_lock:
        s = dict(_bot_state)
        s.pop("log_lines", None)   # returned separately
    return s


def get_bot_log(n: int = 100) -> list[str]:
    with _state_lock:
        return list(_bot_state["log_lines"][-n:])
