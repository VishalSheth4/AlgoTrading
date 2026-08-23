"""
Order Block (OB) detection.

"Order Block" rules vary across sources -- this is the specific,
deterministic definition this implementation uses:

1. Swing highs/lows are detected via `find_swing_points` (a fractal
   method) and tracked as the "structure" price must break to confirm
   a new order block.
2. A Bullish Break of Structure (BOS) happens the first time price
   CLOSES above the most recently confirmed swing high.
3. A Bearish BOS happens the first time price CLOSES below the most
   recently confirmed swing low.
4. Bullish Order Block: on a bullish BOS at bar i, walk backward from
   bar i-1 (within a bounded lookback) and take the LAST bearish
   (down-close) candle before the impulse move -- that candle's
   high/low become the zone's top/bottom.
5. Bearish Order Block: on a bearish BOS at bar i, walk backward and
   take the last bullish (up-close) candle before the impulse -- its
   high/low become the zone's top/bottom.
6. Mitigation (one clear rule, chosen for determinism over the several
   competing conventions in circulation): a Bullish OB is mitigated the
   first time a later candle CLOSES back below the zone's bottom. A
   Bearish OB is mitigated the first time a later candle CLOSES back
   above the zone's top. Once mitigated, the zone stops extending.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import SMCConcept, SMCZone
from .swing_points import find_swing_points


class OrderBlockConcept(SMCConcept):
    name = "order_block"

    def __init__(self, swing_length: int = 5, max_lookback: int = 15):
        self._swing_length = swing_length
        self._max_lookback = max_lookback

    def compute(self, df: pd.DataFrame) -> list[SMCZone]:
        df = df.reset_index(drop=True)
        n = len(df)
        if n < self._swing_length * 2 + 2:
            return []

        open_ = df["open"].to_numpy(dtype="float64")
        high = df["high"].to_numpy(dtype="float64")
        low = df["low"].to_numpy(dtype="float64")
        close = df["close"].to_numpy(dtype="float64")

        swing_high, swing_low = find_swing_points(high, low, self._swing_length)

        zones: list[SMCZone] = []
        last_swing_high = None
        last_swing_high_broken = False
        last_swing_low = None
        last_swing_low_broken = False

        for i in range(n):
            if swing_high[i]:
                last_swing_high = high[i]
                last_swing_high_broken = False
            if swing_low[i]:
                last_swing_low = low[i]
                last_swing_low_broken = False

            if last_swing_high is not None and not last_swing_high_broken and close[i] > last_swing_high:
                last_swing_high_broken = True
                ob_index = self._find_last_opposite_candle(open_, close, i, want_bearish=True)
                if ob_index is not None:
                    zones.append(SMCZone(
                        kind="order_block_bullish", start_index=ob_index, end_index=None,
                        top=high[ob_index], bottom=low[ob_index], mitigated=False,
                    ))

            if last_swing_low is not None and not last_swing_low_broken and close[i] < last_swing_low:
                last_swing_low_broken = True
                ob_index = self._find_last_opposite_candle(open_, close, i, want_bearish=False)
                if ob_index is not None:
                    zones.append(SMCZone(
                        kind="order_block_bearish", start_index=ob_index, end_index=None,
                        top=high[ob_index], bottom=low[ob_index], mitigated=False,
                    ))

        self._apply_mitigation(zones, close, n)
        return zones

    def _find_last_opposite_candle(self, open_: np.ndarray, close: np.ndarray, before_index: int, want_bearish: bool) -> int | None:
        floor = max(0, before_index - self._max_lookback)
        for j in range(before_index - 1, floor - 1, -1):
            is_bearish = close[j] < open_[j]
            if is_bearish == want_bearish:
                return j
        return None

    @staticmethod
    def _apply_mitigation(zones: list[SMCZone], close: np.ndarray, n: int) -> None:
        for zone in zones:
            for j in range(zone.start_index + 1, n):
                if zone.kind == "order_block_bullish" and close[j] < zone.bottom:
                    zone.end_index = j
                    zone.mitigated = True
                    break
                if zone.kind == "order_block_bearish" and close[j] > zone.top:
                    zone.end_index = j
                    zone.mitigated = True
                    break
