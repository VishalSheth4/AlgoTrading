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

    # Alerts
    path("api/alerts/config", views.api_alerts_config, name="api_alerts_config"),
    path("api/alerts/save",   views.api_alerts_save,   name="api_alerts_save"),
    path("api/alerts/test",   views.api_alerts_test,   name="api_alerts_test"),

    # NSE / BSE module
    path("api/nse/config",   views.api_nse_config,       name="api_nse_config"),
    path("api/nse/toggle",   views.api_nse_toggle,       name="api_nse_toggle"),
    path("api/nse/save",     views.api_nse_save_config,  name="api_nse_save"),
    path("api/nse/run",      views.api_nse_run,          name="api_nse_run"),
    path("api/nse/results",  views.api_nse_results,      name="api_nse_results"),

    # Live bot control
    path("api/livebot",        views.api_livebot_status, name="api_livebot"),
    path("api/livebot/start",  views.api_livebot_start,  name="api_livebot_start"),
    path("api/livebot/stop",   views.api_livebot_stop,   name="api_livebot_stop"),
    path("api/livebot/log",    views.api_livebot_log,    name="api_livebot_log"),

    # SuperTrend watcher
    path("api/supertrend/status", views.api_st_status, name="api_st_status"),
    path("api/supertrend/start",  views.api_st_start,  name="api_st_start"),
    path("api/supertrend/stop",   views.api_st_stop,   name="api_st_stop"),
    path("api/supertrend/log",    views.api_st_log,    name="api_st_log"),

    # Legacy endpoints (kept for backward compat)
    path("ohlcv",             views.ohlcv_view,           name="ohlcv"),
    path("trades",            views.trades_view,          name="trades"),
    path("status",            views.status_view,          name="status"),
    path("healthz",           views.healthz_view,         name="healthz"),
    path("dashboard_hash",    views.dashboard_hash_view,  name="dashboard_hash"),
    path("static/<str:name>", views.static_lib_view,      name="static_lib"),
]
