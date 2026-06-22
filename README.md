# Product Catalog Backend — CodeVector Take-Home

A FastAPI + Postgres backend that lets someone browse ~200,000 products,
newest first, filter by category, and paginate through them — fast, and
correctly even while data is being written concurrently.

**Live demo:** `<FILL IN AFTER DEPLOY>`
**API base:** `<FILL IN AFTER DEPLOY>`

---

## The two requirements, and how this meets them

### 1. "Pagination should be fast"

The naive way to paginate SQL is `OFFSET`/`LIMIT`: page 1 is
`OFFSET 0 LIMIT 20`, page 5000 is `OFFSET 100000 LIMIT 20`. The problem is
that Postgres has to walk and discard every row before your offset just to
find where your page starts — it gets linearly slower the deeper you
paginate.

This project uses **keyset (cursor) pagination** instead. Each page
response includes an opaque `next_cursor` built from the last row's
`(created_at, id)`. The next request says "give me rows after this exact
point," which Postgres answers with a direct index seek — not a scan.

I benchmarked this directly on the 200k-row dataset (`EXPLAIN ANALYZE`,
included below) at a depth of 100,000 rows in:

| Approach | Execution time |
|---|---|
| `OFFSET 100000 LIMIT 20` | **47.9 ms** (scans & discards 100,020 rows) |
| Keyset: `WHERE (created_at, id) < (cursor) LIMIT 20` | **0.14 ms** |

That's a ~350x difference, and the gap widens the deeper you go — keyset
pagination stays roughly constant-time regardless of page depth, because
it's always just "find this point in the index, read 20 rows forward."

```
EXPLAIN ANALYZE
SELECT id, name, category, price, created_at, updated_at
FROM products
WHERE (created_at, id) < ('2025-06-21 10:14:35.618538+00', 55688)
ORDER BY created_at DESC, id DESC
LIMIT 20;

 Limit  (cost=0.42..5.32 rows=20 width=104) (actual time=0.040..0.111 rows=20 loops=1)
   ->  Index Scan using idx_products_created_id on products
       (cost=0.42..11071.09 rows=45181 width=104) (actual time=0.039..0.109 rows=20 loops=1)
         Index Cond: (ROW(created_at, id) < ROW('2025-06-21 10:14:35...', 55688))
 Execution Time: 0.141 ms
```

This is backed by a composite index: `(created_at DESC, id DESC)`, and a
second one prefixed with `category` so the filtered case is equally fast:

```sql
CREATE INDEX idx_products_created_id ON products (created_at DESC, id DESC);
CREATE INDEX idx_products_category_created_id ON products (category, created_at DESC, id DESC);
```

**Tradeoff worth knowing:** keyset pagination can't jump to an arbitrary
page number (there's no "skip to page 743" without walking there) — it
only supports "next page from where I am." For a browse-and-click-next UI
(what this task asks for) that's not a real limitation, but it's why
there's no page-number parameter in the API.

### 2. "Show the correct data while data is changing"

This is the part that's easy to get subtly wrong even *with* keyset
pagination, so it's worth spelling out exactly what makes it correct.

**The cursor is `(created_at, id)`, not `created_at` alone.** At this
volume, two products can share the exact same `created_at` timestamp (the
seed data deliberately allows this — see `scripts/seed.py`). If the cursor
were `created_at` alone, "give me rows older than X" would be ambiguous
about where exactly to resume among same-timestamp rows, and could skip or
repeat them. `id` is unique and immutable, so `(created_at, id)` gives
every row an unambiguous, total order — there's always exactly one correct
"next" row.

**Sorting is by `created_at` (insertion time), not `updated_at`.**
This is the actual key decision for the "50 products added/updated while
browsing" requirement. `created_at` is set once at insert and never
changes. That means a row's position in the "newest first" list is
**permanently fixed** once it's created. So:

- A **new** product (created during the user's scroll) is newer than
  everything else, so it always sorts to the very front — ahead of
  everything the user has already paginated past. It simply won't appear
  in their current scroll, the same way a new tweet wouldn't appear if
  you're scrolled halfway down a timeline. It'll show up next time they
  go back to page 1. That's correct, expected behavior, not a bug.
- An **updated** existing product (price change, name edit, etc.) only
  touches `updated_at`. Its `created_at` — and therefore its position in
  the list — doesn't move. The user won't see it twice, and won't miss it.

If instead the list were sorted by "most recently updated," an update to a
product the user already scrolled past could move it back in front of
their cursor, causing a duplicate — that's the trap this design
specifically avoids.

**I wrote an automated test that actually proves this**, rather than just
asserting it: `scripts/test_concurrent_correctness.py`. It:
1. Snapshots all 200,000 product IDs as a baseline.
2. Starts paginating through the live API, page by page, just like a real
   client (following `next_cursor`).
3. After a few pages, while pagination is still in progress, inserts 50
   new products **and** updates 50 random existing products (price +
   `updated_at`) directly against the DB — simulating the exact scenario
   in the task description.
4. Finishes paginating to the end.
5. Asserts: every baseline product appeared **exactly once** (no dupes, no
   skips), and none of the 50 newly-inserted products leaked into the
   in-flight scroll.

Result on the full 200k-row dataset:
```
1. Recording baseline product IDs before pagination starts...
   baseline count: 200,000
2. Paginating through API in pages of 50, injecting writes after page 3...
   ...reached page 3 (150 items so far). Injecting concurrent writes now.
  -> inserted 50 new products + updated 50 existing products mid-scroll
3. Pagination complete. Total items returned: 200,000

--- RESULTS ---
Duplicate products seen: 0
Baseline products missed: 0
Newly-inserted products that leaked into the in-flight scroll: 0

PASS: every pre-existing product was returned exactly once, with no
skips, no duplicates, despite 50 inserts + 50 updates happening mid-pagination.
```

---

## Project structure

```
app/
  main.py     - FastAPI app, /api/products endpoint + pagination logic
  db.py       - asyncpg connection pool setup
  cursor.py   - opaque cursor encode/decode (base64 of created_at+id)
scripts/
  schema.sql                       - table + index definitions, with reasoning
  seed.py                          - generates & bulk-loads 200k products
  test_concurrent_correctness.py   - proves the no-dupes/no-skips requirement
frontend/
  index.html  - single-file browsing UI (vanilla JS, no build step)
requirements.txt
.env.example
```

## Running locally

```bash
# 1. Postgres running locally, then:
createdb products_db
psql products_db -f scripts/schema.sql

# 2. Install deps
pip install -r requirements.txt

# 3. Seed 200k products (takes ~7s)
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/products_db
python3 scripts/seed.py

# 4. Run the API
uvicorn app.main:app --reload --port 8000

# 5. (optional) prove correctness under concurrent writes
python3 scripts/test_concurrent_correctness.py

# 6. Open frontend/index.html in a browser (or `python3 -m http.server 8080`
#    from the frontend/ folder) — it points at localhost:8000 by default.
```

## API

`GET /api/products?category=Books&limit=25&cursor=<opaque>`
Returns `{ items: [...], next_cursor: "<opaque or null>", has_more: bool }`.
Omit `cursor` for the first page. `category` is optional.

`GET /api/categories` — distinct category list, for a filter dropdown.

`GET /api/stats` — total product count (used by the demo UI).

## Deploying (Neon + Render, both free, no card required)

1. **Neon**: create a project, copy the pooled connection string into
   `DATABASE_URL`. Run `psql "$DATABASE_URL" -f scripts/schema.sql`, then
   `python3 scripts/seed.py` against it once to load the 200k rows.
2. **Render**: new Web Service from this repo.
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - Env var: `DATABASE_URL` = your Neon connection string
3. Update `API_BASE` in `frontend/index.html` to the Render URL, and host
   the single HTML file anywhere static (Render static site / GitHub
   Pages / Netlify all work).

---

## What I'd improve with more time

- **Total counts / "Showing X–Y of N":** keyset pagination intentionally
  avoids a `COUNT(*)` per request (expensive at this scale, especially
  filtered), so the UI only knows "has more / doesn't have more," not an
  exact total or position. A reasonable middle ground would be a
  periodically-refreshed approximate count (Postgres's planner stats, or
  a cached count updated on a timer) shown as "~200,000 products" rather
  than an exact live number.
- **Bulk load with indexes dropped/recreated:** at 200k rows this isn't
  needed (the seed finishes in ~7s with indexes already in place), but for
  a much larger seed, dropping indexes before `COPY` and rebuilding them
  after is faster, since index maintenance during insert is a real cost.
- **Cursor signing:** the cursor is just base64-encoded JSON, not signed.
  It's not exploitable for data access beyond what the API already exposes,
  but I'd add an HMAC if this were a real product, mostly so a malformed/
  tampered cursor fails predictably rather than just decoding into nonsense.
- **Rate limiting / auth:** none right now — out of scope for the brief,
  but obviously needed before this is a real public API.
- **Connection pool tuning for serverless Postgres:** Neon's free tier has
  connection limits; I kept the pool small (`max_size=5`) for that reason,
  but a production setup would likely sit behind Neon's pooler endpoint
  (PgBouncer) rather than direct connections.

---

## How I used AI

I used Claude throughout — for the initial architecture discussion (keyset
vs offset pagination, why `created_at` vs `updated_at` matters for
correctness), generating the bulk seed script, the FastAPI endpoint, and
the frontend. I ran everything locally against a real Postgres instance
with the full 200k rows (not just trusting generated code): I checked the
`EXPLAIN ANALYZE` plans myself to confirm the indexes were actually being
used and to get the real OFFSET-vs-keyset timing numbers in this README,
and I wrote and ran `test_concurrent_correctness.py` against the live API
to actually verify the no-duplicates/no-skips behavior end-to-end rather
than just reasoning about it.

One thing worth flagging honestly: the first draft of the seed script's
`generate_rows()` used Python's `random.choice` calls inline per-row in a
way that would've been fine functionally but I tightened the batching
logic (yielding fixed-size batches for `execute_values`) after checking it
against Postgres's recommended bulk-insert batch sizes — that part I
adjusted myself rather than taking as-is.

Full chat transcript: `<attach or link if you want to share it>`
