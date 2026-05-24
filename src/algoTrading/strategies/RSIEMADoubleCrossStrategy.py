import pandas as pd
import numpy as np
from algoTrading.config import Config
from algoTrading.strategies.mark2_strategy import _load_rr, _load_lot_size, _load_risk_per_trade


class RSIEMADoubleCrossStrategy:
    """
    ============================================================
    RSI EMA DOUBLE CROSS STRATEGY
    ============================================================

    SELL FLOW
    ------------------------------------------------------------
    1. RSI > 70
    2. RSI crosses ABOVE RSI EMA
    3. RSI crosses BELOW RSI EMA
    4. RSI again crosses ABOVE RSI EMA
    5. SELL when:
          - bearish crossover
          OR
          - bearish engulfing + consolidation
    6. Price must be below EMA200

    BUY FLOW
    ------------------------------------------------------------
    1. After SELL signal
    2. Wait for bullish RSI crossover
    3. Start 7 candle watch window
    4. If bullish engulfing forms
    5. BUY trade
    6. Price must be above EMA200

    ============================================================
    """

    _STRATEGY_KEY = "rsi_ema_double_cross"

    def __init__(self):

        self.rsi_period     = Config.RSI_PERIOD
        self.rsi_ema_period = Config.RSI_EMA_PERIOD
        self.rsi_threshold  = Config.RSI_THRESHOLD
        self.ema_period     = Config.EMA_PERIOD

        self.rr             = _load_rr(self._STRATEGY_KEY)
        self.lot_size       = _load_lot_size(self._STRATEGY_KEY)
        self.risk_per_trade = _load_risk_per_trade(self._STRATEGY_KEY)

        self.tp_mode    = Config.TP_MODE
        self.fix_profit = Config.FIX_PROFIT

    # =========================================================
    # EMA
    # =========================================================

    def _compute_ema(self, close: pd.Series) -> pd.Series:

        return close.ewm(
            span=self.ema_period,
            adjust=False
        ).mean()

    # =========================================================
    # RSI
    # =========================================================

    def _compute_rsi(self, close: pd.Series) -> pd.Series:

        delta = close.diff()

        gain = delta.clip(lower=0)

        loss = (-delta).clip(lower=0)

        avg_gain = gain.ewm(
            alpha=1 / self.rsi_period,
            min_periods=self.rsi_period,
            adjust=False
        ).mean()

        avg_loss = loss.ewm(
            alpha=1 / self.rsi_period,
            min_periods=self.rsi_period,
            adjust=False
        ).mean()

        rs = avg_gain / avg_loss

        return 100 - (100 / (1 + rs))

    # =========================================================
    # RSI EMA
    # =========================================================

    def _compute_rsi_ema(self, rsi: pd.Series) -> pd.Series:

        return rsi.ewm(
            span=self.rsi_ema_period,
            adjust=False
        ).mean()

    # =========================================================
    # CROSS ABOVE
    # =========================================================

    def _cross_above(
        self,
        a_prev,
        a_curr,
        b_prev,
        b_curr
    ):

        return (
            a_prev <= b_prev and
            a_curr > b_curr
        )

    # =========================================================
    # CROSS BELOW
    # =========================================================

    def _cross_below(
        self,
        a_prev,
        a_curr,
        b_prev,
        b_curr
    ):

        return (
            a_prev >= b_prev and
            a_curr < b_curr
        )

    # =========================================================
    # BEARISH ENGULFING
    # =========================================================

    def _bearish_engulfing(self, prev, curr):

        return (

            prev['close'] > prev['open'] and

            curr['close'] < curr['open'] and

            curr['open'] >= prev['close'] and

            curr['close'] <= prev['open']
        )

    # =========================================================
    # BULLISH ENGULFING
    # =========================================================

    def _bullish_engulfing(self, prev, curr):

        return (

            prev['close'] < prev['open'] and

            curr['close'] > curr['open'] and

            curr['open'] <= prev['close'] and

            curr['close'] >= prev['open']
        )

    # =========================================================
    # CONSOLIDATION
    # =========================================================

    def _consolidation(self, df, i):

        recent_range = (
            df.iloc[i - 5:i]['high'].max() -
            df.iloc[i - 5:i]['low'].min()
        )

        avg_range = (
            df.iloc[i - 20:i]['high'] -
            df.iloc[i - 20:i]['low']
        ).mean()

        return recent_range < (avg_range * 1.2)

    # =========================================================
    # TAKE PROFIT
    # =========================================================

    def _calculate_tp(self, entry, risk, side):

        if self.tp_mode == "rr":

            if side == "sell":
                return entry - (risk * self.rr)

            return entry + (risk * self.rr)

        elif self.tp_mode == "fix_profit":

            if side == "sell":
                return entry - self.fix_profit

            return entry + self.fix_profit

        elif self.tp_mode == "both":

            if side == "sell":

                rr_tp = entry - (risk * self.rr)
                fixed_tp = entry - self.fix_profit

                return min(rr_tp, fixed_tp)

            rr_tp = entry + (risk * self.rr)
            fixed_tp = entry + self.fix_profit

            return max(rr_tp, fixed_tp)

        else:

            if side == "sell":
                return entry - (risk * self.rr)

            return entry + (risk * self.rr)

    # =========================================================
    # MAIN SIGNAL GENERATION
    # =========================================================

    def generate_signals(self, df: pd.DataFrame):

        df = df.copy()

        # =====================================================
        # OUTPUT COLUMNS
        # =====================================================

        df['signal'] = 0

        df['sl'] = np.nan

        df['tp'] = np.nan

        df['lot'] = 0.0

        df['risk_per_trade'] = np.nan

        df['sl_exit_on_close'] = 1

        # =====================================================
        # INDICATORS
        # =====================================================

        ema200 = self._compute_ema(df['close'])

        rsi = self._compute_rsi(df['close'])

        rsi_ema = self._compute_rsi_ema(rsi)

        df['ema200'] = ema200

        df['rsi'] = rsi

        df['rsi_ema'] = rsi_ema

        # =====================================================
        # SELL STATE MACHINE
        # =====================================================

        sell_armed = False

        first_cross_done = False

        pullback_done = False

        second_push_done = False

        # =====================================================
        # BUY STATE MACHINE
        # =====================================================

        buy_watch_active = False

        buy_watch_start_index = None

        # =====================================================
        # LOOP
        # =====================================================

        for i in range(50, len(df)):

            curr = df.iloc[i]

            prev = df.iloc[i - 1]

            # -------------------------------------------------
            # RSI VALUES
            # -------------------------------------------------

            rsi_prev = rsi.iloc[i - 1]
            rsi_curr = rsi.iloc[i]

            rsi_ema_prev = rsi_ema.iloc[i - 1]
            rsi_ema_curr = rsi_ema.iloc[i]

            # -------------------------------------------------
            # EMA FILTERS
            # -------------------------------------------------

            below_ema = curr['close'] < ema200.iloc[i]

            above_ema = curr['close'] > ema200.iloc[i]

            # =================================================
            # STEP 1
            # RSI crosses ABOVE EMA while RSI > 70
            # =================================================

            if (
                not sell_armed
                and rsi_curr > self.rsi_threshold
                and self._cross_above(
                    rsi_prev,
                    rsi_curr,
                    rsi_ema_prev,
                    rsi_ema_curr
                )
            ):

                sell_armed = True

                first_cross_done = True

                continue

            # =================================================
            # STEP 2
            # RSI crosses BELOW EMA
            # =================================================

            if (
                first_cross_done
                and not pullback_done
                and self._cross_below(
                    rsi_prev,
                    rsi_curr,
                    rsi_ema_prev,
                    rsi_ema_curr
                )
            ):

                pullback_done = True

                continue

            # =================================================
            # STEP 3
            # RSI crosses ABOVE EMA again
            # =================================================

            if (
                pullback_done
                and not second_push_done
                and self._cross_above(
                    rsi_prev,
                    rsi_curr,
                    rsi_ema_prev,
                    rsi_ema_curr
                )
            ):

                second_push_done = True

                continue

            # =================================================
            # FINAL SELL ENTRY
            # =================================================

            if second_push_done and below_ema:

                bearish_cross = self._cross_below(
                    rsi_prev,
                    rsi_curr,
                    rsi_ema_prev,
                    rsi_ema_curr
                )

                bearish_engulfing = self._bearish_engulfing(
                    prev,
                    curr
                )

                consolidation = self._consolidation(df, i)

                if (
                    bearish_cross or
                    (bearish_engulfing and consolidation)
                ):

                    entry = curr['close']

                    sl = max(
                        df.iloc[i - 1]['high'],
                        df.iloc[i - 2]['high'],
                        df.iloc[i - 3]['high'],
                        df.iloc[i - 4]['high']
                    )

                    risk = sl - entry

                    if risk > 0:

                        tp = self._calculate_tp(
                            entry,
                            risk,
                            "sell"
                        )

                        # -------------------------------------
                        # SELL SIGNAL
                        # -------------------------------------

                        df.at[i, 'signal'] = -1

                        df.at[i, 'sl'] = sl

                        df.at[i, 'tp'] = tp

                        df.at[i, 'lot'] = self.lot_size

                        df.at[i, 'risk_per_trade'] = (
                            self.risk_per_trade
                        )

                        # -------------------------------------
                        # ACTIVATE BUY WATCH
                        # -------------------------------------

                        buy_watch_active = True

                        buy_watch_start_index = None

                        # -------------------------------------
                        # RESET SELL STATE
                        # -------------------------------------

                        sell_armed = False

                        first_cross_done = False

                        pullback_done = False

                        second_push_done = False

                        continue

            # =================================================
            # BUY WATCH LOGIC
            # =================================================

            if buy_watch_active:

                bullish_cross = self._cross_above(
                    rsi_prev,
                    rsi_curr,
                    rsi_ema_prev,
                    rsi_ema_curr
                )

                # ---------------------------------------------
                # FIRST BULLISH RSI CROSS
                # ---------------------------------------------

                if (
                    bullish_cross and
                    buy_watch_start_index is None
                ):

                    buy_watch_start_index = i

                # ---------------------------------------------
                # WATCH NEXT 7 CANDLES
                # ---------------------------------------------

                if buy_watch_start_index is not None:

                    candles_since_cross = (
                        i - buy_watch_start_index
                    )

                    if candles_since_cross <= 7:

                        bullish_engulfing = (
                            self._bullish_engulfing(
                                prev,
                                curr
                            )
                        )

                        if (
                            bullish_engulfing and
                            above_ema
                        ):

                            entry = curr['close']

                            sl = min(
                                df.iloc[i - 1]['low'],
                                df.iloc[i - 2]['low'],
                                df.iloc[i - 3]['low'],
                                df.iloc[i - 4]['low']
                            )

                            risk = entry - sl

                            if risk > 0:

                                tp = self._calculate_tp(
                                    entry,
                                    risk,
                                    "buy"
                                )

                                # =========================
                                # BUY SIGNAL
                                # =========================

                                df.at[i, 'signal'] = 1

                                df.at[i, 'sl'] = sl

                                df.at[i, 'tp'] = tp

                                df.at[i, 'lot'] = (
                                    self.lot_size
                                )

                                df.at[i, 'risk_per_trade'] = (
                                    self.risk_per_trade
                                )

                                # =========================
                                # RESET BUY WATCH
                                # =========================

                                buy_watch_active = False

                                buy_watch_start_index = None

                    else:

                        # -------------------------------------
                        # EXPIRE BUY WINDOW
                        # -------------------------------------

                        buy_watch_active = False

                        buy_watch_start_index = None

        return df