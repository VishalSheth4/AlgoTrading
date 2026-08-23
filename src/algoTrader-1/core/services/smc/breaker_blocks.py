"""
Breaker Block (BB) detection.

Like Order Blocks, "Breaker Block" rules vary across sources -- this is
the specific definition used here:

A Breaker Block is what remains when an Order Block FAILS (gets
mitigated) and the market then reverses structure again in the
breaker's favor:

- Bullish Breaker: a Bearish Order Block that gets mitigated (price
  closes back above its top -- it failed to hold as resistance), and
  AFTER that mitigation bar price makes a fresh Bullish BOS (closes
  above a later swing high). The old bearish OB zone is relabeled as a
  bullish breaker, now expected to act as support.
- Bearish Breaker: the mirror case -- a Bullish Order Block that gets
  mitigated (closes below its bottom), followed by a fresh Bearish BOS.
  The old bullish OB zone is relabeled as a bearish breaker (resistance).

Depends on OrderBlockConcept via constructor injection (composition, not
duplicated detection logic) -- pass a differently-configured instance
(e.g. a different swing_length) if needed.

Sizing: a Breaker Block is drawn as a fixed-size rectangle -- the height
is the origin candle's own high-to-low range (same candle that defines
the underlying Order Block), and the width is exactly `box_width` candles
starting from that candle. This replaces open-ended mitigation-based
extension: a breaker is always a fixed box, not a growing/shrinking zone.
"""

from __future__ import annotations

import pandas as pd

from .base import SMCConcept, SMCZone
from .order_blocks import OrderBlockConcept
from .swing_points import find_swing_points


class BreakerBlockConcept(SMCConcept):
    name = "breaker_block"

    def __init__(self, order_block_concept: OrderBlockConcept | None = None, swing_length: int = 5, box_width: int = 10):
        self._order_blocks = order_block_concept or OrderBlockConcept(swing_length)
        self._swing_length = swing_length
        self._box_width = box_width

    def compute(self, df: pd.DataFrame) -> list[SMCZone]:
        df = df.reset_index(drop=True)
        n = len(df)
        if n < self._swing_length * 2 + 2:
            return []

        high = df["high"].to_numpy(dtype="float64")
        low = df["low"].to_numpy(dtype="float64")
        close = df["close"].to_numpy(dtype="float64")

        order_blocks = self._order_blocks.compute(df)
        swing_high, swing_low = find_swing_points(high, low, self._swing_length)

        breakers: list[SMCZone] = []

        for ob in order_blocks:
            if not ob.mitigated:
                continue  # only a failed OB can become a breaker

            mitigation_index = ob.end_index
            flip_index = None

            if ob.kind == "order_block_bearish":
                next_swing_high = None
                for j in range(mitigation_index + 1, n):
                    if swing_high[j]:
                        next_swing_high = high[j]
                    if next_swing_high is not None and close[j] > next_swing_high:
                        flip_index = j
                        break
                if flip_index is not None:
                    breakers.append(SMCZone(
                        kind="breaker_block_bullish",
                        start_index=ob.start_index,
                        end_index=min(ob.start_index + self._box_width, n - 1),
                        top=ob.top, bottom=ob.bottom, mitigated=True,
                    ))

            elif ob.kind == "order_block_bullish":
                next_swing_low = None
                for j in range(mitigation_index + 1, n):
                    if swing_low[j]:
                        next_swing_low = low[j]
                    if next_swing_low is not None and close[j] < next_swing_low:
                        flip_index = j
                        break
                if flip_index is not None:
                    breakers.append(SMCZone(
                        kind="breaker_block_bearish",
                        start_index=ob.start_index,
                        end_index=min(ob.start_index + self._box_width, n - 1),
                        top=ob.top, bottom=ob.bottom, mitigated=True,
                    ))

        return breakers
