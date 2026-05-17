# Algo Trading Setup Guide

## Step 1: Install Environment

Install required Python packages:

```bash
pip install pandas numpy matplotlib pandas-ta backtrader requests fastapi uvicorn
pip install requests beautifulsoup4 pandas lxml
pip install requests pandas
```

Upgrade pip:

```bash
python.exe -m pip install --upgrade pip
```

---

## MetaTrader 5 Setup

Install MetaTrader5 package:

```bash
pip install MetaTrader5 pandas
```

---

## Run Backtest

```bash
python -m algoTrading.main_backtest
```
# AlgoTrading

A Python algorithmic trading system built on MetaTrader5 (MT5). Supports backtesting across multiple strategies and symbols, live trading, and an interactive chart dashboard.

---

## Prerequisites

- **Python 3.10+**
- **MetaTrader5 terminal** installed and running on Windows (required for live data and trading)
- A funded or demo MT5 account logged in inside the terminal

---

## 1. Clone the Repository

```bash
git clone https://github.com/VishalSheth4/AlgoTrading.git
cd AlgoTrading
```

---

## 2. Install Dependencies

```bash
pip install MetaTrader5 pandas numpy
```

---

## 3. Set Up the Package Path

`main_backtest.py` and `chart_server.py` import modules using the `algoTrading.*` namespace. Rename (or copy) the `src/` folder to `algoTrading/` so Python can resolve these imports:

```bash
# Windows (PowerShell)
Rename-Item -Path src -NewName algoTrading
```

> All commands below assume `src/` has been renamed to `algoTrading/`.

---

## 4. Configure the Project

Edit `algoTrading/config.py` to match your trading preferences:

| Setting | Default | Description |
|---|---|---|
| `SYMBOL` | `"XAUUSD"` | Symbol(s) to trade — comma-separated for multi-symbol |
| `STRATEGY` | `"mark2,mark_dollar_supertrend"` | Strategy/strategies to run — comma-separated |
| `TIMEFRAME` | `"M5"` | Candle timeframe |
| `BARS` | `90000` | Number of historical bars to fetch |
| `INITIAL_CAPITAL` | `100` | Starting capital per symbol (USD) |
| `LOT_SIZE` | `0.01` | Default lot size |
| `STOP_LOSS` | `50` | Stop loss in pips |
| `TAKE_PROFIT` | `100` | Take profit in pips |
| `TP_MODE` | `"rr"` | TP mode: `"rr"` (risk-reward), `"st"` (supertrend), `"both"`, `"fix_profit"` |
| `RR` | `3` | Risk-reward ratio (used when `TP_MODE = "rr"`) |

**Multi-symbol example:**
```python
SYMBOL = "XAUUSD,EURUSD,GBPUSD"
```

**Multi-strategy example:**
```python
STRATEGY = "mark2,mark_dollar_supertrend,engulfing"
```

Per-strategy lot size overrides live in `algoTrading/config.yaml`.

---

## 5. Available Strategies

| Key | Class |
|---|---|
| `mark2` | Mark2Strategy |
| `mark_dollar_supertrend` | MarkDollarSuperTrendStrategy |
| `engulfing` | EngulfingStrategy |
| `engulfing_consolidation` | EngulfingConsolidationStrategy |
| `engulfing_reversal` | EngulfingReversalStrategy |
| `green_dollar` | GreenDollarStrategy |
| `supertrend` | SupertrendStrategy |
| `ma` | MovingAverageStrategy |

---

## 6. Run the Backtest

Make sure the MetaTrader5 terminal is open and logged in, then run from the **project root**:

```bash
python algoTrading/main_backtest.py
```

What it does:
1. Connects to MT5 and fetches historical OHLCV data for each configured symbol
2. Generates signals from all configured strategies
3. Merges signals chronologically (first strategy to fire on a bar wins)
4. Runs the backtest engine with a shared capital pool
5. Prints trade count, P&L, win/loss stats, and return %
6. Saves the full trade log to `algoTrading/data/trade_data.csv`
7. Generates an interactive HTML dashboard at `algoTrading/data/dashboard.html`

---

## 7. View the Dashboard (Chart Server)

After running the backtest, start the chart server to view the interactive dashboard:

```bash
# Default port 8765
python algoTrading/chart_server.py

# Custom port
python algoTrading/chart_server.py 9000
```

Then open your browser:

| URL | Description |
|---|---|
| `http://127.0.0.1:8765/` | Interactive OHLCV + trade marker dashboard |
| `http://127.0.0.1:8765/ohlcv?limit=1000` | Raw OHLCV + Supertrend JSON API |
| `http://127.0.0.1:8765/status` | MT5 live feed status |
| `http://127.0.0.1:8765/healthz` | Health check |

If MT5 is running, the server automatically polls for live bars every 15 seconds and overlays them on the chart. Without MT5, it falls back to `sample_data.csv`.

---

## 8. Run Live Trading (Experimental)

A basic live trading script is available. Run it from inside the `algoTrading/` directory:

```bash
cd algoTrading
python main.py
```

This connects to MT5, fetches the latest bars for `XAUUSD`, and prints available symbols. Actual order execution is commented out — uncomment the signal/order logic in `main.py` to enable it.

---

## Project Structure

```
AlgoTrading/
├── algoTrading/          # Main source package (rename from src/)
│   ├── config.py         # Global configuration
│   ├── config.yaml       # Per-strategy lot size overrides
│   ├── main.py           # Live trading entry point
│   ├── main_backtest.py  # Backtest entry point
│   ├── chart_server.py   # HTTP chart/dashboard server
│   ├── dashboard.py      # Dashboard HTML generator
│   ├── core/             # MT5 connection utilities
│   ├── broker/           # MT5 and paper broker implementations
│   ├── data/             # Data fetching, loading, and CSV files
│   ├── backtest/         # Backtest engine and metrics
│   └── strategies/       # Strategy implementations
└── README.md
```

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'algoTrading'`**
Ensure you renamed `src/` to `algoTrading/` (Step 3) and are running from the project root.

**`MT5 not running` or connection failure**
Open the MetaTrader5 terminal and log into your account before running any script.

**`Symbol 'XAUUSD' not found in MT5`**
Open the MT5 Market Watch, right-click, and add the symbol. Or change `SYMBOL` in `config.py` to a symbol available on your broker.
