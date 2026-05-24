# AlgoTrading

A Python algorithmic trading system built on MetaTrader5 (MT5). Supports backtesting across multiple strategies and symbols, live MT5 trading, and an interactive React dashboard served by Django.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.10+ | |
| MetaTrader5 terminal | Windows only — required for live data and trading |
| MT5 account | Demo or live account logged in inside the terminal |

---

## 1. Clone & Set Up Virtual Environment

```bash
git clone https://github.com/VishalSheth4/AlgoTrading.git
cd AlgoTrading/src

python -m venv venv

# Windows
venv\Scripts\activate

# Linux / macOS
source venv/bin/activate
```

---

## 2. Install Dependencies

```bash
pip install --upgrade pip

pip install MetaTrader5 pandas numpy matplotlib
pip install django djangorestframework django-cors-headers
pip install gunicorn waitress whitenoise python-dotenv
pip install requests beautifulsoup4 lxml pyyaml
```

---

## 3. Configure the Project

Edit `src/algoTrading/config.py`:

| Setting | Default | Description |
|---|---|---|
| `SYMBOL` | `"XAUUSD"` | Symbol(s) — comma-separated for multi-symbol |
| `STRATEGY` | `"mark2,..."` | Strategy/strategies — comma-separated |
| `TIMEFRAME` | `"M15"` | Primary candle timeframe |
| `INITIAL_CAPITAL` | `100` | Starting capital per symbol (USD) |
| `RISK_PER_TRADE` | `0.04` | Fraction of capital risked per trade |
| `TP_MODE` | `"rr"` | `"rr"` / `"st"` / `"both"` / `"fix_profit"` |
| `RR` | `5` | Risk-reward ratio (used when `TP_MODE = "rr"`) |
| `START_DATE` | `"2016-01-01"` | Backtest start date |
| `END_DATE` | `"2026-06-01"` | Backtest end date |
| `MT5_LOGIN` | *(your login)* | MT5 account number |
| `MT5_PASSWORD` | *(your password)* | MT5 account password |
| `MT5_SERVER` | `"ICMarketsSC-Demo"` | MT5 broker server name |

**Multi-symbol / multi-strategy examples:**
```python
SYMBOL   = "XAUUSD,EURUSD,GBPUSD"
STRATEGY = "mark2,mark_dollar_supertrend,engulfing"
```

Per-strategy lot size overrides live in `src/algoTrading/config.yaml`.

---

## 4. Available Strategies

| Key | Description |
|---|---|
| `mark2` | Supertrend flip with counter-trend filter |
| `mark_dollar_supertrend` | Dollar-weighted Supertrend |
| `engulfing` | Bullish / bearish engulfing candle |
| `engulfing_consolidation` | Engulfing within consolidation range |
| `engulfing_reversal` | Engulfing at key reversal levels |
| `SupertrendCounterFlip_X1` | Counter-flip on Supertrend change |
| `EmaCrossoverRetestStrategy` | EMA crossover + retest entry |
| `Ema200PullbackEngulfingStrategy` | EMA-200 pullback with engulfing |
| `RSIBuySellStrategy` | RSI overbought/oversold signals |
| `RSIEMADoubleCrossStrategy` | RSI + EMA dual-cross system |
| `DojiStrategy` | Doji candle pattern entries |

---

## 5. Run a Backtest

Make sure the MetaTrader5 terminal is open and logged in, then run from `src/`:

```bash
cd src
python -m algoTrading.main_backtest
```

What happens:
1. Fetches fresh OHLCV bars from MT5 for each configured symbol
2. Concurrently downloads M5, M15, M30, H1, H4 timeframes (15-min cache)
3. Generates and merges signals from all configured strategies
4. Runs the backtest engine bar-by-bar with risk-based position sizing
5. Saves the full trade log → `src/algoTrading/data/trade_data.csv`
6. Generates the dashboard → `src/algoTrading/data/dashboard.html`
7. Opens the dashboard in your browser at `http://localhost:8765`

---

## 6. Run the Dashboard Server

The dashboard is a **Django application** that serves the interactive UI and all API endpoints on a single port. Start it once — no separate processes needed.

### Development (auto-reload on code changes)

```bash
cd src
python run.py           # port 8765
python run.py 9000      # custom port
```

### Production (Windows — waitress)

```bash
cd src
python run_server.py
```

Or double-click:

```
src/deploy/windows/start.bat
```

### Production (Linux — gunicorn)

```bash
cd src
python run_server.py
```

Or use the shell script:

```bash
chmod +x src/deploy/linux/start.sh
./src/deploy/linux/start.sh
```

### Environment Configuration

Copy the example env file and edit it before going public:

```bash
# From src/
cp .env.example .env
```

Key variables in `.env`:

```env
DJANGO_SECRET_KEY=<generate with python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())">
ALLOWED_HOSTS=localhost,127.0.0.1,your-server-ip
PORT=8765
DJANGO_SETTINGS_MODULE=config.production_settings
```

---

## 7. Auto-Start on Boot

### Linux — systemd service

```bash
# Edit the paths inside the file first
sudo cp src/deploy/linux/algotrading.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now algotrading

# View logs
sudo journalctl -u algotrading -f
```

### Windows — Windows Service (requires NSSM)

1. Download [NSSM](https://nssm.cc/download) and place `nssm.exe` next to `install_service.bat`
2. Right-click `src/deploy/windows/install_service.bat` → **Run as administrator**
3. The service starts automatically on Windows boot

---

## 8. Dashboard Endpoints

| URL | Description |
|---|---|
| `http://localhost:8765/` | Interactive React dashboard |
| `http://localhost:8765/trades` | Full analytics JSON (metrics, monthly breakdown, trade log) |
| `http://localhost:8765/ohlcv?limit=0` | OHLCV + Supertrend + trade markers (all bars) |
| `http://localhost:8765/status` | MT5 live feed status |
| `http://localhost:8765/healthz` | Health check |
| `http://localhost:8765/dashboard_hash` | Mtime hash — browser auto-refreshes after each backtest |
| `http://localhost:8765/admin/` | Django admin panel |

---

## 9. Project Structure

```
AlgoTrading/
└── src/                          ← Django project root (run everything from here)
    ├── run.py                    ← Development server (port 8765)
    ├── run_server.py             ← Production server (waitress/gunicorn, OS-aware)
    ├── manage.py                 ← Standard Django management utility
    ├── .env                      ← Your secrets and config (not in git)
    ├── .env.example              ← Template for .env
    │
    ├── config/                   ← Django project config
    │   ├── settings.py           ← Base settings
    │   ├── production_settings.py← Production overrides (DEBUG=False, WhiteNoise)
    │   ├── urls.py               ← Root URL routing
    │   ├── wsgi.py
    │   └── asgi.py
    │
    ├── trading/                  ← Django app — views, MT5 service, URLs
    │   ├── views.py              ← All HTTP endpoint handlers
    │   ├── urls.py               ← URL patterns
    │   ├── apps.py               ← Starts MT5 live-feed thread on startup
    │   └── mt5_service.py        ← Shared MT5 state, analytics, OHLCV loading
    │
    ├── algoTrading/              ← Core trading package
    │   ├── config.py             ← Global trading configuration
    │   ├── config.yaml           ← Per-strategy overrides
    │   ├── main_backtest.py      ← Backtest entry point
    │   ├── main.py               ← Live trading entry point
    │   ├── dashboard.py          ← HTML dashboard generator
    │   ├── chart_server.py       ← Standalone HTTP server (legacy fallback)
    │   ├── core/                 ← MT5 connection utilities
    │   ├── broker/               ← MT5 and paper broker
    │   ├── data/                 ← Data fetching, loading, CSV files
    │   │   ├── fetch_mt5.py      ← MT5 fetcher (15-min cache, multi-TF)
    │   │   └── ohlcv_*.csv       ← Cached OHLCV files per symbol/timeframe
    │   ├── backtest/             ← Backtest engine and metrics
    │   └── strategies/           ← All strategy implementations
    │
    └── deploy/
        ├── linux/
        │   ├── start.sh
        │   ├── algotrading.service   ← systemd unit file
        │   └── nginx.conf            ← Optional reverse proxy config
        └── windows/
            ├── start.bat
            └── install_service.bat   ← Windows Service installer (NSSM)
```

---

## 10. Nginx Reverse Proxy (Optional — Linux)

Put nginx in front for SSL, compression, and caching:

```bash
sudo apt install nginx certbot python3-certbot-nginx

# Edit YOUR_DOMAIN in the config first
sudo cp src/deploy/linux/nginx.conf /etc/nginx/sites-available/algotrading
sudo ln -s /etc/nginx/sites-available/algotrading /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# Free SSL certificate
sudo certbot --nginx -d YOUR_DOMAIN
```

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'algoTrading'`**
Run all commands from the `src/` directory.

**`MT5 not running` or connection failure**
Open MetaTrader5 terminal and log into your account before running any script.

**`Symbol 'XAUUSD' not found in MT5`**
In MT5 → Market Watch → right-click → add the symbol. Or change `SYMBOL` in `config.py`.

**Dashboard shows "Server Offline"**
Start the server first: `cd src && python run.py`

**MT5 not available on Linux**
MetaTrader5 is Windows-only. On Linux, the live feed is disabled and the dashboard uses the last saved historical data (`sample_data.csv`). Backtesting still works if you copy CSV data manually.
