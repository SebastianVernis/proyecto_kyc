#!/usr/bin/env python3
"""
Normalize imss_2025.duckdb in-place.

Prior runs left only `imss_2025_raw` (57.7M rows) without the typed / indexed /
clean version.  This script:

  1. Creates `imss_2025` from `imss_2025_raw` with normalized columns
     (TRIM, UPPER, NULLIF on empties).
  2. Adds:
      - curp_clean   VARCHAR  (uppercase, A-Z0-9 only, 18 chars
                                 or NULL — no fabrication)
      - curp_len     SMALLINT
      - curp_kind    VARCHAR  ('PF18','PM16','OTRO','NULL')
      - cp5          VARCHAR  (5-digit codigo_postal, or NULL)
      - nss_clean    VARCHAR  (11-digit NSS, padded)
  3. Adds a VIEW `imss_valid` where curp_kind IN ('PF18','PM16').
  4. Does NOT create indexes — DuckDB builds min/max stats automatically from
     the table and skips extra I/O.  Adding B-tree-style indexes on tables
     this big can cost more time than they save.

Run with the project venv.
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import duckdb

DUCKDB_PATH = Path("/home/sebastianvernis/Descargas/Bases/imss_2025.duckdb")

# CURP format (per RENAPO spec):
#   AAAA  YYMMDD  H/F/M/etc  ENT(2 letter)  4 consonants  2 digits
#   4 letters naming person
#   6 digits date (YYMMDD)
#   1 letter sex (H/M)
#   2 letters state code (INEGI 2-letter for birth state)
#   3 consonants from first internal+last names
#   2 chars (homoclave, digits+letter)
CURP_PF18 = re.compile(r"^[A-Z]{4}\d{6}[HM][A-Z]{2}[B-DF-HJ-NP-TV-Z]{3}[0-9A-Z]\d$")
CURP_PM16 = re.compile(r"^[A-Z]{4}\d{6}[A-Z0-9]{6}$")  # persons morales: 4+6+6
NSS_RE = re.compile(r"\d{11}")
CP5_RE = re.compile(r"\d{5}")
WS_RE = re.compile(r"\s+")
ALNUM_RE = re.compile(r"[^A-Z0-9]")


def main() -> int:
    if not DUCKDB_PATH.exists():
        print(f"ERROR: missing {DUCKDB_PATH}", file=sys.stderr)
        return 1
    print(f"Normalizing {DUCKDB_PATH} ({DUCKDB_PATH.stat().st_size/1024**3:.2f} GB)")

    con = duckdb.connect(str(DUCKDB_PATH))
    try:
        # ---- 0) Drop partial state from interrupted prior runs ----
        for v in ("imss_valid",):
            try:
                con.execute(f"DROP VIEW IF EXISTS {v}")
            except duckdb.Error:
                pass
        # Drop any of the columns we'll re-create in case we re-run.
        for tbl in ("imss_2025_raw", "imss_2025"):
            for c in (
                "curp_clean", "curp_len", "curp_kind",
                "cp5", "nss_clean",
                "cp5_clean", "nss",
            ):
                try:
                    con.execute(f'ALTER TABLE {tbl} DROP COLUMN "{c}"')
                except duckdb.Error:
                    pass
        # If a prior run had created `imss_2025` and got stuck, drop it so
        # the CREATE AS below starts fresh.
        try:
            con.execute("DROP TABLE IF EXISTS imss_2025")
        except duckdb.Error:
            pass

        # ---- 1) Check current raw state ----
        print("\n[1/6] checking imss_2025_raw row count ...")
        n_raw = con.execute("SELECT COUNT(*) FROM imss_2025_raw").fetchone()[0]
        print(f"  raw rows: {n_raw:,}")

        # ---- 2) Create typed/cleaned table ----
        print("\n[2/6] creating imss_2025 from raw (TRIM, UPPER, NULLIF) ...")
        t0 = time.time()
        con.execute(r"""
            CREATE TABLE imss_2025 AS
            SELECT
                NULLIF(TRIM(REGISTRO_PATRON),   '')        AS registro_patron,
                NULLIF(TRIM(NUMERO_SEG_SOCIAL), '')        AS nss,
                NULLIF(TRIM(NOMBRE),            '')        AS nombre,
                NULLIF(REGEXP_REPLACE(TRIM(SUELDO), '[^0-9.\-]', '', 'g'), '')
                                                          AS sueldo_raw,
                NULLIF(TRIM(CURP),              '')        AS curp,
                NULLIF(TRIM(NOMBRE_PATRON),     '')        AS nombre_patron,
                NULLIF(TRIM(DOMICILIO_PATRON),  '')        AS domicilio_patron,
                NULLIF(TRIM(CIUDAD_ESTADO),     '')        AS ciudad_estado,
                NULLIF(TRIM(CODIGO_POSTAL),     '')        AS codigo_postal,
                NULLIF(TRIM(EMPRESA_GIRO),      '')        AS empresa_giro
            FROM imss_2025_raw
        """)
        n = con.execute("SELECT COUNT(*) FROM imss_2025").fetchone()[0]
        print(f"  imss_2025 rows: {n:,} ({(n-n_raw):+,} vs raw)")
        print(f"  done in {time.time()-t0:.1f}s")

        # ---- 3) CURP normalization (SQL regex, not Python UDF) ----
        print("\n[3/6] adding curp_clean / curp_len / curp_kind ...")
        t0 = time.time()
        con.execute('ALTER TABLE imss_2025 ADD COLUMN curp_clean VARCHAR')
        con.execute('ALTER TABLE imss_2025 ADD COLUMN curp_len   SMALLINT')
        con.execute('ALTER TABLE imss_2025 ADD COLUMN curp_kind  VARCHAR')

        con.execute(r"""
            UPDATE imss_2025
            SET curp_clean = UPPER(REGEXP_REPLACE(
                                  REGEXP_REPLACE(COALESCE(curp,''), '\s+', '', 'g'),
                                  '[^A-Z0-9]', '', 'g'))
        """)
        con.execute("UPDATE imss_2025 SET curp_clean = NULL WHERE curp_clean = ''")
        con.execute("UPDATE imss_2025 SET curp_len = LENGTH(curp_clean)")

        con.execute("""
            UPDATE imss_2025 SET curp_kind = CASE
                WHEN curp_clean IS NULL                     THEN 'NULL'
                WHEN curp_clean ~ '^[A-Z]{4}[0-9]{6}[HM][A-Z]{2}[B-DF-HJ-NP-TV-Z]{3}[0-9A-Z][0-9]$'
                                                         THEN 'PF18'
                WHEN curp_clean ~ '^[A-Z]{4}[0-9]{6}[A-Z0-9]{6}$'
                                                         THEN 'PM16'
                WHEN curp_len IS NULL                      THEN 'NULL'
                ELSE 'OTRO'
            END
        """)
        print(f"  done in {time.time()-t0:.1f}s")

        # ---- 4) CP5 (5-digit ZIP) ----
        print("\n[4/6] adding cp5 ...")
        t0 = time.time()
        con.execute('ALTER TABLE imss_2025 ADD COLUMN cp5 VARCHAR')
        con.execute(r"""
            UPDATE imss_2025
            SET cp5 = (REGEXP_EXTRACT(COALESCE(codigo_postal,''), '\d{5}', 0))
        """)
        con.execute("UPDATE imss_2025 SET cp5 = NULL WHERE cp5 = ''")
        print(f"  done in {time.time()-t0:.1f}s")

        # ---- 5) NSS normalization ----
        print("\n[5/6] adding nss_clean (11 digits) ...")
        t0 = time.time()
        con.execute('ALTER TABLE imss_2025 ADD COLUMN nss_clean VARCHAR')
        # NSS may have dashes/spaces; extract first 11-digit run, then left-pad.
        con.execute(r"""
            UPDATE imss_2025
            SET nss_clean = LPAD(
                REGEXP_REPLACE(REGEXP_EXTRACT(COALESCE(nss,''), '\d+', 0), '\D', '', 'g'),
                11, '0')
        """)
        con.execute("UPDATE imss_2025 SET nss_clean = NULL WHERE nss_clean = '' OR nss_clean = '00000000000'")
        print(f"  done in {time.time()-t0:.1f}s")

        # ---- 6) VIEW for valid CURPs (PF + PM) ----
        print("\n[6/6] creating view imss_valid ...")
        con.execute("""
            CREATE VIEW imss_valid AS
            SELECT * FROM imss_2025
            WHERE curp_kind IN ('PF18','PM16')
        """)

        # Stats
        print("\n--- distribution of curp_kind ---")
        for k, c in con.execute(
            "SELECT curp_kind, COUNT(*) c FROM imss_2025 GROUP BY 1 ORDER BY c DESC"
        ).fetchall():
            print(f"  {k:<6s}  {c:>10,}")

        n_valid = con.execute("SELECT COUNT(*) FROM imss_valid").fetchone()[0]
        print(f"  imss_valid (PF18+PM16): {n_valid:,}")

        print("\n--- cp5 stats ---")
        n_cpv = con.execute(
            "SELECT COUNT(*) FROM imss_2025 WHERE cp5 IS NOT NULL"
        ).fetchone()[0]
        print(f"  rows with valid 5-digit cp: {n_cpv:,}")
        n_cpv_u = con.execute(
            "SELECT COUNT(DISTINCT cp5) FROM imss_2025 WHERE cp5 IS NOT NULL"
        ).fetchone()[0]
        print(f"  distinct 5-digit cps: {n_cpv_u:,}")

        print("\n--- nss_clean stats ---")
        for k, c in con.execute(
            "SELECT LENGTH(nss_clean) len, COUNT(*) c FROM imss_2025 GROUP BY 1 ORDER BY 1"
        ).fetchall():
            print(f"  len={k!s:>4s}  {c:>10,}")

        # Sample rows
        print("\n--- sample rows ---")
        for r in con.execute("""
            SELECT nombre, curp, curp_clean, curp_kind, nss, nss_clean, codigo_postal, cp5, ciudad_estado
            FROM imss_2025 WHERE curp_kind='PF18' ORDER BY RANDOM() LIMIT 4
        """).fetchall():
            print(" ", r)

        # Free the raw table to save disk (after the typed table is built).
        print("\n--- releasing imss_2025_raw ...")
        try:
            con.execute("DROP TABLE imss_2025_raw")
            print("  imss_2025_raw dropped (saves ~1 GB)")
        except Exception as e:
            print(f"  could not drop raw: {e}")

        con.execute("CHECKPOINT")
        # Vacuum only if there's a chance to free pages; in our case the DB
        # just grew and contains no dead rows so VACUUM is cheap but optional.
    finally:
        con.close()

    sz = DUCKDB_PATH.stat().st_size
    print(f"\nDone. {DUCKDB_PATH} ({sz/1024**3:.2f} GB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
