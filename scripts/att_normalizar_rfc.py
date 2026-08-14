#!/usr/bin/env python3
"""
Normalize the RFC column in att.duckdb.

Source : /home/sebastianvernis/Descargas/Bases/att.duckdb
Outputs:
  /home/sebastianvernis/Descargas/Bases/att.duckdb
      + column `rfc_clean`   VARCHAR  (normalized, 10 or 13 chars, uppercase, A-Z0-9 only)
      + column `rfc_len`     SMALLINT (10 / 12 / 13 / 14 / NULL)
      + column `rfc_kind`    VARCHAR  ('PF10','PM12','PF13','LEGACY14','NULL','OTRO')
      + view    `att_valid`  WHERE rfc_kind IN ('PF10','PM12','PF13')

Normalization rules:
  1. Strip leading/trailing whitespace.
  2. Strip internal whitespace runs to a single separator (we collapse to nothing).
  3. Upper-case, keep [A-Z0-9&Ñ] only.
  4. If the result is exactly 13 chars (4 letters + 6 digits + 3 alnum) → PF + homoclave.
  5. If 12 chars (4 letters + 6 digits + 2 alnum)  → PM12 (2 char homoclave, rare).
  6. If 10 chars (4 letters + 6 digits)            → PF10 (homoclave truncada por fuente).
  7. Otherwise NULL — no synthesizing from partial data (honesty rule).

Run with the project venv.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import duckdb

DUCKDB_PATH = Path("/home/sebastianvernis/Descargas/Bases/att.duckdb")

# Prefilter pattern: only A-Z, 0-9, Ñ & — we re-upper-case afterward.
ALNUM_RE = re.compile(r"[^A-Z0-9Ñ&]")
WS_RE = re.compile(r"\s+")
PF13_RE = re.compile(r"^[A-ZÑ&]{4}\d{6}[A-Z0-9Ñ&]{3}$")
PM12_RE = re.compile(r"^[A-ZÑ&]{4}\d{6}[A-Z0-9Ñ&]{2}$")
PF10_RE = re.compile(r"^[A-ZÑ&]{4}\d{6}$")


def normalize_rfc(value):
    """Return (clean, len, kind)."""
    if value is None:
        return (None, None, "NULL")
    s = WS_RE.sub("", str(value)).upper()  # collapse internal whitespace + upper
    s = ALNUM_RE.sub("", s)               # drop garbage
    if not s:
        return (None, None, "NULL")
    n = len(s)
    if PF13_RE.match(s):
        return (s, 13, "PF13")
    if PM12_RE.match(s):
        return (s, 12, "PM12")
    if PF10_RE.match(s):
        return (s, 10, "PF10")
    if n == 14:
        return (None, None, "LEGACY14")  # many source strings have a space; logged separately
    return (s, n, "OTRO")


def main() -> int:
    if not DUCKDB_PATH.exists():
        print(f"ERROR: missing {DUCKDB_PATH}", file=sys.stderr)
        return 1

    # Open RW so we can ALTER / CREATE VIEW.
    con = duckdb.connect(str(DUCKDB_PATH))
    try:
        # 1) Drop prior objects to keep this script idempotent.
        for v in ("att_valid",):
            con.execute(f"DROP VIEW IF EXISTS {v}")
        for c in ("rfc_clean", "rfc_len", "rfc_kind"):
            try:
                con.execute(f"ALTER TABLE att DROP COLUMN {c}")
            except duckdb.Error:
                pass  # column didn't exist

        print("Step 1/3 - normalizing RFCs ...")
        n_rows = con.execute("SELECT COUNT(*) FROM att").fetchone()[0]
        print(f"  att rows: {n_rows:,}")

        # Add columns.
        print("  adding rfc_clean / rfc_len / rfc_kind columns ...")
        con.execute("ALTER TABLE att ADD COLUMN rfc_clean VARCHAR")
        con.execute("ALTER TABLE att ADD COLUMN rfc_len   SMALLINT")
        con.execute("ALTER TABLE att ADD COLUMN rfc_kind  VARCHAR")

        # Strip non-A-Z0-9Ñ& and collapse whitespace inline.  DuckDB's regex
        # engine does the job in a single fast sweep over the column, which
        # is far faster than a per-row Python UDF for ~1M rows.
        t0 = time.time()
        con.execute(r"""
            UPDATE att
            SET rfc_clean = UPPER(REGEXP_REPLACE(
                                 REGEXP_REPLACE(COALESCE(rfc, ''),
                                                '\s+', '', 'g'),
                                 '[^A-Z0-9Ñ&]', '', 'i'))
        """)
        con.execute("""
            UPDATE att SET rfc_clean = NULL WHERE rfc_clean = ''
        """)
        con.execute("""
            UPDATE att SET rfc_len = LENGTH(rfc_clean)
        """)
        print(f"  rfc_clean + rfc_len written ({time.time()-t0:.1f}s)")

        # Classify: regex match in SQL is much cheaper than Python calls.
        t0 = time.time()
        con.execute("""
            UPDATE att SET rfc_kind = CASE
                WHEN rfc_clean IS NULL THEN 'NULL'
                WHEN rfc_clean ~ '^[A-ZÑ&]{4}[0-9]{6}[A-Z0-9Ñ&]{3}$' THEN 'PF13'
                WHEN rfc_clean ~ '^[A-ZÑ&]{4}[0-9]{6}[A-Z0-9Ñ&]{2}$' THEN 'PM12'
                WHEN rfc_clean ~ '^[A-ZÑ&]{4}[0-9]{6}$' THEN 'PF10'
                WHEN rfc_len  = 14 THEN 'LEGACY14'
                ELSE 'OTRO'
            END
        """)
        print(f"  rfc_kind written ({time.time()-t0:.1f}s)")

        # 2) Stats.
        print("\nStep 2/3 - distribution of rfc_kind:")
        for k, c in con.execute("""
            SELECT rfc_kind, COUNT(*) c
            FROM att GROUP BY 1 ORDER BY c DESC
        """).fetchall():
            print(f"  {k:<10s} {c:>10,}")

        # 3) Convenience view of valid RFCs.
        print("\nStep 3/3 - creating view att_valid (PF10/PM12/PF13) ...")
        con.execute("""
            CREATE VIEW att_valid AS
            SELECT * FROM att WHERE rfc_kind IN ('PF10','PM12','PF13')
        """)
        nv = con.execute("SELECT COUNT(*) FROM att_valid").fetchone()[0]
        print(f"  att_valid rows: {nv:,}")

        # A few diagnostic rows so the user can eyeball.
        print("\nExamples (original vs clean):")
        for orig, clean, kind, lon in con.execute("""
            SELECT rfc, rfc_clean, rfc_kind, rfc_len
            FROM att WHERE rfc_clean IS NOT NULL
            ORDER BY RANDOM() LIMIT 6
        """).fetchall():
            print(f"  '{orig}' -> '{clean}' [{kind}, len={lon}]")

        # Show some legacy rows to confirm we did NOT silently drop them.
        print("\nSample LEGACY14 / OTRO rows (intentionally NOT synthesized):")
        for orig, kind, lon in con.execute("""
            SELECT rfc, rfc_kind, rfc_len
            FROM att WHERE rfc_kind IN ('LEGACY14','OTRO')
            ORDER BY RANDOM() LIMIT 5
        """).fetchall():
            print(f"  '{orig}' [{kind}, len={lon}]")

        # Verify invariants.
        miss = con.execute("""
            SELECT COUNT(*) FROM att WHERE rfc IS NOT NULL AND rfc_clean IS NULL
        """).fetchone()[0]
        ok = con.execute("""
            SELECT COUNT(*) FROM att WHERE rfc_clean IS NOT NULL
        """).fetchone()[0]
        print(f"\nInvariants:")
        print(f"  rows with rfc_clean populated : {ok:,}")
        print(f"  non-null rfc that failed normalize: {miss:,}")

        con.execute("CHECKPOINT")
        con.execute("VACUUM")
    finally:
        con.close()

    sz = DUCKDB_PATH.stat().st_size
    print(f"\nDone. DuckDB at {DUCKDB_PATH} ({sz/1024/1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
