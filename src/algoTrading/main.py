from core.mt5_connector import connect, shutdown, get_available_symbols
from broker.mt5_broker import MT5Broker
from strategies.moving_average import MovingAverageStrategy
import pandas as pd
import MetaTrader5 as mt5


def get_data(symbol, timeframe=mt5.TIMEFRAME_M5, bars=200):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    return df


if __name__ == "__main__":

    if not connect():
        exit()

    broker = MT5Broker()
    strategy = MovingAverageStrategy()

    data = get_data("XAUUSD")
    print (data)
    
    ###################################### 
    # To check Available symbol in MT5 
    ######################################
    symbols = get_available_symbols(limit=20)
    print(f"Total Symbols: {len(symbols)}\n")
    print("\nAvailable Symbols:")
    for s in symbols:
        print(s)

    # data = strategy.generate_signals(data)

    # last_signal = data['signal'].iloc[-1]

    # if last_signal == 1:
    #     broker.place_order("BUY")

    # elif last_signal == -1:
    #     broker.place_order("SELL")

    shutdown()