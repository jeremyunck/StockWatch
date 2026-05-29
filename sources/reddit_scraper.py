"""Reddit scraper for StockWatch."""

import os
import logging
import urllib.request
import urllib.parse
import json
import base64
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Subreddits to scrape for Reddit sentiment
DEFAULT_SUBREDDITS = ["Stocks_Picks", "TheRaceTo10Million", "smallstreetbets"]


def get_reddit_bearer_token() -> str:
    """Obtain Reddit API OAuth2 bearer token (server-side app)."""
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("REDDIT_CLIENT_ID and READDIT_CLIENT_SECRET must be set")

    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    req = urllib.request.Request(
        "https://www.reddit.com/api/v1/access_token",
        data=data,
        headers={
            "Authorization": f"Basic {creds}",
            "User-Agent": "stockwatch:v0.2.0",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        return body["access_token"]


def get_reddit_sentiment(ticker: str, subreddits: list[str] | None = None) -> dict:
    """Fetch Reddit sentiment for a ticker across specified subreddits."""
    if subreddits is None:
        subreddits = DEFAULT_SUBREDDITS

    token = get_reddit_bearer_token()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    all_posts = []
    mentions = 0

    for sub in subreddits:
        url = f"https://oauth.reddit.com/r/{sub}/search?{urllib.parse.urlencode({'q': ticker, 'sort': 'new', 't': 'day', 'limit': '50', 'type': 'link'})}"
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {token}", "User-Agent": "stockwatch:v0.2.0"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                for child in body.get("data", {}).get("children", []):
                    post = child.get("data", {})
                    created = datetime.fromtimestamp(post.get("created_utc", 0), tz=timezone.utc)
                    if created < cutoff:
                        continue
                    all_posts.append(post)
                    mentions += 1
        except Exception as e:
            logger.warning(f"Reddit search failed for r/{sub}: {e}")

    # Compute simple sentiment
    ratios = [p.get("upvote_ratio", 0) for p in all_posts if p.get("upvote_ratio")]
    avg_ratio = round(sum(ratios) / len(ratios), 3) if ratios else None

    return {
        "mentions": mentions,
        "avg_upvote_ratio": avg_ratio,
        "posts": all_posts[:5],
    }
