"""Tests for sources/reddit_scraper.py."""

import time
from unittest.mock import patch, MagicMock
import pytest

from sources.reddit_scraper import get_posts, _post_key


# --- unit tests (no network) ---

SAMPLE_RESPONSE = {
    "data": {
        "children": [
            {
                "data": {
                    "id": "abc123",
                    "title": "AMD earnings beat expectations",
                    "selftext": "Big quarter for AMD.",
                    "permalink": "/r/stocks/comments/abc123/amd_earnings/",
                    "created_utc": 1700000000.0,
                }
            },
            {
                "data": {
                    "id": "def456",
                    "title": "General market discussion",
                    "selftext": "",
                    "permalink": "/r/stocks/comments/def456/general/",
                    "created_utc": 1700001000.0,
                }
            },
        ]
    }
}


def _mock_fetch(url, params):
    return SAMPLE_RESPONSE


@patch("sources.reddit_scraper._fetch_json", side_effect=_mock_fetch)
def test_get_posts_returns_list(mock_fetch):
    posts = get_posts("stocks")
    assert isinstance(posts, list)
    assert len(posts) == 2


@patch("sources.reddit_scraper._fetch_json", side_effect=_mock_fetch)
def test_post_fields_present(mock_fetch):
    posts = get_posts("stocks")
    required = {"headline", "url", "datetime", "source", "summary", "article_key"}
    for post in posts:
        assert required.issubset(post.keys()), f"Missing keys in {post}"


@patch("sources.reddit_scraper._fetch_json", side_effect=_mock_fetch)
def test_post_url_format(mock_fetch):
    posts = get_posts("stocks")
    for post in posts:
        assert post["url"].startswith("https://www.reddit.com/r/")


@patch("sources.reddit_scraper._fetch_json", side_effect=_mock_fetch)
def test_source_label(mock_fetch):
    posts = get_posts("stocks")
    assert all(p["source"] == "r/stocks" for p in posts)


@patch("sources.reddit_scraper._fetch_json", side_effect=_mock_fetch)
def test_keyword_filter_match(mock_fetch):
    posts = get_posts("stocks", keyword_filter=["AMD"])
    assert len(posts) == 1
    assert "AMD" in posts[0]["headline"]


@patch("sources.reddit_scraper._fetch_json", side_effect=_mock_fetch)
def test_keyword_filter_no_match(mock_fetch):
    posts = get_posts("stocks", keyword_filter=["NVDA"])
    assert posts == []


@patch("sources.reddit_scraper._fetch_json", side_effect=_mock_fetch)
def test_datetime_is_int(mock_fetch):
    posts = get_posts("stocks")
    for post in posts:
        assert isinstance(post["datetime"], int)


@patch("sources.reddit_scraper._fetch_json", side_effect=_mock_fetch)
def test_article_key_length(mock_fetch):
    posts = get_posts("stocks")
    for post in posts:
        assert len(post["article_key"]) == 16


@patch("sources.reddit_scraper._fetch_json", side_effect=_mock_fetch)
def test_invalid_sort_falls_back_to_hot(mock_fetch):
    # Should not raise; falls back to 'hot'
    posts = get_posts("stocks", sort="invalid")
    assert isinstance(posts, list)
    call_url = mock_fetch.call_args[0][0]
    assert "/hot.json" in call_url


@patch("sources.reddit_scraper._fetch_json", side_effect=Exception("network error"))
def test_returns_empty_on_failure(mock_fetch):
    posts = get_posts("stocks")
    assert posts == []


def test_post_key_deterministic():
    k1 = _post_key("abc", "title")
    k2 = _post_key("abc", "title")
    assert k1 == k2


def test_post_key_unique():
    assert _post_key("abc", "title") != _post_key("abc", "other")


# --- live smoke test (skipped in CI) ---

@pytest.mark.live
def test_live_fetch_stocks():
    """Hits the real Reddit API — run with: pytest -m live tests/test_reddit_scraper.py"""
    posts = get_posts("stocks", sort="hot", limit=5)
    assert len(posts) > 0, "Expected at least one post from r/stocks"
    post = posts[0]
    assert post["headline"]
    assert post["url"].startswith("https://www.reddit.com")
    assert post["source"] == "r/stocks"
    print(f"\nSample post: {post['headline'][:80]}")
    print(f"URL: {post['url']}")
    print(f"Posted: {post['datetime']}")
