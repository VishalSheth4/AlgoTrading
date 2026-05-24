#!/usr/bin/env python
"""
run_server.py  —  Production server startup (works on Windows AND Linux).

Reads config from .env file in the same directory (if present), then falls
back to environment variables.

Usage:
    cd src
    python run_server.py           # port 8765
    PORT=9000 python run_server.py # custom port

Servers used:
    Windows  →  waitress  (pure-Python, no compiler needed)
    Linux    →  gunicorn  (multi-worker, production-grade)
"""

import os
import sys
from pathlib import Path

# ── Load .env if present ───────────────────────────────────────────────────────
_ENV = Path(__file__).parent / ".env"
if _ENV.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV)
        print(f"[server] Loaded env from {_ENV}")
    except ImportError:
        pass

# ── Settings ───────────────────────────────────────────────────────────────────
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.production_settings")

PORT    = int(os.environ.get("PORT",    "8765"))
HOST    = os.environ.get("HOST",         "0.0.0.0")
WORKERS = int(os.environ.get("WORKERS", "2"))

print(f"[server] Settings : {os.environ['DJANGO_SETTINGS_MODULE']}")
print(f"[server] Binding  : {HOST}:{PORT}")
print(f"[server] Platform : {sys.platform}")

# ── Run migrations (safe — no-ops if already up to date) ─────────────────────
import django
django.setup()
from django.core.management import call_command
call_command("migrate", "--run-syncdb", verbosity=0)

# ── Start WSGI server ──────────────────────────────────────────────────────────
if sys.platform == "win32":
    # waitress — works natively on Windows, no fork() required
    from django.core.wsgi import get_wsgi_application
    app = get_wsgi_application()
    from waitress import serve
    print(f"[server] Starting waitress (threads=4) …")
    serve(app, host=HOST, port=PORT, threads=4, channel_timeout=120)

else:
    # gunicorn — Linux / macOS
    import shutil
    if not shutil.which("gunicorn"):
        sys.exit("gunicorn not found — run: pip install gunicorn")

    args = [
        "gunicorn",
        "config.wsgi:application",
        "--bind",        f"{HOST}:{PORT}",
        "--workers",     str(WORKERS),
        "--timeout",     "120",
        "--access-logfile", "-",
        "--error-logfile",  "-",
        "--log-level",  "info",
    ]
    print(f"[server] exec: {' '.join(args)}")
    os.execvp("gunicorn", args)
