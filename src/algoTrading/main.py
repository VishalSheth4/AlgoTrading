"""
Live Trading Engine
───────────────────
• Polls MT5 every POLL_INTERVAL seconds
• Signals read from the last CLOSED candle (iloc[-2]) — never acts on an open bar
• One trade per symbol at a time — skips if a position is already open
• Daily loss cap (MAX_DAILY_LOSSES) enforced via MT5 deal history
• SL + TP set on the order — MT5 handles exits natively
• Full logging to console + data/live_trading.log
"""

import time
import logging
import pandas as pd
import MetaTrader5 as mt5
from datetime import datetime, date, timedelta
from pathlib import Path

from algoTrading.config import Config
from algoTrading.core.mt5_connector import connect, shutdown
from algoTrading.strategies.moving_average import MovingAverageStrategy
from algoTrading.strategies.supertrend_strategy import SupertrendStrategy
from algoTrading.strategies.engulfing_strategy import EngulfingStrategy
from algoTrading.strategies.green_dollar import GreenDollarStrategy
from algoTrading.strategies.engulfing_consolidation import EngulfingConsolidationStrategy
from algoTrading.strategies.mark2_strategy import Mark2Strategy
from algoTrading.strategies.engulfing_reversal import EngulfingReversalStrategy
from algoTrading.strategies.mark_dollar_supertrend import MarkDollarSuperTrendStrategy
from algoTrading.strategies.rsi_engulfing_strategy import RSIEngulfingStrategy

BASE = Path(__file__).resolve().parent
CONFIG_YAML = BASE / "config.yaml"


def load_risk_per_trade() -> float:
    """Read risk_per_trade from config.yaml; fall back to Config.RISK_PER_TRADE."""
    try:
        import yaml
        with open(CONFIG_YAML, "r") as f:
            data = yaml.safe_load(f) or {}
        return float(data["risk_per_trade"])
    except Exception:
        return float(Config.RISK_PER_TRADE)

MAGIC         = 234567   # identifies this bot's orders in MT5
POLL_INTERVAL = 60       # seconds between scans
BARS          = 500      # bars fetched per symbol (strategy warmup)

TIMEFRAME_MAP = {
    "M1":  mt5.TIMEFRAME_M1,
    "M5":  mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1":  mt5.TIMEFRAME_H1,
    "H4":  mt5.TIMEFRAME_H4,
    "D1":  mt5.TIMEFRAME_D1,
}

STRATEGY_MAP = {
    "engulfing":               EngulfingStrategy,
    "green_dollar":            GreenDollarStrategy,
    "ma":                      MovingAverageStrategy,
    "supertrend":              SupertrendStrategy,
    "engulfing_consolidation": EngulfingConsolidationStrategy,
    "engulfing_reversal":      EngulfingReversalStrategy,
    "mark2":                   Mark2Strategy,
    "mark_dollar_supertrend":  MarkDollarSuperTrendStrategy,
    "rsi_engulfing":           RSIEngulfingStrategy,
}


# ── Logging ───────────────────────────────────────────────────────────────────

def setup_logging() -> logging.Logger:
    log_path = BASE / "data" / "live_trading.log"
    log_path.parent.mkdir(exist_ok=True)

    fmt     = "%(asctime)s | %(levelname)-8s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        datefmt=datefmt,
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    return logging.getLogger("LiveBot")


# ── MT5 helpers ───────────────────────────────────────────────────────────────

def fetch_bars(symbol: str, timeframe_str: str, bars: int) -> pd.DataFrame | None:
    tf = TIMEFRAME_MAP.get(timeframe_str)
    if tf is None:
        return None
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def has_open_position(symbol: str) -> bool:
    """True if our bot already has an open position for this symbol."""
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return False
    return any(p.magic == MAGIC for p in positions)


def get_today_losses() -> int:
    """Count losing closing deals placed by this bot today."""
    from_dt = datetime.combine(date.today(), datetime.min.time())
    to_dt   = datetime.now() + timedelta(hours=1)
    deals   = mt5.history_deals_get(from_dt, to_dt)
    if not deals:
        return 0
    return sum(
        1 for d in deals
        if d.magic == MAGIC
        and d.profit < 0
        and d.entry == mt5.DEAL_ENTRY_OUT   # only exit deals, not entries
    )


def log_account(log: logging.Logger):
    info = mt5.account_info()
    if info:
        log.info(
            f"Account | Balance: ${info.balance:,.2f} | "
            f"Equity: ${info.equity:,.2f} | "
            f"Floating P&L: ${info.profit:+,.2f}"
        )


def log_open_positions(log: logging.Logger):
    positions = mt5.positions_get()
    if not positions:
        log.info("Positions | None open")
        return
    bot_pos = [p for p in positions if p.magic == MAGIC]
    if not bot_pos:
        log.info("Positions | None from this bot")
        return
    for p in bot_pos:
        direction = "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL"
        log.info(
            f"Position  | {p.symbol} {direction} | "
            f"lot={p.volume} | entry={p.price_open:.5f} | "
            f"SL={p.sl:.5f} | TP={p.tp:.5f} | "
            f"P&L=${p.profit:+.2f} | ticket={p.ticket}"
        )


# ── Order placement ───────────────────────────────────────────────────────────

def place_order(
    symbol: str,
    direction: int,   # 1 = BUY, -1 = SELL
    sl: float,
    tp: float,
    lot: float,
    log: logging.Logger,
) -> bool:
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        log.error(f"  {symbol}: cannot get tick data")
        return False

    sym_info = mt5.symbol_info(symbol)
    if not sym_info:
        log.error(f"  {symbol}: cannot get symbol info")
        return False

    digits = sym_info.digits

    if direction == 1:
        order_type = mt5.ORDER_TYPE_BUY
        price      = tick.ask
    else:
        order_type = mt5.ORDER_TYPE_SELL
        price      = tick.bid

    # Pick the filling mode supported by the broker
    if sym_info.filling_mode & mt5.SYMBOL_FILLING_FOK:
        filling = mt5.ORDER_FILLING_FOK
    elif sym_info.filling_mode & mt5.SYMBOL_FILLING_IOC:
        filling = mt5.ORDER_FILLING_IOC
    else:
        filling = mt5.ORDER_FILLING_RETURN

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       symbol,
        "volume":       round(float(lot), 2),
        "type":         order_type,
        "price":        price,
        "sl":           round(sl, digits),
        "tp":           round(tp, digits),
        "deviation":    20,
        "magic":        MAGIC,
        "comment":      "AlgoBot",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": filling,
    }

    result = mt5.order_send(request)

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        dir_str = "BUY" if direction == 1 else "SELL"
        log.info(
            f"  ✅ ORDER | {symbol} {dir_str} | "
            f"lot={lot} | price={price:.{digits}f} | "
            f"SL={sl:.{digits}f} | TP={tp:.{digits}f} | "
            f"ticket={result.order}"
        )
        return True
    else:
        log.error(
            f"  ❌ ORDER FAILED | {symbol} | "
            f"retcode={result.retcode} | {result.comment}"
        )
        return False


# ── Signal scan ───────────────────────────────────────────────────────────────

def run_scan(
    strategies: dict,
    symbols: list,
    last_bar_times: dict,
    log: logging.Logger,
):
    """Scan all symbols with all strategies. One order per symbol per new candle."""

    max_daily_losses = getattr(Config, "MAX_DAILY_LOSSES", None)

    # Daily loss cap — check once for the whole scan
    if max_daily_losses is not None:
        today_losses = get_today_losses()
        if today_losses >= max_daily_losses:
            log.info(
                f"🚫 Daily loss cap reached "
                f"({today_losses}/{max_daily_losses}) — no new entries today"
            )
            return

    tf_str = Config.TIMEFRAME

    for symbol in symbols:

        # Skip if already in a position
        if has_open_position(symbol):
            log.debug(f"  {symbol}: position open — skipping")
            continue

        # Fetch bars
        df = fetch_bars(symbol, tf_str, BARS)
        if df is None or len(df) < 10:
            log.warning(f"  {symbol}: failed to fetch bars")
            continue

        # Last CLOSED candle — index -2 (index -1 is the in-progress bar)
        last_closed_time = df.iloc[-2]["time"]

        # Skip if we already processed this candle for this symbol
        if last_bar_times.get(symbol) == last_closed_time:
            log.debug(f"  {symbol}: no new candle since last scan")
            continue

        # Scan every strategy — first valid signal wins (mirrors backtest merge logic)
        signal_taken = False
        for strat_name, strategy in strategies.items():
            try:
                sig_df = strategy.generate_signals(df)
            except Exception as e:
                log.error(f"  {symbol} [{strat_name}] generate_signals error: {e}", exc_info=True)
                continue

            last = sig_df.iloc[-2]
            signal = int(last.get("signal", 0))

            if signal == 0:
                continue

            sl  = float(last["sl"])
            tp  = float(last["tp"])
            lot = float(last.get("lot", Config.LOT_SIZE))

            dir_str = "BUY" if signal == 1 else "SELL"
            log.info(
                f"  🔔 SIGNAL | {symbol} | [{strat_name}] | {dir_str} | "
                f"candle={str(last_closed_time)[:16]} | "
                f"SL={sl:.5f} | TP={tp:.5f}"
            )

            success = place_order(symbol, signal, sl, tp, lot, log)
            if success:
                signal_taken = True
                break   # one trade per symbol per candle

        # Mark this candle as processed regardless of whether a trade was taken
        last_bar_times[symbol] = last_closed_time

        if not signal_taken:
            log.info(f"  {symbol}: no signal on {str(last_closed_time)[:16]}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    log = setup_logging()
    risk_per_trade = load_risk_per_trade()

    log.info("=" * 60)
    log.info("  ALGO TRADING BOT  —  LIVE MODE")
    log.info(f"  Strategies       : {Config.STRATEGY}")
    log.info(f"  Symbols          : {Config.SYMBOL}")
    log.info(f"  Timeframe        : {Config.TIMEFRAME}")
    log.info(f"  Risk per trade   : {risk_per_trade * 100:.1f}%")
    log.info(f"  Lot size         : {Config.LOT_SIZE}")
    log.info(f"  Max daily losses : {getattr(Config, 'MAX_DAILY_LOSSES', 'disabled')}")
    log.info(f"  Poll interval    : {POLL_INTERVAL}s")
    log.info(f"  Magic number     : {MAGIC}")
    log.info("=" * 60)

    if not connect():
        log.error("Failed to connect to MT5 — exiting")
        return

    symbols      = [s.strip() for s in Config.SYMBOL.split(",")   if s.strip()]
    strat_names  = [s.strip() for s in Config.STRATEGY.split(",") if s.strip()]

    # Load strategies
    strategies = {}
    for name in strat_names:
        cls = STRATEGY_MAP.get(name)
        if cls is None:
            log.warning(f"Unknown strategy '{name}' — skipping")
            continue
        try:
            strategies[name] = cls()
            log.info(f"  ✅ Loaded: {name}")
        except Exception as e:
            log.error(f"  ❌ Failed to load '{name}': {e}")

    if not strategies:
        log.error("No strategies loaded — exiting")
        shutdown()
        return

    log.info(f"Bot running — {len(strategies)} strategies | {len(symbols)} symbols")

    last_bar_times: dict = {}   # tracks last processed candle per symbol

    try:
        while True:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log.info(f"─── Scan [{now}] ───────────────────────────────")

            log_account(log)
            log_open_positions(log)

            try:
                run_scan(strategies, symbols, last_bar_times, log)
            except Exception as e:
                log.error(f"Scan error: {e}", exc_info=True)

            log.info(f"Sleeping {POLL_INTERVAL}s...")
            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        log.info("KeyboardInterrupt — shutting down cleanly")
    finally:
        shutdown()
        log.info("MT5 disconnected. Bot stopped.")


if __name__ == "__main__":
    main()
