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


@csrf_exempt
def api_run_backtest(request):
    """
    POST /api/run-backtest
    Runs the full backtest pipeline in a background thread.
    Returns immediately with {"status": "started"}.
    GET  /api/run-backtest  → returns current run status.
    """
    import threading
    import time as _time

    global _backtest_status
    if request.method == "GET":
        return _cors(JsonResponse(_backtest_status))

    if _backtest_status.get("running"):
        return _cors(JsonResponse({"status": "already_running", **_backtest_status}))

    def _run():
        global _backtest_status
        _backtest_status = {"running": True, "status": "running", "started": _time.strftime("%H:%M:%S"), "error": None}
        try:
            import sys
            from pathlib import Path
            _src = Path(__file__).resolve().parents[1]
            if str(_src) not in sys.path:
                sys.path.insert(0, str(_src))
            from algoTrading.main_backtest import main
            main()
            _backtest_status = {"running": False, "status": "done", "finished": _time.strftime("%H:%M:%S"), "error": None}
        except Exception as exc:
            _backtest_status = {"running": False, "status": "error", "error": str(exc)}

    _backtest_status = {"running": True, "status": "starting"}
    threading.Thread(target=_run, daemon=True, name="backtest").start()
    return _cors(JsonResponse({"status": "started"}))


_backtest_status: dict = {"running": False, "status": "idle"}

# ── NSE / BSE endpoints ───────────────────────────────────────────────────────

_nse_status: dict = {"running": False, "status": "idle", "error": None}


@require_GET
def api_alerts_config(request):
    """GET /api/alerts/config"""
    try:
        import yaml
        from pathlib import Path
        cfg_path = Path(__file__).resolve().parents[1] / "algoTrading" / "config.yaml"
        with open(cfg_path) as f:
            data = yaml.safe_load(f) or {}
        return _cors(JsonResponse({"ok": True, "config": data.get("alerts", {})}))
    except Exception as exc:
        return _cors(JsonResponse({"ok": False, "error": str(exc)}, status=500))


@csrf_exempt
def api_alerts_save(request):
    """POST /api/alerts/config — save full alerts block"""
    if request.method != "POST":
        return _cors(JsonResponse({"error": "POST only"}, status=405))
    try:
        import yaml
        from pathlib import Path
        body = json.loads(request.body or b"{}")
        cfg_path = Path(__file__).resolve().parents[1] / "algoTrading" / "config.yaml"
        with open(cfg_path) as f:
            data = yaml.safe_load(f) or {}
        data["alerts"] = body
        with open(cfg_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return _cors(JsonResponse({"ok": True}))
    except Exception as exc:
        return _cors(JsonResponse({"ok": False, "error": str(exc)}, status=500))


@csrf_exempt
def api_alerts_test(request):
    """POST /api/alerts/test?channel=telegram|ntfy|email|all"""
    if request.method != "POST":
        return _cors(JsonResponse({"error": "POST only"}, status=405))
    try:
        import sys
        from pathlib import Path
        _src = Path(__file__).resolve().parents[1]
        if str(_src) not in sys.path:
            sys.path.insert(0, str(_src))
        from algoTrading.alerts.notifier import broadcast, send_telegram, send_ntfy, send_email, _load_alert_cfg

        channel = request.GET.get("channel", "all")
        title   = "Test Alert — AlgoTrading Pro"
        body    = "This is a test alert. Your notifications are working correctly!"
        cfg     = _load_alert_cfg()

        if channel == "telegram":
            ok, err = send_telegram(f"{title}\n{body}", cfg.get("telegram", {}))
            results = {"telegram": {"ok": ok, "error": err}}
        elif channel == "ntfy":
            ok, err = send_ntfy(title, body, cfg.get("ntfy", {}))
            results = {"ntfy": {"ok": ok, "error": err}}
        elif channel == "email":
            ok, err = send_email(title, body, cfg.get("email", {}))
            results = {"email": {"ok": ok, "error": err}}
        else:
            results = broadcast(title, body, cfg)

        return _cors(JsonResponse({"ok": True, "results": results}))
    except Exception as exc:
        return _cors(JsonResponse({"ok": False, "error": str(exc)}, status=500))


@require_GET
def api_nse_config(request):
    """GET /api/nse/config — current nse_bse block from config.yaml"""
    try:
        import yaml
        from pathlib import Path
        cfg_path = Path(__file__).resolve().parents[1] / "algoTrading" / "config.yaml"
        with open(cfg_path) as f:
            data = yaml.safe_load(f) or {}
        nse = data.get("nse_bse", {})
        return _cors(JsonResponse({"ok": True, "config": nse}))
    except Exception as exc:
        return _cors(JsonResponse({"ok": False, "error": str(exc)}, status=500))


@csrf_exempt
def api_nse_toggle(request):
    """POST /api/nse/toggle  body: {"enabled": true|false}"""
    if request.method != "POST":
        return _cors(JsonResponse({"error": "POST only"}, status=405))
    try:
        import yaml
        from pathlib import Path
        body = json.loads(request.body or b"{}")
        enabled = bool(body.get("enabled", False))

        cfg_path = Path(__file__).resolve().parents[1] / "algoTrading" / "config.yaml"
        with open(cfg_path) as f:
            data = yaml.safe_load(f) or {}

        if "nse_bse" not in data:
            data["nse_bse"] = {}
        data["nse_bse"]["enabled"] = enabled

        with open(cfg_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        return _cors(JsonResponse({"ok": True, "enabled": enabled}))
    except Exception as exc:
        return _cors(JsonResponse({"ok": False, "error": str(exc)}, status=500))


@csrf_exempt
def api_nse_save_config(request):
    """POST /api/nse/config  body: {exchange, timeframe, symbols, start_date, end_date, ...}"""
    if request.method != "POST":
        return _cors(JsonResponse({"error": "POST only"}, status=405))
    try:
        import yaml
        from pathlib import Path
        body = json.loads(request.body or b"{}")

        cfg_path = Path(__file__).resolve().parents[1] / "algoTrading" / "config.yaml"
        with open(cfg_path) as f:
            data = yaml.safe_load(f) or {}

        if "nse_bse" not in data:
            data["nse_bse"] = {}

        allowed = ["enabled", "exchange", "timeframe", "symbols",
                   "start_date", "end_date", "initial_capital", "risk_per_trade", "strategy"]
        for key in allowed:
            if key in body:
                data["nse_bse"][key] = body[key]

        with open(cfg_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        return _cors(JsonResponse({"ok": True}))
    except Exception as exc:
        return _cors(JsonResponse({"ok": False, "error": str(exc)}, status=500))


@csrf_exempt
def api_nse_run(request):
    """POST /api/nse/run — start NSE/BSE backtest in background thread"""
    import threading, time as _time

    if request.method == "GET":
        return _cors(JsonResponse(_nse_status))

    if _nse_status.get("running"):
        return _cors(JsonResponse({"status": "already_running", **_nse_status}))

    body = {}
    try:
        body = json.loads(request.body or b"{}")
    except Exception:
        pass

    def _run():
        global _nse_status
        _nse_status = {"running": True, "status": "running", "started": _time.strftime("%H:%M:%S"), "error": None}
        try:
            import sys
            from pathlib import Path
            _src = Path(__file__).resolve().parents[1]
            if str(_src) not in sys.path:
                sys.path.insert(0, str(_src))
            from algoTrading.nse_backtest import run_nse_backtest
            result = run_nse_backtest(override_cfg=body if body else None)
            if result.get("error"):
                _nse_status = {"running": False, "status": "error", "error": result["error"]}
            else:
                _nse_status = {
                    "running":  False,
                    "status":   "done",
                    "finished": _time.strftime("%H:%M:%S"),
                    "error":    None,
                    "result":   result,
                }
        except Exception as exc:
            _nse_status = {"running": False, "status": "error", "error": str(exc)}

    _nse_status = {"running": True, "status": "starting"}
    threading.Thread(target=_run, daemon=True, name="nse-backtest").start()
    return _cors(JsonResponse({"status": "started"}))


@require_GET
def api_nse_results(request):
    """GET /api/nse/results — last saved NSE results JSON"""
    try:
        from pathlib import Path
        result_file = Path(__file__).resolve().parents[1] / "algoTrading" / "data" / "nse_results.json"
        if not result_file.exists():
            return _cors(JsonResponse({"error": "No results yet — run backtest first"}))
        return _cors(JsonResponse(json.loads(result_file.read_text())))
    except Exception as exc:
        return _cors(JsonResponse({"error": str(exc)}, status=500))


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


# ── Live Bot control endpoints ─────────────────────────────────────────────────

@require_GET
def api_livebot_status(request):
    """GET /api/livebot — live bot state (running, scan_count, last_signal, etc.)"""
    from trading.live_bot_service import get_bot_state
    return _cors(JsonResponse(get_bot_state()))


@csrf_exempt
def api_livebot_start(request):
    """POST /api/livebot/start — start the live bot"""
    if request.method != "POST":
        return _cors(JsonResponse({"error": "POST only"}, status=405))
    from trading.live_bot_service import start_live_bot
    ok, msg = start_live_bot()
    return _cors(JsonResponse({"ok": ok, "status": msg}))


@csrf_exempt
def api_livebot_stop(request):
    """POST /api/livebot/stop — signal the live bot to stop cleanly"""
    if request.method != "POST":
        return _cors(JsonResponse({"error": "POST only"}, status=405))
    from trading.live_bot_service import stop_live_bot
    ok, msg = stop_live_bot()
    return _cors(JsonResponse({"ok": ok, "status": msg}))


@require_GET
def api_livebot_log(request):
    """GET /api/livebot/log?n=100 — last N in-memory log lines"""
    from trading.live_bot_service import get_bot_log
    n = min(int(request.GET.get("n", 100)), 300)
    return _cors(JsonResponse({"lines": get_bot_log(n)}))


# ── SuperTrend Watcher endpoints ───────────────────────────────────────────────

@require_GET
def api_st_status(request):
    """GET /api/supertrend/status — current watcher state and trend snapshot"""
    from trading.supertrend_watcher_service import get_watcher_state
    return _cors(JsonResponse(get_watcher_state()))


@csrf_exempt
def api_st_start(request):
    """POST /api/supertrend/start — launch the SuperTrend watcher"""
    if request.method != "POST":
        return _cors(JsonResponse({"error": "POST only"}, status=405))
    from trading.supertrend_watcher_service import start_watcher
    ok, msg = start_watcher()
    return _cors(JsonResponse({"ok": ok, "status": msg}))


@csrf_exempt
def api_st_stop(request):
    """POST /api/supertrend/stop — signal the watcher to stop"""
    if request.method != "POST":
        return _cors(JsonResponse({"error": "POST only"}, status=405))
    from trading.supertrend_watcher_service import stop_watcher
    ok, msg = stop_watcher()
    return _cors(JsonResponse({"ok": ok, "status": msg}))


@require_GET
def api_st_log(request):
    """GET /api/supertrend/log?n=100 — last N watcher log lines"""
    from trading.supertrend_watcher_service import get_watcher_log
    n = min(int(request.GET.get("n", 100)), 300)
    return _cors(JsonResponse({"lines": get_watcher_log(n)}))
