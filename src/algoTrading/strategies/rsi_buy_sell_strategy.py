import numpy as np
import pandas as pd

from algoTrading.strategies.SupertrendEngulfingReversalStrategy import Mark2Strategy


class RSIBuySellStrategy(Mark2Strategy):
    """
    RSI Candle Strategy

    SELL CONDITION:
    - RSI < 30
    - Current candle is RED

    BUY CONDITION:
    - RSI > 70
    - Current candle is GREEN

    TARGET:
    - Risk-reward target based on config.yaml, falling back to Config.RR

    STOP LOSS:
    BUY  -> candle low below close
    SELL -> candle high above close
    """

    _STRATEGY_KEY = "rsi_buy_sell"

    def __init__(
            self,
            rsi_period=14,
            sl_buffer=0.5
    ):

        super().__init__()

        self.rsi_period = rsi_period

        self.sl_buffer = sl_buffer

    # =========================================================
    # RSI Calculation
    # =========================================================

    def calculate_rsi(
            self,
            df: pd.DataFrame
    ) -> pd.DataFrame:

        df = df.copy()

        delta = df['close'].diff()

        gain = np.where(delta > 0, delta, 0)

        loss = np.where(delta < 0, abs(delta), 0)

        gain = pd.Series(gain).rolling(
            self.rsi_period
        ).mean()

        loss = pd.Series(loss).rolling(
            self.rsi_period
        ).mean()

        rs = gain / loss

        df['rsi'] = 100 - (
            100 / (1 + rs)
        )

        return df

    # =========================================================
    # Candle Checks
    # =========================================================

    def is_green_candle(self, row) -> bool:

        return row['close'] > row['open']

    def is_red_candle(self, row) -> bool:

        return row['close'] < row['open']

    # =========================================================
    # Generate Signals
    # =========================================================

    def generate_signals(
            self,
            df: pd.DataFrame
    ) -> pd.DataFrame:

        df = df.copy()

        # -----------------------------------------------------
        # RSI
        # -----------------------------------------------------

        df = self.calculate_rsi(df)

        # -----------------------------------------------------
        # Columns
        # -----------------------------------------------------

        df['signal'] = 0
        df['sl'] = np.nan
        df['tp'] = np.nan
        df['lot'] = 0.0

        n = len(df)

        # =====================================================
        # MAIN LOOP
        # =====================================================

        for i in range(self.rsi_period, n):

            row = df.iloc[i]

            close_price = row['close']

            candle_high = row['high']

            candle_low = row['low']

            rsi = row['rsi']

            # =================================================
            # SELL CONDITION
            # RSI < 30 + RED CANDLE
            # =================================================

            if (
                rsi < 20
                and self.is_red_candle(row)
            ):

                entry = close_price

                # SL above candle high
                sl = candle_high + self.sl_buffer

                risk = sl - entry

                # Risk-reward target from config.yaml, fallback Config.RR
                tp = entry - (risk * self.rr)

                df.at[i, 'signal'] = -1

                df.at[i, 'sl'] = sl

                df.at[i, 'tp'] = tp

                df.at[i, 'lot'] = self.lot_size

            # =================================================
            # BUY CONDITION
            # RSI > 70 + GREEN CANDLE
            # =================================================

            elif (
                rsi > 80
                and self.is_green_candle(row)
            ):

                entry = close_price

                # SL below candle low
                sl = candle_low - self.sl_buffer

                risk = entry - sl

                # Risk-reward target from config.yaml, fallback Config.RR
                tp = entry + (risk * self.rr)

                df.at[i, 'signal'] = 1

                df.at[i, 'sl'] = sl

                df.at[i, 'tp'] = tp

                df.at[i, 'lot'] = self.lot_size

        return df
