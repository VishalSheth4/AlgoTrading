"""
Free, open forex news headlines -- no API key required.

Fetches from a public RSS feed (ForexLive/Investinglive's breaking-news
feed) using only the standard library, and caches the result in memory
for a few minutes so the ticker's periodic polling doesn't hammer the
source on every request.

Filtered to XAUUSD/Gold/USD-relevant headlines only, and each is tagged
with an impact level (high/medium/low). The feed doesn't carry an
official impact rating, so this is a keyword heuristic -- documented
explicitly here since it's a judgment call, same as other business logic
in this project (see strategies/green_dollar.py, smc/order_blocks.py).
"""

from __future__ import annotations

import threading
import time
import urllib.request
import xml.etree.ElementTree as ET

NEWS_FEED_URL = "https://www.forexlive.com/feed/news"
CACHE_SECONDS = 300

# Unambiguous on their own -- these words only ever show up in a
# gold/USD-relevant headline.
STRONG_KEYWORDS = (
    "gold", "xau", "usd", "dollar", "fed", "fomc", "powell", "treasury",
    "dxy", "safe haven", "risk-off", "risk-on",
)

# Generic macro-data terms (CPI, GDP, unemployment, ...) are published for
# every country, not just the US -- a German CPI print isn't XAUUSD-
# relevant the way a US one is. These only count as relevant when they
# co-occur with a marker that the headline is actually about the US.
MACRO_KEYWORDS = (
    "cpi", "ppi", "pce", "nfp", "non-farm", "nonfarm", "payroll", "gdp",
    "ism", "unemployment", "jobless", "retail sales", "interest rate",
    "rate decision", "rate cut", "rate hike", "tariff", "recession",
    "inflation", "yield", "yields",
)
US_CONTEXT_MARKERS = (
    "us ", "u.s.", "america", "dollar", "usd", "fed", "washington",
    "white house", "trump", "treasury", "powell",
)

# Highest-impact market movers first; a headline matches "high" if any of
# these appear, else "medium" if any of the medium set appear, else "low".
HIGH_IMPACT_KEYWORDS = (
    "fomc", "nfp", "non-farm", "nonfarm", "payroll", "cpi", "interest rate",
    "rate decision", "rate cut", "rate hike", "powell", "gdp",
    "unemployment rate", "jobless claims", "recession", "war",
    "intervention", "crisis",
)
MEDIUM_IMPACT_KEYWORDS = (
    "fed", "ppi", "pce", "ism", "retail sales", "treasury", "yield",
    "yields", "tariff", "inflation",
)


def _classify_impact(title_lower: str) -> str:
    if any(kw in title_lower for kw in HIGH_IMPACT_KEYWORDS):
        return "high"
    if any(kw in title_lower for kw in MEDIUM_IMPACT_KEYWORDS):
        return "medium"
    return "low"


def _is_relevant(title_lower: str) -> bool:
    if any(kw in title_lower for kw in STRONG_KEYWORDS):
        return True
    return (
        any(kw in title_lower for kw in MACRO_KEYWORDS)
        and any(marker in title_lower for marker in US_CONTEXT_MARKERS)
    )


class NewsService:
    def __init__(
        self,
        feed_url: str = NEWS_FEED_URL,
        cache_seconds: int = CACHE_SECONDS,
        max_items: int = 20,
        raw_fetch_limit: int = 80,
    ):
        self._feed_url = feed_url
        self._cache_seconds = cache_seconds
        self._max_items = max_items
        self._raw_fetch_limit = raw_fetch_limit
        self._cache: list[dict] | None = None
        self._cached_at: float = 0.0
        self._lock = threading.Lock()

    def get_headlines(self) -> list[dict]:
        with self._lock:
            now = time.time()
            if self._cache is not None and (now - self._cached_at) < self._cache_seconds:
                return self._cache

            try:
                headlines = self._fetch()
            except Exception:
                # Feed hiccup: keep serving the last good cache rather than
                # blanking the ticker out.
                return self._cache or []

            self._cache = headlines
            self._cached_at = now
            return headlines

    def _fetch(self) -> list[dict]:
        req = urllib.request.Request(self._feed_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()

        root = ET.fromstring(raw)
        items = root.findall("./channel/item")[: self._raw_fetch_limit]

        headlines = []
        for item in items:
            title = (item.findtext("title") or "").strip()
            if not title:
                continue

            title_lower = title.lower()
            if not _is_relevant(title_lower):
                continue

            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            headlines.append({
                "title": title,
                "link": link,
                "pub_date": pub_date,
                "impact": _classify_impact(title_lower),
            })
            if len(headlines) >= self._max_items:
                break

        return headlines


_service: NewsService | None = None
_service_lock = threading.Lock()


def get_news_service() -> NewsService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = NewsService()
    return _service
