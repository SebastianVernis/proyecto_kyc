#!/usr/bin/env python3
"""Carga rápida del padrón INE 2018 a DuckDB usando python-calamine + CSV COPY."""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import tempfile
import time
from datetime import datetime, date
from pathlib import Path

import duckdb

try:
    import python_calamine as calamine
    HAS_CALAMINE = True
except Exception:
    HAS_CALAMINE = False

ROOT = Path(__file__).parent.resolve()
DB_PATH = ROOT / "ine.duckdb"

HEADER = [
    "cve", "nombre", "paterno", "materno", "fecnac", "sexo",
    "calle", "int", "ext", "colonia", "cp", "e", "d", "m",
    "s", "l", "mza", "consec", "cred", "folio", "nac", "curp",
]


def discover_files(root: Path) -> list[Path]:
    candidates = sorted(root.glob("*/Excel/*.xlsx"))
    if not candidates and root.name.lower() in ("src", "source"):
        candidates = sorted((root.parent).glob("*/Excel/*.xlsx"))
    return [f for f in candidates if f.is_file()]


def read_xlsx(path: Path) -> tuple[list[str], list[tuple]]:
    wb = calamine.load_workbook(str(path))
    sheet = wb.get_sheet_by_name(wb.sheet_names[0])
    rows = sheet.to_python()
    if not rows:
        return [], []
    header = [str(c).strip().upper() if c is not None else "" for c in rows[0]]
    idx = {h: i for i, h in enumerate(header) if h}
    faltan = [c for c in ("NOMBRE", "PATERNO", "MATERNO", "CURP") if c not in idx]
    if faltan:
        raise ValueError(f"faltan columnas {faltan}")

    def get(row, col):
        i = idx.get(col)
        if i is None or i >= len(row):
            return ""
        v = row[i]
        if v is None:
            return ""
        if isinstance(v, (datetime, date)):
            return v.strftime("%Y-%m-%d")
        return str(v)

    out = []
    for row in rows[1:]:
        if row is None:
            continue
        out.append(tuple(get(row, h) for h in HEADER))
    return HEADER, out


def write_csv_tmp(header, rows, tmpdir) -> Path:
    fd, path = tempfile.mkstemp(suffix=".csv", dir=tmpdir, text=True)
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    return Path(path)


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


def load_file(con, xlsx_path: Path, tmpdir: str):
    header, rows = read_xlsx(xlsx_path)
    if not rows:
        return 0
    csv_path = write_csv_tmp(header, rows, tmpdir)
    origen = str(xlsx_path)
    stem = xlsx_path.stem.replace('-','_').replace(' ','_')
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE raw_{stem} AS
        SELECT *
        FROM read_csv('{csv_path.as_posix()}',
                      header=true,
                      columns={{
                          'cve':'VARCHAR','nombre':'VARCHAR','paterno':'VARCHAR','materno':'VARCHAR',
                          'fecnac':'VARCHAR','sexo':'VARCHAR','calle':'VARCHAR','int':'VARCHAR',
                          'ext':'VARCHAR','colonia':'VARCHAR','cp':'VARCHAR','e':'VARCHAR','d':'VARCHAR',
                          'm':'VARCHAR','s':'VARCHAR','l':'VARCHAR','mza':'VARCHAR','consec':'VARCHAR',
                          'cred':'VARCHAR','folio':'VARCHAR','nac':'VARCHAR','curp':'VARCHAR'
                      }})
    """)
    con.execute(f"""
        INSERT INTO padron (id, cve, nombre, paterno, materno, fecnac, sexo,
                            calle, "int", "ext", colonia, cp, e, d, m, s, l, mza,
                            consec, cred, folio, nac, curp, origen)
        SELECT nextval('seq_id') AS id,
               NULLIF(UPPER(TRIM(cve)), '') AS cve,
               NULLIF(UPPER(TRIM(nombre)), '') AS nombre,
               NULLIF(UPPER(TRIM(paterno)), '') AS paterno,
               NULLIF(UPPER(TRIM(materno)), '') AS materno,
               TRY_STRPTIME(NULLIF(TRIM(fecnac), ''), '%Y-%m-%d')::DATE AS fecnac,
               NULLIF(UPPER(TRIM(sexo)), '') AS sexo,
               NULLIF(UPPER(TRIM(calle)), '') AS calle,
               NULLIF(UPPER(TRIM("int")), '') AS "int",
               NULLIF(UPPER(TRIM(ext)), '') AS ext,
               NULLIF(UPPER(TRIM(colonia)), '') AS colonia,
               TRY_CAST(NULLIF(TRIM(cp), '') AS BIGINT) AS cp,
               TRY_CAST(NULLIF(TRIM(e), '') AS BIGINT) AS e,
               TRY_CAST(NULLIF(TRIM(d), '') AS BIGINT) AS d,
               TRY_CAST(NULLIF(TRIM(m), '') AS BIGINT) AS m,
               TRY_CAST(NULLIF(TRIM(s), '') AS BIGINT) AS s,
               TRY_CAST(NULLIF(TRIM(l), '') AS BIGINT) AS l,
               TRY_CAST(NULLIF(TRIM(mza), '') AS BIGINT) AS mza,
               TRY_CAST(NULLIF(TRIM(consec), '') AS BIGINT) AS consec,
               TRY_CAST(NULLIF(TRIM(cred), '') AS BIGINT) AS cred,
               TRY_CAST(NULLIF(TRIM(folio), '') AS BIGINT) AS folio,
               TRY_CAST(NULLIF(TRIM(nac), '') AS BIGINT) AS nac,
               NULLIF(UPPER(TRIM(curp)), '') AS curp,
               ? AS origen
        FROM raw_{stem}
    """, [origen])
    csv_path.unlink()
    return len(rows)


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
    ap = argparse.ArgumentParser(description="Normaliza padrón INE 2018 a DuckDB (muy rápido, CSV COPY)")
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
    with tempfile.TemporaryDirectory(prefix="ine_csv_") as tmpdir:
        for f in files:
            if str(f) in loaded:
                continue
            print(f"  {f.name} ...", end=" ", flush=True)
            t1 = time.time()
            try:
                n = load_file(con, f, tmpdir)
            except Exception as e:
                print(f"ERROR: {e}")
                continue
            total_new += n
            dt = time.time() - t1
            print(f"{n:,} filas ({dt:.1f}s, {n/max(dt,0.001):,.0f}/s)")

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
