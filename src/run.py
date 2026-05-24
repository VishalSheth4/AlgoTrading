#!/usr/bin/env python
"""
run.py  —  Start the AlgoTrading dashboard server.

Usage (from src/ directory):
    python run.py              # port 8765 (default)
    python run.py 9000         # custom port

Opens:  http://localhost:8765
"""

import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

if __name__ == "__main__":
    port = sys.argv[1] if len(sys.argv) > 1 else "8765"
    from django.core.management import execute_from_command_line
    execute_from_command_line(["manage.py", "runserver", f"0.0.0.0:{port}"])
