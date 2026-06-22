-- Schema for the products table.
--
-- Design notes (why it looks like this):
--
-- 1. `id` is a BIGINT generated as IDENTITY (auto-incrementing), not a UUID.
--    We need it to be unique and monotonically increasing so it can act as
--    a deterministic tiebreaker for pagination (see below). UUIDs would work
--    for uniqueness but are bigger, fragment indexes more, and aren't
--    naturally ordered, which we don't need anyway.
--
-- 2. `created_at` is what we sort "newest first" by. It's set once at
--    insert time and never changes, which matters: it means a row's
--    position in the "newest first" feed is permanently fixed. Updates to
--    a product (price change, name fix, etc.) don't move it. That's what
--    makes keyset pagination correctness possible (see API code for the
--    full explanation) -- if updates could move a row within the sort
--    order, a user paginating forward could end up re-seeing a row that
--    jumped past their cursor, or missing one that jumped behind it.
--
-- 3. The composite index on (created_at DESC, id DESC) is what makes
--    pagination fast. Postgres can satisfy
--      WHERE (created_at, id) < (:cursor_ts, :cursor_id)
--      ORDER BY created_at DESC, id DESC
--      LIMIT 20
--    as a single index range scan -- it walks the index from the cursor
--    point and stops after 20 rows, regardless of whether you're on page 1
--    or page 9000. No OFFSET, no scanning/discarding rows.
--
-- 4. category has its own index, and the pagination index also exists
--    per-category (composite) so that "filter by category + paginate" is
--    just as fast as the unfiltered case.

CREATE TABLE IF NOT EXISTS products (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,
    price       NUMERIC(10, 2) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Powers: "newest first" pagination with no category filter.
CREATE INDEX IF NOT EXISTS idx_products_created_id
    ON products (created_at DESC, id DESC);

-- Powers: "newest first" pagination WITH a category filter.
-- Composite index, category first, so Postgres can jump straight to the
-- category's rows and walk them in created_at order without a separate sort.
CREATE INDEX IF NOT EXISTS idx_products_category_created_id
    ON products (category, created_at DESC, id DESC);
