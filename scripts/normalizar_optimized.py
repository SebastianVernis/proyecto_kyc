#!/usr/bin/env python3
"""Carga robusta y rápida del padrón INE 2018 a DuckDB.

Estrategia:
  - python-calamine para lectura rápida de .xlsx.
  - pandas para limpieza vectorizada.
  - INSERT por archivo completo usando con.register() + SQL.
  - Sin UNIQUE constraints durante carga (evita lentitud con archivos grandes).
  - COMMIT por archivo (cada archivo es una transacción independiente).
  - Índices simples al final.
  - Resistente a suspensiones: si se interrumpe, se puede reanudar (modo incremental).
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, date
from pathlib import Path

import duckdb
import pandas as pd

try:
    import python_calamine as calamine
    HAS_CALAMINE = True
except Exception:
    HAS_CALAMINE = False

ROOT = Path(__file__).parent.resolve()
DB_PATH = ROOT / "ine.duckdb"

COLS = [
    "cve", "nombre", "paterno", "materno", "fecnac", "sexo",
    "calle", "int", "ext", "colonia", "cp", "e", "d", "m",
    "s", "l", "mza", "consec", "cred", "folio", "nac", "curp",
]
TEXT_COLS = ("cve", "nombre", "paterno", "materno", "sexo",
             "calle", "int", "ext", "colonia", "curp")
INT_COLS = ("cp", "e", "d", "m", "s", "l", "mza", "consec", "cred", "nac")
FOLIO_COL = "folio"


def discover_files(root: Path) -> list[Path]:
    candidates = sorted(root.glob("*/Excel/*.xlsx"))
    if not candidates and root.name.lower() in ("src", "source"):
        candidates = sorted((root.parent).glob("*/Excel/*.xlsx"))
    return [f for f in candidates if f.is_file()]


def read_xlsx(path: Path) -> list[dict]:
    wb = calamine.load_workbook(str(path))
    sheet = wb.get_sheet_by_name(wb.sheet_names[0])
    rows = sheet.to_python()
    if not rows:
        return []
    header = [str(c).strip().upper() if c is not None else "" for c in rows[0]]
    idx = {h: i for i, h in enumerate(header) if h}
    faltan = [c for c in ("NOMBRE", "PATERNO", "MATERNO", "CURP") if c not in idx]
    if faltan:
        raise ValueError(f"faltan columnas {faltan}")

    def get(row, col, default=None):
        i = idx.get(col)
        if i is None or i >= len(row):
            return default
        v = row[i]
        if v is None:
            return default
        if isinstance(v, (datetime, date)):
            return v.strftime("%Y-%m-%d")
        return v

    out = []
    for row in rows[1:]:
        if row is None:
            continue
        out.append({
            "cve": get(row, "CVE"),
            "nombre": get(row, "NOMBRE"),
            "paterno": get(row, "PATERNO"),
            "materno": get(row, "MATERNO"),
            "fecnac": get(row, "FECNAC"),
            "sexo": get(row, "SEXO"),
            "calle": get(row, "CALLE"),
            "int": get(row, "INT"),
            "ext": get(row, "EXT"),
            "colonia": get(row, "COLONIA"),
            "cp": get(row, "CP"),
            "e": get(row, "E"),
            "d": get(row, "D"),
            "m": get(row, "M"),
            "s": get(row, "S"),
            "l": get(row, "L"),
            "mza": get(row, "MZA"),
            "consec": get(row, "CONSEC"),
            "cred": get(row, "CRED"),
            "folio": get(row, "FOLIO"),
            "nac": get(row, "NAC"),
            "curp": get(row, "CURP"),
        })
    return out


def _parse_fecnac_series(s: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(s, errors="coerce", format="%Y-%m-%d", exact=False)
    numeric = pd.to_numeric(s, errors="coerce")
    excel_dates = pd.Series([pd.NaT] * len(s), index=s.index)
    mask = numeric.notna() & (numeric > 1) & (numeric < 80000)
    if mask.any():
        excel_dates[mask] = pd.to_datetime(numeric[mask], unit="D", origin="1899-12-30")
    combined = parsed.combine_first(excel_dates)
    return combined.dt.strftime("%Y-%m-%d").where(combined.notna(), None)


def clean_rows(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=COLS)
    for c in TEXT_COLS:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip().str.upper()
            df[c] = df[c].replace({"": pd.NA, "NAN": pd.NA, "NONE": pd.NA, "NAT": pd.NA})
    for c in INT_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
            df[c] = df[c].where(df[c].notna() & (df[c] < 9e18)).astype("Int64")
    if FOLIO_COL in df.columns:
        df[FOLIO_COL] = pd.to_numeric(df[FOLIO_COL], errors="coerce")
        df[FOLIO_COL] = df[FOLIO_COL].where(df[FOLIO_COL].notna() & (df[FOLIO_COL] < 9e18)).astype("Int64")
    if "fecnac" in df.columns:
        df["fecnac"] = _parse_fecnac_series(df["fecnac"])
    return df


def ensure_schema(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS padron (
            id BIGINT PRIMARY KEY,
            cve VARCHAR,
            nombre VARCHAR,
            paterno VARCHAR,
            materno VARCHAR,
            fecnac DATE,
            sexo VARCHAR,
            calle VARCHAR,
            "int" VARCHAR,
            "ext" VARCHAR,
            colonia VARCHAR,
            cp BIGINT,
            e BIGINT,
            d BIGINT,
            m BIGINT,
            s BIGINT,
            l BIGINT,
            mza BIGINT,
            consec BIGINT,
            cred BIGINT,
            folio BIGINT,
            nac BIGINT,
            curp VARCHAR,
            origen VARCHAR
        )
    """)
    con.execute("CREATE SEQUENCE IF NOT EXISTS seq_id START 1")
    max_id = con.execute("SELECT COALESCE(MAX(id), 0) FROM padron").fetchone()[0]
    if max_id > 0:
        cur = con.execute("SELECT nextval('seq_id')").fetchone()[0]
        if cur < max_id + 1:
            con.execute(f"ALTER SEQUENCE seq_id RESTART WITH {max_id + 1}")


def insert_dataframe(con, df: pd.DataFrame, origen: str):
    if df.empty:
        return 0
    con.register("tmp_chunk", df)
    con.execute("""
        INSERT INTO padron (id, cve, nombre, paterno, materno, fecnac, sexo,
                            calle, "int", "ext", colonia, cp, e, d, m, s, l, mza,
                            consec, cred, folio, nac, curp, origen)
        SELECT nextval('seq_id') AS id, *, ? AS origen FROM tmp_chunk
    """, [origen])
    con.unregister("tmp_chunk")
    return len(df)


def already_loaded(con) -> set[str]:
    return {r[0] for r in con.execute("SELECT DISTINCT origen FROM padron").fetchall()}


def crear_indices(con):
    print("Creando índices...")
    sqls = [
        "CREATE INDEX IF NOT EXISTS idx_padron_curp ON padron(curp)",
        "CREATE INDEX IF NOT EXISTS idx_padron_folio ON padron(folio)",
        "CREATE INDEX IF NOT EXISTS idx_padron_cp ON padron(cp)",
        "CREATE INDEX IF NOT EXISTS idx_padron_paterno ON padron(paterno)",
        "CREATE INDEX IF NOT EXISTS idx_padron_materno ON padron(materno)",
        "CREATE INDEX IF NOT EXISTS idx_padron_nombre ON padron(nombre)",
        "CREATE INDEX IF NOT EXISTS idx_padron_fecnac ON padron(fecnac)",
        "CREATE INDEX IF NOT EXISTS idx_padron_origen ON padron(origen)",
        "CREATE INDEX IF NOT EXISTS idx_padron_e ON padron(e)",
        "CREATE INDEX IF NOT EXISTS idx_padron_mza ON padron(mza)",
        "CREATE INDEX IF NOT EXISTS idx_padron_sexo ON padron(sexo)",
        'CREATE INDEX IF NOT EXISTS idx_padron_int ON padron("int")',
        'CREATE INDEX IF NOT EXISTS idx_padron_ext ON padron("ext")',
    ]
    for s in sqls:
        try:
            con.execute(s)
        except Exception as e:
            print(f"  AVISO índice: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="Normaliza padrón INE 2018 a DuckDB (rápido, sin UNIQUE)")
    ap.add_argument("--db", default=str(DB_PATH), help="Path al .duckdb")
    ap.add_argument("--root", default=str(ROOT), help="Directorio raíz con carpetas de estados")
    ap.add_argument("--rebuild", action="store_true", help="Borra la DB y reconstruye desde cero")
    ap.add_argument("--no-index", action="store_true", help="No crear índices al final")
    args = ap.parse_args()

    if not HAS_CALAMINE:
        print("ERROR: python-calamine no está disponible", file=sys.stderr)
        sys.exit(1)

    db_path = Path(args.db)
    root = Path(args.root)
    if not root.exists():
        print(f"ERROR: root no existe: {root}", file=sys.stderr)
        sys.exit(1)

    if args.rebuild and db_path.exists():
        db_path.unlink()
        print(f"DB borrada: {db_path}")

    t0 = time.time()
    con = duckdb.connect(str(db_path))
    ensure_schema(con)
    loaded = already_loaded(con)

    files = discover_files(root)
    if not files:
        print(f"AVISO: no se encontraron .xlsx bajo {root}/<estado>/Excel/*.xlsx", file=sys.stderr)
        sys.exit(0)

    if loaded:
        print(f"Ya cargados: {len(loaded)} archivos. Modo incremental.")
    print(f"Archivos a procesar: {len(files)}")
    t_load = time.time()

    total_new = 0
    for f in files:
        if str(f) in loaded:
            continue
        print(f"  {f.name} ...", end=" ", flush=True)
        t1 = time.time()
        try:
            rows = read_xlsx(f)
        except Exception as e:
            print(f"ERROR leyendo: {e}")
            continue
        if not rows:
            print("vacío")
            continue
        df = clean_rows(rows)
        n = insert_dataframe(con, df, str(f))
        con.commit()
        total_new += n
        dt = time.time() - t1
        print(f"{len(rows):,} filas ({dt:.1f}s, {len(rows)/max(dt,0.001):,.0f}/s)")

    if not args.no_index:
        crear_indices(con)
        con.commit()

    total = con.execute("SELECT COUNT(*) FROM padron").fetchone()[0]
    print()
    print("=" * 60)
    print(f"Total filas: {total:,}")
    print(f"Tiempo carga: {time.time() - t_load:.1f}s")
    print(f"Tiempo total: {time.time() - t0:.1f}s")
    print("=" * 60)

    res = con.execute("""
        SELECT origen, COUNT(*) AS n
        FROM padron
        GROUP BY origen
        ORDER BY n DESC
        LIMIT 10
    """).fetchall()
    for o, n in res:
        print(f"  {n:>12,}  {o}")

    con.close()


if __name__ == "__main__":
    main()
