"""
Live Trading Engine
───────────────────
• Polls MT5 every POLL_INTERVAL seconds
• Signals read from the last CLOSED candle (iloc[-2]) — never acts on an open bar
• One trade per symbol at a time — skips if a position is already open
• Lot size computed from LIVE account balance + risk_per_trade (not fixed)
• Daily loss cap (MAX_DAILY_LOSSES) enforced via MT5 deal history
• SL + TP set on the order — MT5 handles exits natively
• Full logging to console + data/live_trading.log

HOW TO RUN:
  cd src
  python -m algoTrading.main

SWITCH STRATEGY:
  Edit active_preset in config.yaml — no code changes needed.
"""

import math
import time
import logging
import pandas as pd
import MetaTrader5 as mt5
from datetime import datetime, date, timedelta
from pathlib import Path

from algoTrading.config import Config
from algoTrading.core.mt5_connector import connect, shutdown

# ── Strategy imports ──────────────────────────────────────────────────────────
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

BASE        = Path(__file__).resolve().parent
CONFIG_YAML = BASE / "config.yaml"

MAGIC         = 234567   # identifies this bot's orders in MT5
POLL_INTERVAL = 60       # seconds between candle scans
BARS          = 500      # bars fetched per symbol (strategy warmup)
MIN_SL_DIST   = 0.10     # minimum SL distance — prevents micro-stop lot explosions
MAX_LOT       = 10.0     # absolute lot cap

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
    "engulfing":                       EngulfingStrategy,
    "green_dollar":                    GreenDollarStrategy,
    "ma":                              MovingAverageStrategy,
    "supertrend":                      SupertrendStrategy,
    "engulfing_consolidation":         EngulfingConsolidationStrategy,
    "engulfing_reversal":              EngulfingReversalStrategy,
    "mark2":                           Mark2Strategy,
    "supertrend_engulfing_reversal":   SupertrendEngulfingReversalStrategy,
    "mark_dollar_supertrend":          MarkDollarSuperTrendStrategy,
    "rsi_engulfing":                   RSIEngulfingStrategy,
    "SupertrendCounterFlip_X1":        SupertrendCounterFlipX1Strategy,
    "ict_simple_1h5m_fvg":             SimpleICT1H5mFVGStrategy,
    "RSIBuySellStrategy":              RSIBuySellStrategy,
    "RSIEMADoubleCrossStrategy":       RSIEMADoubleCrossStrategy,
    "SupertrendTouchSell":             SupertrendTouchSellStrategy,
    "session_strategy":                SessionStrategy,
}


# ── Config loaders ────────────────────────────────────────────────────────────

def load_risk_per_trade() -> float:
    """Read risk_per_trade: active strategy → global yaml → Config fallback."""
    try:
        import yaml
        with open(CONFIG_YAML, "r") as f:
            data = yaml.safe_load(f) or {}
        preset = data.get("active_preset", "").strip()
        if preset:
            strat_val = data.get("presets", {}).get(preset, preset)
            first_name = strat_val.split(",")[0].strip()
            strats = data.get("strategies", {})
            norm = lambda s: s.replace("_", "").lower()
            for yaml_key, yaml_cfg in strats.items():
                if norm(yaml_key) == norm(first_name) and "risk_per_trade" in yaml_cfg:
                    return float(yaml_cfg["risk_per_trade"]) / 100.0
        if "risk_per_trade" in data:
            return float(data["risk_per_trade"]) / 100.0
    except Exception:
        pass
    return float(getattr(Config, "RISK_PER_TRADE", 0.02))


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


# ── Position sizing ───────────────────────────────────────────────────────────

def compute_lot(
    symbol: str,
    entry: float,
    sl: float,
    direction: int,          # 1 = BUY, -1 = SELL
    risk_per_trade: float,   # decimal e.g. 0.01 = 1%
    signal_risk_pct: float,  # per-signal override (NaN = use global)
    balance: float,
) -> float:
    """
    Compute lot size from live account balance.
      lot = (balance × risk%) / (sl_distance × contract_size)
    Floored to 0.01 step, capped at MAX_LOT.
    """
    if direction == 1:
        sl_dist = entry - sl
    else:
        sl_dist = sl - entry

    if sl_dist < MIN_SL_DIST:
        return 0.0   # signal rejected — SL too tight

    # Use per-signal risk if present (already decimal from strategy)
    if not (pd.isna(signal_risk_pct) or signal_risk_pct == 0):
        effective_risk = float(signal_risk_pct)
    else:
        effective_risk = risk_per_trade

    cs_map   = getattr(Config, "CONTRACT_SIZES",        {})
    cs_def   = getattr(Config, "DEFAULT_CONTRACT_SIZE", 100000)
    contract = float(cs_map.get(symbol, cs_def))

    risk_amount = balance * effective_risk
    raw         = risk_amount / (sl_dist * contract)
    floored     = math.floor(raw / 0.01) * 0.01
    return min(max(0.01, floored), MAX_LOT)


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
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return False
    return any(p.magic == MAGIC for p in positions)


def get_today_losses() -> int:
    from_dt = datetime.combine(date.today(), datetime.min.time())
    to_dt   = datetime.now() + timedelta(hours=1)
    deals   = mt5.history_deals_get(from_dt, to_dt)
    if not deals:
        return 0
    return sum(
        1 for d in deals
        if d.magic == MAGIC
        and d.profit < 0
        and d.entry == mt5.DEAL_ENTRY_OUT
    )


def get_balance() -> float:
    info = mt5.account_info()
    return float(info.balance) if info else 0.0


def log_account(log: logging.Logger):
    info = mt5.account_info()
    if info:
        log.info(
            f"Account  | Balance: ${info.balance:,.2f} | "
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
            f"lot={p.volume:.2f} | entry={p.price_open:.5f} | "
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

    digits     = sym_info.digits
    order_type = mt5.ORDER_TYPE_BUY  if direction == 1 else mt5.ORDER_TYPE_SELL
    price      = tick.ask             if direction == 1 else tick.bid

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
        "comment":      f"AlgoBot:{Config.STRATEGY}",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": filling,
    }

    result = mt5.order_send(request)

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        dir_str = "BUY" if direction == 1 else "SELL"
        log.info(
            f"  ORDER PLACED | {symbol} {dir_str} | "
            f"lot={lot:.2f} | price={price:.{digits}f} | "
            f"SL={sl:.{digits}f} | TP={tp:.{digits}f} | "
            f"ticket={result.order}"
        )
        return True
    else:
        log.error(
            f"  ORDER FAILED  | {symbol} | "
            f"retcode={result.retcode} | {result.comment}"
        )
        return False


# ── Signal scan ───────────────────────────────────────────────────────────────

def run_scan(
    strategies: dict,
    symbols: list,
    last_bar_times: dict,
    risk_per_trade: float,
    log: logging.Logger,
):
    """Scan all symbols with all strategies. One order per symbol per new candle."""

    max_daily_losses = getattr(Config, "MAX_DAILY_LOSSES", None)
    if max_daily_losses is not None:
        today_losses = get_today_losses()
        if today_losses >= max_daily_losses:
            log.info(
                f"Daily loss cap reached "
                f"({today_losses}/{max_daily_losses}) — skipping all entries"
            )
            return

    balance = get_balance()
    tf_str  = Config.TIMEFRAME

    for symbol in symbols:

        if has_open_position(symbol):
            log.debug(f"  {symbol}: position already open — skipping")
            continue

        df = fetch_bars(symbol, tf_str, BARS)
        if df is None or len(df) < 10:
            log.warning(f"  {symbol}: failed to fetch bars")
            continue

        last_closed_time = df.iloc[-2]["time"]

        if last_bar_times.get(symbol) == last_closed_time:
            log.debug(f"  {symbol}: no new candle — skipping")
            continue

        # First-wins: scan strategies in order, stop at first valid signal
        signal_taken = False
        for strat_name, strategy in strategies.items():
            try:
                sig_df = strategy.generate_signals(df)
            except Exception as e:
                log.error(
                    f"  {symbol} [{strat_name}] generate_signals error: {e}",
                    exc_info=True,
                )
                continue

            last   = sig_df.iloc[-2]
            signal = int(last.get("signal", 0))
            if signal == 0:
                continue

            sl  = float(last["sl"])
            tp  = float(last["tp"])
            entry = float(df.iloc[-2]["close"])

            # Compute lot from live balance
            sig_risk = last.get("risk_per_trade", float("nan"))
            lot = compute_lot(symbol, entry, sl, signal, risk_per_trade, sig_risk, balance)

            if lot <= 0:
                log.warning(
                    f"  {symbol} [{strat_name}]: SL too tight "
                    f"(dist={abs(entry - sl):.5f}) — signal skipped"
                )
                continue

            dir_str = "BUY" if signal == 1 else "SELL"
            log.info(
                f"  SIGNAL | {symbol} [{strat_name}] {dir_str} | "
                f"candle={str(last_closed_time)[:16]} | "
                f"entry={entry:.5f} | SL={sl:.5f} | TP={tp:.5f} | lot={lot:.2f}"
            )

            success = place_order(symbol, signal, sl, tp, lot, log)
            if success:
                signal_taken = True
                break

        last_bar_times[symbol] = last_closed_time

        if not signal_taken:
            log.info(f"  {symbol}: no signal on {str(last_closed_time)[:16]}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    log            = setup_logging()
    risk_per_trade = load_risk_per_trade()

    log.info("=" * 60)
    log.info("  ALGO TRADING BOT  —  LIVE MODE")
    log.info(f"  Strategy     : {Config.STRATEGY}")
    log.info(f"  Symbols      : {Config.SYMBOL}")
    log.info(f"  Timeframe    : {Config.TIMEFRAME}")
    log.info(f"  Risk/trade   : {risk_per_trade * 100:.1f}%")
    log.info(f"  Max lot      : {MAX_LOT}")
    log.info(f"  Min SL dist  : {MIN_SL_DIST}")
    log.info(f"  Daily loss cap: {getattr(Config, 'MAX_DAILY_LOSSES', 'off')}")
    log.info(f"  Poll interval: {POLL_INTERVAL}s")
    log.info(f"  Magic #      : {MAGIC}")
    log.info("=" * 60)

    if not connect():
        log.error("Failed to connect to MT5 — exiting")
        return

    symbols     = [s.strip() for s in Config.SYMBOL.split(",")   if s.strip()]
    strat_names = [s.strip() for s in Config.STRATEGY.split(",") if s.strip()]

    strategies = {}
    for name in strat_names:
        cls = STRATEGY_MAP.get(name)
        if cls is None:
            log.warning(f"Unknown strategy '{name}' — skipping")
            continue
        try:
            strategies[name] = cls()
            log.info(f"  Loaded: {name}")
        except Exception as e:
            log.error(f"  Failed to load '{name}': {e}")

    if not strategies:
        log.error("No strategies loaded — exiting")
        shutdown()
        return

    log.info(f"Bot running — {len(strategies)} strategies on {len(symbols)} symbols")

    last_bar_times: dict = {}

    try:
        while True:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log.info(f"── Scan [{now}] " + "─" * 36)
            log_account(log)
            log_open_positions(log)

            try:
                run_scan(strategies, symbols, last_bar_times, risk_per_trade, log)
            except Exception as e:
                log.error(f"Scan error: {e}", exc_info=True)

            log.info(f"Sleeping {POLL_INTERVAL}s ...")
            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        log.info("Keyboard interrupt — shutting down cleanly")
    finally:
        shutdown()
        log.info("MT5 disconnected. Bot stopped.")


if __name__ == "__main__":
    main()
