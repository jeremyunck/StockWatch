"""Reddit sentiment analysis via PRAW + LLM summarization."""

import praw
import os
import json
import time
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field
from pathlib import Path

# State file for tracking seen Reddit post IDs
STATE_DIR = Path.home() / ".local" / "share" / "stockwatch"
STATE_FILE = STATE_DIR / "reddit-seen-posts.json"


def _load_seen_ids() -> set[str]:
    """Load previously seen Reddit post IDs from state file."""
    if not STATE_FILE.exists():
        return set()
    try:
        return set(json.loads(STATE_FILE.read_text()))
    except Exception:
        return set()


def _save_seen_ids(seen: set[str]) -> None:
    """Save seen Reddit post IDs to state file."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # Keep only last 5000 IDs to avoid unbounded growth
    ids = list(seen)[-5000:]
    STATE_FILE.write_text(json.dumps(ids))


@dataclass
class SentimentResult:
    ticker: str
    reddit_score: Optional[float] = None  # average upvote ratio / sentiment proxy
    reddit_mentions: int = 0
    reddit_top_comment: Optional[str] = None
    reddit_sentiment_summary: Optional[str] = None
    reddit_posts_last_day: int = 0
    error: Optional[str] = None


def get_reddit_client() -> Optional[praw.Reddit]:
    """Initialize PRAW client from environment variables."""
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    user_agent = os.environ.get("REDDIT_USER_AGENT", "stockwatch:v0.1.0")

    if not client_id or not client_secret:
        raise ValueError(
            "REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET must be set. "
            "Get them from https://www.reddit.com/prefs/apps"
        )

    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
    )


def fetch_reddit_sentiment(
    reddit: praw.Reddit,
    ticker: str,
    subreddits: list[str] | None = None,
    window_hours: int = 24,
) -> SentimentResult:
    """Fetch Reddit sentiment for a ticker over the last `window_hours`."""
    if subreddits is None:
        subreddits = ["wallstreetbets", "stocks", "investing", "StockMarket"]

    result = SentimentResult(ticker=ticker)
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=window_hours)

    # Load seen post IDs to avoid re-processing
    seen_ids = _load_seen_ids()
    new_ids: list[str] = []

    try:
        all_posts = []
        for sub_name in subreddits:
            sub = reddit.subreddit(sub_name)
            # Search for the ticker in post titles
            for submission in sub.search(
                ticker, time_filter="day", sort="new", limit=50
            ):
                created = datetime.utcfromtimestamp(submission.created_utc)
                if created < cutoff:
                    continue
                # Skip already-seen posts
                if submission.id in seen_ids:
                    continue
                seen_ids.add(submission.id)
                new_ids.append(submission.id)
                all_posts.append(submission)
                result.reddit_mentions += 1

        if not all_posts:
            return result

        # Compute simple sentiment proxy: upvote ratio
        upvote_ratios = [p.upvote_ratio for p in all_posts if p.upvote_ratio]
        if upvote_ratios:
            result.reddit_score = round(
                sum(upvote_ratios) / len(upvote_ratios), 3
            )

        result.reddit_posts_last_day = len(all_posts)

        # Persist seen IDs for next run
        if new_ids:
            _save_seen_ids(seen_ids)

        # Grab the top-scoring post's top comment for context
        top_post = max(all_posts, key=lambda p: p.score)
        if top_post.num_comments > 0:
            top_post.comments.replace_more(limit=0)
            comments = list(top_post.comments)[:3]
            if comments:
                result.reddit_top_comment = (
                    comments[0].body[:300] if comments[0].body else None
                )

    except Exception as e:
        result.error = str(e)

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
    import json
    import urllib.request
    import urllib.error

    # Build the prompt
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
