from django.apps import AppConfig
import threading
import subprocess


class TradingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'trading'

    def ready(self):
        def run_bot():
            subprocess.Popen(
                ["python", "-m", "algoTrading.main_backtest"]
            )

        threading.Thread(target=run_bot).start()