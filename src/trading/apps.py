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

        # ── 2. Start MT5 live feed thread ─────────────────────────────────────
        from trading.mt5_service import start_live_feed
        start_live_feed()

        # ── 3. Start file watcher → auto-reruns backtest on config change ─────
        from trading.watcher import start as start_watcher
        start_watcher()
