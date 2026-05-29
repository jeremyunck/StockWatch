"""Finnhub data source: real-time quotes and company news."""

import os
import time
import logging
from datetime import datetime, timezone
from typing import Optional

import finnhub
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

_client: Optional[finnhub.Client] = None


def _get_client() -> finnhub.Client:
    global _client
    if _client is None:
        api_key = os.environ.get("FINNHUB_API_KEY", "")
        if not api_key:
            raise RuntimeError("FINNHUB_API_KEY not set")
        _client = finnhub.Client(api_key=api_key)
    return _client


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def get_quote(ticker: str) -> dict:
    """Return current price data from Finnhub /quote endpoint.

    Returns dict with keys: price, prev_close, change, change_pct, high, low, open, timestamp.
    Raises on failure after retries.
    """
    client = _get_client()
    raw = client.quote(ticker)
    if not raw or raw.get("c") is None:
        raise ValueError(f"Empty quote response for {ticker}")

    current = raw["c"]
    prev = raw["pc"] or current
    change = current - prev
    change_pct = (change / prev * 100) if prev else 0.0

    return {
        "price": current,
        "prev_close": prev,
        "change": change,
        "change_pct": change_pct,
        "high": raw.get("h"),
        "low": raw.get("l"),
        "open": raw.get("o"),
        "timestamp": raw.get("t"),
    }


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def get_company_news(ticker: str, frm: str, to: str) -> list[dict]:
    """Fetch company news from Finnhub.

    frm / to: 'YYYY-MM-DD' strings.
    Returns list of {headline, url, datetime, source, summary}.
    """
    client = _get_client()
    raw = client.company_news(ticker, _from=frm, to=to)
    articles = []
    for item in (raw or []):
        headline = item.get("headline", "").strip()
        url = item.get("url", "").strip()
        if not headline or not url:
            continue
        articles.append({
            "headline": headline,
            "url": url,
            "datetime": item.get("datetime"),
            "source": item.get("source", ""),
            "summary": item.get("summary", ""),
        })
    return articles
