import numpy as np
import pandas as pd

from algoTrading.strategies.SupertrendEngulfingReversalStrategy import Mark2Strategy


class Ema200PullbackEngulfingStrategy(Mark2Strategy):
    """
    EMA200 Pullback Engulfing Strategy

    BUY CONDITIONS
    --------------
    1. Bullish engulfing candle forms
    2. Candle touches EMA200
    3. Entire candle body closes above EMA200
    4. Buy at candle close
    5. Stop loss = candle low
    6. TP = Risk Reward based target

    SELL CONDITIONS
    ---------------
    1. Bearish engulfing candle forms
    2. Candle touches EMA200
    3. Entire candle body closes below EMA200
    4. Sell at candle close
    5. Stop loss = candle high
    6. TP = Risk Reward based target
    """

    _STRATEGY_KEY = "ema200_pullback_engulfing"
    _STRATEGY_NAME = "EMA200_Pullback_Engulfing"
    _ENTRY_MODE = "engulfing_pullback"

    def __init__(
        self,
        ema_period=200,
        risk_reward=3
    ):

        super().__init__(period=10, multiplier=3)

        self.ema_period = ema_period
        self.risk_reward = risk_reward

    # ==========================================================
    # BULLISH ENGULFING
    # ==========================================================

    def _is_bullish_engulfing(
        self,
        prev_open,
        prev_close,
        curr_open,
        curr_close
    ):

        prev_bearish = prev_close < prev_open
        curr_bullish = curr_close > curr_open

        engulfing = (
            curr_open <= prev_close and
            curr_close >= prev_open
        )

        return (
            prev_bearish and
            curr_bullish and
            engulfing
        )

    # ==========================================================
    # BEARISH ENGULFING
    # ==========================================================

    def _is_bearish_engulfing(
        self,
        prev_open,
        prev_close,
        curr_open,
        curr_close
    ):

        prev_bullish = prev_close > prev_open
        curr_bearish = curr_close < curr_open

        engulfing = (
            curr_open >= prev_close and
            curr_close <= prev_open
        )

        return (
            prev_bullish and
            curr_bearish and
            engulfing
        )

    # ==========================================================
    # MAIN SIGNAL GENERATION
    # ==========================================================

    def generate_signals(
        self,
        df: pd.DataFrame
    ) -> pd.DataFrame:

        df = df.copy()

        # ======================================================
        # EMA200
        # ======================================================

        df['ema200'] = (
            df['close']
            .ewm(span=self.ema_period, adjust=False)
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

        # ======================================================
        # ARRAYS
        # ======================================================

        opens = df['open'].values
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values
        ema200 = df['ema200'].values

        n = len(df)

        # ======================================================
        # ENTRY LOGIC
        # ======================================================

        for i in range(1, n):

            prev_open = opens[i - 1]
            prev_close = closes[i - 1]

            curr_open = opens[i]
            curr_close = closes[i]

            curr_high = highs[i]
            curr_low = lows[i]

            ema = ema200[i]

            # ==================================================
            # BUY CONDITIONS
            # ==================================================

            bullish_engulfing = self._is_bullish_engulfing(
                prev_open,
                prev_close,
                curr_open,
                curr_close
            )

            touched_ema = curr_low <= ema

            body_closed_above_ema = (
                curr_open > ema and
                curr_close > ema
            )

            if (
                bullish_engulfing and
                touched_ema and
                body_closed_above_ema
            ):

                entry_price = curr_close
                sl_price = curr_low

                risk = entry_price - sl_price

                if risk > 0:

                    tp_price = (
                        entry_price +
                        (risk * self.risk_reward)
                    )

                    df.at[i, 'signal'] = 1
                    df.at[i, 'sl'] = sl_price
                    df.at[i, 'tp'] = tp_price
                    df.at[i, 'lot'] = self.lot_size
                    df.at[i, 'entry_mode'] = self._ENTRY_MODE

                    continue

            # ==================================================
            # SELL CONDITIONS
            # ==================================================

            bearish_engulfing = self._is_bearish_engulfing(
                prev_open,
                prev_close,
                curr_open,
                curr_close
            )

            touched_ema = curr_high >= ema

            body_closed_below_ema = (
                curr_open < ema and
                curr_close < ema
            )

            if (
                bearish_engulfing and
                touched_ema and
                body_closed_below_ema
            ):

                entry_price = curr_close
                sl_price = curr_high

                risk = sl_price - entry_price

                if risk > 0:

                    tp_price = (
                        entry_price -
                        (risk * self.risk_reward)
                    )

                    df.at[i, 'signal'] = -1
                    df.at[i, 'sl'] = sl_price
                    df.at[i, 'tp'] = tp_price
                    df.at[i, 'lot'] = self.lot_size
                    df.at[i, 'entry_mode'] = self._ENTRY_MODE

        # ======================================================
        # EXIT LOGIC
        # ======================================================

        sigs = df['signal'].values
        sl_vals = df['sl'].values
        tp_vals = df['tp'].values

        trade_idx = None
        trade_dir = 0

        for i in range(1, n):

            # ==================================================
            # NEW TRADE
            # ==================================================

            if sigs[i] != 0:

                trade_idx = i
                trade_dir = int(sigs[i])

                continue

            if trade_idx is None:
                continue

            close_price = closes[i]

            sl_price = sl_vals[trade_idx]
            tp_price = tp_vals[trade_idx]

            # ==================================================
            # BUY EXIT
            # ==================================================

            if trade_dir == 1:

                # STOP LOSS
                if close_price <= sl_price:

                    df.at[i, 'exit_reason'] = 'sl'

                    trade_idx = None
                    trade_dir = 0

                # TAKE PROFIT
                elif close_price >= tp_price:

                    df.at[i, 'exit_reason'] = 'tp'

                    trade_idx = None
                    trade_dir = 0

            # ==================================================
            # SELL EXIT
            # ==================================================

            elif trade_dir == -1:

                # STOP LOSS
                if close_price >= sl_price:

                    df.at[i, 'exit_reason'] = 'sl'

                    trade_idx = None
                    trade_dir = 0

                # TAKE PROFIT
                elif close_price <= tp_price:

                    df.at[i, 'exit_reason'] = 'tp'

                    trade_idx = None
                    trade_dir = 0

        return df