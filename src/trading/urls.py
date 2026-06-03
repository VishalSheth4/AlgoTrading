from django.urls import path
from trading import views

urlpatterns = [
    # Legacy dashboard (HTML)
    path("",                  views.dashboard_view,       name="dashboard"),

    # React frontend API endpoints
    path("api/ohlcv",         views.api_ohlcv,            name="api_ohlcv"),
    path("api/trades",        views.api_trades,           name="api_trades"),
    path("api/status",        views.api_status,           name="api_status"),
    path("api/symbols",       views.api_symbols,          name="api_symbols"),
    path("api/run-backtest",  views.api_run_backtest,     name="api_run_backtest"),

    # Legacy endpoints (kept for backward compat)
    path("ohlcv",             views.ohlcv_view,           name="ohlcv"),
    path("trades",            views.trades_view,          name="trades"),
    path("status",            views.status_view,          name="status"),
    path("healthz",           views.healthz_view,         name="healthz"),
    path("dashboard_hash",    views.dashboard_hash_view,  name="dashboard_hash"),
    path("static/<str:name>", views.static_lib_view,      name="static_lib"),
]
