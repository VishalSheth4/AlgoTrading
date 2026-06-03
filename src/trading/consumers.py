"""
WebSocket consumers — MT5 data only.

PriceConsumer  ws/price/<symbol>/
  ├─ On connect : full OHLCV history (from live_data.csv if MT5 active, else sample_data.csv)
  │               + supertrend overlay + trade markers
  └─ Every 1 s  : MT5 tick → {price, bid, ask, change, bar}
                  If MT5 is offline, sends last known price with source="OFFLINE"

TradesConsumer  ws/trades/
  ├─ On connect : full trade analytics from trade_data.csv
  └─ Polls every 3 s : re-sends when trade_data.csv changes
"""

import json
import asyncio
import time
import sys
from pathlib import Path

from channels.generic.websocket import AsyncWebsocketConsumer

_SRC = Path(__file__).resolve().parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# ── Sync helpers (run in thread pool via asyncio.to_thread) ───────────────────

def _get_ohlcv_snapshot(limit: int = 600) -> dict:
    from trading.mt5_service import load_ohlcv
    try:
        ohlcv, st, markers = load_ohlcv(limit)
        return {"bars": ohlcv, "supertrend": st, "markers": markers}
    except Exception as exc:
        return {"bars": [], "supertrend": [], "markers": [], "error": str(exc)}


def _get_mt5_tick(symbol: str) -> dict | None:
    """
    Return live MT5 tick data as a dict, or None if MT5 is unavailable.
    Uses credentials from Config if the terminal needs login.
    """
    try:
        import MetaTrader5 as mt5
        from trading.mt5_service import _mt5_connect
        if not _mt5_connect():
            return None

        tick = mt5.symbol_info_tick(symbol)
        mt5.shutdown()

        if tick is None or tick.bid <= 0:
            return None

        return {
            "bid":   round(tick.bid, 2),
            "ask":   round(tick.ask, 2),
            "price": round((tick.bid + tick.ask) / 2, 2),
            "time":  tick.time,   # unix seconds from MT5
        }
    except Exception as exc:
        print(f"[tick] {symbol} error: {exc}")
        return None


def _get_csv_last_price() -> float | None:
    """Last close from the active OHLCV CSV (live_data.csv or sample_data.csv)."""
    try:
        from trading.mt5_service import active_csv
        import pandas as pd
        df = pd.read_csv(active_csv(), usecols=["close"])
        return round(float(df["close"].iloc[-1]), 2)
    except Exception as exc:
        print(f"[price] CSV read failed: {exc}")
        return None


def _get_analytics() -> dict:
    from trading.mt5_service import compute_trade_analytics
    return compute_trade_analytics()


# ── Price Consumer ─────────────────────────────────────────────────────────────

class PriceConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.symbol   = self.scope["url_route"]["kwargs"]["symbol"]
        self._running = False
        await self.accept()

        # ── 1. Send full OHLCV history ────────────────────────────────────────
        snap = await asyncio.to_thread(_get_ohlcv_snapshot, 600)
        await self.send(json.dumps({"type": "history", **snap}))

        # ── 2. Get initial price from MT5 or CSV (no simulation) ─────────────
        tick = await asyncio.to_thread(_get_mt5_tick, self.symbol)
        if tick:
            self._price    = tick["price"]
            self._day_open = tick["price"]
            src = "MT5"
        else:
            csv_price = await asyncio.to_thread(_get_csv_last_price)
            self._price    = csv_price or 0.0
            self._day_open = self._price
            src = "CSV" if csv_price else "OFFLINE"

        # Bar tracking
        self._bar_open = self._price
        self._bar_high = self._price
        self._bar_low  = self._price
        self._bar_ts   = (int(time.time()) // 60) * 60

        # Tell frontend the initial price + source
        await self.send(json.dumps({
            "type":   "price_source",
            "source": src,
            "price":  self._price,
            "symbol": self.symbol,
        }))

        self._running = True
        asyncio.ensure_future(self._stream())

    async def disconnect(self, code):
        self._running = False

    async def _stream(self):
        while self._running:
            tick = await asyncio.to_thread(_get_mt5_tick, self.symbol)

            if tick:
                price  = tick["price"]
                bid    = tick["bid"]
                ask    = tick["ask"]
                source = "MT5"
            else:
                # MT5 offline — hold last known price, mark OFFLINE, do NOT simulate
                price  = self._price
                bid    = round(price - 0.20, 2) if price else 0.0
                ask    = round(price + 0.20, 2) if price else 0.0
                source = "OFFLINE"

            now    = int(time.time())
            bar_ts = (now // 60) * 60

            if bar_ts > self._bar_ts:
                self._bar_ts   = bar_ts
                self._bar_open = price
                self._bar_high = price
                self._bar_low  = price
            else:
                if price > 0:
                    self._bar_high = max(self._bar_high, price)
                    self._bar_low  = min(self._bar_low,  price)

            self._price = price

            change     = round(price - self._day_open, 2) if self._day_open else 0.0
            change_pct = round(change / self._day_open * 100, 3) if self._day_open else 0.0

            payload = {
                "type":       "tick",
                "price":      price,
                "bid":        bid,
                "ask":        ask,
                "change":     change,
                "change_pct": change_pct,
                "time":       now,
                "source":     source,
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
