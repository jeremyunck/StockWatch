"""Yahoo Finance per-ticker RSS feed via feedparser."""

import hashlib
import logging
import time
from datetime import datetime, timezone

import feedparser

logger = logging.getLogger(__name__)

_YF_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"


def _article_key(url: str, headline: str) -> str:
    return hashlib.sha256(f"{url}|{headline}".encode()).hexdigest()[:16]


def get_news(ticker: str, max_items: int = 10) -> list[dict]:
    """Fetch recent news for ticker from Yahoo Finance RSS.

    Returns list of {headline, url, datetime, source, summary, article_key}.
    Never raises — returns empty list on any failure.
    """
    url = _YF_RSS.format(ticker=ticker)
    try:
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:max_items]:
            headline = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()
            if not headline or not link:
                continue
            pub = entry.get("published_parsed")
            pub_ts = None
            if pub:
                pub_ts = int(datetime(*pub[:6], tzinfo=timezone.utc).timestamp())
            summary = (entry.get("summary") or "").strip()
            source = feed.feed.get("title", "Yahoo Finance")
            key = entry.get("id") or _article_key(link, headline)
            articles.append({
                "headline": headline,
                "url": link,
                "datetime": pub_ts,
                "source": source,
                "summary": summary,
                "article_key": key,
            })
        return articles
    except Exception as exc:
        logger.warning("RSS fetch failed for %s: %s", ticker, exc)
        return []
