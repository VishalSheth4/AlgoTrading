"""
data/models.py
==============
Core domain models: Candle, MarketStructure, LiquidityLevel,
FairValueGap, OrderBlock, TradeSignal, OpenTrade.
Pure dataclasses — no external dependencies.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
from config.settings import Instrument, Timeframe, Bias, Session, StrategyID


# ─────────────────────────────────────────────
# OHLCV CANDLE
# ─────────────────────────────────────────────

@dataclass
class Candle:
    timestamp: datetime
    open:  float
    high:  float
    low:   float
    close: float
    volume: float = 0.0
    instrument: Instrument = Instrument.EURUSD
    timeframe:  Timeframe  = Timeframe.M5

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def full_range(self) -> float:
        return self.high - self.low

    @property
    def body_ratio(self) -> float:
        return self.body / self.full_range if self.full_range > 0 else 0.0

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def midpoint(self) -> float:
        return (self.high + self.low) / 2

    def __repr__(self):
        direction = "▲" if self.is_bullish else "▼"
        return (f"Candle({self.instrument.value} {self.timeframe.value} "
                f"{self.timestamp:%Y-%m-%d %H:%M} {direction} "
                f"O={self.open:.5f} H={self.high:.5f} "
                f"L={self.low:.5f} C={self.close:.5f})")


# ─────────────────────────────────────────────
# SWING POINT
# ─────────────────────────────────────────────

@dataclass
class SwingPoint:
    timestamp: datetime
    price: float
    is_high: bool     # True = swing high, False = swing low
    timeframe: Timeframe = Timeframe.H4
    broken: bool = False

    @property
    def label(self) -> str:
        return "SwingHigh" if self.is_high else "SwingLow"


# ─────────────────────────────────────────────
# MARKET STRUCTURE
# ─────────────────────────────────────────────

@dataclass
class MarketStructure:
    instrument:  Instrument
    timeframe:   Timeframe
    bias:        Bias
    last_hh:     Optional[SwingPoint] = None   # Higher High
    last_hl:     Optional[SwingPoint] = None   # Higher Low
    last_lh:     Optional[SwingPoint] = None   # Lower High
    last_ll:     Optional[SwingPoint] = None   # Lower Low
    last_updated: datetime = field(default_factory=datetime.utcnow)

    @property
    def is_bullish(self) -> bool:
        return self.bias == Bias.BULLISH

    @property
    def is_bearish(self) -> bool:
        return self.bias == Bias.BEARISH


# ─────────────────────────────────────────────
# MARKET STRUCTURE SHIFT (MSS)
# ─────────────────────────────────────────────

@dataclass
class MarketStructureShift:
    instrument:   Instrument
    timeframe:    Timeframe
    direction:    Bias          # direction AFTER the shift
    break_price:  float         # price level that was broken
    displacement_candle: Optional[Candle] = None
    timestamp:    datetime = field(default_factory=datetime.utcnow)
    confirmed:    bool = False

    @property
    def is_bullish_mss(self) -> bool:
        return self.direction == Bias.BULLISH

    @property
    def is_bearish_mss(self) -> bool:
        return self.direction == Bias.BEARISH


# ─────────────────────────────────────────────
# LIQUIDITY LEVEL
# ─────────────────────────────────────────────

@dataclass
class LiquidityLevel:
    instrument:  Instrument
    price:       float
    level_type:  str          # "equal_highs","equal_lows","prev_day_high","prev_day_low",
                              # "weekly_high","weekly_low","session_high","session_low"
    timeframe:   Timeframe
    formed_at:   datetime
    swept:       bool  = False
    swept_at:    Optional[datetime] = None
    sweep_wick:  float = 0.0  # how far price went past the level

    @property
    def is_buy_side(self) -> bool:
        """Buy-side liquidity sits ABOVE price (equal highs / prev highs)."""
        return "high" in self.level_type.lower()

    @property
    def is_sell_side(self) -> bool:
        return not self.is_buy_side


# ─────────────────────────────────────────────
# FAIR VALUE GAP (FVG / IFVG)
# ─────────────────────────────────────────────

@dataclass
class FairValueGap:
    instrument:  Instrument
    timeframe:   Timeframe
    direction:   Bias          # BULLISH = gap above, BEARISH = gap below
    top:         float
    bottom:      float
    formed_at:   datetime
    candle_index: int = 0      # index of the middle candle in the 3-candle sequence
    filled:      bool  = False
    filled_at:   Optional[datetime] = None

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def size(self) -> float:
        return self.top - self.bottom

    @property
    def is_bullish(self) -> bool:
        return self.direction == Bias.BULLISH


# ─────────────────────────────────────────────
# ORDER BLOCK
# ─────────────────────────────────────────────

@dataclass
class OrderBlock:
    instrument:  Instrument
    timeframe:   Timeframe
    direction:   Bias          # direction of displacement AFTER the OB
    top:         float
    bottom:      float
    formed_at:   datetime
    broken:      bool  = False   # True if OB becomes a Breaker Block
    is_breaker:  bool  = False
    tested:      bool  = False

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def is_bullish(self) -> bool:
        return self.direction == Bias.BULLISH


# ─────────────────────────────────────────────
# PREMIUM / DISCOUNT ZONE
# ─────────────────────────────────────────────

@dataclass
class PremiumDiscountZone:
    instrument:  Instrument
    swing_high:  float
    swing_low:   float
    equilibrium: float = 0.0

    def __post_init__(self):
        self.equilibrium = (self.swing_high + self.swing_low) / 2

    def is_premium(self, price: float) -> bool:
        """Above 50% = premium (sell zone)."""
        return price > self.equilibrium

    def is_discount(self, price: float) -> bool:
        """Below 50% = discount (buy zone)."""
        return price < self.equilibrium

    def ote_zone(self) -> tuple:
        """62%–79% retracement zone."""
        rng = self.swing_high - self.swing_low
        low_ote  = self.swing_high - 0.79 * rng
        high_ote = self.swing_high - 0.62 * rng
        return (low_ote, high_ote)

    def in_ote_zone(self, price: float) -> bool:
        lo, hi = self.ote_zone()
        return lo <= price <= hi


# ─────────────────────────────────────────────
# SMT DIVERGENCE
# ─────────────────────────────────────────────

@dataclass
class SMTDivergence:
    primary_instrument:    Instrument
    correlated_instrument: Instrument
    direction:             Bias     # BULLISH = primary makes LL, correlated doesn't
    primary_low:           float
    correlated_low:        float
    timestamp:             datetime
    confirmed:             bool = False


# ─────────────────────────────────────────────
# TRADE SIGNAL
# ─────────────────────────────────────────────

@dataclass
class TradeSignal:
    strategy_id:    StrategyID
    instrument:     Instrument
    direction:      Bias
    entry_price:    float
    stop_loss:      float
    take_profit_1:  float         # Primary TP (1:2)
    take_profit_2:  float         # Extended TP (1:3+)
    session:        Session
    timestamp:      datetime
    rr_ratio:       float = 0.0
    confidence:     float = 0.0   # 0.0–1.0 checklist score
    checklist_score: int  = 0     # items passed out of total
    checklist_total: int  = 0
    notes:          str   = ""
    valid:          bool  = True

    def __post_init__(self):
        risk  = abs(self.entry_price - self.stop_loss)
        reward = abs(self.take_profit_1 - self.entry_price)
        self.rr_ratio = round(reward / risk, 2) if risk > 0 else 0.0

    @property
    def is_long(self) -> bool:
        return self.direction == Bias.BULLISH

    @property
    def risk_pips(self) -> float:
        return abs(self.entry_price - self.stop_loss)


# ─────────────────────────────────────────────
# OPEN / CLOSED TRADE
# ─────────────────────────────────────────────

@dataclass
class Trade:
    trade_id:       str
    signal:         TradeSignal
    account_size:   float
    risk_amount:    float          # $ at risk
    position_size:  float          # lots / units
    open_time:      datetime
    close_time:     Optional[datetime] = None
    close_price:    float = 0.0
    pnl:            float = 0.0
    pnl_r:          float = 0.0    # PnL in R multiples
    partial_closed: bool  = False
    status:         str   = "OPEN" # OPEN | PARTIAL | CLOSED | STOPPED

    def close(self, price: float, timestamp: datetime):
        self.close_price = price
        self.close_time  = timestamp
        risk = abs(self.signal.entry_price - self.signal.stop_loss)
        pnl_per_unit = (price - self.signal.entry_price) if self.signal.is_long \
                       else (self.signal.entry_price - price)
        self.pnl   = pnl_per_unit * self.position_size
        self.pnl_r = pnl_per_unit / risk if risk > 0 else 0.0
        self.status = "CLOSED"