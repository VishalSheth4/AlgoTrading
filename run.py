#!/usr/bin/env python
"""
AlgoTrading Platform — single-command launcher.

Usage:
    python run.py

Starts:
  Backend  → http://localhost:8000   (Django + Channels WebSocket)
  Frontend → http://localhost:5173   (React + Vite dev server)

First-run setup is automatic:
  - pip-installs channels, daphne, djangorestframework, django-cors-headers
  - npm-installs React dependencies if node_modules is absent
"""
import os
import sys
import subprocess
import signal
import time
from pathlib import Path

ROOT     = Path(__file__).resolve().parent
BACKEND  = ROOT / "src"
FRONTEND = ROOT / "frontend"

# ── Colour helpers ─────────────────────────────────────────────────────────────
def _c(code, text): return f"\033[{code}m{text}\033[0m"
INFO  = lambda t: print(_c("36", f"[run] {t}"))
OK    = lambda t: print(_c("32", f"[ok]  {t}"))
WARN  = lambda t: print(_c("33", f"[warn] {t}"))
ERR   = lambda t: print(_c("31", f"[err] {t}"))

# ── Dependency bootstrap ────────────────────────────────────────────────────────
def ensure_python_deps():
    REQUIRED = [
        "channels",
        "daphne",
        "djangorestframework",
        "django-cors-headers",
    ]
    missing = []
    for pkg in REQUIRED:
        import importlib
        mod = pkg.replace("-", "_").split("[")[0]
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        INFO(f"Installing Python deps: {', '.join(missing)}")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet"] + missing
        )
        OK("Python deps installed")


def ensure_node_deps():
    nm = FRONTEND / "node_modules"
    pkg = FRONTEND / "package.json"
    if not pkg.exists():
        ERR(f"frontend/package.json not found at {FRONTEND}")
        sys.exit(1)
    if not nm.exists():
        INFO("Running npm install in frontend/ …")
        npm = "npm.cmd" if sys.platform == "win32" else "npm"
        subprocess.check_call([npm, "install"], cwd=FRONTEND)
        OK("Node deps installed")


# ── Process launchers ──────────────────────────────────────────────────────────
def start_backend() -> subprocess.Popen:
    INFO("Starting Django backend on http://localhost:8000 …")
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    return subprocess.Popen(
        [sys.executable, "manage.py", "runserver", "0.0.0.0:8000"],
        cwd=BACKEND,
        env=env,
    )


def start_frontend() -> subprocess.Popen:
    INFO("Starting React frontend on http://localhost:5173 …")
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    return subprocess.Popen(
        [npm, "run", "dev"],
        cwd=FRONTEND,
    )


# ── Main ────────────────────────────────────────────────────────────────────────
def main():
    print()
    print(_c("1;36", "  AlgoTrading Pro Platform"))
    print(_c("90",   "  ─────────────────────────"))
    print()

    ensure_python_deps()
    ensure_node_deps()

    print()
    OK("Backend  → http://localhost:8000")
    OK("Frontend → http://localhost:5173")
    print(_c("90", "  Press Ctrl+C to stop both servers."))
    print()

    backend  = start_backend()
    time.sleep(1.5)        # give Django a moment to bind before Vite opens browser
    frontend = start_frontend()

    procs = [backend, frontend]

    def _shutdown(sig=None, frame=None):
        print()
        INFO("Shutting down…")
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Wait — if either process dies unexpectedly, kill the other and exit
    while True:
        for p in procs:
            ret = p.poll()
            if ret is not None:
                WARN(f"A server process exited (code {ret}). Stopping all.")
                _shutdown()
        time.sleep(1)


if __name__ == "__main__":
    main()
