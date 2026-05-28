"""Reddit sentiment analysis via Reddit API v2 (server-side OAuth2) + LLM summarization."""

import os
import json
import urllib.request
import urllib.parse
import urllib.error
import base64
import time
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field
from pathlib import Path

# State file for tracking seen Reddit post IDs
STATE_DIR = Path.home() / ".local" / "share" / "stockwatch"
STATE_FILE = STATE_DIR / "reddit-seen-posts.json"

# Subreddits to search
DEFAULT_SUBREDDITS = ["wallstreetbets", "stocks", "investing", "StockMarket"]


def _load_seen_ids() -> set:
    """Load previously seen Reddit post IDs from state file."""
    if not STATE_FILE.exists():
        return set()
    try:
        return set(json.loads(STATE_FILE.read_text()))
    except Exception:
        return set()


def _save_seen_ids(seen: set) -> None:
    """Save seen Reddit post IDs to state file."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # Keep only last 5000 IDs to avoid unbounded growth
    ids = list(seen)[-5000:]
    STATE_FILE.write_text(json.dumps(ids))


@dataclass
class SentimentResult:
    ticker: str
    reddit_score: Optional[float] = None  # avg upvote ratio / sentiment proxy
    reddit_mentions: int = 0
    reddit_top_comment: Optional[str] = None
    reddit_sentiment_summary: Optional[str] = None
    reddit_posts_last_day: int = 0
    error: Optional[str] = None


def get_reddit_bearer_token() -> str:
    """Obtain a Reddit API OAuth2 bearer token (server-side app)."""
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    user_agent = os.environ.get("REDDIT_USER_AGENT", "stockwatch:v0.1.0")

    if not client_id or not client_secret:
        raise ValueError(
            "REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET must be set. "
            "Get them from https://www.reddit.com/prefs/apps"
        )

    # Reddit OAuth2: https://www.reddit.com/api/v1/access_token
    # Server-side: grant_type=client_credentials
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    req = urllib.request.Request(
        "https://www.reddit.com/api/v1/access_token",
        data=data,
        headers={
            "Authorization": f"Basic {creds}",
            "User-Agent": user_agent,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            token = body.get("access_token")
            if not token:
                raise RuntimeError(f"No access_token in response: {body}")
            return token
    except Exception as e:
        raise RuntimeError(f"Failed to get Reddit bearer token: {e}")


def search_reddit_posts(
    bearer_token: str,
    ticker: str,
    subreddits: list[str] | None = None,
    window_hours: int = 24,
    seen_ids: set[str] | None = None,
) -> tuple[list[dict], list[str]]:
    """
    Search Reddit via API v2 for a ticker in the given subreddits.
    Returns (posts, new_ids).
    """
    if subreddits is None:
        subreddits = DEFAULT_SUBREDDITS

    user_agent = os.environ.get("REDDIT_USER_AGENT", "stockwatch:v0.1.0")
    cutoff = datetime.utcnow() - timedelta(hours=window_hours)
    all_posts: list[dict] = []
    new_ids: list[str] = []

    for sub_name in subreddits:
        # Search via Reddit API v2: /r/{sub}/search
        # https://www.reddit.com/dev/api#GET_search
        params = {
            "q": ticker,
            "sort": "new",
            "t": "day",
            "limit": "50",
            "type": "link",
        }
        url = f"https://oauth.reddit.com/r/{sub_name}/search?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {bearer_token}",
                "User-Agent": user_agent,
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                children = body.get("data", {}).get("children", [])
                for child in children:
                    post = child.get("data", {})
                    post_id = post.get("id", "")
                    created = datetime.utcfromtimestamp(post.get("created_utc", 0))

                    # Skip old / already-seen
                    if created < cutoff:
                        continue
                    if seen_ids is not None and post_id in seen_ids:
                        continue

                    if seen_ids is not None:
                        seen_ids.add(post_id)
                        new_ids.append(post_id)

                    all_posts.append(post)
        except Exception as e:
            import logging
            logging.warning(f"Reddit search failed for r/{sub_name}: {e}")

    return all_posts, new_ids


def fetch_reddit_sentiment(
    ticker: str,
    subreddits: list[str] | None = None,
    window_hours: int = 24,
) -> SentimentResult:
    """Fetch Reddit sentiment for a ticker over the last `window_hours`."""
    result = SentimentResult(ticker=ticker)
    seen_ids = _load_seen_ids()
    new_ids: list[str] = []

    try:
        # Get bearer token
        token = get_reddit_bearer_token()

        # Search posts
        posts, new_ids = search_reddit_posts(
            token, ticker, subreddits, window_hours, seen_ids
        )
        result.reddit_posts_last_day = len(posts)
        result.reddit_mentions = len(posts)

        if not posts:
            return result

        # Compute simple sentiment: avg upvote ratio
        ratios = [p.get("upvote_ratio", 0) for p in posts if p.get("upvote_ratio")]
        if ratios:
            result.reddit_score = round(sum(ratios) / len(ratios), 3)

        # Top post's top comment
        if posts:
            top = max(posts, key=lambda p: p.get("score", 0))
            top_id = top.get("id", "")
            result.reddit_top_comment = f"Post: {top.get('title', '')[:80]}..."

    except Exception as e:
        result.error = str(e)
        return result

    # Persist seen IDs
    if new_ids:
        _save_seen_ids(seen_ids)

    return result


def summarize_with_llm(
    ticker: str,
    price: float | None,
    change_pct: float | None,
    reddit_result: SentimentResult,
    openrouter_api_key: str,
    model: str = "deepseek/deepseek-v4-flash",
) -> str:
    """Use an LLM via OpenRouter to summarize Reddit sentiment."""
    price_s = f"${price:.2f}" if price else "N/A"
    change_s = f"{change_pct:+.2f}%" if change_pct is not None else "N/A"

    reddit_info = f"""
Reddit data for {ticker} (last 24h):
- Mentions: {reddit_result.reddit_mentions}
- Posts found: {reddit_result.reddit_posts_last_day}
- Avg upvote ratio: {reddit_result.reddit_score}
- Top comment snippet: {reddit_result.reddit_top_comment or 'N/A'}
""".strip()

    prompt = f"""You are a financial sentiment analyst. Given the following data, write a concise 2-3 sentence sentiment summary for {ticker}.

Stock: {ticker}
Current price: {price_s}
Daily change: {change_s}

{reddit_info}

Focus on:
1. What the Reddit sentiment appears to be (bullish, bearish, neutral, mixed)
2. Any notable themes or concerns from the top comments
3. Keep it under 60 words, professional tone."""

    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 150,
        "temperature": 0.3,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {openrouter_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[LLM summary unavailable: {e}]"


def format_watchlist_summary(
    quotes: list,
    reddit_results: dict[str, SentimentResult],
    summaries: dict[str, str],
) -> str:
    """Format a summary table with Reddit sentiment for each ticker."""
    lines = []
    lines.append("=" * 90)
    lines.append(
        f"{'TICKER':>6}  {'PRICE':>8}  {'%CHG':>7}  "
        f"{'REDDIT↓24H':>12}  {'SENTIMENT (Reddit / LLM)':<40}"
    )
    lines.append("-" * 90)

    for q in quotes:
        price_s = f"${q.price:.2f}" if q.price else "---"
        pct_s = f"{q.change_pct:+.2%}" if q.change_pct is not None else "---"

        rr = reddit_results.get(q.ticker)
        mentions = str(rr.reddit_posts_last_day) if rr else "0"
        score = f"{rr.reddit_score:.2f}" if rr and rr.reddit_score else "---"

        summary = summaries.get(q.ticker, "---")[:60]

        lines.append(
            f"{q.ticker:>6}  {price_s:>8}  {pct_s:>7}  "
            f"{mentions:>5} posts  {score:>6}  {summary:<40}"
        )

    lines.append("=" * 90)
    return "\n".join(lines)
