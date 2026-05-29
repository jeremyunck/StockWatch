"""Reddit subreddit scraper using the public JSON endpoint (no API key required)."""

import hashlib
import logging
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.reddit.com/r/{subreddit}/{sort}.json"
_HEADERS = {"User-Agent": "StockWatch/1.0 (research bot)"}
_VALID_SORTS = frozenset({"hot", "new", "top", "rising"})


def _post_key(post_id: str, title: str) -> str:
    return hashlib.sha256(f"{post_id}|{title}".encode()).hexdigest()[:16]


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=15),
    retry=retry_if_exception_type(requests.RequestException),
    reraise=True,
)
def _fetch_json(url: str, params: dict) -> dict:
    resp = requests.get(url, headers=_HEADERS, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_posts(
    subreddit: str,
    sort: str = "hot",
    limit: int = 25,
    keyword_filter: Optional[list[str]] = None,
) -> list[dict]:
    """Fetch posts from a subreddit via the public JSON endpoint.

    Args:
        subreddit: Subreddit name without the r/ prefix.
        sort: One of 'hot', 'new', 'top', 'rising'.
        limit: Max posts to return (Reddit caps at 100).
        keyword_filter: If provided, only return posts whose title or selftext
                        contains at least one keyword (case-insensitive).

    Returns list of dicts with keys: headline, url, datetime, source, summary, article_key.
    Never raises — returns empty list on any failure.
    """
    if sort not in _VALID_SORTS:
        sort = "hot"
    url = _BASE_URL.format(subreddit=subreddit, sort=sort)

    try:
        data = _fetch_json(url, {"limit": min(limit, 100)})
        children = data.get("data", {}).get("children", [])
        posts = []
        for child in children:
            post = child.get("data", {})
            title = (post.get("title") or "").strip()
            post_id = post.get("id", "")
            if not title or not post_id:
                continue

            selftext = (post.get("selftext") or "").strip()
            permalink = post.get("permalink", "")
            post_url = f"https://www.reddit.com{permalink}" if permalink else ""

            if keyword_filter:
                combined = f"{title} {selftext}".lower()
                if not any(kw.lower() in combined for kw in keyword_filter):
                    continue

            created_utc = post.get("created_utc")
            pub_ts = int(created_utc) if created_utc else None

            posts.append({
                "headline": title,
                "url": post_url,
                "datetime": pub_ts,
                "source": f"r/{subreddit}",
                "summary": selftext[:500] if selftext else "",
                "article_key": _post_key(post_id, title),
            })
        return posts
    except Exception as exc:
        logger.warning("Reddit fetch failed for r/%s: %s", subreddit, exc)
        return []
