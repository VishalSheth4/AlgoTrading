# AlgoTrading Pro

A Python algorithmic trading platform built on MetaTrader5 (MT5).  
Supports backtesting across multiple strategies, live MT5 trading, and a **real-time React dashboard** served by Django + WebSockets.

---

## Quick Start (Windows)

```
Double-click  start.bat
```

Or from a terminal:

```bat
start.bat
```

That single file:
1. Checks Python + Node are installed
2. Installs all Python packages (`django`, `channels`, `daphne`, `pandas`, …)
3. Runs `npm install` in `frontend/` on first run
4. Starts both servers and opens the dashboard

| URL | What |
|-----|------|
| **http://localhost:5173** | React dashboard ← open this |
| http://localhost:8000/api/trades | Trade analytics JSON |
| http://localhost:8000/api/status | Live feed status |

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Python 3.10+ | `python --version` to check |
| Node.js 18+ | `node --version` to check — [nodejs.org](https://nodejs.org) |
| MetaTrader5 terminal | Windows only — for live price data |

---

## Manual Setup (step-by-step)

### 1. Clone

```bash
git clone https://github.com/VishalSheth4/AlgoTrading.git
cd AlgoTrading
```

### 2. Python dependencies

```bash
pip install django channels daphne djangorestframework django-cors-headers
pip install pandas numpy pyyaml python-dotenv MetaTrader5
```

### 3. Node dependencies

```bash
cd frontend
npm install
cd ..
```

### 4. Run

```bash
python run.py
```

Open **http://localhost:5173**

---

## Dashboard Features

| Feature | Detail |
|---------|--------|
| **Real-time candlestick chart** | LightweightCharts v4, updates every 1 second |
| **Live price ticker** | Current price, bid/ask, daily change %, IST clock |
| **Supertrend overlay** | Toggle on/off directly on the chart |
| **6 metric cards** | P&L, Win Rate, Max Drawdown, Profit Factor, Avg Win/Loss, Streak |
| **Equity curve** | Recharts area chart, downsampled for performance |
| **Trade log** | Paginated table with Symbol / Strategy / Direction filters |
| **WebSocket feed** | Auto-reconnects, falls back to CSV simulation when MT5 is offline |

---

## Running a Backtest

```bash
cd src
python -m algoTrading.main_backtest
```

What happens:
1. Fetches OHLCV bars from MT5 (or uses cached CSV)
2. Generates signals from all configured strategies
3. Runs the event-driven backtest engine
4. Saves trade log → `src/algoTrading/data/trade_data.csv`
5. Dashboard auto-refreshes to show new results

---

## Configuration

Edit `src/algoTrading/config.py`:

| Setting | Default | Description |
|---------|---------|-------------|
| `SYMBOL` | `"XAUUSD"` | Symbol — comma-separated for multi-symbol |
| `STRATEGY` | `"session_strategy"` | Strategy key(s) from `config.yaml` |
| `TIMEFRAME` | `"M5"` | Primary candle timeframe |
| `INITIAL_CAPITAL` | `100` | Starting capital (USD) |
| `RISK_PER_TRADE` | `0.01` | Fraction risked per trade |
| `TP_MODE` | `"rr"` | `"rr"` / `"st"` / `"both"` |
| `START_DATE` | `"2016-01-01"` | Backtest start |
| `END_DATE` | `"2026-06-01"` | Backtest end |

Per-strategy overrides (lot size, RR, filters) live in `src/algoTrading/config.yaml`.

---

## Available Strategies

| Key | Description |
|-----|-------------|
| `session_strategy` | Trade candle closes at configurable session times (IST/UTC) |
| `SupertrendCounterFlip_X1` | Counter-flip on Supertrend direction change |
| `ict_simple_1h5m_fvg` | ICT fair-value gap with sweep + displacement |
| `engulfing_consolidation` | Engulfing pattern within consolidation |
| `engulfing_reversal` | Engulfing at key reversal levels |
| `supertrend_engulfing_reversal` | Supertrend + engulfing combo |
| `SupertrendTouchSell` | Supertrend touch-and-reverse |
| `rsi_buy_sell` | RSI overbought/oversold |
| `rsi_ema_double_cross` | RSI + EMA dual-cross |
| `DojiStrategy` | Doji candle entries |

---

## Project Structure

```
AlgoTrading/
├── start.bat               ← ONE command: installs everything + starts servers
├── run.py                  ← starts Django + React together
│
├── frontend/               ← React + Vite dashboard
│   ├── package.json
│   ├── vite.config.js      ← proxies /api + /ws to Django:8000
│   └── src/
│       ├── App.jsx         ← React Router shell
│       ├── store/          ← Zustand global state
│       ├── hooks/
│       │   └── useWebSocket.js    ← auto-reconnecting WS hook
│       ├── components/
│       │   ├── CandleChart.jsx    ← real-time M1 candles (LightweightCharts)
│       │   ├── Header.jsx         ← live price, bid/ask, IST clock
│       │   ├── MetricsPanel.jsx   ← 6 stat cards
│       │   ├── TradeLog.jsx       ← filterable, paginated table
│       │   └── EquityCurve.jsx    ← Recharts equity chart
│       └── pages/
│           ├── Dashboard.jsx      ← main view
│           └── Trades.jsx         ← full trade history
│
└── src/                    ← Django backend
    ├── manage.py
    ├── config/
    │   ├── settings.py     ← channels + daphne + rest_framework
    │   └── asgi.py         ← HTTP + WebSocket routing
    ├── trading/
    │   ├── consumers.py    ← PriceConsumer (1s tick) + TradesConsumer
    │   ├── routing.py      ← ws/price/<symbol>/ + ws/trades/
    │   ├── views.py        ← /api/ohlcv, /api/trades, /api/status
    │   └── mt5_service.py  ← MT5 live feed + analytics
    └── algoTrading/        ← core backtest engine + strategies
        ├── config.py
        ├── config.yaml
        ├── main_backtest.py
        ├── backtest/engine.py
        └── strategies/
```

---

## WebSocket API

| Endpoint | Description |
|----------|-------------|
| `ws://localhost:8000/ws/price/XAUUSD/` | On connect: 600 historical bars + supertrend + markers. Then every **1 second**: `{price, bid, ask, change, bar}` |
| `ws://localhost:8000/ws/trades/` | On connect: full trade analytics. Pushes update whenever `trade_data.csv` changes |

---

## REST API

| Endpoint | Description |
|----------|-------------|
| `GET /api/ohlcv?limit=500` | OHLCV bars + supertrend + markers |
| `GET /api/trades` | Full trade analytics (metrics, monthly, equity curve) |
| `GET /api/status` | MT5 live feed status |
| `GET /api/symbols` | Available OHLCV symbols |

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'channels'`**
```
pip install channels daphne
```

**MT5 not connected**
Open MetaTrader5 terminal and log in. If MT5 is unavailable, the chart simulates prices from the last saved CSV data.

**Port already in use**
Kill existing processes:
```bat
netstat -ano | findstr :8000
taskkill /PID <pid> /F
```

**Node not found**
Install from [nodejs.org](https://nodejs.org) — LTS version, then re-run `start.bat`.
