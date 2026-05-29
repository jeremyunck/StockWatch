"""Reddit JSON scraper for StockWatch — no API credentials needed.

Scrapes Reddit by appending `.json` to URLs (e.g., /r/sub/search.json?q=ticker).
Uses requests library with browser-like headers to avoid 403 blocks.
Uses old.reddit.com which is sometimes less restrictive.
"""

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    import urllib.request
    import urllib.parse

logger = logging.getLogger(__name__)

# Subreddits to scrape for Reddit sentiment
DEFAULT_SUBREDDITS = ["Stocks_Picks", "TheRaceTo10Million", "smallstreetbets"]

# Browser-like headers to avoid 403 blocks
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://old.reddit.com/",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


def get_reddit_sentiment(ticker: str, subreddits: list[str] | None = None) -> dict:
    """Fetch Reddit sentiment for a ticker by scraping .json endpoints."""
    if subreddits is None:
        subreddits = DEFAULT_SUBREDDITS

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    all_posts = []
    mentions = 0

    for sub in subreddits:
        # Add small delay to avoid rate limiting
        time.sleep(1.0)
        
        # Build URL using old.reddit.com
        params = {
            "q": ticker,
            "sort": "new",
            "t": "day",
            "limit": "50",
            "restrict_sr": "1",
        }
        url = f"https://old.reddit.com/r/{sub}/search.json"
        
        try:
            if HAS_REQUESTS:
                resp = requests.get(url, params=params, headers=_HEADERS, timeout=15)
                resp.raise_for_status()
                body = resp.json()
            else:
                # Fallback to urllib
                full_url = f"{url}?{urllib.parse.urlencode(params)}"
                req = urllib.request.Request(full_url, headers=_HEADERS)
                with urllib.request.urlopen(req, timeout=15) as r:
                    body = json.loads(r.read().decode("utf-8"))
                
            children = body.get("data", {}).get("children", [])
            
            for child in children:
                post = child.get("data", {})
                created = datetime.fromtimestamp(post.get("created_utc", 0), tz=timezone.utc)
                if created < cutoff:
                    continue
                all_posts.append(post)
                mentions += 1
                    
        except Exception as e:
            logger.warning(f"Reddit JSON scrape failed for r/{sub}: {e}")

    # Compute simple sentiment from upvote ratios
    ratios = [p.get("upvote_ratio", 0) for p in all_posts if p.get("upvote_ratio")]
    avg_ratio = round(sum(ratios) / len(ratios), 3) if ratios else None

    # Get top post snippet
    top_posts = sorted(all_posts, key=lambda p: p.get("score", 0), reverse=True)[:3]
    
    return {
        "mentions": mentions,
        "avg_upvote_ratio": avg_ratio,
        "posts": top_posts,
        "top_snippet": top_posts[0].get("selftext", "")[:200] if top_posts else None,
    }
