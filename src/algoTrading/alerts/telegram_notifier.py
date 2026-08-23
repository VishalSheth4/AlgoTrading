"""
Telegram alerting.

Design (SOLID):
- SRP:  TelegramNotifier only knows how to send a message to Telegram.
        AlertDispatcher only knows how to broadcast to a list of notifiers.
- OCP:  Add a new channel (email, Slack, ...) by writing another Notifier
        implementation -- AlertDispatcher and callers don't change.
- LSP:  Any Notifier can replace TelegramNotifier wherever Notifier is used.
- ISP:  Notifier exposes exactly one method (send).
- DIP:  AlertDispatcher depends on the Notifier abstraction, not on
        requests/Telegram specifics.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

import requests


class Notifier(ABC):
    @abstractmethod
    def send(self, message: str) -> bool:
        ...


class TelegramNotifier(Notifier):
    """Sends messages via the Telegram Bot API (no extra SDK needed)."""

    API_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, bot_token: str, chat_id: str, session: requests.Session | None = None, timeout: int = 10):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._session = session or requests.Session()
        self._timeout = timeout

    def send(self, message: str) -> bool:
        url = self.API_URL.format(token=self._bot_token)
        payload = {"chat_id": self._chat_id, "text": message, "parse_mode": "HTML"}

        try:
            response = self._session.post(url, data=payload, timeout=self._timeout)
            response.raise_for_status()
            print("✅ Telegram alert sent")
            return True
        except requests.RequestException as exc:
            print(f"❌ Telegram alert failed: {exc}")
            return False


class AlertDispatcher:
    """Broadcasts one message to every registered notifier."""

    def __init__(self, notifiers: list[Notifier]):
        self._notifiers = notifiers

    def broadcast(self, message: str) -> None:
        for notifier in self._notifiers:
            notifier.send(message)


def build_telegram_notifier_from_env() -> TelegramNotifier:
    """Reads TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID from the environment."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise EnvironmentError(
            "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables to use TelegramNotifier."
        )

    return TelegramNotifier(bot_token=token, chat_id=chat_id)


if __name__ == "__main__":
    notifier = build_telegram_notifier_from_env()
    dispatcher = AlertDispatcher([notifier])
    dispatcher.broadcast("🚀 Test alert from algoTrading bot")
