from django.urls import path
from trading import views

urlpatterns = [
    path("",                  views.dashboard_view,      name="dashboard"),
    path("ohlcv",             views.ohlcv_view,          name="ohlcv"),
    path("trades",            views.trades_view,          name="trades"),
    path("status",            views.status_view,          name="status"),
    path("healthz",           views.healthz_view,         name="healthz"),
    path("dashboard_hash",    views.dashboard_hash_view,  name="dashboard_hash"),
    path("static/<str:name>", views.static_lib_view,      name="static_lib"),
]
