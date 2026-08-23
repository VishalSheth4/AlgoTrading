"""
Smart Money Concepts (SMC) abstraction.

Distinct from Indicator (indicators/base.py, one scalar value per candle)
and Strategy (strategies/base.py, a point-in-time signal marker): an
SMCConcept detects rectangular ZONES -- a price range spanning a time
range (Order Blocks, Breaker Blocks, Fair Value Gaps, liquidity pools,
...). That different shape is why this gets its own interface rather than
reusing Indicator/Strategy.

SOLID, same pattern as the other two pipelines in this project:
- SRP: a concept only detects its own zones; it doesn't know about MT5,
       Django, or how zones get drawn.
- OCP: the 100+ future SMC topics (FVG, liquidity sweeps, equal highs/
       lows, mitigation blocks, ...) are added by writing a new
       SMCConcept subclass and registering it with SMCEngine -- nothing
       here or in SMCEngine changes.
- LSP: any SMCConcept is interchangeable wherever the interface is used.
- ISP: one method, compute().
- DIP: SMCEngine (and CandleService) depend on this abstraction, never on
       a concrete concept.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class SMCZone:
    """A rectangular chart zone: a price range [bottom, top] spanning bar
    positions [start_index, end_index]. end_index=None means the zone is
    still active/unmitigated and should extend to the latest bar."""

    kind: str
    start_index: int
    end_index: int | None
    top: float
    bottom: float
    mitigated: bool

    def to_payload(self, time_unix: np.ndarray, last_index: int) -> dict:
        end_idx = self.end_index if self.end_index is not None else last_index
        return {
            "start_time_unix": int(time_unix[self.start_index]),
            "end_time_unix": int(time_unix[end_idx]),
            "top": float(self.top),
            "bottom": float(self.bottom),
            "mitigated": self.mitigated,
        }


class SMCConcept(ABC):
    name: str

    @abstractmethod
    def compute(self, df: pd.DataFrame) -> list[SMCZone]:
        """Return the zones this concept detects in df (open/high/low/close,
        sorted oldest -> newest, plain 0..n-1 integer positional index)."""
        ...
