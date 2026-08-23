from django.http import JsonResponse
from django.shortcuts import render

from .services.nse_dashboard_service import get_nse_dashboard_service


def nse_dashboard_page(request):
    return render(request, "nse/dashboard.html")


def nse_signals_data(request):
    service = get_nse_dashboard_service()
    return JsonResponse({
        "signals": service.get_signals(),
        "first_cycle_done": service.is_first_cycle_done,
    })
