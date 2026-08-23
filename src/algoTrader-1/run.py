"""
One-shot setup + launch for the algoTrader Django project.

Usage:
    python run.py
"""

import subprocess
import sys


def run(cmd: list[str]) -> None:
    print(f"$ {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def main() -> None:
    run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
    run([sys.executable, "manage.py", "migrate"])
    run([sys.executable, "manage.py", "runserver"])


if __name__ == "__main__":
    main()
