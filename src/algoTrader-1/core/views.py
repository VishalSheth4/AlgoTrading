import json
import logging

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from .services.backtest_runner import cancel_backtest, get_status, start_backtest
from .services.backtest_service import (
    list_available_candle_timeframes,
    list_available_timeframe_results,
    load_backtest_results,
    load_candles_with_markers,
)
from .services.candle_service import get_candle_service
from .services.settings_service import (
    get_rr_matrix,
    get_settings,
    reset_rr_matrix,
    reset_settings,
    save_rr_matrix_cells,
    save_settings,
)
from .services.news_service import get_news_service
from .services.price_service import get_price_service

logger = logging.getLogger(__name__)


def health(request):
    return JsonResponse({"status": "ok"})


def candles_page(request):
    service = get_candle_service()
    return render(request, "core/candles.html", {
        "symbol": service.symbol, "active_tab": "dashboard",
        "header_title": "vishal terminal", "header_variant": "dashboard",
    })


def full_chart_page(request):
    service = get_candle_service()
    return render(request, "core/full_chart.html", {
        "symbol": service.symbol, "active_tab": "chart",
        "header_title": f"{service.symbol} Chart", "header_variant": "chart",
    })


def backtest_page(request):
    return render(request, "core/backtest.html", {
        "active_tab": "backtest", "header_title": "Backtest",
    })


def backtest_data(request):
    timeframe = request.GET.get("timeframe") or None
    available_timeframes = list_available_timeframe_results()
    results = load_backtest_results(timeframe=timeframe)
    if results is None:
        return JsonResponse({"available": False, "available_timeframe_results": available_timeframes})
    return JsonResponse({"available": True, "available_timeframe_results": available_timeframes, **results})


@require_POST
def backtest_run(request):
    # POST-only and CSRF-protected, same as the other action buttons --
    # this launches a real MT5 fetch + backtest subprocess (see
    # backtest_runner.py), not a passive read.
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)

    strategies = body.get("strategies") or None
    symbol = body.get("symbol") or None
    timeframe = body.get("timeframe") or None
    timeframes = body.get("timeframes") or None
    bars = body.get("bars") or None
    date_from = body.get("date_from") or None
    date_to = body.get("date_to") or None
    risk_mode = body.get("risk_mode") or None
    risk_amount = body.get("risk_amount")
    if risk_amount not in (None, ""):
        try:
            risk_amount = float(risk_amount)
        except (TypeError, ValueError):
            return JsonResponse({"error": "'risk_amount' must be a number"}, status=400)
    else:
        risk_amount = None
    max_lot_size = body.get("max_lot_size")
    if max_lot_size not in (None, ""):
        try:
            max_lot_size = float(max_lot_size)
        except (TypeError, ValueError):
            return JsonResponse({"error": "'max_lot_size' must be a number"}, status=400)
    else:
        max_lot_size = None
    return JsonResponse(start_backtest(
        strategies=strategies, symbol=symbol, timeframe=timeframe, timeframes=timeframes, bars=bars,
        date_from=date_from, date_to=date_to, risk_mode=risk_mode, risk_amount=risk_amount,
        max_lot_size=max_lot_size,
    ))


def backtest_run_status(request):
    return JsonResponse(get_status())


@require_POST
def backtest_cancel(request):
    # POST-only and CSRF-protected, same as the other action buttons --
    # this kills a real MT5-fetching subprocess, not a passive read.
    return JsonResponse(cancel_backtest())


def settings_page(request):
    return render(request, "core/settings.html", {
        "active_tab": "settings", "header_title": "Settings",
    })


def settings_data(request):
    return JsonResponse(get_settings())


@require_POST
def settings_save(request):
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)

    values = body.get("values")
    if not isinstance(values, dict):
        return JsonResponse({"error": "'values' must be an object"}, status=400)

    try:
        result = save_settings(values)
    except (TypeError, ValueError) as exc:  # unknown field, or a bad numeric cast (e.g. "abc" for BARS)
        return JsonResponse({"error": str(exc)}, status=400)
    logger.info("Settings saved: %s", list(values.keys()))
    return JsonResponse(result)


@require_POST
def settings_reset(request):
    logger.info("Settings reset to defaults")
    return JsonResponse(reset_settings())


def rr_matrix_data(request):
    return JsonResponse(get_rr_matrix())


@require_POST
def rr_matrix_save(request):
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)

    updates = body.get("updates")
    if not isinstance(updates, dict):
        return JsonResponse({"error": "'updates' must be an object"}, status=400)

    try:
        result = save_rr_matrix_cells(updates)
    except (TypeError, ValueError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    logger.info("RR matrix updated: %s", {k: list(v.keys()) for k, v in updates.items()})
    return JsonResponse(result)


@require_POST
def rr_matrix_reset(request):
    logger.info("RR matrix reset to 1:1 everywhere")
    return JsonResponse(reset_rr_matrix())


def backtest_candles(request):
    symbol = request.GET.get("symbol") or "XAUUSD"
    timeframe = request.GET.get("timeframe")
    if not timeframe:
        # No timeframe picked yet -- still report which ones have data, so
        # the frontend can render/enable the right timeframe buttons before
        # the user picks one.
        return JsonResponse({
            "available": False,
            "available_candle_timeframes": list_available_candle_timeframes(symbol),
        })

    max_bars = int(request.GET.get("max_bars", 5000))
    data = load_candles_with_markers(symbol, timeframe, max_bars=max_bars)
    if data is None:
        return JsonResponse({
            "available": False,
            "available_candle_timeframes": list_available_candle_timeframes(symbol),
        })
    return JsonResponse({
        "available": True,
        "available_candle_timeframes": list_available_candle_timeframes(symbol),
        **data,
    })


def candles_data(request):
    service = get_candle_service()
    return JsonResponse(service.get_candles())


def price_data(request):
    service = get_price_service()
    price = service.get_price()
    return JsonResponse(price or {"symbol": service.symbol})


def news_data(request):
    service = get_news_service()
    return JsonResponse({"headlines": service.get_headlines()})


def auto_trade_status(request):
    service = get_candle_service()
    return JsonResponse({"enabled": service.auto_trade_enabled})


@require_POST
def auto_trade_toggle(request):
    # POST-only and CSRF-protected on purpose (not @csrf_exempt) -- this
    # flips whether REAL market orders get placed, so it deliberately
    # does NOT take the easy "just disable CSRF" shortcut.
    service = get_candle_service()
    new_state = not service.auto_trade_enabled
    service.set_auto_trade_enabled(new_state)
    logger.info("Auto Trade %s", "ENABLED" if new_state else "DISABLED")
    return JsonResponse({"enabled": new_state})


def strategies_status(request):
    service = get_candle_service()
    return JsonResponse({"strategies": service.list_strategy_states()})


@require_POST
def strategies_toggle(request):
    # POST-only and CSRF-protected on purpose, same as auto-trade toggle --
    # this flips whether a strategy's alerts/markers/auto-trading are live.
    service = get_candle_service()
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)

    name = body.get("name")
    if not name:
        return JsonResponse({"error": "'name' is required"}, status=400)

    current_state = service.list_strategy_states().get(name)
    if current_state is None:
        return JsonResponse({"error": f"unknown strategy: {name}"}, status=400)

    new_state = service.set_strategy_enabled(name, not current_state)
    logger.info("Strategy %s %s", name, "ENABLED" if new_state else "DISABLED")
    return JsonResponse({"name": name, "enabled": new_state})


def strategy_rr_status(request):
    service = get_candle_service()
    return JsonResponse({"rr": service.list_strategy_rr()})


@require_POST
def strategy_rr_set(request):
    # POST-only and CSRF-protected on purpose, same as the enable/disable
    # toggles -- this changes the SL/TP auto-trading uses for real orders.
    service = get_candle_service()
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)

    name = body.get("name")
    rr = body.get("rr")
    if not name:
        return JsonResponse({"error": "'name' is required"}, status=400)
    try:
        rr = float(rr)
    except (TypeError, ValueError):
        return JsonResponse({"error": "'rr' must be a number"}, status=400)
    if rr <= 0:
        return JsonResponse({"error": "'rr' must be positive"}, status=400)

    try:
        new_rr = service.set_strategy_rr(name, rr)
    except KeyError:
        return JsonResponse({"error": f"unknown or non-configurable strategy: {name}"}, status=400)

    logger.info("Strategy %s RR set to %s", name, new_rr)
    return JsonResponse({"name": name, "rr": new_rr})


def strategy_supertrend_filter_status(request):
    service = get_candle_service()
    return JsonResponse({"supertrend_filter": service.list_strategy_supertrend_filter()})


@require_POST
def strategy_supertrend_filter_set(request):
    # POST-only and CSRF-protected on purpose, same as the other live-
    # trading toggles -- this changes which signals reach auto-trading.
    service = get_candle_service()
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)

    name = body.get("name")
    enabled = body.get("enabled")
    if not name:
        return JsonResponse({"error": "'name' is required"}, status=400)
    if not isinstance(enabled, bool):
        return JsonResponse({"error": "'enabled' must be a boolean"}, status=400)

    try:
        new_value = service.set_strategy_supertrend_filter(name, enabled)
    except KeyError:
        return JsonResponse({"error": f"unknown or non-configurable strategy: {name}"}, status=400)

    logger.info("Strategy %s Supertrend filter set to %s", name, new_value)
    return JsonResponse({"name": name, "supertrend_filter": new_value})
