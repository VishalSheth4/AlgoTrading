"""
WebSocket consumers for real-time price streaming and trade updates.

PriceConsumer  — ws/price/<symbol>/
  On connect : sends full OHLCV history + supertrend + markers
  Every 1 s  : sends live tick (price, bid, ask, daily change, forming bar)

TradesConsumer — ws/trades/
  On connect : sends full trade analytics
  Polls every 3 s: re-sends if trade_data.csv mtime changed
"""

import json
import asyncio
import time
import random
import sys
from pathlib import Path

from channels.generic.websocket import AsyncWebsocketConsumer

_SRC = Path(__file__).resolve().parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# ── Helpers (sync, run in thread pool) ────────────────────────────────────────

def _get_ohlcv_snapshot(limit: int = 600) -> dict:
    from trading.mt5_service import load_ohlcv
    try:
        ohlcv, st, markers = load_ohlcv(limit)
        return {"bars": ohlcv, "supertrend": st, "markers": markers}
    except Exception as exc:
        return {"bars": [], "supertrend": [], "markers": [], "error": str(exc)}


def _get_last_close() -> float:
    from trading.mt5_service import active_csv
    import pandas as pd
    try:
        df = pd.read_csv(active_csv(), usecols=["close"])
        return float(df["close"].iloc[-1])
    except Exception:
        return 2000.0


def _try_mt5_tick(symbol: str) -> float | None:
    """Return mid-price from live MT5, or None if unavailable."""
    try:
        import MetaTrader5 as mt5
        if mt5.initialize():
            tick = mt5.symbol_info_tick(symbol)
            mt5.shutdown()
            if tick and tick.bid > 0:
                return round((tick.bid + tick.ask) / 2, 2)
    except Exception:
        pass
    return None


def _get_analytics() -> dict:
    from trading.mt5_service import compute_trade_analytics
    return compute_trade_analytics()


# ── Price Consumer ─────────────────────────────────────────────────────────────

class PriceConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.symbol  = self.scope["url_route"]["kwargs"]["symbol"]
        self._running = False
        await self.accept()

        # 1. Send historical bars
        snap = await asyncio.to_thread(_get_ohlcv_snapshot, 600)
        await self.send(json.dumps({"type": "history", **snap}))

        # 2. Bootstrap live-price state
        self._price      = await asyncio.to_thread(_get_last_close)
        self._day_open   = self._price
        self._bar_open   = self._price
        self._bar_high   = self._price
        self._bar_low    = self._price
        self._bar_ts     = (int(time.time()) // 60) * 60  # minute boundary

        self._running = True
        asyncio.ensure_future(self._stream())

    async def disconnect(self, code):
        self._running = False

    async def _stream(self):
        SPREAD = 0.40          # XAUUSD typical bid-ask spread
        SIGMA  = 0.25          # random walk std-dev per second

        while self._running:
            # Try real MT5, fall back to Brownian motion simulation
            live = await asyncio.to_thread(_try_mt5_tick, self.symbol)
            if live is not None:
                price = live
            else:
                price = round(max(1.0, self._price + random.gauss(0, SIGMA)), 2)

            now     = int(time.time())
            bar_ts  = (now // 60) * 60

            if bar_ts > self._bar_ts:          # new minute → close old bar, open new
                self._bar_ts   = bar_ts
                self._bar_open = price
                self._bar_high = price
                self._bar_low  = price
            else:
                self._bar_high = max(self._bar_high, price)
                self._bar_low  = min(self._bar_low,  price)

            self._price = price

            change     = round(price - self._day_open, 2)
            change_pct = round(change / self._day_open * 100, 3) if self._day_open else 0.0

            payload = {
                "type":       "tick",
                "price":      price,
                "bid":        round(price - SPREAD / 2, 2),
                "ask":        round(price + SPREAD / 2, 2),
                "change":     change,
                "change_pct": change_pct,
                "time":       now,
                "bar": {
                    "time":  self._bar_ts,
                    "open":  round(self._bar_open, 2),
                    "high":  round(self._bar_high, 2),
                    "low":   round(self._bar_low,  2),
                    "close": price,
                },
            }

            try:
                await self.send(json.dumps(payload))
            except Exception:
                break

            await asyncio.sleep(1)


# ── Trades Consumer ────────────────────────────────────────────────────────────

class TradesConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        await self.accept()
        self._running = True

        data = await asyncio.to_thread(_get_analytics)
        await self.send(json.dumps({"type": "trades", "data": data}))

        asyncio.ensure_future(self._poll())

    async def disconnect(self, code):
        self._running = False

    async def _poll(self):
        from trading.mt5_service import TRADE_CSV
        last_mtime = TRADE_CSV.stat().st_mtime if TRADE_CSV.exists() else 0.0

        while self._running:
            await asyncio.sleep(3)
            try:
                mtime = TRADE_CSV.stat().st_mtime if TRADE_CSV.exists() else 0.0
                if mtime != last_mtime:
                    last_mtime = mtime
                    data = await asyncio.to_thread(_get_analytics)
                    await self.send(json.dumps({"type": "trades", "data": data}))
            except Exception:
                pass
