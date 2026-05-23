# =============================
# config.py
# =============================

class Config:
    TIMEFRAME = "M15"
    INITIAL_CAPITAL = 1000
    STRATEGY = "engulfing_consolidation,RSIBuySellStrategy,mark_dollar_supertrend,mark2,Ema200PullbackEngulfingStrategy,SupertrendCounterFlip_X1,EmaCrossoverRetestStrategy"
    # SupertrendCounterFlip_X1"
    # comma-separated for multi-strategy: STRATEGY = "mark2,mark_dollar_supertrend,engulfing""
    #Ema200PullbackEngulfingStrategy , EmaCrossoverRetestStrategy 
    # RSIBuySellStrategy
    RISK_PER_TRADE = 0.04
    #SYMBOL = "XAUUSD"           # comma-separated for multi-symbol: "XAUUSD,EURUSD,GBPUSD,AUDUSD"
    SYMBOL = "XAUUSD"#,EURUSD,GBPUSD,USDJPY,XAGUSD"
    LOT_SIZE = 0.01
    STOP_LOSS = 50
    TAKE_PROFIT = 100
    MODE = "mt5"   # "mt5" → fetch live from MetaTrader5 | "csv" → use local CSV (Kaggle)

    # MT5 account credentials — leave None to use whatever terminal is already open
    MT5_LOGIN    = 52879886
    MT5_PASSWORD = "zF!X0XEP1hvwWP"
    MT5_SERVER   = "ICMarketsSC-Demo"

    # Backtest date range — set both to filter bars, or None to use all data.
    # Format: "YYYY-MM-DD"
    START_DATE = "2016-01-01"       # earliest IC Markets M5 bar
    END_DATE   = "2026-06-01"       # up to latest available bar

    # -------------------------------------------------------
    # Mark2 / MarkDollarSuperTrend TP Settings
    #
    # TP_MODE   : which target to use
    #   "rr"         → R:R ratio  (entry ± RR × risk)
    #   "st"         → Supertrend line
    #   "both"       → whichever is hit first (closer of the two)
    #   "fix_profit" → fixed price distance regardless of risk
    #                  LONG  TP = entry + FIX_PROFIT
    #                  SHORT TP = entry - FIX_PROFIT
    #
    # RR         : multiplier used when TP_MODE = "rr" or "both"
    # FIX_PROFIT : price units of profit when TP_MODE = "fix_profit"
    #              e.g. 5 → $5 for XAUUSD, 50 pips for EURUSD (0.0050)
    # -------------------------------------------------------
    RR         = 5
    TP_MODE    = "rr"   # "rr" | "st" | "both" | "fix_profit"
    FIX_PROFIT = 5      # price-unit target when TP_MODE = "fix_profit"

    # Minimum candles the PREVIOUS trend must have lasted before the flip
    # counts as a valid X/Y candle.  Filters 1-2 candle micro-flips caused
    # by MT5 vs TradingView data discrepancies.  Set to 1 to disable.
    MIN_TREND_CANDLES = 1

    # Maximum candle size (high - low) allowed for a signal candle.
    # Candles larger than this are skipped to avoid chasing volatile spikes.
    # Set to None to disable.
    MAX_CANDLE_SIZE = 20

    # Stop taking new trades after this many losses in one calendar day.
    # Set to None to disable.
    MAX_DAILY_LOSSES = 2

