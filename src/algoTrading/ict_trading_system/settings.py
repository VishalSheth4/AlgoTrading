"""
config/settings.py
==================
Central configuration for the ICT Trading System.
All parameters, constants, and environment-level settings live here.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple
from enum import Enum


# ─────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────

class Instrument(str, Enum):
    EURUSD  = "EURUSD"
    GBPUSD  = "GBPUSD"
    XAUUSD  = "XAUUSD"   # Gold
    NAS100  = "NAS100"   # NASDAQ
    US500   = "US500"    # S&P 500

class Session(str, Enum):
    ASIAN    = "ASIAN"
    LONDON   = "LONDON"
    NEW_YORK = "NEW_YORK"
    DEAD     = "DEAD"

class Bias(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"

class Timeframe(str, Enum):
    M1  = "1min"
    M5  = "5min"
    M15 = "15min"
    H1  = "1H"
    H4  = "4H"
    D1  = "1D"
    W1  = "1W"

class StrategyID(str, Enum):
    BEGINNER_ICT       = "S1_BEGINNER_ICT"
    OTE_MSS            = "S2_OTE_MSS"
    LONDON_JUDAS       = "S3_LONDON_JUDAS"
    SMT_DIVERGENCE     = "S4_SMT_DIVERGENCE"
    AMD_CYCLE          = "S5_AMD_CYCLE"
    GOLD_MASTER        = "S6_GOLD_MASTER"
    NASDAQ_DRIVE       = "S7_NASDAQ_DRIVE"
    EURUSD_LONDON      = "S8_EURUSD_LONDON"
    GBPUSD_VOLATILITY  = "S9_GBPUSD_VOLATILITY"
    BREAKER_BLOCK      = "S10_BREAKER_BLOCK"
    MASTER_COMBINED    = "S_MASTER"


# ─────────────────────────────────────────────
# RISK CONFIGURATION
# ─────────────────────────────────────────────

@dataclass
class RiskConfig:
    risk_per_trade_pct: float   = 0.01    # 1% max per trade
    risk_per_trade_min: float   = 0.005   # 0.5% minimum
    daily_max_loss_pct: float   = 0.02    # 2% daily drawdown limit
    weekly_max_loss_pct: float  = 0.05    # 5% weekly drawdown limit
    max_open_trades: int        = 2
    min_rr_ratio: float         = 2.0     # 1:2 minimum
    preferred_rr_ratio: float   = 3.0     # 1:3 preferred
    partial_tp_rr: float        = 2.0     # Take partial at 1:2
    breakeven_rr: float         = 1.5     # Move SL to BE at 1:1.5


# ─────────────────────────────────────────────
# SESSION WINDOWS (UTC)
# ─────────────────────────────────────────────

@dataclass
class SessionConfig:
    # (start_hour, end_hour) in UTC
    asian_window: Tuple[int, int]     = (0,  7)
    london_window: Tuple[int, int]    = (7,  12)
    new_york_window: Tuple[int, int]  = (13, 17)
    london_open_prime: Tuple[int, int]= (7,  10)   # Best London window
    ny_open_prime: Tuple[int, int]    = (13, 15)   # Best NY window
    dead_sessions: List[Tuple[int,int]] = field(
        default_factory=lambda: [(12, 13), (17, 23)]
    )


# ─────────────────────────────────────────────
# STRATEGY-LEVEL INSTRUMENT FILTERS
# ─────────────────────────────────────────────

STRATEGY_INSTRUMENTS: Dict[StrategyID, List[Instrument]] = {
    StrategyID.BEGINNER_ICT:      [Instrument.EURUSD, Instrument.GBPUSD, Instrument.XAUUSD],
    StrategyID.OTE_MSS:           [Instrument.EURUSD, Instrument.GBPUSD, Instrument.XAUUSD],
    StrategyID.LONDON_JUDAS:      [Instrument.EURUSD, Instrument.GBPUSD, Instrument.NAS100, Instrument.XAUUSD],
    StrategyID.SMT_DIVERGENCE:    [Instrument.EURUSD, Instrument.GBPUSD, Instrument.NAS100],
    StrategyID.AMD_CYCLE:         [Instrument.EURUSD, Instrument.GBPUSD, Instrument.XAUUSD],
    StrategyID.GOLD_MASTER:       [Instrument.XAUUSD],
    StrategyID.NASDAQ_DRIVE:      [Instrument.NAS100],
    StrategyID.EURUSD_LONDON:     [Instrument.EURUSD],
    StrategyID.GBPUSD_VOLATILITY: [Instrument.GBPUSD],
    StrategyID.BREAKER_BLOCK:     [Instrument.EURUSD, Instrument.GBPUSD, Instrument.XAUUSD],
    StrategyID.MASTER_COMBINED:   list(Instrument),
}

# SMT correlation pairs
SMT_PAIRS: Dict[Instrument, Instrument] = {
    Instrument.EURUSD: Instrument.GBPUSD,
    Instrument.NAS100: Instrument.US500,
}

# OTE Fibonacci zone
OTE_FIBO_LOW:  float = 0.62
OTE_FIBO_HIGH: float = 0.79

# Minimum displacement body size as fraction of candle range
MIN_DISPLACEMENT_BODY_RATIO: float = 0.6

# FVG minimum size in pips
MIN_FVG_PIPS: Dict[Instrument, float] = {
    Instrument.EURUSD:  3.0,
    Instrument.GBPUSD:  4.0,
    Instrument.XAUUSD:  50.0,
    Instrument.NAS100:  20.0,
    Instrument.US500:   5.0,
}

# Pip value per instrument
PIP_SIZE: Dict[Instrument, float] = {
    Instrument.EURUSD:  0.0001,
    Instrument.GBPUSD:  0.0001,
    Instrument.XAUUSD:  0.01,
    Instrument.NAS100:  1.0,
    Instrument.US500:   0.1,
}

# ─────────────────────────────────────────────
# TIMEFRAME HIERARCHY PER STRATEGY
# ─────────────────────────────────────────────

STRATEGY_TIMEFRAMES: Dict[StrategyID, Dict[str, Timeframe]] = {
    StrategyID.BEGINNER_ICT: {
        "bias":  Timeframe.H4,
        "setup": Timeframe.M15,
        "entry": Timeframe.M5,
    },
    StrategyID.OTE_MSS: {
        "bias":  Timeframe.D1,
        "setup": Timeframe.M15,
        "entry": Timeframe.M5,
    },
    StrategyID.LONDON_JUDAS: {
        "bias":  Timeframe.H4,
        "setup": Timeframe.M15,
        "entry": Timeframe.M5,
    },
    StrategyID.GOLD_MASTER: {
        "bias":  Timeframe.H4,
        "setup": Timeframe.M15,
        "entry": Timeframe.M5,
    },
    StrategyID.NASDAQ_DRIVE: {
        "bias":  Timeframe.H4,
        "setup": Timeframe.M15,
        "entry": Timeframe.M5,
    },
    StrategyID.MASTER_COMBINED: {
        "bias":  Timeframe.W1,
        "setup": Timeframe.H4,
        "entry": Timeframe.M15,
    },
}

# ─────────────────────────────────────────────
# LOGGING & DATA PATHS
# ─────────────────────────────────────────────

DATA_DIR    = "data/market_data"
JOURNAL_DIR = "journal/trades"
REPORT_DIR  = "reports"
LOG_LEVEL   = "INFO"