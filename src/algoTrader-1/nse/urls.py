from django.urls import path

from . import views

urlpatterns = [
    path('', views.nse_dashboard_page, name='nse_dashboard_page'),
    path('api/signals/', views.nse_signals_data, name='nse_signals_data'),
]
