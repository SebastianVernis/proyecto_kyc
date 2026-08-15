#!/usr/bin/env python3
"""Carga rápida del padrón INE 2018 a DuckDB usando python-calamine."""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Iterable

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


def discover_files(root: Path) -> list[Path]:
    candidates = sorted(root.glob("*/Excel/*.xlsx"))
    if not candidates and root.name.lower() in ("src", "source"):
        candidates = sorted((root.parent).glob("*/Excel/*.xlsx"))
    return [f for f in candidates if f.is_file()]


def read_xlsx_calamine(path: Path) -> tuple[list[dict] | None, list[str] | None]:
    try:
        wb = calamine.load_workbook(str(path))
        sheet = wb.get_sheet_by_name(wb.sheet_names[0])
        rows = sheet.to_python()
    except Exception as e:
        print(f"  ERROR abriendo {path.name}: {e}", file=sys.stderr)
        return None, None
    if not rows:
        return None, None
    header = [str(c).strip().upper() if c is not None else "" for c in rows[0]]
    idx = {h: i for i, h in enumerate(header) if h}
    faltan = [c for c in ("NOMBRE", "PATERNO", "MATERNO", "CURP") if c not in idx]
    if faltan:
        print(f"  AVISO {path.name}: faltan columnas {faltan} -- saltando", file=sys.stderr)
        return None, None

    def get(row, col, default=None):
        i = idx.get(col)
        return row[i] if i is not None and i < len(row) else default

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
    return out, header


def _parse_fecnac(v):
    if v is None:
        return None
    if isinstance(v, (datetime, date)):
        return v.strftime("%Y-%m-%d")
    sv = str(v).strip()
    if not sv or sv.lower() in ("nan", "none", "nat"):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
                "%d/%m/%Y", "%m/%d/%Y", "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(sv[:len(fmt)], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    try:
        f = float(sv)
        if 1 < f < 80000:
            return (datetime(1899, 12, 30) + timedelta(days=f)).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        pass
    return None


def clean_rows(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=COLS)
    for tcol in ("cve", "nombre", "paterno", "materno", "sexo",
                 "calle", "int", "ext", "colonia", "curp"):
        df[tcol] = df[tcol].astype(str).str.strip().str.upper().replace(["", "NAN", "NONE", "NAT"], None)
    for icol in ("cp", "e", "d", "m", "s", "l", "mza", "consec", "cred", "nac", "folio"):
        df[icol] = pd.to_numeric(df[icol], errors="coerce")
        df[icol] = df[icol].where(df[icol].notna() & (df[icol] < 1e18)).astype("Int64")
    df["fecnac"] = df["fecnac"].apply(_parse_fecnac)
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
            cp INTEGER,
            e INTEGER,
            d INTEGER,
            m INTEGER,
            s INTEGER,
            l INTEGER,
            mza INTEGER,
            consec INTEGER,
            cred INTEGER,
            folio BIGINT,
            nac INTEGER,
            curp VARCHAR,
            origen VARCHAR,
            UNIQUE (curp, folio, origen)
        )
    """)
    con.execute("CREATE SEQUENCE IF NOT EXISTS seq_id START 1")
    max_id = con.execute("SELECT COALESCE(MAX(id), 0) FROM padron").fetchone()[0]
    if max_id > 0:
        cur = con.execute("SELECT nextval('seq_id')").fetchone()[0]
        if cur < max_id + 1:
            con.execute(f"ALTER SEQUENCE seq_id RESTART WITH {max_id + 1}")


def insert_chunk(con, df: pd.DataFrame, origen: str):
    if df.empty:
        return
    con.register("tmp_chunk", df)
    con.execute("""
        INSERT INTO padron (id, cve, nombre, paterno, materno, fecnac, sexo,
                            calle, "int", "ext", colonia, cp, e, d, m, s, l, mza,
                            consec, cred, folio, nac, curp, origen)
        SELECT nextval('seq_id') AS id, *, ? AS origen FROM tmp_chunk
    """, [origen])
    con.unregister("tmp_chunk")


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
    ap = argparse.ArgumentParser(description="Normaliza padrón INE 2018 a DuckDB (rápido)")
    ap.add_argument("--db", default=str(DB_PATH), help="Path al .duckdb")
    ap.add_argument("--root", default=str(ROOT), help="Directorio raíz con carpetas de estados")
    ap.add_argument("--rebuild", action="store_true", help="Borra la DB y reconstruye desde cero")
    ap.add_argument("--no-index", action="store_true", help="No crear índices al final")
    ap.add_argument("--chunk", type=int, default=200_000, help="Tamaño de chunk para flush")
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
        rows, _ = read_xlsx_calamine(f)
        if rows is None:
            print("saltado")
            continue
        # Procesar en chunks de filas
        for i in range(0, len(rows), args.chunk):
            chunk = rows[i:i + args.chunk]
            df = clean_rows(chunk)
            insert_chunk(con, df, str(f))
            total_new += len(df)
        dt = time.time() - t1
        print(f"{len(rows):,} filas ({dt:.1f}s, {len(rows)/max(dt,0.001):,.0f}/s)")

    print("Deduplicando por (curp, folio) global...")
    before = con.execute("SELECT COUNT(*) FROM padron").fetchone()[0]
    con.execute("""
        DELETE FROM padron
        WHERE id NOT IN (
            SELECT MIN(id) FROM padron
            GROUP BY COALESCE(curp, ''), COALESCE(folio, 0)
        )
    """)
    after = con.execute("SELECT COUNT(*) FROM padron").fetchone()[0]
    print(f"  Eliminadas {before - after:,} filas duplicadas")

    if not args.no_index:
        crear_indices(con)

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
