import MetaTrader5 as mt5

def connect():
    if not mt5.initialize():
        print("❌ MT5 Initialization Failed")
        return False

    print("✅ MT5 Connected")
    return True


def shutdown():
    mt5.shutdown()

def get_available_symbols(limit=20):

    # Fetch available symbols from MT5

    symbols = mt5.symbols_get()

    if symbols is None:
        print("❌ symbols_get() failed:", mt5.last_error())
        return []

    symbol_names = [s.name for s in symbols]

    print(f"Total Symbols: {len(symbol_names)}")

    return symbol_names[:limit]