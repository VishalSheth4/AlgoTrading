import json
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET
from django.views.decorators.csrf import csrf_exempt

from trading.mt5_service import (
    DASHBOARD_HTML, TRADE_CSV,
    _live, serve_lib, load_ohlcv, compute_trade_analytics,
)


# ── Legacy views (serve existing dashboard.html) ──────────────────────────────

@require_GET
def dashboard_view(request):
    if DASHBOARD_HTML.exists():
        return HttpResponse(DASHBOARD_HTML.read_bytes(), content_type="text/html; charset=utf-8")
    return HttpResponse(b"<h1>Run backtest first</h1>", status=404)


@require_GET
def ohlcv_view(request):
    try:
        limit = max(0, int(request.GET.get("limit", 0)))
        ohlcv, st, markers = load_ohlcv(limit)
        return JsonResponse({"ohlcv": ohlcv, "supertrend": st, "markers": markers, "live": dict(_live)})
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)


@require_GET
def trades_view(request):
    try:
        return JsonResponse(compute_trade_analytics())
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)


@require_GET
def status_view(request):
    return JsonResponse({"live": dict(_live)})


@require_GET
def healthz_view(request):
    return JsonResponse({"status": "ok", "live": dict(_live)})


@require_GET
def dashboard_hash_view(request):
    try:
        mtime = int(TRADE_CSV.stat().st_mtime * 1000) if TRADE_CSV.exists() else 0
    except Exception:
        mtime = 0
    return JsonResponse({"hash": mtime})


@require_GET
def static_lib_view(request, name):
    data = serve_lib(name)
    if data:
        return HttpResponse(data, content_type="application/javascript; charset=utf-8",
                            headers={"Cache-Control": "max-age=86400"})
    return HttpResponse(status=404)


# ── React API views ────────────────────────────────────────────────────────────

def _cors(response):
    response["Access-Control-Allow-Origin"] = "*"
    return response


@require_GET
def api_ohlcv(request):
    """GET /api/ohlcv?symbol=XAUUSD&limit=500&tf=M5"""
    try:
        limit  = max(0, int(request.GET.get("limit", 500)))
        ohlcv, st, markers = load_ohlcv(limit)
        return _cors(JsonResponse({
            "ohlcv":      ohlcv,
            "supertrend": st,
            "markers":    markers,
            "live":       dict(_live),
        }))
    except Exception as exc:
        return _cors(JsonResponse({"error": str(exc)}, status=500))


@require_GET
def api_trades(request):
    """GET /api/trades"""
    try:
        return _cors(JsonResponse(compute_trade_analytics()))
    except Exception as exc:
        return _cors(JsonResponse({"error": str(exc)}, status=500))


@require_GET
def api_status(request):
    """GET /api/status — live feed state"""
    return _cors(JsonResponse({
        "live":   dict(_live),
        "server": "ok",
    }))


@require_GET
def api_symbols(request):
    """GET /api/symbols — available OHLCV CSV symbols"""
    from pathlib import Path
    from trading.mt5_service import BASE
    data_dir = BASE / "data"
    symbols = sorted({
        p.stem.replace("ohlcv_", "").split("_")[0]
        for p in data_dir.glob("ohlcv_*.csv")
    })
    return _cors(JsonResponse({"symbols": symbols or ["XAUUSD"]}))
