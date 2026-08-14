#!/usr/bin/env python3
"""
Normalize the copied imss_segmentacion.duckdb in place.

Adds:
  - curp_clean   VARCHAR  (uppercase A-Z0-9, 18 chars or NULL)
  - curp_len     SMALLINT
  - curp_kind    VARCHAR  ('PF18','PM16','OTRO','NULL')
  - nss_clean    VARCHAR  (11 digits, LPAD)
  - rfc_clean    VARCHAR  (uppercase A-Z0-9Ñ&, 10/12/13 chars)
  - rfc_kind     VARCHAR  ('PF13','PM12','PF10','OTRO','NULL')

Creates view: imss_personas_valid (rows with curp_kind IN (PF18,PM16)).

This script edits the COPY at bases_consolidadas/imss_segmentacion/duckdb/
and does NOT touch the originals in /proyectos/imss-normalizer/out/.
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import duckdb

DUCKDB_PATH = Path("/home/sebastianvernis/Descargas/Bases/bases_consolidadas/imss_segmentacion/duckdb/imss_segmentacion.duckdb")
TBL = "imss_personas"

CURP_PF18_RE = r"^[A-Z]{4}[0-9]{6}[HM][A-Z]{2}[B-DF-HJ-NP-TV-Z]{3}[0-9A-Z][0-9]$"
CURP_PM16_RE = r"^[A-Z]{4}[0-9]{6}[A-Z0-9]{6}$"


def main() -> int:
    if not DUCKDB_PATH.exists():
        print(f"ERROR: missing {DUCKDB_PATH}", file=sys.stderr)
        return 1

    print(f"Normalizing {DUCKDB_PATH} ({DUCKDB_PATH.stat().st_size/1024**3:.2f} GB)")

    # Make sure temp dir exists
    tmp_dir = DUCKDB_PATH.parent / "_tmp"
    tmp_dir.mkdir(exist_ok=True)

    con = duckdb.connect(str(DUCKDB_PATH))
    try:
        con.execute(f"PRAGMA temp_directory = '{tmp_dir}'")
        # Drop prior state in case this script is re-run.
        for v in (f"{TBL}_valid",):
            try: con.execute(f"DROP VIEW IF EXISTS {v}")
            except duckdb.Error: pass

        # Drop ALL indexes that depend on columns we want to drop.
        for idx_row in con.execute(
            f"SELECT index_name FROM duckdb_indexes() WHERE table_name = '{TBL}'"
        ).fetchall():
            idx_name = idx_row[0]
            try:
                con.execute(f"DROP INDEX IF EXISTS \"{idx_name}\"")
                print(f"  dropped index {idx_name}")
            except duckdb.Error as e:
                print(f"  could not drop {idx_name}: {e}")

        # Drop columns only if they exist.
        existing_cols = {
            r[0] for r in con.execute(
                f"SELECT column_name FROM duckdb_columns() WHERE table_name = '{TBL}'"
            ).fetchall()
        }
        DROP_COLS = ("curp_clean", "curp_len", "curp_kind",
                     "nss_clean", "rfc_clean", "rfc_kind")
        for c in DROP_COLS:
            if c in existing_cols:
                try:
                    con.execute(f'ALTER TABLE {TBL} DROP COLUMN "{c}"')
                    print(f"  dropped column {c}")
                except duckdb.Error as e:
                    print(f"  could not drop {c}: {e}")

        n = con.execute(f"SELECT COUNT(*) FROM {TBL}").fetchone()[0]
        print(f"  rows: {n:,}")

        print("\n[1/3] curp_clean / curp_len / curp_kind")
        t0 = time.time()
        con.execute(f'ALTER TABLE {TBL} ADD COLUMN curp_clean VARCHAR')
        con.execute(f'ALTER TABLE {TBL} ADD COLUMN curp_len   SMALLINT')
        con.execute(f'ALTER TABLE {TBL} ADD COLUMN curp_kind  VARCHAR')

        con.execute(f"""
            UPDATE {TBL}
            SET curp_clean = UPPER(REGEXP_REPLACE(
                                  REGEXP_REPLACE(COALESCE(curp,''), '\s+', '', 'g'),
                                  '[^A-Z0-9]', '', 'g'))
        """)
        con.execute(f"UPDATE {TBL} SET curp_clean = NULL WHERE curp_clean = ''")
        con.execute(f"UPDATE {TBL} SET curp_len = LENGTH(curp_clean)")
        con.execute(f"""
            UPDATE {TBL} SET curp_kind = CASE
                WHEN curp_clean IS NULL THEN 'NULL'
                WHEN curp_clean ~ '{CURP_PF18_RE}' THEN 'PF18'
                WHEN curp_clean ~ '{CURP_PM16_RE}' THEN 'PM16'
                ELSE 'OTRO'
            END
        """)
        print(f"  done in {time.time()-t0:.1f}s")

        print("\n[2/3] nss_clean (11 digits, LPAD)")
        t0 = time.time()
        con.execute(f'ALTER TABLE {TBL} ADD COLUMN nss_clean VARCHAR')
        con.execute(f"""
            UPDATE {TBL}
            SET nss_clean = LPAD(
                REGEXP_REPLACE(REGEXP_EXTRACT(COALESCE(nss::VARCHAR,''), '\d+', 0), '\D', '', 'g'),
                11, '0')
        """)
        con.execute(f"UPDATE {TBL} SET nss_clean = NULL WHERE nss_clean = '' OR nss_clean = '00000000000'")
        print(f"  done in {time.time()-t0:.1f}s")

        print("\n[3/3] rfc_clean / rfc_kind")
        t0 = time.time()
        con.execute(f'ALTER TABLE {TBL} ADD COLUMN rfc_clean VARCHAR')
        con.execute(f'ALTER TABLE {TBL} ADD COLUMN rfc_kind  VARCHAR')
        con.execute(rf"""
            UPDATE {TBL}
            SET rfc_clean = UPPER(REGEXP_REPLACE(
                                  REGEXP_REPLACE(COALESCE(rfc, ''), '\s+', '', 'g'),
                                  '[^A-Z0-9Ñ&]', '', 'i'))
        """)
        con.execute(f"UPDATE {TBL} SET rfc_clean = NULL WHERE rfc_clean = ''")
        con.execute(f"""
            UPDATE {TBL} SET rfc_kind = CASE
                WHEN rfc_clean IS NULL THEN 'NULL'
                WHEN rfc_clean ~ '^[A-ZÑ&]{{4}}[0-9]{{6}}[A-Z0-9Ñ&]{{3}}$' THEN 'PF13'
                WHEN rfc_clean ~ '^[A-ZÑ&]{{4}}[0-9]{{6}}[A-Z0-9Ñ&]{{2}}$' THEN 'PM12'
                WHEN rfc_clean ~ '^[A-ZÑ&]{{4}}[0-9]{{6}}$' THEN 'PF10'
                ELSE 'OTRO'
            END
        """)
        print(f"  done in {time.time()-t0:.1f}s")

        con.execute(f"""
            CREATE VIEW {TBL}_valid AS
            SELECT * FROM {TBL}
            WHERE curp_kind IN ('PF18','PM16')
        """)

        # Stats
        print("\n--- curp_kind distribution ---")
        for k, c in con.execute(f"SELECT curp_kind, COUNT(*) c FROM {TBL} GROUP BY 1 ORDER BY c DESC").fetchall():
            print(f"  {k:<6s}  {c:>10,}")
        n_valid = con.execute(f"SELECT COUNT(*) FROM {TBL}_valid").fetchone()[0]
        print(f"  valid (PF18+PM16): {n_valid:,}")

        print("\n--- rfc_kind distribution ---")
        for k, c in con.execute(f"SELECT rfc_kind, COUNT(*) c FROM {TBL} GROUP BY 1 ORDER BY c DESC").fetchall():
            print(f"  {k:<6s}  {c:>10,}")

        # Drop old ART indexes if present (imss-normalizer tried to create them
        # but hit "already exists" — they were on the raw columns, not the
        # normalized ones; we'll re-create on the clean columns).
        print("\n--- indexes (drop old, create new on clean columns) ---")
        try:
            for nm in ('idx_curp', 'idx_nss', 'idx_rfc'):
                con.execute(f'DROP INDEX IF EXISTS {nm}')
        except duckdb.Error:
            pass
        for col in ('curp_clean', 'nss_clean', 'rfc_clean', 'id_persona', 'ooad'):
            try:
                con.execute(f'CREATE INDEX idx_seg_{col} ON {TBL} ("{col}");')
                print(f"  idx_seg_{col} created")
            except duckdb.Error as e:
                print(f"  idx_seg_{col}: {e}")

        con.execute("CHECKPOINT")
    finally:
        con.close()

    sz = DUCKDB_PATH.stat().st_size
    print(f"\nDone. {DUCKDB_PATH} ({sz/1024**3:.2f} GB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
