"""
Orchestrates a list of SMCConcept instances against a candle DataFrame.

Adding concept #3, #4, ... #100+ (Fair Value Gaps, liquidity sweeps,
equal highs/lows, mitigation blocks, ...) means writing a new SMCConcept
subclass and appending an instance to the list passed into SMCEngine --
this class never changes (Open/Closed Principle).
"""

from __future__ import annotations

import pandas as pd

from .base import SMCConcept, SMCZone


class SMCEngine:
    def __init__(self, concepts: list[SMCConcept]):
        self._concepts = concepts

    def run(self, df: pd.DataFrame) -> dict[str, list[SMCZone]]:
        zones_by_kind: dict[str, list[SMCZone]] = {}
        for concept in self._concepts:
            for zone in concept.compute(df):
                zones_by_kind.setdefault(zone.kind, []).append(zone)
        return zones_by_kind
