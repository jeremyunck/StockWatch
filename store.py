"""SQLite persistence: OHLC cache, seen news dedup, last signal state."""

import hashlib
import json
import logging
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "state.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS ohlc_cache (
                ticker       TEXT PRIMARY KEY,
                bars_json    TEXT NOT NULL,
                fetched_date TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS seen_news (
                article_key TEXT PRIMARY KEY,
                ticker      TEXT NOT NULL,
                url         TEXT NOT NULL,
                published   INTEGER,
                posted_at   INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS last_signal (
                ticker      TEXT PRIMARY KEY,
                label       TEXT NOT NULL,
                computed_at INTEGER NOT NULL
            );
        """)


def get_or_refresh_ohlc(ticker: str, fetch_fn) -> Optional[pd.DataFrame]:
    """Return cached OHLC DataFrame; refresh once per calendar day via fetch_fn."""
    today = date.today().isoformat()
    with _connect() as conn:
        row = conn.execute(
            "SELECT bars_json, fetched_date FROM ohlc_cache WHERE ticker = ?", (ticker,)
        ).fetchone()
        if row and row[1] == today:
            logger.debug("OHLC cache hit for %s", ticker)
            records = json.loads(row[0])
            df = pd.DataFrame(records)
            df.index = pd.to_datetime(df.index)
            return df

    logger.info("Fetching fresh OHLC for %s", ticker)
    df = fetch_fn(ticker)

    records = df.copy()
    records.index = records.index.astype(str)
    bars_json = records.to_json(orient="index")

    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO ohlc_cache (ticker, bars_json, fetched_date) VALUES (?, ?, ?)",
            (ticker, bars_json, today),
        )
    return df


def is_seen(article_key: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM seen_news WHERE article_key = ?", (article_key,)
        ).fetchone()
        return row is not None


def mark_seen(article_key: str, ticker: str, url: str, published: Optional[int]) -> None:
    now = int(datetime.now(timezone.utc).timestamp())
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO seen_news (article_key, ticker, url, published, posted_at) VALUES (?, ?, ?, ?, ?)",
            (article_key, ticker, url, published, now),
        )


def get_last_signal(ticker: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT label, computed_at FROM last_signal WHERE ticker = ?", (ticker,)
        ).fetchone()
        if row:
            return {"label": row[0], "computed_at": row[1]}
        return None


def set_last_signal(ticker: str, label: str) -> None:
    now = int(datetime.now(timezone.utc).timestamp())
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO last_signal (ticker, label, computed_at) VALUES (?, ?, ?)",
            (ticker, label, now),
        )


def make_article_key(url: str, headline: str) -> str:
    return hashlib.sha256(f"{url}|{headline}".encode()).hexdigest()[:16]
