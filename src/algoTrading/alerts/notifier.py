"""
notifier.py — Multi-channel alert sender.

Supported channels (all FREE):
  1. Telegram   — best for iPhone: instant push, rich formatting, free forever
  2. Email      — Gmail SMTP, delivered to iPhone Mail app
  3. ntfy.sh    — free open-source push, no account needed, iPhone app available

Setup:
  Fill in config.yaml under the `alerts:` block.

  Telegram (recommended):
    1. Open Telegram → search @BotFather → /newbot → copy the token
    2. Start a chat with your new bot
    3. Visit https://api.telegram.org/bot<TOKEN>/getUpdates
       send any message to your bot first, then reload that URL
    4. Copy the "id" value from "chat" → that is your chat_id
    5. Paste token + chat_id into config.yaml alerts.telegram block

  Email:
    Use a Gmail App Password (not your regular password):
    Google Account → Security → 2-Step Verification → App Passwords → Mail

  ntfy.sh:
    1. Install "ntfy" app on iPhone from App Store (free)
    2. In the app, subscribe to your topic name (make it unique!)
    3. Set alerts.ntfy.topic to the same name
    No account required.
"""

import json
import smtplib
import urllib.request
import urllib.parse
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

_CFG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"


def _load_alert_cfg() -> dict:
    try:
        import yaml
        with open(_CFG_PATH) as f:
            data = yaml.safe_load(f) or {}
        return data.get("alerts", {})
    except Exception:
        return {}


# ── Telegram ───────────────────────────────────────────────────────────────────

def send_telegram(message: str, cfg: dict | None = None) -> tuple[bool, str]:
    """
    Send a message via Telegram Bot API.
    Returns (success, error_message).
    """
    c = cfg or _load_alert_cfg().get("telegram", {})
    if not c.get("enabled"):
        return False, "Telegram disabled"

    token   = str(c.get("bot_token", "")).strip()
    chat_id = str(c.get("chat_id",   "")).strip()

    if not token or not chat_id:
        return False, "Telegram bot_token or chat_id not set in config.yaml"

    try:
        url     = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({
            "chat_id":    chat_id,
            "text":       message,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
        if resp.get("ok"):
            return True, "ok"
        return False, str(resp.get("description", "Unknown error"))
    except Exception as exc:
        return False, str(exc)


# ── ntfy.sh ───────────────────────────────────────────────────────────────────

def send_ntfy(title: str, message: str, cfg: dict | None = None) -> tuple[bool, str]:
    """
    Send push via ntfy.sh — no account needed.
    Install the "ntfy" iPhone app and subscribe to your topic.
    """
    c = cfg or _load_alert_cfg().get("ntfy", {})
    if not c.get("enabled"):
        return False, "ntfy disabled"

    topic = str(c.get("topic", "")).strip()
    if not topic:
        return False, "ntfy topic not set in config.yaml"

    server = str(c.get("server", "https://ntfy.sh")).rstrip("/")
    priority = str(c.get("priority", "high"))

    try:
        url     = f"{server}/{topic}"
        payload = message.encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Title":    title,
                "Priority": priority,
                "Tags":     "chart_with_upwards_trend",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


# ── Email ─────────────────────────────────────────────────────────────────────

def send_email(subject: str, body: str, cfg: dict | None = None) -> tuple[bool, str]:
    """
    Send alert via Gmail SMTP.
    Requires a Gmail App Password (not your main password).
    """
    c = cfg or _load_alert_cfg().get("email", {})
    if not c.get("enabled"):
        return False, "Email disabled"

    host     = str(c.get("smtp_host", "smtp.gmail.com"))
    port     = int(c.get("smtp_port", 587))
    username = str(c.get("username",  "")).strip()
    password = str(c.get("password",  "")).strip()
    to_addr  = str(c.get("to",        "")).strip() or username

    if not username or not password:
        return False, "Email username or password not set in config.yaml"

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = username
        msg["To"]      = to_addr
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(host, port, timeout=15) as s:
            s.starttls()
            s.login(username, password)
            s.sendmail(username, to_addr, msg.as_string())
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


# ── Broadcast to all enabled channels ─────────────────────────────────────────

def broadcast(title: str, body: str, cfg: dict | None = None) -> dict:
    """
    Send alert to ALL enabled channels.
    Returns {channel: (success, error)} dict.
    """
    c = cfg or _load_alert_cfg()
    if not c.get("enabled", True):
        return {}

    full_message = f"{title}\n{body}"
    results = {}

    # Telegram
    ok, err = send_telegram(full_message, c.get("telegram", {}))
    results["telegram"] = {"ok": ok, "error": err if not ok else None}

    # ntfy
    ok, err = send_ntfy(title, body, c.get("ntfy", {}))
    results["ntfy"] = {"ok": ok, "error": err if not ok else None}

    # Email
    ok, err = send_email(title, f"{title}\n\n{body}", c.get("email", {}))
    results["email"] = {"ok": ok, "error": err if not ok else None}

    sent = [ch for ch, r in results.items() if r["ok"]]
    failed = [f"{ch}: {r['error']}" for ch, r in results.items() if not r["ok"] and r["error"] != f"{ch} disabled"]

    if sent:
        print(f"[alert] Sent via {', '.join(sent)}: {title}")
    if failed:
        print(f"[alert] Failed: {'; '.join(failed)}")

    return results
