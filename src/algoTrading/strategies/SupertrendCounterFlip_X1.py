import numpy as np
import pandas as pd
from algoTrading.config import Config
from algoTrading.strategies.mark2_strategy import Mark2Strategy


class SupertrendCounterFlipX1Strategy(Mark2Strategy):
    """
    Strategy  : SupertrendCounterFlip_X1
    Entry mode: X+1  (one candle AFTER the Supertrend flip candle)

    ┌─────────────────────────────────────────────────────────────────┐
    │  200 EMA FILTER  (applied before any entry)                     │
    │  LONG  trades → only when X+1 close is ABOVE 200 EMA           │
    │  SHORT trades → only when X+1 close is BELOW 200 EMA           │
    ├─────────────────────────────────────────────────────────────────┤
    │  SHORT ENTRY  (counter-trend on BUY flip)                       │
    │  Step 1 → Supertrend flips to BUY  (trend = +1)  → X candle   │
    │  Step 2 → X+1 is BEARISH  (close < open)                       │
    │           + close < 200 EMA                                     │
    │           + body >= 50% of range   (no doji / wicks)           │
    │           + range <= MAX_CANDLE_SIZE  (if configured)           │
    │           → Enter SHORT at close of X+1                        │
    │  SL  = X candle high × (1 + 1%)   — candle close basis only   │
    │  TP  = Supertrend line (dynamic); updated every candle;        │
    │        exit when close <= st_line                               │
    ├─────────────────────────────────────────────────────────────────┤
    │  LONG ENTRY  (counter-trend on SELL flip)                       │
    │  Step 1 → Supertrend flips to SELL (trend = -1)  → X candle   │
    │  Step 2 → X+1 is BULLISH ENGULFING vs X candle                 │
    │           + close > 200 EMA                                     │
    │           + body >= 50% of range   (no doji / wicks)           │
    │           + range <= MAX_CANDLE_SIZE  (if configured)           │
    │           → Enter LONG at close of X+1                         │
    │  SL  = X candle low × (1 - 1%)    — candle close basis only   │
    │  TP  = Supertrend line (dynamic); updated every candle;        │
    │        exit when close >= st_line                               │
    ├─────────────────────────────────────────────────────────────────┤
    │  OTHER EXITS  (checked on candle close only)                    │
    │  • Supertrend flips against trade → trend_flip exit            │
    │  • Close breaches SL              → sl exit                    │
    │  • Close reaches ST line          → tp_st exit                 │
    ├─────────────────────────────────────────────────────────────────┤
    │  TP UPDATE RULE                                                 │
    │  While a trade is open, df['tp'] is updated to the current     │
    │  Supertrend line value on every candle close.  This means the  │
    │  target displayed in the backtester / dashboard always shows   │
    │  the live ST level, not the stale entry-bar value.             │
    ├─────────────────────────────────────────────────────────────────┤
    │  CANDLE SIZE RULES  (applied to X+1 candle range = high - low)  │
    │  range > $10  → skip trade entirely (candle too volatile)      │
    │  range < $3   → enter with DOUBLE lot size (tight candle)      │
    │  $3 – $10     → enter with normal lot size                     │
    ├─────────────────────────────────────────────────────────────────┤
    │  TRADE LIMIT                                                    │
    │  One trade per Supertrend segment.                              │
    │  If X+1 candle does not qualify → entire segment is skipped.   │
    └─────────────────────────────────────────────────────────────────┘
    """

    # ── Identity ──────────────────────────────────────────────────────
    _STRATEGY_KEY  = "supertrend_counter_flip_x1"
    _STRATEGY_NAME = "SupertrendCounterFlip_X1"
    _ENTRY_MODE    = "X+1"

    def __init__(self, period=10, multiplier=3, ema_period=200):
        super().__init__(period, multiplier)
        self.max_candle_size     = getattr(Config, 'MAX_CANDLE_SIZE', None)
        self.sl_buffer_pct       = 0.01        # 1% buffer on SL
        self.ema_period          = ema_period  # trend bias EMA (default 200)
        self.candle_skip_above   = 8.0        # skip X+1 if range > $10
        self.candle_double_below = 3.0         # double lot if range < $3

    # ─────────────────────────────────────────────────────────────────
    # Candle quality filters
    # ─────────────────────────────────────────────────────────────────

    def _candle_too_big(self, row) -> bool:
        """True if candle range exceeds MAX_CANDLE_SIZE."""
        if self.max_candle_size is None:
            return False
        return (row['high'] - row['low']) > self.max_candle_size

    def _candle_range_filter(self, row):
        """
        Returns the lot multiplier for this candle, or 0 to skip entirely.

        range > $10  → 0   (skip — too volatile)
        range < $3   → 2.0 (double lot — tight / low-risk candle)
        $3 – $10     → 1.0 (normal lot)
        """
        candle_range = row['high'] - row['low']
        if candle_range > self.candle_skip_above:
            return 0          # skip trade
        if candle_range < self.candle_double_below:
            return 2.0        # double lot
        return 1.0            # normal lot

    def _candle_strong(self, row) -> bool:
        """
        True if candle body >= 50% of high-low range.
        Rejects doji / indecision / wick-heavy candles.
        """
        full_range = row['high'] - row['low']
        if full_range <= 0:
            return False
        body = abs(row['close'] - row['open'])
        return (body / full_range) >= 0.5

    def _is_bearish(self, row) -> bool:
        """True if close < open."""
        return row['close'] < row['open']

    def _is_bullish_engulfing(self, prev_row, curr_row) -> bool:
        """
        True if curr_row bullish-engulfs prev_row.
          - curr_row is bullish       (close > open)
          - curr_row opens at or below prev_row close
          - curr_row closes at or above prev_row open
        """
        return (
            curr_row['close'] > curr_row['open']
            and curr_row['open']  <= prev_row['close']
            and curr_row['close'] >= prev_row['open']
        )

    # ─────────────────────────────────────────────────────────────────
    # Resolve Supertrend column names (base-class agnostic)
    # ─────────────────────────────────────────────────────────────────

    def _resolve_st_columns(self, df: pd.DataFrame):
        """Return (trend_col, st_col) regardless of base-class naming."""
        if 'st_trend' in df.columns:
            trend_col = 'st_trend'
        elif 'trend' in df.columns:
            trend_col = 'trend'
        else:
            raise KeyError(
                "calculate_supertrend() did not produce a trend column. "
                f"Available: {df.columns.tolist()}"
            )

        for candidate in ('st_value', 'supertrend', 'st'):
            if candidate in df.columns:
                st_col = candidate
                break
        else:
            raise KeyError(
                "calculate_supertrend() did not produce a supertrend-line column. "
                f"Available: {df.columns.tolist()}"
            )

        return trend_col, st_col

    # ─────────────────────────────────────────────────────────────────
    # Signal generation  —  X+1 entry mode  (LONG + SHORT)
    # ─────────────────────────────────────────────────────────────────

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = self.calculate_supertrend(df)

        # ── 200 EMA bias filter ───────────────────────────────────────
        df['ema200'] = df['close'].ewm(span=self.ema_period, adjust=False).mean()
        ema200 = df['ema200'].values

        # ── Output columns ────────────────────────────────────────────
        df['signal']      = 0     # +1 = long, -1 = short
        df['sl']          = np.nan
        df['tp']          = np.nan
        df['lot']         = 0.0
        df['entry_mode']  = ''
        df['exit_reason'] = ''    # 'sl' | 'tp_st' | 'trend_flip'

        trend_col, st_col = self._resolve_st_columns(df)

        trend   = df[trend_col].values
        st_line = df[st_col].values
        closes  = df['close'].values
        highs   = df['high'].values
        lows    = df['low'].values
        n       = len(df)

        # ── State machine ─────────────────────────────────────────────
        # watching_x1  : flip seen on candle i; next candle is X+1
        # pending_dir  : +1 = watching for LONG  |  -1 = watching for SHORT
        # x_sl_price   : SL computed from X candle
        # segment_done : no more entries allowed this segment
        watching_x1  = False
        pending_dir  = 0
        x_sl_price   = None
        segment_done = False

        for i in range(1, n):

            # ── Supertrend flip ───────────────────────────────────────
            if trend[i] != trend[i - 1]:
                # Reset state on every flip
                watching_x1  = False
                pending_dir  = 0
                x_sl_price   = None
                segment_done = False

                if trend[i] == 1:
                    # BUY flip → X candle; watch X+1 for SHORT
                    # SL = X candle high + 1% buffer
                    x_sl_price  = highs[i] * (1 + self.sl_buffer_pct)
                    pending_dir = -1
                    watching_x1 = True

                elif trend[i] == -1:
                    # SELL flip → X candle; watch X+1 for LONG
                    # SL = X candle low - 1% buffer
                    x_sl_price  = lows[i] * (1 - self.sl_buffer_pct)
                    pending_dir = 1
                    watching_x1 = True

                continue

            if segment_done:
                continue

            # ── X+1 evaluation ────────────────────────────────────────
            if watching_x1:
                watching_x1 = False           # one shot only
                row_x1 = df.iloc[i]
                row_x  = df.iloc[i - 1]       # X candle (the flip candle)

                # ── SHORT: BUY flip + bearish X+1 + below 200 EMA ────
                if pending_dir == -1:
                    lot_mult = self._candle_range_filter(row_x1)
                    if (lot_mult > 0
                            and closes[i] < ema200[i]
                            and self._is_bearish(row_x1)
                            and self._candle_strong(row_x1)
                            and not self._candle_too_big(row_x1)):

                        entry = closes[i]
                        risk  = x_sl_price - entry   # sl above entry

                        if risk > 0:
                            df.at[i, 'signal']     = -1
                            df.at[i, 'sl']         = x_sl_price
                            df.at[i, 'tp']         = st_line[i]  # initial ST; updated dynamically below
                            df.at[i, 'lot']        = self.lot_size * lot_mult
                            df.at[i, 'entry_mode'] = self._ENTRY_MODE

                # ── LONG: SELL flip + bullish engulfing X+1 + above 200 EMA ──
                elif pending_dir == 1:
                    lot_mult = self._candle_range_filter(row_x1)
                    if (lot_mult > 0
                            and closes[i] > ema200[i]
                            and self._is_bullish_engulfing(row_x, row_x1)
                            and self._candle_strong(row_x1)
                            and not self._candle_too_big(row_x1)):

                        entry = closes[i]
                        risk  = entry - x_sl_price   # sl below entry

                        if risk > 0:
                            df.at[i, 'signal']     = 1
                            df.at[i, 'sl']         = x_sl_price
                            df.at[i, 'tp']         = st_line[i]  # initial ST; updated dynamically below
                            df.at[i, 'lot']        = self.lot_size * lot_mult
                            df.at[i, 'entry_mode'] = self._ENTRY_MODE

                segment_done = True   # one trade per segment regardless

        # ── Second pass: candle-close-based exit marking ──────────────
        # SL and TP only checked on candle CLOSE — never intra-bar.
        # TP (df['tp']) is updated to the live Supertrend line on every
        # candle while a trade is open, so the target always reflects
        # the current ST level rather than the stale entry-bar value.
        sigs    = df['signal'].values
        sl_vals = df['sl'].values

        trade_idx = None   # row index of the open trade
        trade_dir = 0      # direction of open trade: +1 or -1

        for i in range(1, n):
            if sigs[i] != 0:
                trade_idx = i
                trade_dir = int(sigs[i])
                continue

            if trade_idx is None:
                continue

            sl_price    = sl_vals[trade_idx]
            close_price = closes[i]
            st_val      = st_line[i]
            flipped     = trend[i] != trend[i - 1]

            # ── Dynamic TP: update tp to current ST line every candle ──
            # This keeps df['tp'] live so the backtester / dashboard
            # always displays the correct target price as ST moves.
            df.at[i, 'tp'] = st_val

            if trade_dir == -1:
                # ── SHORT exits ──────────────────────────────────────
                if close_price >= sl_price:
                    # SL hit: close crossed above SL on candle close
                    df.at[i, 'exit_reason'] = 'sl'
                    trade_idx = None

                elif flipped and trend[i] == -1:
                    # ST flipped to SELL (opposite of original BUY flip)
                    df.at[i, 'exit_reason'] = 'trend_flip'
                    trade_idx = None

                elif close_price <= st_val:
                    # TP hit: price touched / crossed Supertrend line (support)
                    df.at[i, 'tp']          = st_val   # lock final TP at exit bar
                    df.at[i, 'exit_reason'] = 'tp_st'
                    trade_idx = None

            elif trade_dir == 1:
                # ── LONG exits ───────────────────────────────────────
                if close_price <= sl_price:
                    # SL hit: close crossed below SL on candle close
                    df.at[i, 'exit_reason'] = 'sl'
                    trade_idx = None

                elif flipped and trend[i] == 1:
                    # ST flipped to BUY (opposite of original SELL flip)
                    df.at[i, 'exit_reason'] = 'trend_flip'
                    trade_idx = None

                elif close_price >= st_val:
                    # TP hit: price touched / crossed Supertrend line (resistance)
                    df.at[i, 'tp']          = st_val   # lock final TP at exit bar
                    df.at[i, 'exit_reason'] = 'tp_st'
                    trade_idx = None

        return df