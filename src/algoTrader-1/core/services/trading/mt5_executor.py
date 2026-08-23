"""
Places real MT5 market orders for a TradeSignal.

Mirrors the request-payload shape already used elsewhere in this codebase
(algoTrading/broker/mt5_broker.py) for consistency, but stop-loss/take-
profit are configured as an absolute PRICE distance (dollars, appropriate
for XAUUSD) rather than pips.
"""

from __future__ import annotations

import logging
import os

import MetaTrader5 as mt5

from ..mt5_connection import get_shared_connection
from .base import TradeExecutor, TradeSignal

logger = logging.getLogger(__name__)

DEFAULT_SYMBOL = os.environ.get("MT5_SYMBOL", "XAUUSD")
DEFAULT_LOT_SIZE = float(os.environ.get("AUTO_TRADE_LOT_SIZE", "0.01"))
DEFAULT_STOP_LOSS = float(os.environ.get("AUTO_TRADE_STOP_LOSS", "5"))
DEFAULT_TAKE_PROFIT = float(os.environ.get("AUTO_TRADE_TAKE_PROFIT", "10"))
DEFAULT_MAGIC = int(os.environ.get("AUTO_TRADE_MAGIC", "778899"))


class MT5TradeExecutor(TradeExecutor):
    def __init__(
        self,
        symbol: str = DEFAULT_SYMBOL,
        lot_size: float = DEFAULT_LOT_SIZE,
        stop_loss_distance: float = DEFAULT_STOP_LOSS,
        take_profit_distance: float = DEFAULT_TAKE_PROFIT,
        magic: int = DEFAULT_MAGIC,
        mt5_module=mt5,
    ):
        self._symbol = symbol
        self._lot_size = lot_size
        self._stop_loss_distance = stop_loss_distance
        self._take_profit_distance = take_profit_distance
        self._magic = magic
        self._mt5 = mt5_module
        self._connection = get_shared_connection()

    def execute(self, signal: TradeSignal) -> dict:
        logger.info(
            "%s %s signal on %s @ %s -- attempting trade (sl_distance=%s, tp_distance=%s)",
            signal.source, signal.direction.upper(), signal.timeframe, signal.price_hint,
            signal.sl_distance, signal.tp_distance,
        )

        if not self._connection.is_connected and not self._connection.connect():
            logger.warning("Trade SKIPPED: MT5 not connected")
            return {"success": False, "message": "MT5 not connected"}

        tick = self._mt5.symbol_info_tick(self._symbol)
        if tick is None:
            logger.warning("Trade SKIPPED: no tick for %s", self._symbol)
            return {"success": False, "message": f"No tick for {self._symbol}"}

        is_buy = signal.direction == "buy"
        price = tick.ask if is_buy else tick.bid
        # Prefer the strategy's OWN computed SL/TP distance (see
        # AutoTradingService's {rule.column}_sl/_tp handling) over the one
        # fixed distance every strategy used to share -- falls back to the
        # env-configured default only when a strategy didn't provide one.
        sl_distance = signal.sl_distance if signal.sl_distance is not None else self._stop_loss_distance
        tp_distance = signal.tp_distance if signal.tp_distance is not None else self._take_profit_distance
        sl = price - sl_distance if is_buy else price + sl_distance
        tp = price + tp_distance if is_buy else price - tp_distance

        request = {
            "action": self._mt5.TRADE_ACTION_DEAL,
            "symbol": self._symbol,
            "volume": self._lot_size,
            "type": self._mt5.ORDER_TYPE_BUY if is_buy else self._mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 20,
            "magic": self._magic,
            "comment": f"{signal.source}-{signal.timeframe}"[:31],
            "type_time": self._mt5.ORDER_TIME_GTC,
            "type_filling": self._mt5.ORDER_FILLING_IOC,
        }

        result = self._mt5.order_send(request)
        if result is None:
            logger.warning("Trade FAILED: order_send returned None -- %s", self._mt5.last_error())
            return {"success": False, "message": f"order_send failed: {self._mt5.last_error()}"}

        success = result.retcode == self._mt5.TRADE_RETCODE_DONE
        if success:
            logger.info("Trade EXECUTED: order #%s, %s", getattr(result, "order", None), result.comment)
        else:
            logger.warning("Trade REJECTED by broker: retcode=%s, %s", result.retcode, result.comment)
        return {
            "success": success,
            "message": str(result.comment),
            "order_id": getattr(result, "order", None),
            "retcode": result.retcode,
        }
