import os
import sys
from django.apps import AppConfig


class TradingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "trading"

    def ready(self):
        # In Django's dev reloader the outer process also calls ready().
        # RUN_MAIN is set only in the actual server child process.
        # For WSGI/production RUN_MAIN is not set — start thread unconditionally.
        is_runserver = "runserver" in sys.argv
        if is_runserver and not os.environ.get("RUN_MAIN"):
            return  # skip in reloader wrapper process

        from trading.mt5_service import start_live_feed
        start_live_feed()
