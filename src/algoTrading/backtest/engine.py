import pandas as pd
from pathlib import Path
from algoTrading.config import Config

class BacktestEngine:

    def __init__(self, capital, risk_per_trade, symbol="", risk_mode=None, risk_amount=None,
                 contract_size=None, max_lot_size=None):
        self.initial_capital = capital
        self.capital = capital
        self.risk_per_trade = risk_per_trade
        self.symbol = symbol

        # Position sizing mode -- see Config.RISK_MODE's docstring.
        # "percent" (default) keeps the original capital*risk_per_trade
        # math exactly as before; "fixed" risks a flat dollar amount per
        # trade, scaled to a real lot size via contract_size. Applies to
        # EVERY strategy's entry, not just one -- this engine is shared
        # across whichever strategies a run selects (see main_backtest.py).
        self.risk_mode = risk_mode if risk_mode is not None else Config.RISK_MODE
        self.risk_amount = Config.RISK_AMOUNT if risk_amount is None else risk_amount
        self.contract_size = Config.CONTRACT_SIZE if contract_size is None else contract_size
        # Safety ceiling, in REAL LOTS, on whatever either formula above
        # computes -- see Config.MAX_LOT_SIZE's docstring (an unusually
        # tight stoploss otherwise blows up position size in either mode).
        self.max_lot_size = Config.MAX_LOT_SIZE if max_lot_size is None else max_lot_size

        self.position = 0
        self.entry_price = None
        self.position_size = 0
        self.sl = None
        self.tp = None
        self.entry_time = None
        self.entry_candle_size = None  # (high - low) of the candle that triggered entry
        # $ profit per 1.0-price-unit move per 1.0 position_size unit --
        # "fixed" mode's position_size is a real lot count (contract_size
        # was already divided OUT to get there), so profit needs it
        # multiplied back IN. "percent" mode's position_size already IS
        # the $-per-point figure (no contract_size baked into how it was
        # computed), so its multiplier stays 1 -- unchanged from before
        # this feature existed. Set alongside position_size at entry.
        self._pnl_multiplier = 1

        self.trades           = []
        self._tp_mode         = Config.TP_MODE
        self._open_strategy   = ""   # strategy that opened the current position

    def _size_position(self, risk_per_unit: float) -> None:
        """risk_per_unit is the stoploss distance in price units (e.g. $5
        for a XAUUSD candle whose SL sits $5 from entry) -- the smaller
        this is, the bigger either formula below sizes the trade, since
        both divide by it. Sets BOTH position_size and _pnl_multiplier
        together -- they must agree on whether contract_size has already
        been divided out, or profit comes out scaled wrong (see
        _pnl_multiplier's comment above). Shared by both the LONG and
        SHORT entry blocks below.

        max_lot_size caps the result in both modes, always in the same
        REAL-LOTS unit: "fixed" mode's position_size already IS lots, so
        it's capped directly; "percent" mode's position_size is $-per-point
        (contract_size not divided out), so it's converted to a lot
        equivalent just for the comparison, then the $-per-point figure is
        scaled back down to match if it was over the cap."""
        if self.risk_mode == "fixed":
            lots = self.risk_amount / (risk_per_unit * self.contract_size)
            self.position_size = min(lots, self.max_lot_size)
            self._pnl_multiplier = self.contract_size
        else:
            risk_amount = self.capital * self.risk_per_trade
            size = risk_amount / risk_per_unit
            lot_equivalent = size / self.contract_size
            if lot_equivalent > self.max_lot_size:
                size = self.max_lot_size * self.contract_size
            self.position_size = size
            self._pnl_multiplier = 1

    def run(self, df, save=True):

        df = df.reset_index(drop=True)

        for i in range(len(df)):
            row = df.iloc[i]

            signal = row.get('signal', 0)
            close = row['close']
            time = row['time']

            # =========================
            # LONG ENTRY
            # =========================
            if signal == 1 and self.position == 0:

                sl = row.get('sl')
                tp = row.get('tp')

                if pd.isna(sl) or pd.isna(tp):
                    continue

                risk_per_unit = close - sl

                if risk_per_unit <= 0:
                    continue

                self._size_position(risk_per_unit)

                self.entry_price      = close
                self.sl               = sl
                self.tp               = tp
                self.position         = 1
                self._open_strategy   = row.get('_strategy', '')
                self.entry_time        = time
                self.entry_candle_size = row['high'] - row['low']

                self.trades.append({
                    "symbol":   self.symbol,
                    "strategy": self._open_strategy,
                    "time":     time,
                    "type":     "BUY",
                    "entry_price": close,
                    "lot_size":    self.position_size,
                    "candle_size": self.entry_candle_size,
                    "sl":          self.sl,
                    "tp":          self.tp,
                    "capital":     self.capital,
                })

            # =========================
            # LONG EXIT
            # =========================
            elif self.position == 1:

                exit_trade = False
                exit_reason = None

                # reverse engulfing on candle immediately after entry
                if row.get('reverse_exit', 0) == 1:
                    exit_trade = True
                    exit_reason = "REV"

                # candle CLOSE below SL
                elif close < self.sl:
                    exit_trade = True
                    exit_reason = "SL"

                elif close >= self.tp:
                    exit_trade = True
                    exit_reason = "TP"

                if exit_trade:

                    exit_price = close
                    profit = (exit_price - self.entry_price) * self.position_size * self._pnl_multiplier

                    self.capital += profit
                    self.capital = max(self.capital, 0)

                    if exit_reason == "TP":
                        close_label = "ST" if self._tp_mode == "st" else ("R:R" if self._tp_mode == "rr" else "TP")
                    elif exit_reason == "REV":
                        close_label = "REV"
                    else:
                        close_label = "SL"

                    self.trades.append({
                        "symbol":      self.symbol,
                        "strategy":    self._open_strategy,
                        "time":        time,
                        "entry_time":  self.entry_time,
                        "type":        "SELL",
                        "entry_price": self.entry_price,
                        "exit_price":  exit_price,
                        "sl":          self.sl,
                        "tp":          self.tp,
                        "lot_size":    self.position_size,
                        "candle_size": self.entry_candle_size,
                        "profit":      profit,
                        "exit_reason": exit_reason,
                        "exit_label":  close_label,
                        "capital":     self.capital,
                    })

                    self.position = 0
                    self.entry_price = None
                    self.sl = None
                    self.tp = None
                    self.entry_time = None
                    self.entry_candle_size = None

            # =========================
            # SHORT ENTRY
            # =========================
            elif signal == -1 and self.position == 0:

                sl = row.get('sl')
                tp = row.get('tp')

                if pd.isna(sl) or pd.isna(tp):
                    continue

                risk_per_unit = sl - close  # SL is above entry for short

                if risk_per_unit <= 0:
                    continue

                self._size_position(risk_per_unit)

                self.entry_price    = close
                self.sl             = sl
                self.tp             = tp
                self.position       = -1
                self._open_strategy = row.get('_strategy', '')
                self.entry_time      = time
                self.entry_candle_size = row['high'] - row['low']

                self.trades.append({
                    "symbol":   self.symbol,
                    "strategy": self._open_strategy,
                    "time":     time,
                    "type":     "SHORT",
                    "entry_price": close,
                    "lot_size":    self.position_size,
                    "candle_size": self.entry_candle_size,
                    "sl":          self.sl,
                    "tp":          self.tp,
                    "capital":     self.capital,
                })

            # =========================
            # SHORT EXIT
            # =========================
            elif self.position == -1:

                exit_trade = False
                exit_reason = None

                # reverse engulfing on candle immediately after entry
                if row.get('reverse_exit', 0) == 1:
                    exit_trade = True
                    exit_reason = "REV"

                # candle CLOSE above SL high
                elif close > self.sl:
                    exit_trade = True
                    exit_reason = "SL"

                # price reached target
                elif close <= self.tp:
                    exit_trade = True
                    exit_reason = "TP"

                if exit_trade:

                    exit_price = close
                    profit = (self.entry_price - exit_price) * self.position_size * self._pnl_multiplier

                    self.capital += profit
                    self.capital = max(self.capital, 0)

                    if exit_reason == "TP":
                        close_label = "ST" if self._tp_mode == "st" else ("R:R" if self._tp_mode == "rr" else "TP")
                    elif exit_reason == "REV":
                        close_label = "REV"
                    else:
                        close_label = "SL"

                    self.trades.append({
                        "symbol":      self.symbol,
                        "strategy":    self._open_strategy,
                        "time":        time,
                        "entry_time":  self.entry_time,
                        "type":        "COVER",
                        "entry_price": self.entry_price,
                        "exit_price":  exit_price,
                        "sl":          self.sl,
                        "tp":          self.tp,
                        "lot_size":    self.position_size,
                        "candle_size": self.entry_candle_size,
                        "profit":      profit,
                        "exit_reason": exit_reason,
                        "exit_label":  close_label,
                        "capital":     self.capital,
                    })

                    self.position = 0
                    self.entry_price = None
                    self.sl = None
                    self.tp = None
                    self.entry_time = None
                    self.entry_candle_size = None

            # stop if account gone
            if self.capital <= 0:
                print("❌ Account blown")
                break

        if save:
            self.save_trades()
        return self.results()

    def save_trades(self):
        if not self.trades:
            print("⚠️ No trades to save")
            return

        df = pd.DataFrame(self.trades)

        base_dir = Path(__file__).resolve().parents[1]
        file_path = base_dir / "data" / "trade_data.csv"

        df.to_csv(file_path, index=False)
        print(f"✅ Trades saved to {file_path}")

    def results(self):
        return {
            "initial_capital": self.initial_capital,
            "final_capital": round(self.capital, 2),
            "return (%)": round(((self.capital - self.initial_capital) / self.initial_capital) * 100, 2),
            "total_trades": len(self.trades)
        }