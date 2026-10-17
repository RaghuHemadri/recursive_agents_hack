---
name: sql-basics
description: Efficient aggregate SQL habits for SQLite warehouses
---
The warehouse is accessed ONLY through the `schema`, `sample`, and `sql` tools — there is no database file on disk.
Do not search the filesystem or use python/sqlite3.

Prefer one aggregate query over many small ones. Use COUNT/SUM/AVG in SQL rather than pulling rows.

Common gotchas in this warehouse:
- Status columns have inconsistent casing (e.g. 'COMPLETED', 'Completed', 'completed'). Always compare with
  LOWER(col) or UPPER(col).
- Timestamps are epoch milliseconds (Unix ms). Convert with strftime('%Y-%m-%d', ts/1000, 'unixepoch') or
  DATE(ts/1000, 'unixepoch').
- Tables may have a soft-delete flag (e.g. is_deleted). Decide whether deleted rows should be excluded based on
  the question's wording.
- There may be multiple similar tables (e.g. jobs vs jobs_v1). Check which is canonical for the question.