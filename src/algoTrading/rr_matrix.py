"""
Per-(strategy, timeframe) risk:reward overrides.

Config.RR is a single global value shared by every strategy/timeframe. This
module layers a matrix on top of it: {strategy_key: {timeframe: rr}},
persisted to rr_matrix.json next to config.py, defaulting to 1:1 for any
combination not explicitly set. main_backtest.get_strategy() reads this to
pick the RR for whichever (strategy, Config.TIMEFRAME) pair it's about to
instantiate.

Not folded into config_overrides.json (config.py's own override store)
because that file is one flat value per field -- this is a 2D table, wide
enough (8 strategies x 7 timeframes = 56 cells) to deserve its own file
managed by algoTrader's Settings tab.
"""

from __future__ import annotations

import json
from pathlib import Path

_PATH = Path(__file__).resolve().parent / "rr_matrix.json"

DEFAULT_RR = 1.0


def load_rr_matrix() -> dict[str, dict[str, float]]:
    if not _PATH.exists():
        return {}
    try:
        return json.loads(_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_rr(strategy: str, timeframe: str) -> float:
    """RR for one (strategy, timeframe) pair -- DEFAULT_RR (1:1) if never
    explicitly set for that combination."""
    matrix = load_rr_matrix()
    try:
        return float(matrix.get(strategy, {}).get(timeframe, DEFAULT_RR))
    except (TypeError, ValueError):
        return DEFAULT_RR


def save_rr_matrix(matrix: dict[str, dict[str, float]]) -> None:
    import uuid

    from algoTrading.core.fs_utils import replace_with_retry

    tmp = f"{_PATH}.{uuid.uuid4().hex}.tmp"
    Path(tmp).write_text(json.dumps(matrix, indent=2), encoding="utf-8")
    replace_with_retry(tmp, str(_PATH))
