"""RSS news feed fetcher + digest formatter for StockWatch."""

import feedparser
import json
import os
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field
from pathlib import Path

# State file for tracking seen news article links
STATE_DIR = Path.home() / ".local" / "share" / "stockwatch"
STATE_FILE = STATE_DIR / "news-seen-articles.json"

def _load_seen_links() -> set:
    """Load previously seen article links from state file."""
    if not STATE_FILE.exists():
        return set()
    try:
        return set(json.loads(STATE_FILE.read_text()))
    except Exception:
        return set()


def _save_seen_links(seen: set) -> None:
    """Save seen article links to state file."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # Keep only last 10000 links to avoid unbounded growth
    links = list(seen)[-10000:]
    STATE_FILE.write_text(json.dumps(links))


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


def fetch_feed(url: str, window_hours: int = NEWS_WINDOW_HOURS, seen_links: set | None = None) -> tuple:
    """Fetch and parse a single RSS feed, returning recent items and new links."""
    cutoff = datetime.utcnow() - timedelta(hours=window_hours)
    items: list[NewsItem] = []
    new_links: list[str] = []

    try:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            link = entry.get("link", "")

            # Skip already-seen articles
            if seen_links is not None and link and link in seen_links:
                continue

            # Parse published date
            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime(*entry.published_parsed[:6])
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                published = datetime(*entry.updated_parsed[:6])

            # Skip old items
            if published and published < cutoff:
                continue

            # Mark as seen
            if link and seen_links is not None:
                seen_links.add(link)
                new_links.append(link)

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
                    link=link,
                    published=published,
                    summary=summary,
                    source=feed.feed.get("title", url),
                )
            )
    except Exception as e:
        import logging
        logging.warning(f"Failed to fetch RSS feed {url}: {e}")

    return items, new_links


def fetch_all_feeds(
    feed_urls: list[str] | None = None,
    window_hours: int = NEWS_WINDOW_HOURS,
    clear_state: bool = False,
) -> tuple[list[NewsItem], int]:
    """Fetch multiple RSS feeds in parallel, skipping seen articles."""
    import concurrent.futures

    urls = feed_urls or DEFAULT_FEEDS
    seen_links = set() if clear_state else _load_seen_links()
    all_new_links: list[str] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(urls), 5)) as executor:
        futures = {
            executor.submit(fetch_feed, u, window_hours, seen_links): u
            for u in urls
        }
        results = []
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            items = result[0]
            new_links = result[1]
            results.extend(items)
            all_new_links.extend(new_links)

    # Flatten + deduplicate by link
    seen_this_run = set()
    unique_items: list[NewsItem] = []
    for item in results:
        if item.link not in seen_this_run:
            seen_this_run.add(item.link)
            unique_items.append(item)

    # Sort by published date (newest first)
    unique_items.sort(key=lambda x: x.published or datetime.min, reverse=True)

    # Persist seen links
    if all_new_links:
        _save_seen_links(seen_links)

    return unique_items, len(all_new_links)


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
