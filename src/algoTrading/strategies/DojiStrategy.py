import pandas as pd
import numpy as np
from algoTrading.config import Config
from algoTrading.strategies.mark2_strategy import Mark2Strategy


class DojiStrategy(Mark2Strategy):

    _STRATEGY_KEY = "doji_strategy"

    def __init__(
        self,
        doji_size=0.05,
        long_candle_ratio=0.7,
        use_volume_filter=False,
        volume_ma_period=24,
        use_sl_tp=True,
    ):

        super().__init__()

        self.doji_size = doji_size
        self.long_candle_ratio = long_candle_ratio
        self.use_volume_filter = use_volume_filter
        self.volume_ma_period = volume_ma_period
        self.use_sl_tp = use_sl_tp

        self.stop_loss = Config.STOP_LOSS
        self.take_profit = Config.TAKE_PROFIT
        self.max_candle_size = getattr(Config, "MAX_CANDLE_SIZE", None)

    # ============================================================
    # Candle Helpers
    # ============================================================

    def is_doji(self, row):

        candle_range = row["high"] - row["low"]

        if candle_range == 0:
            return False

        body = abs(row["open"] - row["close"])

        return body <= candle_range * self.doji_size

    def is_long_candle(self, row):

        candle_range = row["high"] - row["low"]

        if candle_range == 0:
            return False

        body = abs(row["open"] - row["close"])

        ratio = body / candle_range

        return ratio > self.long_candle_ratio

    def candle_too_big(self, row):

        if self.max_candle_size is None:
            return False

        return (row["high"] - row["low"]) > self.max_candle_size

    # ============================================================
    # Volume Filter
    # ============================================================

    def prepare_volume_filter(self, df):

        if self.use_volume_filter:

            df = df.copy()

            df["vol_ema"] = (
                df["volume"]
                .ewm(span=self.volume_ma_period, adjust=False)
                .mean()
            )

        return df

    # ============================================================
    # Entry Price Logic (same as Pine)
    # ============================================================

    def get_long_entry(self, current_row, prev_row, prev_long):

        current_range = current_row["high"] - current_row["low"]
        prev_range = prev_row["high"] - prev_row["low"]

        if prev_long and prev_range > current_range:
            return prev_row["high"]

        return max(current_row["high"], prev_row["high"])

    def get_short_entry(self, current_row, prev_row, prev_long):

        current_range = current_row["high"] - current_row["low"]
        prev_range = prev_row["high"] - prev_row["low"]

        if prev_long and prev_range > current_range:
            return prev_row["low"]

        return min(current_row["low"], prev_row["low"])

    # ============================================================
    # Signal Row
    # ============================================================

    def create_signal_row(
        self,
        base_row,
        signal,
        entry_price,
        oca_group,
    ):

        if signal == 1:

            sl = entry_price - self.stop_loss
            tp = entry_price + self.take_profit

        else:

            sl = entry_price + self.stop_loss
            tp = entry_price - self.take_profit

        row = base_row.to_dict()

        row.update(
            {
                "signal": signal,
                "entry_price": entry_price,
                "sl": sl,
                "tp": tp,
                "lot": self.lot_size,
                "oca_group": oca_group,
                "doji": True,
                "order_type": "STOP",
                "status": "PENDING",
            }
        )

        return row

    # ============================================================
    # Main Logic
    # ============================================================

    def generate_signals(self, df):

        df = df.copy()

        df = self.prepare_volume_filter(df)

        signal_rows = []

        oca_group = 0

        for i in range(1, len(df)):

            current = df.iloc[i]
            previous = df.iloc[i - 1]

            # ----------------------------------------------------
            # DOJI CHECK
            # ----------------------------------------------------

            if not self.is_doji(current):
                continue

            # ----------------------------------------------------
            # VOLUME FILTER
            # ----------------------------------------------------

            if self.use_volume_filter:

                if current["volume"] <= current["vol_ema"]:
                    continue

            # ----------------------------------------------------
            # MAX CANDLE FILTER
            # ----------------------------------------------------

            if self.candle_too_big(current):
                continue

            # ----------------------------------------------------
            # PREVIOUS LONG CANDLE CHECK
            # ----------------------------------------------------

            prev_long = self.is_long_candle(previous)

            # ----------------------------------------------------
            # ENTRY CALCULATION
            # ----------------------------------------------------

            long_entry = self.get_long_entry(
                current,
                previous,
                prev_long,
            )

            short_entry = self.get_short_entry(
                current,
                previous,
                prev_long,
            )

            # ----------------------------------------------------
            # CREATE OCA GROUP
            # ----------------------------------------------------

            oca_group += 1

            # BUY STOP ORDER

            signal_rows.append(
                self.create_signal_row(
                    current,
                    signal=1,
                    entry_price=long_entry,
                    oca_group=oca_group,
                )
            )

            # SELL STOP ORDER

            signal_rows.append(
                self.create_signal_row(
                    current,
                    signal=-1,
                    entry_price=short_entry,
                    oca_group=oca_group,
                )
            )

        # ========================================================
        # EMPTY CASE
        # ========================================================

        if not signal_rows:

            columns = list(df.columns) + [
                "signal",
                "entry_price",
                "sl",
                "tp",
                "lot",
                "oca_group",
                "doji",
                "order_type",
                "status",
            ]

            return pd.DataFrame(columns=columns)

        # ========================================================
        # FINAL DF
        # ========================================================

        result = pd.DataFrame(signal_rows)

        result.reset_index(drop=True, inplace=True)

        result["signal"] = result["signal"].astype(int)
        result["oca_group"] = result["oca_group"].astype(int)
        result["doji"] = result["doji"].astype(bool)

        return result