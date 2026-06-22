"""
Proof test for the core requirement: "show the correct data while data is
changing... they must not see the same product twice or miss one."

What this script does:
  1. Records the full set of product IDs that currently exist (the
     "baseline" -- these are the ones a user paginating through the list
     should see exactly once, no dupes, no skips).
  2. Starts paginating through the API from page 1, using next_cursor like
     a real client would.
  3. Partway through (after consuming a few pages), inserts 50 new
     products AND updates 50 random existing products' `updated_at` /
     price -- simulating exactly the scenario in the task description,
     mid-scroll.
  4. Keeps paginating to the end.
  5. Asserts:
       a) every baseline ID was seen exactly once (no skips, no dupes)
       b) none of the 50 newly-inserted products appeared in the results
          (since they're "newer" than everything the user already
          scrolled past -- they'd only show up on a fresh page 1, which
          is expected/correct, same as a live feed)

Run with the API server already running locally:
    DATABASE_URL=postgresql://postgres:postgres@localhost:5432/products_db \
        python3 scripts/test_concurrent_correctness.py
"""

import os
import random
import time
from collections import Counter

import psycopg2
import requests

API_BASE = os.environ.get("API_BASE", "http://localhost:8000")
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/products_db"
)

PAGE_SIZE = 50
INSERT_AFTER_PAGES = 3  # simulate the write happening after a few pages of scrolling


def get_baseline_ids(conn) -> set[int]:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM products")
        return {row[0] for row in cur.fetchall()}


def insert_and_update_concurrently(conn):
    """Simulates '50 new products are added/updated while someone is browsing'."""
    with conn.cursor() as cur:
        # Insert 50 new products (these will have created_at = now(), i.e.
        # newer than everything else, so they belong at the very front of
        # the "newest first" list)
        cur.execute(
            """
            INSERT INTO products (name, category, price, created_at, updated_at)
            SELECT
                'New Product ' || i,
                'Electronics',
                99.99,
                now(),
                now()
            FROM generate_series(1, 50) AS i
            """
        )
        # Update 50 random EXISTING products (price + updated_at change,
        # created_at does NOT change -- this is the case that must not
        # break pagination)
        cur.execute(
            """
            UPDATE products
            SET price = price + 1, updated_at = now()
            WHERE id IN (
                SELECT id FROM products ORDER BY random() LIMIT 50
            )
            """
        )
    conn.commit()
    print("  -> inserted 50 new products + updated 50 existing products mid-scroll")


def paginate_all(category=None) -> list[dict]:
    items = []
    cursor = None
    page_num = 0
    while True:
        page_num += 1
        params = {"limit": PAGE_SIZE}
        if cursor:
            params["cursor"] = cursor
        if category:
            params["category"] = category

        resp = requests.get(f"{API_BASE}/api/products", params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        items.extend(data["items"])

        if page_num == INSERT_AFTER_PAGES:
            yield ("INSERT_POINT", items)

        if not data["has_more"]:
            break
        cursor = data["next_cursor"]

    yield ("DONE", items)


def main():
    conn = psycopg2.connect(DATABASE_URL)

    print("1. Recording baseline product IDs before pagination starts...")
    baseline_ids = get_baseline_ids(conn)
    print(f"   baseline count: {len(baseline_ids):,}")

    print(f"2. Paginating through API in pages of {PAGE_SIZE}, "
          f"injecting writes after page {INSERT_AFTER_PAGES}...")

    collected = []
    did_insert = False
    for marker, items in paginate_all():
        collected = items
        if marker == "INSERT_POINT" and not did_insert:
            print(f"   ...reached page {INSERT_AFTER_PAGES} "
                  f"({len(items)} items so far). Injecting concurrent writes now.")
            insert_and_update_concurrently(conn)
            did_insert = True

    print(f"3. Pagination complete. Total items returned: {len(collected):,}")

    seen_ids = [item["id"] for item in collected]
    id_counts = Counter(seen_ids)
    duplicates = {pid: c for pid, c in id_counts.items() if c > 1}

    seen_id_set = set(seen_ids)
    missed_from_baseline = baseline_ids - seen_id_set

    print("\n--- RESULTS ---")
    print(f"Duplicate products seen: {len(duplicates)}"
          + (f"  {duplicates}" if duplicates else ""))
    print(f"Baseline products missed: {len(missed_from_baseline)}")

    new_products_leaked_in = [
        item for item in collected if item["name"].startswith("New Product ")
    ]
    print(f"Newly-inserted products that leaked into the in-flight scroll: "
          f"{len(new_products_leaked_in)} (expected 0 -- new items should "
          f"only appear on a fresh page 1, not retroactively inserted into "
          f"a scroll already in progress)")

    assert len(duplicates) == 0, "FAIL: duplicates were returned!"
    assert len(missed_from_baseline) == 0, "FAIL: some baseline products were skipped!"
    assert len(new_products_leaked_in) == 0, "FAIL: new products leaked into the scroll!"

    print("\nPASS: every pre-existing product was returned exactly once, "
          "with no skips, no duplicates, despite 50 inserts + 50 updates "
          "happening mid-pagination.")

    conn.close()


if __name__ == "__main__":
    main()
