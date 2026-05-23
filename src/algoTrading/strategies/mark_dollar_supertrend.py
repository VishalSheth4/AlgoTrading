import numpy as np
import pandas as pd

from algoTrading.config import Config
from algoTrading.strategies.mark2_strategy import Mark2Strategy


class MarkDollarSuperTrendStrategy(Mark2Strategy):
    """
    Strategy Logic

    FAST SUPER TREND  : (10,3)
    SLOW SUPER TREND  : (100,3)

    Trade only when:
    - Fast supertrend flips
    - Slow supertrend agrees with fast supertrend direction

    Entry Rules

    RULE 1:
    BUY trend + bullish engulfing -> LONG
    SELL trend + bearish engulfing -> SHORT

    RULE 2:
    BUY trend + bullish dollar candle
    -> wait next 2 candles
    -> bearish engulfing -> SHORT

    SELL trend + bearish dollar candle
    -> wait next 2 candles
    -> bullish engulfing -> LONG
    """

    _STRATEGY_KEY = "mark_dollar_supertrend"

    def __init__(self, period=10, multiplier=3):
        super().__init__(period, multiplier)

        self.max_candle_size = getattr(
            Config,
            'MAX_CANDLE_SIZE',
            None
        )

    # =========================================================
    # Candle Filters
    # =========================================================

    def _candle_too_big(self, row) -> bool:

        if self.max_candle_size is None:
            return False

        return (
            row['high'] - row['low']
        ) > self.max_candle_size

    def _candle_strong(self, row) -> bool:

        full_range = row['high'] - row['low']

        if full_range <= 0:
            return False

        body = abs(
            row['close'] - row['open']
        )

        return (body / full_range) >= 0.5

    # =========================================================
    # Supertrend Calculation
    # =========================================================

    def calculate_supertrend_custom(
            self,
            df: pd.DataFrame,
            period: int,
            multiplier: float,
            prefix: str
    ) -> pd.DataFrame:

        df = df.copy()

        hl2 = (
            df['high'] + df['low']
        ) / 2

        tr1 = df['high'] - df['low']

        tr2 = abs(
            df['high'] - df['close'].shift(1)
        )

        tr3 = abs(
            df['low'] - df['close'].shift(1)
        )

        tr = pd.concat(
            [tr1, tr2, tr3],
            axis=1
        ).max(axis=1)

        atr = tr.rolling(period).mean()

        upperband = hl2 + (
            multiplier * atr
        )

        lowerband = hl2 - (
            multiplier * atr
        )

        final_upperband = upperband.copy()
        final_lowerband = lowerband.copy()

        trend = np.ones(len(df))

        for i in range(1, len(df)):

            if (
                upperband.iloc[i] < final_upperband.iloc[i - 1]
                or df['close'].iloc[i - 1] > final_upperband.iloc[i - 1]
            ):

                final_upperband.iloc[i] = upperband.iloc[i]

            else:

                final_upperband.iloc[i] = final_upperband.iloc[i - 1]

            if (
                lowerband.iloc[i] > final_lowerband.iloc[i - 1]
                or df['close'].iloc[i - 1] < final_lowerband.iloc[i - 1]
            ):

                final_lowerband.iloc[i] = lowerband.iloc[i]

            else:

                final_lowerband.iloc[i] = final_lowerband.iloc[i - 1]

            if (
                trend[i - 1] == -1
                and df['close'].iloc[i] > final_upperband.iloc[i - 1]
            ):

                trend[i] = 1

            elif (
                trend[i - 1] == 1
                and df['close'].iloc[i] < final_lowerband.iloc[i - 1]
            ):

                trend[i] = -1

            else:

                trend[i] = trend[i - 1]

        df[f'{prefix}_trend'] = trend

        return df

    # =========================================================
    # Dollar Flags
    # =========================================================

    def _prepare_dollar_flags(
            self,
            df: pd.DataFrame
    ) -> pd.DataFrame:

        df = df.copy()

        df['_ema5'] = (
            df['close']
            .ewm(span=5)
            .mean()
        )

        df['_is_above_ema'] = (
            (df['open'] > df['_ema5'])
            &
            (df['low'] > df['_ema5'])
        )

        df['_not_above_ema'] = (
            ~df['_is_above_ema']
        )

        df['_avg_vol12'] = (
            df['volume']
            .rolling(12)
            .mean()
        )

        df['_max_vol6'] = (
            df['volume']
            .rolling(6)
            .max()
            .shift(1)
        )

        df['_prev_low_vol'] = (
            (df['volume'].shift(1) < df['_avg_vol12'])
            &
            (df['volume'].shift(2) < df['_avg_vol12'])
            &
            (df['volume'].shift(3) < df['_avg_vol12'])
            &
            (df['volume'].shift(4) < df['_avg_vol12'])
            &
            (df['volume'].shift(5) < df['_avg_vol12'])
        )

        df['_vol_spike'] = (
            (df['volume'] > df['_max_vol6'])
            &
            (df['volume'] > df['_avg_vol12'])
            &
            df['_prev_low_vol']
        )

        return df

    def _is_dollar_long(self, row) -> bool:

        return bool(
            row['_vol_spike']
            and row['close'] > row['open']
            and row['_not_above_ema']
        )

    def _is_dollar_short(self, row) -> bool:

        return bool(
            row['_vol_spike']
            and row['open'] > row['close']
            and row['_is_above_ema']
        )

    # =========================================================
    # Generate Signals
    # =========================================================

    def generate_signals(
            self,
            df: pd.DataFrame
    ) -> pd.DataFrame:

        df = df.copy()

        # ---------------------------------------------
        # Fast Supertrend
        # ---------------------------------------------

        df = self.calculate_supertrend(df)

        # ---------------------------------------------
        # Slow Supertrend
        # ---------------------------------------------

        df = self.calculate_supertrend_custom(
            df,
            period=100,
            multiplier=3,
            prefix='slow_st'
        )

        # ---------------------------------------------
        # Dollar Flags
        # ---------------------------------------------

        df = self._prepare_dollar_flags(df)

        # ---------------------------------------------
        # Columns
        # ---------------------------------------------

        df['signal'] = 0
        df['sl'] = np.nan
        df['tp'] = np.nan
        df['lot'] = 0.0
        df['reverse_exit'] = 0

        trend = df['st_trend'].values

        slow_trend = df['slow_st_trend'].values

        closes = df['close'].values
        highs = df['high'].values
        lows = df['low'].values

        n = len(df)

        x_idx = None
        active_trend = None
        done = False
        running_ref = None

        # =====================================================
        # MAIN LOOP
        # =====================================================

        for i in range(1, n):

            # -------------------------------------------------
            # Fast ST flip + Slow ST confirmation
            # -------------------------------------------------

            if (
                trend[i] != trend[i - 1]
                and trend[i] == slow_trend[i]
            ):

                new_trend = trend[i]

                row_x = df.iloc[i]

                prev_x = df.iloc[i - 1]

                # =================================================
                # RULE 1
                # BUY TREND + bullish engulfing -> LONG
                # =================================================

                if (
                    new_trend == 1
                    and self.is_bullish_engulfing(prev_x, row_x)
                    and not self._candle_too_big(row_x)
                    and self._candle_strong(row_x)
                ):

                    entry = closes[i]

                    sl = lows[i] - (
                        (highs[i] - lows[i]) * 0.05
                    )

                    risk = entry - sl

                    if risk > 0:

                        df.at[i, 'signal'] = 1

                        df.at[i, 'sl'] = sl

                        df.at[i, 'tp'] = self.calc_tp(
                            entry,
                            risk,
                            np.nan,
                            'long'
                        )

                        df.at[i, 'lot'] = self.lot_size

                    x_idx = None
                    active_trend = new_trend
                    done = True

                # =================================================
                # RULE 1
                # SELL TREND + bearish engulfing -> SHORT
                # =================================================

                elif (
                    new_trend == -1
                    and self.is_bearish_engulfing(prev_x, row_x)
                    and not self._candle_too_big(row_x)
                    and self._candle_strong(row_x)
                ):

                    entry = closes[i]

                    sl = highs[i] + (
                        (highs[i] - lows[i]) * 0.05
                    )

                    risk = sl - entry

                    if risk > 0:

                        df.at[i, 'signal'] = -1

                        df.at[i, 'sl'] = sl

                        df.at[i, 'tp'] = self.calc_tp(
                            entry,
                            risk,
                            np.nan,
                            'short'
                        )

                        df.at[i, 'lot'] = self.lot_size

                    x_idx = None
                    active_trend = new_trend
                    done = True

                # =================================================
                # RULE 2 SETUP
                # =================================================

                elif (
                    new_trend == 1
                    and self._is_dollar_long(row_x)
                ):

                    x_idx = i
                    active_trend = new_trend
                    done = False
                    running_ref = highs[i]

                elif (
                    new_trend == -1
                    and self._is_dollar_short(row_x)
                ):

                    x_idx = i
                    active_trend = new_trend
                    done = False
                    running_ref = lows[i]

                else:

                    x_idx = None
                    active_trend = new_trend
                    done = True

                continue

            if x_idx is None or done:
                continue

            # -------------------------------------------------
            # Watch only next 2 candles
            # -------------------------------------------------

            if i - x_idx > 2:

                done = True

                continue

            prev_c = df.iloc[i - 1]

            curr_c = df.iloc[i]

            # =================================================
            # BUY TREND
            # bearish engulfing -> SHORT
            # =================================================

            if active_trend == 1:

                if closes[i] > running_ref:

                    done = True

                elif (
                    self.is_bearish_engulfing(prev_c, curr_c)
                    and not self._candle_too_big(curr_c)
                    and self._candle_strong(curr_c)
                ):

                    entry = closes[i]

                    sl = max(
                        running_ref,
                        highs[i]
                    ) + (
                        (highs[i] - lows[i]) * 0.05
                    )

                    risk = sl - entry

                    if risk > 0:

                        df.at[i, 'signal'] = -1

                        df.at[i, 'sl'] = sl

                        df.at[i, 'tp'] = self.calc_tp(
                            entry,
                            risk,
                            np.nan,
                            'short'
                        )

                        df.at[i, 'lot'] = self.lot_size

                    done = True

                else:

                    running_ref = max(
                        running_ref,
                        highs[i]
                    )

            # =================================================
            # SELL TREND
            # bullish engulfing -> LONG
            # =================================================

            elif active_trend == -1:

                if closes[i] < running_ref:

                    done = True

                elif (
                    self.is_bullish_engulfing(prev_c, curr_c)
                    and not self._candle_too_big(curr_c)
                    and self._candle_strong(curr_c)
                ):

                    entry = closes[i]

                    sl = min(
                        running_ref,
                        lows[i]
                    ) - (
                        (highs[i] - lows[i]) * 0.05
                    )

                    risk = entry - sl

                    if risk > 0:

                        df.at[i, 'signal'] = 1

                        df.at[i, 'sl'] = sl

                        df.at[i, 'tp'] = self.calc_tp(
                            entry,
                            risk,
                            np.nan,
                            'long'
                        )

                        df.at[i, 'lot'] = self.lot_size

                    done = True

                else:

                    running_ref = min(
                        running_ref,
                        lows[i]
                    )

        # =====================================================
        # Reverse Exit
        # =====================================================

        sigs = df['signal'].values

        for i in range(1, n - 1):

            if sigs[i] == 0:
                continue

            entry_c = df.iloc[i]

            next_c = df.iloc[i + 1]

            if (
                sigs[i] == 1
                and self.is_bearish_engulfing(
                    entry_c,
                    next_c
                )
            ):

                df.at[i + 1, 'reverse_exit'] = 1

            elif (
                sigs[i] == -1
                and self.is_bullish_engulfing(
                    entry_c,
                    next_c
                )
            ):

                df.at[i + 1, 'reverse_exit'] = 1

        return df