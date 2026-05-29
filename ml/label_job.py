"""
label_job.py — Resolve buy-signal labels for matured snapshots.

Label rule (v1):
    label = 1 if, within the next 5 TRADING days after entry,
    the intraday HIGH reaches entry_price * 1.05; else 0.

A snapshot is "matured" once 5 trading days of price_history exist
*after* its bar_date. Until then it stays unresolved (label NULL).

Run this once daily, after price_history has been refreshed with
the latest completed trading day.

Usage:
    python label_job.py --db state.db
    python label_job.py --db state.db --target-pct 0.05 --window 5
"""

import argparse
import sqlite3
from datetime import datetime, timezone


def ensure_label_rows(conn, target_pct):
    """Create label rows for any snapshot that doesn't have one yet."""
    conn.execute(
        """
        INSERT INTO labels (snapshot_id, entry_price, target_price, window_days)
        SELECT s.snapshot_id,
               s.entry_price,
               ROUND(s.entry_price * (1.0 + ?), 4),
               ?
        FROM snapshots s
        LEFT JOIN labels l ON l.snapshot_id = s.snapshot_id
        WHERE l.snapshot_id IS NULL
        """,
        (target_pct, 5),
    )


def forward_bars(conn, ticker, entry_bar_date, window):
    """
    Return up to `window` trading-day bars strictly AFTER entry_bar_date,
    ordered chronologically. Each row: (bar_date, high).
    """
    return conn.execute(
        """
        SELECT bar_date, high
        FROM price_history
        WHERE ticker = ? AND bar_date > ?
        ORDER BY bar_date ASC
        LIMIT ?
        """,
        (ticker, entry_bar_date, window),
    ).fetchall()


def resolve(conn, target_pct, window):
    """Resolve every currently-unresolved label that has matured."""
    ensure_label_rows(conn, target_pct)

    rows = conn.execute(
        """
        SELECT l.snapshot_id, s.ticker, s.bar_date, l.entry_price, l.target_price
        FROM labels l
        JOIN snapshots s ON s.snapshot_id = l.snapshot_id
        WHERE l.label IS NULL
        """
    ).fetchall()

    now = datetime.now(timezone.utc).isoformat()
    resolved = pending = 0

    for snapshot_id, ticker, bar_date, entry_price, target_price in rows:
        bars = forward_bars(conn, ticker, bar_date, window)
        bars_seen = len(bars)

        hit_date = None
        max_high = None
        for bd, high in bars:
            if max_high is None or high > max_high:
                max_high = high
            if high >= target_price and hit_date is None:
                hit_date = bd

        if hit_date is not None:
            # Positive resolves immediately, even before the full window
            # elapses — the target was already touched.
            conn.execute(
                """UPDATE labels SET label=1, hit_date=?, max_high=?,
                   bars_observed=?, resolved_at=? WHERE snapshot_id=?""",
                (hit_date, max_high, bars_seen, now, snapshot_id),
            )
            resolved += 1
        elif bars_seen >= window:
            # Full window elapsed without a hit -> negative.
            conn.execute(
                """UPDATE labels SET label=0, max_high=?,
                   bars_observed=?, resolved_at=? WHERE snapshot_id=?""",
                (max_high, bars_seen, now, snapshot_id),
            )
            resolved += 1
        else:
            # Not enough forward bars yet; record progress, leave NULL.
            conn.execute(
                """UPDATE labels SET max_high=?, bars_observed=? WHERE snapshot_id=?""",
                (max_high, bars_seen, snapshot_id),
            )
            pending += 1

    conn.commit()
    return resolved, pending


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--target-pct", type=float, default=0.05,
                    help="fractional gain that counts as a hit (default 0.05 = 5%%)")
    ap.add_argument("--window", type=int, default=5,
                    help="trading-day window (default 5)")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        resolved, pending = resolve(conn, args.target_pct, args.window)
        print(f"resolved={resolved} still_pending={pending}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
