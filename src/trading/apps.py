import os
import sys
from pathlib import Path
from django.apps import AppConfig


class TradingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "trading"

    def ready(self):
        # Django's dev-reloader forks two processes.
        # RUN_MAIN is set only in the actual server child — avoid starting
        # threads in the outer watcher process which would be killed on reload.
        is_runserver = "runserver" in sys.argv
        if is_runserver and not os.environ.get("RUN_MAIN"):
            return

        # ── 1. Tell Django's autoreloader to also watch config.yaml ──────────
        # When config.yaml is saved, Django restarts the server so fresh imports
        # (strategy params, lot sizes, etc.) are picked up immediately.
        try:
            from django.utils.autoreload import autoreload_started

            _YAML = Path(__file__).resolve().parents[1] / "algoTrading" / "config.yaml"
            _CFG  = Path(__file__).resolve().parents[1] / "algoTrading" / "config.py"

            def _watch_extra_files(sender, **kwargs):
                sender.extra_files.add(_YAML)
                sender.extra_files.add(_CFG)

            autoreload_started.connect(_watch_extra_files)
        except Exception:
            pass   # non-fatal — autoreload is dev-only

        # ── 2. Telegram startup notification ─────────────────────────────────
        try:
            import requests as _req
            from datetime import datetime as _dt
            _BOT   = "8442885474:AAGIxFfSWJNnykAu--el6cjLwCfC4XnLH1k"
            _r = _req.get(f"https://api.telegram.org/bot{_BOT}/getUpdates", timeout=5)
            _data = _r.json()
            if _data.get("ok") and _data["result"]:
                _chat_id = _data["result"][-1]["message"]["chat"]["id"]
                _req.post(
                    f"https://api.telegram.org/bot{_BOT}/sendMessage",
                    json={
                        "chat_id": _chat_id,
                        "parse_mode": "HTML",
                        "text": (
                            "🟢 <b>AlgoTrading Server Started</b>\n"
                            f"Time   : {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                            "Server : http://localhost:8000\n"
                            "Status : Running ✅"
                        ),
                    },
                    timeout=5,
                )
        except Exception as _e:
            print(f"[telegram] startup notify failed: {_e}")

        # ── 3. Start MT5 live feed thread ─────────────────────────────────────
        from trading.mt5_service import start_live_feed
        start_live_feed()

        # ── 3. Start file watcher → auto-reruns backtest on config change ─────
        from trading.watcher import start as start_watcher
        start_watcher()

        # ── 4. Start alert watcher → sends phone notifications on trades ──────
        try:
            from pathlib import Path
            _algo = Path(__file__).resolve().parents[1] / "algoTrading"
            if str(Path(__file__).resolve().parents[1]) not in sys.path:
                sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from algoTrading.alerts.watcher import start as start_alerts
            start_alerts()
        except Exception as exc:
            print(f"[alerts] Failed to start alert watcher: {exc}")

        # ── 5. Auto-start live bot if config.yaml has auto_start_live_bot: true ──
        try:
            import yaml
            from pathlib import Path
            _yaml = Path(__file__).resolve().parents[1] / "algoTrading" / "config.yaml"
            with open(_yaml) as _f:
                _cfg = yaml.safe_load(_f) or {}
            if _cfg.get("auto_start_live_bot", False):
                from trading.live_bot_service import start_live_bot
                ok, msg = start_live_bot()
                print(f"[live-bot] auto-start → {msg}")
            else:
                print("[live-bot] auto_start_live_bot=false — use POST /api/livebot/start to start")
        except Exception as exc:
            print(f"[live-bot] auto-start check failed: {exc}")

        # ── 6. Auto-start XAUUSD SuperTrend watcher ───────────────────────────
        try:
            from trading.supertrend_watcher_service import start_watcher as start_st
            ok, msg = start_st()
            print(f"[st-watcher] auto-start → {msg}")
        except Exception as exc:
            print(f"[st-watcher] auto-start failed: {exc}")
