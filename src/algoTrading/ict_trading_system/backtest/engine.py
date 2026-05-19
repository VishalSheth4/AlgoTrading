"""
backtest/engine.py
==================
Event-driven backtesting engine.
Replays historical candle-by-candle, runs all strategies,
and executes trades via the RiskManager.

Features:
  - Multi-instrument, multi-strategy support
  - Realistic fill simulation (next open after signal)
  - Trade management (partial TP, break-even)
  - Per-trade logging for journal
  - Equity curve generation
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import pandas as pd

from config.settings import Instrument, Timeframe, StrategyID, RiskConfig
from data.models import Candle, Trade, TradeSignal
from data.market_data import MultiTimeframeData, detect_session, get_asian_range
from signals.signal_generator import SignalGenerator
from risk.risk_manager import RiskManager

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# BACKTEST CONFIGURATION
# ─────────────────────────────────────────────

@dataclass
class BacktestConfig:
    account_size:  float                  = 10_000.0
    strategies:    List[StrategyID]       = field(default_factory=list)
    instruments:   List[Instrument]       = field(default_factory=list)
    start_date:    Optional[str]          = None
    end_date:      Optional[str]          = None
    slippage_pips: float                  = 1.0     # simulated fill slippage
    commission:    float                  = 2.0     # USD per trade round-trip
    risk_config:   RiskConfig             = field(default_factory=RiskConfig)


# ─────────────────────────────────────────────
# BACKTEST RESULT
# ─────────────────────────────────────────────

@dataclass
class BacktestResult:
    config:           BacktestConfig
    trades:           List[Trade]
    equity_curve:     pd.Series
    total_return_pct: float
    max_drawdown_pct: float
    win_rate:         float
    profit_factor:    float
    avg_rr:           float
    sharpe_ratio:     float
    total_trades:     int
    winning_trades:   int
    losing_trades:    int
    best_trade_r:     float
    worst_trade_r:    float

    def summary(self) -> str:
        return (
            f"\n{'='*55}\n"
            f"  BACKTEST RESULTS\n"
            f"{'='*55}\n"
            f"  Account       : ${self.config.account_size:,.2f}\n"
            f"  Total Return  : {self.total_return_pct:+.2f}%\n"
            f"  Max Drawdown  : {self.max_drawdown_pct:.2f}%\n"
            f"  Sharpe Ratio  : {self.sharpe_ratio:.2f}\n"
            f"  ─────────────────────────────────────────────\n"
            f"  Total Trades  : {self.total_trades}\n"
            f"  Winners       : {self.winning_trades}\n"
            f"  Losers        : {self.losing_trades}\n"
            f"  Win Rate      : {self.win_rate:.1%}\n"
            f"  Profit Factor : {self.profit_factor:.2f}\n"
            f"  Avg R:R       : 1:{self.avg_rr:.2f}\n"
            f"  Best Trade    : +{self.best_trade_r:.1f}R\n"
            f"  Worst Trade   : {self.worst_trade_r:.1f}R\n"
            f"{'='*55}\n"
        )


# ─────────────────────────────────────────────
# BACKTEST ENGINE
# ─────────────────────────────────────────────

class BacktestEngine:

    def __init__(
        self,
        config: BacktestConfig,
        mtf_data: Dict[Instrument, MultiTimeframeData],
    ):
        self.config      = config
        self.mtf_data    = mtf_data
        self.risk_mgr    = RiskManager(config.account_size, config.risk_config)
        self.signal_gen  = SignalGenerator(
            strategies  = config.strategies or None,
            instruments = config.instruments or None,
        )
        self.equity_log: List[Tuple[datetime, float]] = []
        self.logger      = logging.getLogger(self.__class__.__name__)

    # ─────────────────────────────────────────────
    # MAIN RUN LOOP
    # ─────────────────────────────────────────────

    def run(self) -> BacktestResult:
        """
        Main backtest loop.
        Iterates through M5 candle timestamps as the 'clock'.
        At each tick:
          1. Update open trade management
          2. Check SL / TP hits
          3. Generate signals
          4. Open new trades (fill on next open)
        """
        self.logger.info("Backtest started")

        # Collect all M5 timestamps across instruments
        timestamps = self._get_timestamps()

        for ts in timestamps:
            self._update_open_trades(ts)
            self._generate_and_open_signals(ts)
            self.equity_log.append((ts, self.risk_mgr.equity))

        # Force-close any remaining open trades at last price
        self._close_all_open_trades(timestamps[-1] if timestamps else datetime.utcnow())

        return self._build_result()

    def _get_timestamps(self) -> List[datetime]:
        """Collect sorted M5 timestamps from first available instrument."""
        for instrument in (self.config.instruments or list(self.mtf_data.keys())):
            if instrument in self.mtf_data:
                df = self.mtf_data[instrument].get(Timeframe.M5)
                ts = [t.to_pydatetime() for t in df.index]
                if self.config.start_date:
                    ts = [t for t in ts if str(t.date()) >= self.config.start_date]
                if self.config.end_date:
                    ts = [t for t in ts if str(t.date()) <= self.config.end_date]
                return ts
        return []

    # ─────────────────────────────────────────────
    # TRADE MANAGEMENT UPDATE
    # ─────────────────────────────────────────────

    def _update_open_trades(self, ts: datetime):
        for instrument in (self.config.instruments or list(self.mtf_data.keys())):
            if instrument not in self.mtf_data:
                continue
            try:
                df_m5   = self.mtf_data[instrument].get(Timeframe.M5)
                row     = df_m5.loc[df_m5.index <= pd.Timestamp(ts, tz="UTC")].iloc[-1]
                current = float(row["high"]) # use high/low for SL/TP checks

                trades_to_close = []
                for trade in self.risk_mgr.open_trades:
                    if trade.signal.instrument != instrument:
                        continue

                    sig = trade.signal
                    hit_sl, hit_tp = False, False

                    if sig.is_long:
                        close_price = None
                        if float(df_m5.loc[df_m5.index <= pd.Timestamp(ts, tz="UTC")].iloc[-1]["low"]) <= sig.stop_loss:
                            close_price = sig.stop_loss
                            hit_sl = True
                        elif float(df_m5.loc[df_m5.index <= pd.Timestamp(ts, tz="UTC")].iloc[-1]["high"]) >= sig.take_profit_1:
                            close_price = sig.take_profit_1
                            hit_tp = True
                    else:
                        close_price = None
                        if float(df_m5.loc[df_m5.index <= pd.Timestamp(ts, tz="UTC")].iloc[-1]["high"]) >= sig.stop_loss:
                            close_price = sig.stop_loss
                            hit_sl = True
                        elif float(df_m5.loc[df_m5.index <= pd.Timestamp(ts, tz="UTC")].iloc[-1]["low"]) <= sig.take_profit_1:
                            close_price = sig.take_profit_1
                            hit_tp = True

                    if hit_sl or hit_tp:
                        # Apply slippage on stop-outs
                        if hit_sl:
                            pip = 0.0001
                            slippage = self.config.slippage_pips * pip
                            close_price += slippage if sig.is_long else -slippage
                        trades_to_close.append((trade, close_price))

                for trade, cp in trades_to_close:
                    closed = self.risk_mgr.close_trade(trade, cp, ts)
                    closed.pnl -= self.config.commission   # deduct commission

            except (IndexError, KeyError):
                pass

    # ─────────────────────────────────────────────
    # SIGNAL GENERATION & TRADE OPENING
    # ─────────────────────────────────────────────

    def _generate_and_open_signals(self, ts: datetime):
        if self.risk_mgr.kill_switch:
            return
        if self.risk_mgr.open_trade_count >= self.config.risk_config.max_open_trades:
            return

        candles_by_inst_tf: Dict[Instrument, Dict[Timeframe, List[Candle]]] = {}

        for instrument in (self.config.instruments or list(self.mtf_data.keys())):
            if instrument not in self.mtf_data:
                continue
            mtf      = self.mtf_data[instrument]
            inst_tfs = {}
            for tf in [Timeframe.M5, Timeframe.M15, Timeframe.H1,
                       Timeframe.H4, Timeframe.D1]:
                try:
                    df       = mtf.get(tf)
                    df_slice = df.loc[df.index <= pd.Timestamp(ts, tz="UTC")].tail(200)
                    candles  = mtf._df_to_candles(df_slice, tf)
                    if candles:
                        inst_tfs[tf] = candles
                except KeyError:
                    pass
            if inst_tfs:
                candles_by_inst_tf[instrument] = inst_tfs

        # Asian range context
        extra = {}
        if candles_by_inst_tf:
            first_inst = next(iter(candles_by_inst_tf))
            try:
                df_m15 = self.mtf_data[first_inst].get(Timeframe.M15)
                extra["asian_range"] = get_asian_range(df_m15)
            except Exception:
                pass

        signals = self.signal_gen.generate(
            candles_by_instrument_tf = candles_by_inst_tf,
            current_time             = ts,
            open_trades              = self.risk_mgr.open_trade_count,
            daily_loss_pct           = self.risk_mgr.daily_loss_pct,
            weekly_loss_pct          = self.risk_mgr.weekly_loss_pct,
            extra_context            = extra,
        )

        for sig in signals[:1]:   # max 1 new trade per bar
            self.risk_mgr.open_trade(sig)

    # ─────────────────────────────────────────────
    # FORCE CLOSE ALL
    # ─────────────────────────────────────────────

    def _close_all_open_trades(self, ts: datetime):
        for trade in list(self.risk_mgr.open_trades):
            instrument = trade.signal.instrument
            try:
                df    = self.mtf_data[instrument].get(Timeframe.M5)
                price = float(df.iloc[-1]["close"])
                self.risk_mgr.close_trade(trade, price, ts)
            except Exception:
                pass

    # ─────────────────────────────────────────────
    # BUILD RESULT
    # ─────────────────────────────────────────────

    def _build_result(self) -> BacktestResult:
        closed = self.risk_mgr.closed_trades
        eq     = pd.Series(
            [e for _, e in self.equity_log],
            index=[t for t, _ in self.equity_log],
        )

        total  = len(closed)
        wins   = [t for t in closed if t.pnl > 0]
        losses = [t for t in closed if t.pnl <= 0]
        wr     = len(wins) / total if total > 0 else 0

        gross_profit = sum(t.pnl for t in wins)
        gross_loss   = abs(sum(t.pnl for t in losses))
        pf           = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        avg_rr = sum(t.pnl_r for t in closed) / total if total > 0 else 0

        # Max Drawdown
        roll_max = eq.cummax()
        drawdown = (eq - roll_max) / roll_max
        max_dd   = abs(drawdown.min()) * 100 if len(drawdown) > 0 else 0.0

        # Sharpe (daily returns)
        daily_eq = eq.resample("1D").last().dropna()
        daily_ret = daily_eq.pct_change().dropna()
        sharpe = (daily_ret.mean() / daily_ret.std() * (252 ** 0.5)) \
                 if len(daily_ret) > 1 and daily_ret.std() > 0 else 0.0

        total_ret = ((eq.iloc[-1] - self.config.account_size) / self.config.account_size * 100) \
                    if len(eq) > 0 else 0.0

        return BacktestResult(
            config            = self.config,
            trades            = closed,
            equity_curve      = eq,
            total_return_pct  = round(total_ret, 2),
            max_drawdown_pct  = round(max_dd, 2),
            win_rate          = round(wr, 3),
            profit_factor     = round(pf, 2),
            avg_rr            = round(avg_rr, 2),
            sharpe_ratio      = round(float(sharpe), 2),
            total_trades      = total,
            winning_trades    = len(wins),
            losing_trades     = len(losses),
            best_trade_r      = round(max((t.pnl_r for t in closed), default=0), 2),
            worst_trade_r     = round(min((t.pnl_r for t in closed), default=0), 2),
        )