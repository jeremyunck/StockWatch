"""
seed_test.py — Insert synthetic data and smoke-test the full pipeline.

Creates an in-memory (or on-disk) SQLite database, inserts:
  - price_history rows for a fake ticker spanning 300 trading days
  - snapshots at controlled entry prices with known expected labels:
      * "hit" case:    entry_price such that the window high > entry*1.05
      * "miss" case:   entry_price such that the window high < entry*1.05
      * "pending" case: snapshot whose bar_date is too recent (< 5 forward bars)

Then runs label_job.resolve() and asserts the outcomes.  Finally attempts
to run train.py with --min-rows lowered to the seeded count (skipped if
xgboost is not installed, so the test still passes in a bare environment).

Usage:
    python seed_test.py              # runs assertions, prints PASS/FAIL
    python seed_test.py --db /tmp/test_pipeline.db  # leave db on disk for inspection
    python seed_test.py --skip-train # skip the xgboost training step
"""

import argparse
import sqlite3
import sys
import os
from datetime import date, timedelta

# Make sure local imports work when run from the ml/ directory or its parent
sys.path.insert(0, os.path.dirname(__file__))

from label_job import resolve


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TRADING_DAYS_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
"""


def apply_schema(conn):
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path) as fh:
        conn.executescript(fh.read())
    conn.commit()


def trading_dates(start: date, n: int):
    """Generate n weekday dates starting from `start`."""
    dates = []
    d = start
    while len(dates) < n:
        if d.weekday() < 5:  # Mon–Fri
            dates.append(d)
        d += timedelta(days=1)
    return dates


def insert_price_history(conn, ticker: str, dates: list, base_price: float = 100.0):
    """
    Insert synthetic daily bars.  Price drifts ±0.5% per day.
    The high for each bar is close * 1.01 (roughly).
    """
    rows = []
    price = base_price
    for d in dates:
        price *= 1.002          # gentle uptrend
        rows.append((
            ticker,
            d.isoformat(),
            round(price * 0.995, 4),   # open
            round(price * 1.010, 4),   # high
            round(price * 0.990, 4),   # low
            round(price, 4),           # close
            100_000,                   # volume
        ))
    conn.executemany(
        "INSERT OR REPLACE INTO price_history "
        "(ticker, bar_date, open, high, low, close, volume) VALUES "
        "(?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return rows


def insert_snapshot(conn, ticker: str, bar_date: str,
                    entry_price: float, captured_at: str = None):
    if captured_at is None:
        captured_at = f"{bar_date}T10:30:00+00:00"
    conn.execute(
        """INSERT OR IGNORE INTO snapshots
           (ticker, captured_at, bar_date, entry_price,
            rsi14, macd_hist, sma50, sma200, ema20,
            bb_upper, bb_lower, bb_mid, atr14, vol_avg20, obv,
            ret_5d, ret_20d, hi_52w, lo_52w, news_count_24h)
           VALUES
           (?, ?, ?, ?,
            50.0, 0.1, ?, ?, ?,
            ?, ?, ?, 2.0, 100000, 5000000,
            0.01, 0.05, ?, ?, 0)""",
        (
            ticker, captured_at, bar_date, entry_price,
            entry_price * 0.99,   # sma50
            entry_price * 0.97,   # sma200
            entry_price * 1.00,   # ema20
            entry_price * 1.03,   # bb_upper
            entry_price * 0.97,   # bb_lower
            entry_price * 1.00,   # bb_mid
            entry_price * 1.10,   # hi_52w
            entry_price * 0.85,   # lo_52w
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Seed data design
# ---------------------------------------------------------------------------
#
#  All trading dates: day 0 … day 299  (300 total)
#
#  HIT snapshot  → bar_date = day 100
#      entry_price = 100.0
#      target_price = 105.0
#      In the synthetic history, high[day N] ≈ price[N] * 1.01.
#      price[100] ≈ 100.0 * 1.002^100 ≈ 122.  But we control entry_price
#      separately from the historical price, so we simply set
#      entry_price = price at day 100 * (1/1.01) so target = entry * 1.05
#      and by day 103 the high will exceed target.
#      Easier: just set entry_price low enough that target < max_high in window.
#
#  MISS snapshot → bar_date = day 200
#      entry_price set HIGH so target = entry * 1.05 is above any bar in window.
#
#  PENDING snapshot → bar_date = last available day (day 299)
#      Only 0 forward bars exist → stays NULL.
#
# ---------------------------------------------------------------------------

def seed(conn, ticker="FAKE"):
    start = date(2022, 1, 3)   # first trading day of 2022
    all_dates = trading_dates(start, 300)

    # Insert full price history
    bars = insert_price_history(conn, ticker, all_dates, base_price=100.0)
    # bars[i] = (ticker, date_str, open, high, low, close, volume)

    # ---- HIT: entry_price low enough that window high exceeds entry*1.05 ----
    hit_idx = 100
    hit_bar = all_dates[hit_idx].isoformat()
    # Forward window highs are bars[101..105].  Each high ≈ close*1.01.
    # Close at bar 101 ≈ 100 * 1.002^101 ≈ 122.3; high ≈ 123.5
    # Set entry_price = 117 → target = 117 * 1.05 = 122.85 → will be hit
    hit_entry = 117.0
    insert_snapshot(conn, ticker, hit_bar, hit_entry)

    # ---- MISS: entry_price so high target can never be reached in window ----
    miss_idx = 200
    miss_bar = all_dates[miss_idx].isoformat()
    # Close at 200 ≈ 100 * 1.002^200 ≈ 149.  High of window ≈ 155.
    # Set entry_price = 160 → target = 168 → impossible
    miss_entry = 160.0
    insert_snapshot(conn, ticker, miss_bar, miss_entry)

    # ---- PENDING: too recent — no forward bars yet -------------------------
    pending_idx = 299
    pending_bar = all_dates[pending_idx].isoformat()
    pending_entry = bars[pending_idx][5]   # close price of last bar
    insert_snapshot(conn, ticker, pending_bar, pending_entry)

    return hit_bar, miss_bar, pending_bar


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

def run_tests(conn, hit_bar, miss_bar, pending_bar, ticker="FAKE"):
    target_pct = 0.05
    window = 5

    resolved_count, pending_count = resolve(conn, target_pct, window)

    # Fetch results
    rows = conn.execute(
        """SELECT s.bar_date, l.label, l.bars_observed, l.hit_date, l.max_high,
                  l.entry_price, l.target_price
           FROM labels l JOIN snapshots s ON s.snapshot_id = l.snapshot_id
           WHERE s.ticker = ?
           ORDER BY s.bar_date""",
        (ticker,),
    ).fetchall()

    results = {r[0]: r for r in rows}

    failures = []

    # HIT assertion
    hit = results.get(hit_bar)
    if hit is None:
        failures.append(f"HIT snapshot ({hit_bar}) has no label row")
    elif hit[1] != 1:
        failures.append(
            f"HIT snapshot ({hit_bar}): expected label=1, got label={hit[1]} "
            f"entry={hit[5]:.2f} target={hit[6]:.2f} max_high={hit[4]}"
        )
    else:
        print(f"  [PASS] HIT   bar={hit_bar}  label=1  "
              f"hit_date={hit[3]}  max_high={hit[4]:.2f}")

    # MISS assertion
    miss = results.get(miss_bar)
    if miss is None:
        failures.append(f"MISS snapshot ({miss_bar}) has no label row")
    elif miss[1] != 0:
        failures.append(
            f"MISS snapshot ({miss_bar}): expected label=0, got label={miss[1]} "
            f"entry={miss[5]:.2f} target={miss[6]:.2f} max_high={miss[4]}"
        )
    else:
        print(f"  [PASS] MISS  bar={miss_bar}  label=0  "
              f"bars_observed={miss[2]}  max_high={miss[4]:.2f}")

    # PENDING assertion
    pend = results.get(pending_bar)
    if pend is None:
        failures.append(f"PENDING snapshot ({pending_bar}) has no label row")
    elif pend[1] is not None:
        failures.append(
            f"PENDING snapshot ({pending_bar}): expected label=NULL, got label={pend[1]}"
        )
    else:
        print(f"  [PASS] PENDING bar={pending_bar}  label=NULL  "
              f"bars_observed={pend[2]}")

    # Summary counts
    print(f"\n  label_job: resolved={resolved_count} pending={pending_count}")
    if pending_count != 1:
        failures.append(f"Expected 1 pending, got {pending_count}")
    if resolved_count != 2:
        failures.append(f"Expected 2 resolved, got {resolved_count}")

    return failures


def run_train_smoke(db_path: str):
    """
    Add enough synthetic rows so train.py won't bail on --min-rows,
    then call its main() with a low threshold.  Asserts it doesn't crash.
    """
    try:
        import xgboost  # noqa: F401
    except ImportError:
        print("  [SKIP] xgboost not installed — skipping train smoke test")
        return []

    # Add more synthetic labeled rows by re-seeding extra tickers
    conn = sqlite3.connect(db_path)
    start = date(2022, 1, 3)
    all_dates = _trading_dates_cached(start, 300)

    for i in range(20):
        t = f"SYN{i:02d}"
        bars = insert_price_history(conn, t, all_dates, base_price=50.0 + i * 5)
        # Alternate hit/miss entries so each fold contains both classes.
        # close * 0.90 entry → target = close*0.945 < high(close*1.01) → label=1
        # close * 1.20 entry → target = close*1.26 > any window high      → label=0
        for idx in range(5, 250, 5):
            bd = all_dates[idx].isoformat()
            if idx % 10 == 5:
                entry = bars[idx][5] * 0.90   # will hit (label=1)
            else:
                entry = bars[idx][5] * 1.20   # will not hit (label=0)
            insert_snapshot(conn, t, bd, entry)
    conn.close()

    # Re-run label job to resolve the new rows
    conn = sqlite3.connect(db_path)
    resolve(conn, 0.05, 5)
    conn.close()

    # Now call train.main() via subprocess so it gets its own arg context
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(__file__), "train.py"),
         "--db", db_path,
         "--out-dir", "/tmp/ml_test_models",
         "--min-rows", "10"],
        capture_output=True, text=True,
    )
    print("\n--- train.py output ---")
    print(result.stdout)
    if result.returncode != 0:
        print("stderr:", result.stderr)
        return [f"train.py exited {result.returncode}: {result.stderr[:200]}"]
    return []


def _trading_dates_cached(start, n):
    """Same as trading_dates() — duplicated here to avoid import order issues."""
    dates = []
    d = start
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    return dates


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=":memory:",
                    help="SQLite path (default: in-memory)")
    ap.add_argument("--skip-train", action="store_true",
                    help="Skip the xgboost training smoke test")
    args = ap.parse_args()

    print("=== seed_test.py — buy-signal pipeline smoke test ===\n")
    print("1. Setting up database …")

    use_disk = args.db != ":memory:"
    conn = sqlite3.connect(args.db)
    apply_schema(conn)

    print("2. Seeding synthetic price_history + snapshots …")
    hit_bar, miss_bar, pending_bar = seed(conn)

    print("3. Running label_job.resolve() …")
    failures = run_tests(conn, hit_bar, miss_bar, pending_bar)
    conn.close()

    if not args.skip_train and use_disk:
        print("\n4. Running train.py smoke test (xgboost) …")
        failures += run_train_smoke(args.db)
    elif not args.skip_train and not use_disk:
        print("\n4. [SKIP] train smoke test requires --db on disk (not :memory:)")

    print()
    if failures:
        print("RESULT: FAIL")
        for f in failures:
            print(f"  ✗ {f}")
        sys.exit(1)
    else:
        print("RESULT: PASS — all assertions satisfied")


if __name__ == "__main__":
    main()
