#!/usr/bin/env python3
"""
Convert ATT base (32 per-state .xlsx) to a consolidated Parquet + DuckDB.

Source : /home/sebastianvernis/Descargas/Bases/att_completa (1)/*.xlsx
Outputs:
  /home/sebastianvernis/Descargas/Bases/att.duckdb           single table `att`
  /home/sebastianvernis/Descargas/Bases/att_consolidado.parquet
  /home/sebastianvernis/Descargas/Bases/att_por_estado/*.parquet (per-file)

Run with the project venv (openpyxl, pyarrow, duckdb are present there).
"""
from __future__ import annotations

import glob
import os
import re
import sys
import time
from pathlib import Path

import openpyxl
import pyarrow as pa
import pyarrow.parquet as pq
import duckdb

BASE_DIR = Path("/home/sebastianvernis/Descargas/Bases")
SRC_DIR = BASE_DIR / "att_completa (1)"
DUCKDB_PATH = BASE_DIR / "att.duckdb"
PARQUET_FULL = BASE_DIR / "att_consolidado.parquet"
PARQUET_DIR = BASE_DIR / "att_por_estado"

# Filename → 2-letter INEGI clave (the ESTADO column already has these,
# but we also keep the human-readable origin for traceability).
ESTADO_NOMBRES = {
    "AGUASCUALIENTES": "AGUASCALIENTES",
    "BAJA CALIFORNIA NORTE": "BAJA CALIFORNIA",
    "BAJA CALIFORNIA SUR": "BAJA CALIFORNIA SUR",
    "CAMPECHE": "CAMPECHE",
    "CDMX": "CIUDAD DE MEXICO",
    "CHIAPAS": "CHIAPAS",
    "CHIHUAHUA": "CHIHUAHUA",
    "COAHUILA": "COAHUILA",
    "COLIMA": "COLIMA",
    "DURANGO": "DURANGO",
    "ESTADO DE MEXICO": "MEXICO",
    "GUANAJUATO": "GUANAJUATO",
    "GUERRERO": "GUERRERO",
    "HIDALGO": "HIDALGO",
    "JALISCO": "JALISCO",
    "MICHOACAN": "MICHOACAN",
    "MORELOS": "MORELOS",
    "NAYARIT": "NAYARIT",
    "NUEVO LEON": "NUEVO LEON",
    "OAXACA": "OAXACA",
    "PUEBLA": "PUEBLA",
    "QUERETARO": "QUERETARO",
    "QUINTANA ROO": "QUINTANA ROO",
    "SAN LUIS POTOSI": "SAN LUIS POTOSI",
    "SINALOA": "SINALOA",
    "SONORA": "SONORA",
    "TABASCO": "TABASCO",
    "TAMAULIPAS": "TAMAULIPAS",
    "TLAXCALA": "TLAXCALA",
    "VERACRUZ": "VERACRUZ",
    "YUCATAN": "YUCATAN",
    "ZACATECAS": "ZACATECAS",
}

# Schema (locked once seen in the first file). All lowercased names.
SCHEMA = [
    "nombres", "pat", "may", "nombre", "rfc", "tel1", "celular",
    "direccion", "interior", "exterior", "colonia", "municipio", "estado",
]
# Append our provenance columns.
EXTRA = ["estado_origen", "archivo_origen"]
FINAL_SCHEMA = SCHEMA + EXTRA

ARROW_TYPE = {
    "nombres":        pa.string(),
    "pat":            pa.string(),
    "may":            pa.string(),
    "nombre":         pa.string(),
    "rfc":            pa.string(),
    "tel1":           pa.string(),  # original sheets mix int + floats → keep string
    "celular":        pa.string(),
    "direccion":      pa.string(),
    "interior":       pa.string(),
    "exterior":       pa.string(),
    "colonia":        pa.string(),
    "municipio":      pa.string(),
    "estado":         pa.string(),
    "estado_origen":  pa.string(),
    "archivo_origen": pa.string(),
}


def clean_filename(name: str) -> str:
    """Strip any ' (1)' / ' (2)' appendix that OneDrive adds to duplicates."""
    name = re.sub(r"\s*\(\d+\)$", "", name)
    return name.strip().upper()


def file_to_estado(filename: str) -> str:
    base = clean_filename(Path(filename).stem)
    if base in ESTADO_NOMBRES:
        return ESTADO_NOMBRES[base]
    # Fuzzy fallback: longest key prefix that matches the start of `base`.
    for k in sorted(ESTADO_NOMBRES, key=len, reverse=True):
        if base.startswith(k):
            return ESTADO_NOMBRES[k]
    return base


def _coerce(value):
    """openpyxl returns ints/floats/None/dates; collapse everything to string|None."""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        # Phone numbers: keep digits as plain string (4499710053 -> "4499710053").
        return str(int(value))
    if isinstance(value, (int, float)):
        return str(value)
    return str(value).strip() or None


def stream_xlsx(path: Path):
    """Yield rows as dicts in the final schema."""
    estado_full = file_to_estado(path.stem)
    fname = path.name
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        header = None
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == 0:
                header = [str(c).strip().lower() if c else f"col{j}" for j, c in enumerate(row)]
                # Sanity check vs. SCHEMA.
                if header[:13] != SCHEMA:
                    print(f"  ⚠  {path.name}: header differs → {header[:13]}", file=sys.stderr)
                continue
            # Pad/truncate to 13 cols.
            row = list(row) + [None] * (13 - len(row))
            row = row[:13]
            record = {SCHEMA[j]: _coerce(row[j]) for j in range(13)}
            record["estado_origen"] = estado_full
            record["archivo_origen"] = fname
            yield record
    finally:
        wb.close()


def main() -> int:
    if not SRC_DIR.is_dir():
        print(f"ERROR: source dir missing: {SRC_DIR}", file=sys.stderr)
        return 1

    files = sorted(SRC_DIR.glob("*.xlsx"))
    if not files:
        print(f"ERROR: no xlsx files in {SRC_DIR}", file=sys.stderr)
        return 1
    print(f"Found {len(files)} xlsx files in {SRC_DIR}")

    PARQUET_DIR.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    total_rows = 0
    per_state = {}
    consolidated_writer = None  # ParquetWriter for the full file

    try:
        for fpath in files:
            ft0 = time.time()
            estado = file_to_estado(fpath.stem)
            rows = []
            file_rows = 0
            for r in stream_xlsx(fpath):
                rows.append([r[col] for col in FINAL_SCHEMA])
                file_rows += 1
                total_rows += 1

            if file_rows == 0:
                print(f"  {fpath.name}: empty, skipping")
                continue

            tbl = pa.Table.from_pydict(
                {FINAL_SCHEMA[i]: [r[i] for r in rows] for i in range(len(FINAL_SCHEMA))},
            ) if False else pa.Table.from_arrays(
                [pa.array([r[i] for r in rows], type=ARROW_TYPE[FINAL_SCHEMA[i]])
                 for i in range(len(FINAL_SCHEMA))],
                names=FINAL_SCHEMA,
            )

            # Per-state parquet.
            safe_name = re.sub(r"[^A-Z0-9]+", "_", estado.upper()).strip("_")
            per_state_path = PARQUET_DIR / f"att_{safe_name}.parquet"
            pq.write_table(tbl, per_state_path, compression="snappy")
            per_state[estado] = (file_rows, per_state_path)

            # Write into the consolidated writer.
            if consolidated_writer is None:
                consolidated_writer = pq.ParquetWriter(
                    str(PARQUET_FULL),
                    tbl.schema,
                    compression="snappy",
                )
            consolidated_writer.write_table(tbl)

            dt = time.time() - ft0
            print(f"  [{file_rows:>7d} rows, {dt:5.1f}s] {fpath.name}")

        if consolidated_writer is not None:
            consolidated_writer.close()
    finally:
        if consolidated_writer is not None:
            try:
                consolidated_writer.close()
            except Exception:
                pass

    elapsed = time.time() - t0
    print(f"\nRead all xlsx in {elapsed:.1f}s — {total_rows:,} rows total")

    # Build DuckDB from the consolidated parquet.
    print(f"\nBuilding DuckDB at {DUCKDB_PATH} ...")
    if DUCKDB_PATH.exists():
        DUCKDB_PATH.unlink()
    con = duckdb.connect(str(DUCKDB_PATH))
    try:
        con.execute(f"""
            CREATE TABLE att AS
            SELECT * FROM read_parquet('{PARQUET_FULL}')
        """)
        # DuckDB builds column min/max stats automatically; no ALTER STORAGE here.
        n = con.execute("SELECT COUNT(*) FROM att").fetchone()[0]
        ck = con.execute("""
            SELECT estado_origen, COUNT(*) c
            FROM att GROUP BY 1 ORDER BY 1
        """).fetchall()
        cols = con.execute("PRAGMA table_info('att')").fetchall()
    finally:
        con.close()

    print(f"\nDuckDB rows: {n:,}")
    print(f"Columns ({len(cols)}): {[c[1] for c in cols]}")
    print("\nPer-estado row counts:")
    for k, v in sorted(per_state.items()):
        print(f"  {v[0]:>7d}  {k}  ({v[1].name})")

    print(f"\nOutputs:")
    print(f"  duckdb : {DUCKDB_PATH}")
    print(f"  parq   : {PARQUET_FULL}")
    print(f"  per-ed : {PARQUET_DIR}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
