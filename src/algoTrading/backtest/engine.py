"""
BacktestEngine — Event-driven backtesting engine for long/short equity & forex strategies.
==========================================================================================

WHAT THIS MODULE DOES
---------------------
Simulates live trading on historical OHLCV bar data, processing one candle at a time.
For each bar it reads a pre-computed *signal* column (+1 long, -1 short, 0 flat) and the
matching stop-loss / take-profit levels, then manages the full trade lifecycle:

    1. Entry  — open a long or short position when a signal fires and no trade is open.
    2. Exit   — close the position when price reaches the SL, TP, or a reversal candle fires.
    3. P&L    — compound profits / losses back into the running capital balance.
    4. Guards — skip entries when the daily loss quota (Config.MAX_DAILY_LOSSES) is full.

KEY CONFIG VALUES USED (from algoTrading/config.py)
----------------------------------------------------
    Config.TP_MODE          : "rr" | "st" | "both" | "fix_profit"
                              Controls which take-profit level the strategy passes in, and
                              how the exit_label is named in trade records.

    Config.MAX_DAILY_LOSSES : int | None
                              Maximum losing trades allowed per calendar day before new
                              entries are blocked.  None = no daily cap.

    Config.INITIAL_CAPITAL  : float  (not read here — passed in as `capital` arg)
    Config.RISK_PER_TRADE   : float  (not read here — passed in as `risk_per_trade` arg)
                              Together they size every position so a worst-case SL hit
                              costs exactly (capital × risk_per_trade) dollars.

POSITION SIZING
---------------
Risk-based sizing: position_size = (capital × risk_per_trade) / risk_per_unit
where risk_per_unit = |entry_price − sl|.
This keeps the dollar risk constant as a fraction of the current account balance.

EXIT LABELS
-----------
    "SL"   — stop-loss hit (price closed beyond the SL level)
    "TP"   / "R:R" / "ST" — take-profit hit; label depends on Config.TP_MODE
    "REV"  — reverse-engulfing candle flagged by the strategy (early protective exit)

OUTPUT
------
    trade_data.csv  — written to <project_root>/data/ after each run.
                      One row per trade event (BUY/SELL for longs, SHORT/COVER for shorts).
    results dict    — returned by run(); contains initial/final capital, return %, trade count.

USAGE
-----
    engine = BacktestEngine(
        capital        = Config.INITIAL_CAPITAL,   # e.g. 100
        risk_per_trade = Config.RISK_PER_TRADE,    # e.g. 0.01  → 1 % per trade
        symbol         = Config.SYMBOL,            # e.g. "XAUUSD"
    )
    results = engine.run(df)   # df must have columns: time, close, signal, sl, tp
"""

import math
import pandas as pd
from pathlib import Path
from algoTrading.config import Config

# Minimum and step size for position sizing.
# All instruments use 0.01 as the standard lot step.
_LOT_STEP = 0.01
_LOT_MIN  = 0.01


class BacktestEngine:
    """
    Event-driven backtesting engine.

    Iterates bar-by-bar over a DataFrame of OHLCV + signal data and simulates
    trade entries / exits with risk-based position sizing.

    Parameters
    ----------
    capital : float
        Starting account balance (mirrors Config.INITIAL_CAPITAL).
    risk_per_trade : float
        Fraction of current capital risked on each trade (mirrors Config.RISK_PER_TRADE).
        Example: 0.01 = 1 % per trade.
    symbol : str, optional
        Instrument label stored in trade records (mirrors Config.SYMBOL).
    """

    def __init__(self, capital: float, risk_per_trade: float, symbol: str = "",
                 max_position_size: float = 10.0, min_sl_dist: float = 0.10):
        # ── Account state ────────────────────────────────────────────────────────
        self.initial_capital = capital
        self.capital = capital
        self.risk_per_trade = risk_per_trade  # Config.RISK_PER_TRADE
        self.symbol = symbol                  # Config.SYMBOL
        self.max_position_size = max_position_size  # hard cap — prevents micro-SL blowups
        self.min_sl_dist = min_sl_dist        # skip any signal where SL distance < this

        # Contract size: units per standard lot (100 for XAUUSD, 100000 for forex).
        # Applied to BOTH position sizing and P&L so risk math is correct.
        _cs_map = getattr(Config, "CONTRACT_SIZES", {})
        _default = getattr(Config, "DEFAULT_CONTRACT_SIZE", 100000)
        self.contract_size = float(_cs_map.get(symbol, _default))

        # ── Open-position tracking ───────────────────────────────────────────────
        self.position = 0       # 0 = flat, 1 = long, -1 = short
        self.entry_price = None
        self.position_size = 0
        self.sl = None          # active stop-loss price
        self.tp = None          # active take-profit price

        # ── Trade log & strategy metadata ───────────────────────────────────────
        self.trades = []
        self._tp_mode = Config.TP_MODE          # "rr" | "st" | "both" | "fix_profit"
        self._open_strategy = ""                # strategy name from the signal row
        self._sl_exit_on_close = True            # stop-loss exits use candle close by default

        # ── Daily loss guard ─────────────────────────────────────────────────────
        # Config.MAX_DAILY_LOSSES: int | None — blocks new entries after N losses/day
        self._daily_losses = 0
        self._current_day = None

    # ────────────────────────────────────────────────────────────────────────────
    # PUBLIC API
    # ────────────────────────────────────────────────────────────────────────────

    def run(self, df: pd.DataFrame, save: bool = True) -> dict:
        """
        Run the backtest on a prepared OHLCV + signal DataFrame.

        Expected columns
        ----------------
        time          : datetime-like  — bar timestamp
        close         : float          — closing price (entries and exits use close)
        signal        : int            — +1 long entry, -1 short entry, 0 hold
        sl            : float          — stop-loss price for the signal candle
        tp            : float          — take-profit price for the signal candle
        _strategy     : str (optional) — strategy name tag stored in trade records
        reverse_exit  : int (optional) — 1 if the bar is a reversal-exit candle

        Parameters
        ----------
        df   : pd.DataFrame  — bar data; index is reset internally.
        save : bool          — write trade_data.csv when True (default True).

        Returns
        -------
        dict with keys: initial_capital, final_capital, return (%), total_trades
        """
        df = df.reset_index(drop=True)

        # Read the daily loss cap from Config (None = disabled).
        max_daily_losses = getattr(Config, "MAX_DAILY_LOSSES", None)  # Config.MAX_DAILY_LOSSES

        for i in range(len(df)):
            row = df.iloc[i]

            signal = row.get("signal", 0)
            close = row["close"]
            time = row["time"]

            # ── Reset daily loss counter on a new calendar day ───────────────
            bar_day = pd.Timestamp(time).date()
            if bar_day != self._current_day:
                self._current_day = bar_day
                self._daily_losses = 0

            # day_blocked = True when today's loss quota is exhausted
            day_blocked = (
                max_daily_losses is not None
                and self._daily_losses >= max_daily_losses
            )

            # ================================================================
            # FORCE CLOSE  (force_entry=1 closes any open trade so the new
            #               session signal can always enter on this bar)
            # ================================================================
            if row.get("force_entry", 0) == 1 and signal != 0 and self.position != 0:
                self._close_position(
                    exit_price=close,
                    exit_reason="SESSION",
                    time=time,
                    direction=self.position,
                )

            # ================================================================
            # LONG ENTRY  (signal == +1, flat position, day not blocked)
            # ================================================================
            if signal == 1 and self.position == 0 and not day_blocked:
                sl = row.get("sl")
                tp = row.get("tp")

                # Skip malformed signal rows that lack valid SL/TP.
                if pd.isna(sl) or pd.isna(tp):
                    continue

                risk_per_unit = close - sl      # dollar distance to the stop

                # Skip if SL is above (or at) entry — signal data is inconsistent.
                if risk_per_unit <= 0:
                    continue

                # Skip if SL is too close — prevents micro-stop lot explosions.
                if risk_per_unit < self.min_sl_dist:
                    continue

                row_risk_per_trade = row.get("risk_per_trade", self.risk_per_trade)
                if pd.isna(row_risk_per_trade):
                    row_risk_per_trade = self.risk_per_trade

                # Size position: floor to nearest lot step, cap at max.
                # Divide by contract_size so risk is measured in account currency.
                risk_amount = self.capital * float(row_risk_per_trade)
                raw_size    = risk_amount / (risk_per_unit * self.contract_size)
                floored     = math.floor(raw_size / _LOT_STEP) * _LOT_STEP
                self.position_size = min(max(_LOT_MIN, floored), self.max_position_size)

                # Store open-trade state.
                self.entry_price = close
                self.sl = sl
                self.tp = tp
                self.position = 1
                self._open_strategy = row.get("_strategy", "")
                self._sl_exit_on_close = bool(row.get("sl_exit_on_close", 1))
                self._tp_mode = row.get("_tp_mode", Config.TP_MODE) or Config.TP_MODE

                # Log the entry event.
                self.trades.append({
                    "symbol":      self.symbol,
                    "strategy":    self._open_strategy,
                    "time":        time,
                    "type":        "BUY",
                    "entry_price": close,
                    "lot_size":    self.position_size,
                    "sl":          self.sl,
                    "tp":          self.tp,
                    "capital":     self.capital,
                })

            # ================================================================
            # LONG EXIT  (currently holding a long position)
            # ================================================================
            elif self.position == 1:
                exit_trade = False
                exit_reason = None

                if row.get("reverse_exit", 0) == 1:
                    # Strategy-flagged reversal candle — exit immediately.
                    exit_trade = True
                    exit_reason = "REV"

                elif self._sl_exit_on_close and close < self.sl:
                    # Price CLOSED below the stop-loss level.
                    exit_trade = True
                    exit_reason = "SL"

                elif close >= self.tp:
                    # Price REACHED or EXCEEDED the take-profit target.
                    exit_trade = True
                    exit_reason = "TP"

                if exit_trade:
                    self._close_position(
                        exit_price=close,
                        exit_reason=exit_reason,
                        time=time,
                        direction=1,
                    )

            # ================================================================
            # SHORT ENTRY  (signal == -1, flat position, day not blocked)
            # ================================================================
            elif signal == -1 and self.position == 0 and not day_blocked:
                sl = row.get("sl")
                tp = row.get("tp")

                # Skip malformed signal rows that lack valid SL/TP.
                if pd.isna(sl) or pd.isna(tp):
                    continue

                # For shorts the SL is above entry, so risk is SL − entry.
                risk_per_unit = sl - close

                # Skip if SL is below (or at) entry — signal data is inconsistent.
                if risk_per_unit <= 0:
                    continue

                # Skip if SL is too close — prevents micro-stop lot explosions.
                if risk_per_unit < self.min_sl_dist:
                    continue

                row_risk_per_trade = row.get("risk_per_trade", self.risk_per_trade)
                if pd.isna(row_risk_per_trade):
                    row_risk_per_trade = self.risk_per_trade

                # Size position: floor to nearest lot step, cap at max.
                # Divide by contract_size so risk is measured in account currency.
                risk_amount = self.capital * float(row_risk_per_trade)
                raw_size    = risk_amount / (risk_per_unit * self.contract_size)
                floored     = math.floor(raw_size / _LOT_STEP) * _LOT_STEP
                self.position_size = min(max(_LOT_MIN, floored), self.max_position_size)

                # Store open-trade state.
                self.entry_price = close
                self.sl = sl
                self.tp = tp
                self.position = -1
                self._open_strategy = row.get("_strategy", "")
                self._sl_exit_on_close = bool(row.get("sl_exit_on_close", 1))
                self._tp_mode = row.get("_tp_mode", Config.TP_MODE) or Config.TP_MODE

                # Log the entry event.
                self.trades.append({
                    "symbol":      self.symbol,
                    "strategy":    self._open_strategy,
                    "time":        time,
                    "type":        "SHORT",
                    "entry_price": close,
                    "lot_size":    self.position_size,
                    "sl":          self.sl,
                    "tp":          self.tp,
                    "capital":     self.capital,
                })

            # ================================================================
            # SHORT EXIT  (currently holding a short position)
            # ================================================================
            elif self.position == -1:
                exit_trade = False
                exit_reason = None

                if row.get("reverse_exit", 0) == 1:
                    # Strategy-flagged reversal candle — exit immediately.
                    exit_trade = True
                    exit_reason = "REV"

                elif self._sl_exit_on_close and close > self.sl:
                    # Price CLOSED above the stop-loss level (SL is above entry for shorts).
                    exit_trade = True
                    exit_reason = "SL"

                elif close <= self.tp:
                    # Price REACHED or FELL BELOW the take-profit target.
                    exit_trade = True
                    exit_reason = "TP"

                if exit_trade:
                    self._close_position(
                        exit_price=close,
                        exit_reason=exit_reason,
                        time=time,
                        direction=-1,
                    )

            # ── Ruin check — stop iterating if account is wiped ──────────────
            if self.capital <= 0:
                print("❌ Account blown")
                break

        if save:
            self.save_trades()
        return self.results()

    # ────────────────────────────────────────────────────────────────────────────
    # INTERNAL HELPERS
    # ────────────────────────────────────────────────────────────────────────────

    def _exit_label(self, exit_reason: str) -> str:
        """
        Resolve the human-readable label for a trade exit.

        TP label depends on Config.TP_MODE:
            "st"   → "ST"   (SuperTrend target)
            "rr"   → "R:R"  (Risk:Reward ratio target)
            other  → "TP"   (plain take-profit)
        SL and REV exits always map to their own labels.
        """
        if exit_reason == "TP":
            if self._tp_mode == "st":
                return "ST"
            elif self._tp_mode == "rr":
                return "R:R"
            else:
                return "TP"
        return exit_reason  # "SL" or "REV"

    def _close_position(
        self,
        exit_price: float,
        exit_reason: str,
        time,
        direction: int,
    ) -> None:
        """
        Settle an open position, update capital, log the exit, and reset state.

        Parameters
        ----------
        exit_price  : float  — closing price of the exit candle
        exit_reason : str    — "SL" | "TP" | "REV"
        time        : any    — bar timestamp forwarded to the trade record
        direction   : int    — +1 for long, -1 for short
        """
        # Profit = price move × lots × contract_size (units per lot).
        if direction == 1:
            profit = (exit_price - self.entry_price) * self.position_size * self.contract_size
            exit_type = "SELL"
        else:
            profit = (self.entry_price - exit_price) * self.position_size * self.contract_size
            exit_type = "COVER"

        self.capital += profit
        self.capital = max(self.capital, 0)     # floor at zero — never go negative

        # Log the exit event.
        self.trades.append({
            "symbol":      self.symbol,
            "strategy":    self._open_strategy,
            "time":        time,
            "type":        exit_type,
            "entry_price": self.entry_price,
            "exit_price":  exit_price,
            "sl":          self.sl,
            "tp":          self.tp,
            "lot_size":    self.position_size,
            "profit":      profit,
            "exit_reason": exit_reason,
            "exit_label":  self._exit_label(exit_reason),
            "capital":     self.capital,
        })

        # Increment the daily loss counter when the trade was a loser.
        if profit < 0:
            self._daily_losses += 1

        # Reset open-position state to flat.
        self.position = 0
        self.entry_price = None
        self.sl = None
        self.tp = None

    # ────────────────────────────────────────────────────────────────────────────
    # I/O
    # ────────────────────────────────────────────────────────────────────────────

    def save_trades(self) -> None:
        """
        Persist the trade log to <project_root>/data/trade_data.csv.

        Each row represents one trade event (entry OR exit).  Pair up rows
        with the same strategy + symbol + sequential order to reconstruct
        round-trip P&L for analysis.
        """
        if not self.trades:
            print("⚠️  No trades to save")
            return

        df = pd.DataFrame(self.trades)

        base_dir = Path(__file__).resolve().parents[1]
        file_path = base_dir / "data" / "trade_data.csv"

        df.to_csv(file_path, index=False)
        print(f"✅ Trades saved to {file_path}")

    def results(self) -> dict:
        """
        Return a summary dict of backtest performance.

        Keys
        ----
        initial_capital : float  — starting balance passed to __init__
        final_capital   : float  — ending balance after all trades
        return (%)      : float  — percentage return on initial capital
        total_trades    : int    — total trade-event rows (entries + exits combined)
        """
        return {
            "initial_capital": self.initial_capital,
            "final_capital":   round(self.capital, 2),
            "return (%)":      round(
                ((self.capital - self.initial_capital) / self.initial_capital) * 100, 2
            ),
            "total_trades": len(self.trades),
        }
