"""
Seed script: generates 200,000 products and bulk-loads them into Postgres.

Why not a simple loop with INSERT statements?
    Doing `for i in range(200_000): cursor.execute("INSERT ...")` means
    200,000 separate network round-trips to the database. Even at a
    generous 1-2ms per round trip that's 3-6+ minutes, and in practice
    with index maintenance on every single insert it's worse. It also
    doesn't represent how you'd ever actually load bulk data in real life.

What we do instead:
    We generate all the rows in memory (as a single Pandas-free generator,
    nothing fancy needed) and use Postgres's COPY protocol via
    `psycopg2.extras.execute_values` / `copy_expert`, which streams rows in
    big batches instead of one at a time. This turns 200k inserts into a
    handful of round trips. On a normal machine this finishes in a few
    seconds to ~30s, not minutes.

    We also create the indexes AFTER loading the data (schema.sql is run
    first here for simplicity/clarity, but for a truly huge load you'd
    drop indexes before COPY and recreate them after -- index maintenance
    during bulk insert is a big chunk of the cost.  At 200k rows it's not
    necessary, but it's worth knowing for bigger datasets, and I mention it
    in the README as a "what I'd improve" item.)

Usage:
    python scripts/seed.py
    (reads DATABASE_URL from .env / environment)
"""

import os
import random
import time
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/products_db"
)

NUM_PRODUCTS = 200_000
BATCH_SIZE = 5_000

CATEGORIES = [
    "Electronics", "Home & Kitchen", "Books", "Clothing", "Toys & Games",
    "Sports & Outdoors", "Beauty & Personal Care", "Grocery", "Automotive",
    "Office Supplies", "Pet Supplies", "Garden & Tools", "Health",
    "Furniture", "Footwear",
]

ADJECTIVES = [
    "Premium", "Compact", "Wireless", "Portable", "Eco-Friendly", "Classic",
    "Pro", "Lightweight", "Heavy-Duty", "Smart", "Deluxe", "Essential",
    "Rapid", "Ultra", "Everyday",
]

NOUNS = [
    "Charger", "Backpack", "Blender", "Notebook", "Headphones", "Sneakers",
    "Lamp", "Mug", "Jacket", "Speaker", "Keyboard", "Chair", "Bottle",
    "Monitor", "Wallet", "Watch", "Pan", "Tent", "Bike", "Razor",
]


def random_product_name(rng: random.Random) -> str:
    return f"{rng.choice(ADJECTIVES)} {rng.choice(NOUNS)}"


def generate_rows(n: int):
    """
    Yields (name, category, price, created_at, updated_at) tuples.

    created_at is spread over the last ~2 years so "newest first" pagination
    has something realistic to sort through. We generate them with
    strictly increasing timestamps as `i` increases, then shuffle insert
    order -- this just makes the data feel more realistic (random insert
    order) without needing it to be in created_at order on disk.
    """
    rng = random.Random(42)  # fixed seed -> reproducible dataset
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=730)
    span_seconds = int((now - start).total_seconds())

    rows = []
    for i in range(n):
        # Spread timestamps roughly evenly across the 2-year span, with
        # some jitter, and intentionally allow collisions (two products can
        # share the exact same created_at) since that's realistic at this
        # volume and is exactly why pagination needs `id` as a tiebreaker,
        # not just created_at.
        offset_seconds = rng.randint(0, span_seconds)
        created = start + timedelta(seconds=offset_seconds)
        # updated_at is usually == created_at, sometimes later (simulating
        # a product that was edited after creation)
        if rng.random() < 0.15:
            updated = created + timedelta(
                seconds=rng.randint(0, max(1, span_seconds - offset_seconds))
            )
        else:
            updated = created

        name = random_product_name(rng)
        category = rng.choice(CATEGORIES)
        price = round(rng.uniform(4.99, 999.99), 2)

        rows.append((name, category, price, created, updated))

        if len(rows) >= BATCH_SIZE:
            yield rows
            rows = []
    if rows:
        yield rows


def main():
    print(f"Connecting to {DATABASE_URL.split('@')[-1]}...")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False

    try:
        with conn.cursor() as cur:
            print("Truncating existing data (if any)...")
            cur.execute("TRUNCATE TABLE products RESTART IDENTITY;")
            conn.commit()

        start_time = time.time()
        inserted = 0

        insert_sql = """
            INSERT INTO products (name, category, price, created_at, updated_at)
            VALUES %s
        """

        with conn.cursor() as cur:
            for batch in generate_rows(NUM_PRODUCTS):
                execute_values(cur, insert_sql, batch, page_size=BATCH_SIZE)
                inserted += len(batch)
                print(f"  inserted {inserted:,} / {NUM_PRODUCTS:,}", end="\r")
            conn.commit()

        elapsed = time.time() - start_time
        print(f"\nDone. Inserted {inserted:,} rows in {elapsed:.2f}s "
              f"({inserted / elapsed:,.0f} rows/sec).")

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM products;")
            count = cur.fetchone()[0]
            print(f"Row count in DB: {count:,}")

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
