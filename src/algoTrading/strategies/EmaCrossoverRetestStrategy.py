import numpy as np
import pandas as pd

from algoTrading.strategies.SupertrendEngulfingReversalStrategy import Mark2Strategy


class EmaCrossoverRetestStrategy(Mark2Strategy):
    """
    EMA 20/50 crossover + 3rd retest strategy
    with fixed Risk:Reward target.

    LONG:
    -----
    1. EMA20 crosses above EMA50
    2. Wait for 3 bullish EMA retests
    3. Buy on 3rd retest
    4. SL below EMA50
    5. TP = 1:5 RR

    SHORT:
    ------
    1. EMA20 crosses below EMA50
    2. Wait for 3 bearish EMA retests
    3. Sell on 3rd retest
    4. SL above EMA50
    5. TP = 1:5 RR
    """

    _STRATEGY_KEY = "ema_crossover_retest"
    _STRATEGY_NAME = "EMA_Crossover_Retest"
    _ENTRY_MODE = "3rd_retest"

    def __init__(
        self,
        fast_ema=20,
        slow_ema=50,
        sl_pips=20,
        pip_size=0.1,
        ema_gap_filter=0.6,
        risk_reward=2
    ):

        super().__init__(period=10, multiplier=3)

        self.fast_ema = fast_ema
        self.slow_ema = slow_ema

        self.sl_pips = sl_pips
        self.pip_size = pip_size
        self.sl_offset = sl_pips * pip_size

        self.ema_gap_filter = ema_gap_filter
        self.risk_reward = risk_reward

    # ==========================================================
    # CROSSOVER HELPERS
    # ==========================================================

    def _is_bull_crossover(
        self,
        f_prev,
        s_prev,
        f_curr,
        s_curr
    ):

        return (
            f_prev <= s_prev and
            f_curr > s_curr
        )

    def _is_bear_crossover(
        self,
        f_prev,
        s_prev,
        f_curr,
        s_curr
    ):

        return (
            f_prev >= s_prev and
            f_curr < s_curr
        )

    # ==========================================================
    # RETEST HELPERS
    # ==========================================================

    def _is_long_retest(
        self,
        low,
        high,
        open_price,
        close,
        ema20
    ):

        body = abs(close - open_price)
        candle_range = high - low

        if candle_range <= 0:
            return False

        body_strength = body / candle_range

        return (
            low <= ema20 and
            close > ema20 and
            close > open_price and
            body_strength > 0.5
        )

    def _is_short_retest(
        self,
        high,
        low,
        open_price,
        close,
        ema20
    ):

        body = abs(close - open_price)
        candle_range = high - low

        if candle_range <= 0:
            return False

        body_strength = body / candle_range

        return (
            high >= ema20 and
            close < ema20 and
            close < open_price and
            body_strength > 0.5
        )

    # ==========================================================
    # MAIN SIGNAL GENERATOR
    # ==========================================================

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:

        df = df.copy()

        # ======================================================
        # EMA CALCULATION
        # ======================================================

        df['ema20'] = (
            df['close']
            .ewm(span=self.fast_ema, adjust=False)
            .mean()
        )

        df['ema50'] = (
            df['close']
            .ewm(span=self.slow_ema, adjust=False)
            .mean()
        )

        # ======================================================
        # OUTPUT COLUMNS
        # ======================================================

        df['signal'] = 0
        df['sl'] = np.nan
        df['tp'] = np.nan
        df['lot'] = 0.0
        df['entry_mode'] = ''
        df['exit_reason'] = ''
        df['retest_count'] = 0

        # ======================================================
        # ARRAYS
        # ======================================================

        ema20 = df['ema20'].values
        ema50 = df['ema50'].values

        opens = df['open'].values
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values

        n = len(df)

        # ======================================================
        # STATE VARIABLES
        # ======================================================

        pending_dir = 0
        crossover_active = False
        retest_count = 0
        segment_done = False

        last_retest_idx = -100

        # ======================================================
        # ENTRY LOGIC
        # ======================================================

        for i in range(3, n):

            f_prev = ema20[i - 1]
            s_prev = ema50[i - 1]

            f_curr = ema20[i]
            s_curr = ema50[i]

            bull_cross = self._is_bull_crossover(
                f_prev,
                s_prev,
                f_curr,
                s_curr
            )

            bear_cross = self._is_bear_crossover(
                f_prev,
                s_prev,
                f_curr,
                s_curr
            )

            # ==================================================
            # NEW CROSSOVER
            # ==================================================

            if bull_cross or bear_cross:

                crossover_active = True
                retest_count = 0
                segment_done = False
                last_retest_idx = -100

                pending_dir = 1 if bull_cross else -1

                continue

            if not crossover_active:
                continue

            if segment_done:
                continue

            # ==================================================
            # EMA GAP FILTER
            # ==================================================

            ema_gap = abs(ema20[i] - ema50[i])

            if ema_gap < self.ema_gap_filter:
                continue

            # ==================================================
            # EMA SLOPE FILTER
            # ==================================================

            ema20_slope = ema20[i] - ema20[i - 3]

            if pending_dir == 1 and ema20_slope <= 0:
                continue

            if pending_dir == -1 and ema20_slope >= 0:
                continue

            # ==================================================
            # TREND VALIDATION
            # ==================================================

            if pending_dir == 1:

                if closes[i] < ema50[i]:
                    crossover_active = False
                    retest_count = 0
                    continue

            elif pending_dir == -1:

                if closes[i] > ema50[i]:
                    crossover_active = False
                    retest_count = 0
                    continue

            # ==================================================
            # LONG RETEST
            # ==================================================

            if pending_dir == 1:

                is_retest = self._is_long_retest(
                    lows[i],
                    highs[i],
                    opens[i],
                    closes[i],
                    ema20[i]
                )

                if is_retest:

                    if i - last_retest_idx > 2:

                        retest_count += 1
                        last_retest_idx = i

                        df.at[i, 'retest_count'] = retest_count

            # ==================================================
            # SHORT RETEST
            # ==================================================

            elif pending_dir == -1:

                is_retest = self._is_short_retest(
                    highs[i],
                    lows[i],
                    opens[i],
                    closes[i],
                    ema20[i]
                )

                if is_retest:

                    if i - last_retest_idx > 2:

                        retest_count += 1
                        last_retest_idx = i

                        df.at[i, 'retest_count'] = retest_count

            # ==================================================
            # ENTRY ON 3RD RETEST
            # ==================================================

            if retest_count >= 3:

                entry = closes[i]

                # ==============================================
                # BUY ENTRY
                # ==============================================

                if pending_dir == 1:

                    sl_price = ema50[i] - self.sl_offset

                    risk = entry - sl_price

                    if risk > 0:

                        tp_price = (
                            entry +
                            (risk * self.risk_reward)
                        )

                        df.at[i, 'signal'] = 1
                        df.at[i, 'sl'] = sl_price
                        df.at[i, 'tp'] = tp_price
                        df.at[i, 'lot'] = self.lot_size
                        df.at[i, 'entry_mode'] = self._ENTRY_MODE

                        segment_done = True

                # ==============================================
                # SELL ENTRY
                # ==============================================

                elif pending_dir == -1:

                    sl_price = ema50[i] + self.sl_offset

                    risk = sl_price - entry

                    if risk > 0:

                        tp_price = (
                            entry -
                            (risk * self.risk_reward)
                        )

                        df.at[i, 'signal'] = -1
                        df.at[i, 'sl'] = sl_price
                        df.at[i, 'tp'] = tp_price
                        df.at[i, 'lot'] = self.lot_size
                        df.at[i, 'entry_mode'] = self._ENTRY_MODE

                        segment_done = True

        # ======================================================
        # EXIT LOGIC
        # ======================================================

        sigs = df['signal'].values
        sl_vals = df['sl'].values
        tp_vals = df['tp'].values

        trade_idx = None
        trade_dir = 0

        for i in range(1, n):

            f_prev = ema20[i - 1]
            s_prev = ema50[i - 1]

            f_curr = ema20[i]
            s_curr = ema50[i]

            bull_cross = self._is_bull_crossover(
                f_prev,
                s_prev,
                f_curr,
                s_curr
            )

            bear_cross = self._is_bear_crossover(
                f_prev,
                s_prev,
                f_curr,
                s_curr
            )

            # ==================================================
            # EXIT ON CROSSOVER
            # ==================================================

            if bull_cross or bear_cross:

                if trade_idx is not None:

                    df.at[i, 'exit_reason'] = 'crossover_reset'

                    trade_idx = None
                    trade_dir = 0

                continue

            # ==================================================
            # TRADE START
            # ==================================================

            if sigs[i] != 0:

                trade_idx = i
                trade_dir = int(sigs[i])

                continue

            if trade_idx is None:
                continue

            sl_price = sl_vals[trade_idx]
            tp_price = tp_vals[trade_idx]

            close_price = closes[i]

            # ==================================================
            # LONG EXIT
            # ==================================================

            if trade_dir == 1:

                # STOP LOSS
                if close_price <= sl_price:

                    df.at[i, 'exit_reason'] = 'sl'

                    trade_idx = None
                    trade_dir = 0

                # TAKE PROFIT
                elif close_price >= tp_price:

                    df.at[i, 'exit_reason'] = 'tp_1_5'

                    trade_idx = None
                    trade_dir = 0

            # ==================================================
            # SHORT EXIT
            # ==================================================

            elif trade_dir == -1:

                # STOP LOSS
                if close_price >= sl_price:

                    df.at[i, 'exit_reason'] = 'sl'

                    trade_idx = None
                    trade_dir = 0

                # TAKE PROFIT
                elif close_price <= tp_price:

                    df.at[i, 'exit_reason'] = 'tp_1_5'

                    trade_idx = None
                    trade_dir = 0

        return df