"""
Database connection setup using asyncpg directly (no ORM).

Why asyncpg and not an ORM (SQLAlchemy models, etc.)?
    This API has exactly two query shapes (list products with a cursor,
    optionally filtered by category). An ORM adds an abstraction layer for
    a problem that's already simple, and -- more importantly for this
    task -- it makes it easier to accidentally write the kind of query
    that doesn't use our indexes (e.g. an ORM's default `.offset().limit()`
    pagination helper). Writing the SQL by hand keeps it obvious exactly
    what's being sent to Postgres and why it's fast.

We use a connection pool (not a connection per request) since this will
serve concurrent requests in production.
"""

import os
import asyncpg
from contextlib import asynccontextmanager
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/products_db"
)

_pool: asyncpg.Pool | None = None


async def init_pool():
    global _pool
    # Neon/Supabase free tiers are happy with small pools; Render's free
    # web service is single-instance so we don't need anything large.
    _pool = await asyncpg.create_pool(
        DATABASE_URL, min_size=1, max_size=5, command_timeout=10
    )
    return _pool


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized -- did startup run?")
    return _pool
