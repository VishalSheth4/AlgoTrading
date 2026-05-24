#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# start.sh  —  Start AlgoTrading dashboard on Linux
#
# Usage:
#   chmod +x start.sh
#   ./start.sh
# ─────────────────────────────────────────────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"   # → AlgoTrading/src/

echo "=== AlgoTrading Dashboard ==="
echo "SRC : $SRC_DIR"

# Activate venv if present
if [ -f "$SRC_DIR/venv/bin/activate" ]; then
    source "$SRC_DIR/venv/bin/activate"
    echo "VENV: $VIRTUAL_ENV"
fi

cd "$SRC_DIR"

# Copy .env.example → .env on first run
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    cp .env.example .env
    echo "Created .env from .env.example — edit it before going public!"
fi

exec python run_server.py
