from django.urls import path

from . import views

urlpatterns = [
    path('', views.candles_page, name='candles_page'),
    path('chart/', views.full_chart_page, name='full_chart_page'),
    path('backtest/', views.backtest_page, name='backtest_page'),
    path('health/', views.health, name='health'),
    path('api/candles/', views.candles_data, name='candles_data'),
    path('api/backtest/', views.backtest_data, name='backtest_data'),
    path('api/backtest/run/', views.backtest_run, name='backtest_run'),
    path('api/backtest/run-status/', views.backtest_run_status, name='backtest_run_status'),
    path('api/backtest/cancel/', views.backtest_cancel, name='backtest_cancel'),
    path('settings/', views.settings_page, name='settings_page'),
    path('api/settings/', views.settings_data, name='settings_data'),
    path('api/settings/save/', views.settings_save, name='settings_save'),
    path('api/settings/reset/', views.settings_reset, name='settings_reset'),
    path('api/settings/rr-matrix/', views.rr_matrix_data, name='rr_matrix_data'),
    path('api/settings/rr-matrix/save/', views.rr_matrix_save, name='rr_matrix_save'),
    path('api/settings/rr-matrix/reset/', views.rr_matrix_reset, name='rr_matrix_reset'),
    path('api/backtest/candles/', views.backtest_candles, name='backtest_candles'),
    path('api/price/', views.price_data, name='price_data'),
    path('api/news/', views.news_data, name='news_data'),
    path('api/auto-trade/', views.auto_trade_status, name='auto_trade_status'),
    path('api/auto-trade/toggle/', views.auto_trade_toggle, name='auto_trade_toggle'),
    path('api/strategies/', views.strategies_status, name='strategies_status'),
    path('api/strategies/toggle/', views.strategies_toggle, name='strategies_toggle'),
    path('api/strategies/rr/', views.strategy_rr_status, name='strategy_rr_status'),
    path('api/strategies/rr/set/', views.strategy_rr_set, name='strategy_rr_set'),
    path('api/strategies/supertrend-filter/', views.strategy_supertrend_filter_status, name='strategy_supertrend_filter_status'),
    path('api/strategies/supertrend-filter/set/', views.strategy_supertrend_filter_set, name='strategy_supertrend_filter_set'),
]
