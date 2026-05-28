"""RSS news feed fetcher + digest formatter for StockWatch."""

import feedparser
import os
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field


# Default RSS feeds — major finance news sources
DEFAULT_FEEDS = [
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",           # WSJ Markets
    "https://finance.yahoo.com/news/rssindex",                    # Yahoo Finance
    "https://www.investing.com/rss/news_301.rss",               # Investing.com - Stocks
    "https://www.reddit.com/r/wallstreetbets/search/.rss?q=stocks",  # WSJ via Reddit (backup)
    "https://rss.cnbc.com/rss/markets.xml",                       # CNBC Markets
]

# How far back to look for "fresh" news (hours)
NEWS_WINDOW_HOURS = 24


@dataclass
class NewsItem:
    title: str
    link: str
    published: Optional[datetime] = None
    summary: Optional[str] = None
    source: Optional[str] = None
    tickers_mentioned: list[str] = field(default_factory=list)


def fetch_feed(url: str, window_hours: int = NEWS_WINDOW_HOURS) -> list[NewsItem]:
    """Fetch and parse a single RSS feed, returning recent items."""
    cutoff = datetime.utcnow() - timedelta(hours=window_hours)
    items: list[NewsItem] = []

    try:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            # Parse published date
            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime(*entry.published_parsed[:6])
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                published = datetime(*entry.updated_parsed[:6])

            # Skip old items
            if published and published < cutoff:
                continue

            # Extract summary / description
            summary = None
            if hasattr(entry, "summary"):
                summary = entry.summary
            elif hasattr(entry, "description"):
                summary = entry.description

            # Truncate summary
            if summary:
                summary = summary.strip()[:300]

            items.append(
                NewsItem(
                    title=entry.get("title", "No title"),
                    link=entry.get("link", ""),
                    published=published,
                    summary=summary,
                    source=feed.feed.get("title", url),
                )
            )
    except Exception as e:
        import logging
        logging.warning(f"Failed to fetch RSS feed {url}: {e}")

    return items


def fetch_all_feeds(
    feed_urls: list[str] | None = None,
    window_hours: int = NEWS_WINDOW_HOURS,
) -> list[NewsItem]:
    """Fetch multiple RSS feeds in parallel."""
    import concurrent.futures

    urls = feed_urls or DEFAULT_FEEDS
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(urls), 5)) as executor:
        results = list(executor.map(lambda u: fetch_feed(u, window_hours), urls))

    # Flatten
    items: list[NewsItem] = []
    for chunk in results:
        items.extend(chunk)

    # Sort by published date (newest first)
    items.sort(key=lambda x: x.published or datetime.min, reverse=True)
    return items


def filter_news_for_tickers(
    news: list[NewsItem],
    tickers: list[str],
) -> dict[str, list[NewsItem]]:
    """Filter news items that mention any of the given tickers."""
    tickers_upper = [t.upper() for t in tickers]
    result: dict[str, list[NewsItem]] = {t: [] for t in tickers_upper}

    for item in news:
        title_and_summary = f"{item.title} {item.summary or ''}".upper()
        for ticker in tickers_upper:
            if ticker in title_and_summary:
                result[ticker].append(item)

    # Remove empty entries
    return {k: v for k, v in result.items() if v}


def format_news_digest(
    news_by_ticker: dict[str, list[NewsItem]],
    max_per_ticker: int = 3,
) -> str:
    """Format a text digest of news per ticker."""
    lines = []
    lines.append("")
    lines.append("=" * 80)
    lines.append("📰  NEWS DIGEST (last 24h)")
    lines.append("=" * 80)

    for ticker, items in news_by_ticker.items():
        if not items:
            continue
        lines.append(f"\n--- {ticker} ---")
        for item in items[:max_per_ticker]:
            pub = (
                item.published.strftime("%H:%M UTC")
                if item.published
                else "?"
            )
            title = item.title.strip()
            if len(title) > 70:
                title = title[:67] + "..."
            lines.append(f"  [{pub}] {title}")
            lines.append(f"        {item.link}")

    lines.append("")
    return "\n".join(lines)
