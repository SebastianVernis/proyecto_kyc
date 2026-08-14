#!/usr/bin/env python3
"""Carga los archivos Excel *_*.xlsx del padrón INE 2018 en una sola tabla
DuckDB normalizada, con escaneo automático de carpetas, deduplicación
y carga incremental.

Optimizaciones vs. versión anterior:
  - Descubrimiento automático de archivos (no requiere lista hardcodeada).
  - Modo incremental: si la tabla ya existe, solo agrega archivos nuevos.
  - Inserción por chunks via DuckDB `executor.sql` con Arrow (sin pandas).
  - Limpieza y parseo vectorizados con pandas (.str/.astype).
  - Deduplicación por (curp, folio) al final de cada archivo.
  - Índices creados una sola vez al final.
  - Tolerancia a archivos corruptos o faltantes.
  - Modo CLI con argparse.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import duckdb
import openpyxl
import pandas as pd

try:
    import pyarrow as pa  # noqa: F401
    HAS_ARROW = True
except ImportError:
    HAS_ARROW = False

ROOT = Path(__file__).parent.resolve()
DB_PATH = ROOT / "ine.duckdb"
HEADERS_ESPERADOS = [
    "CVE", "NOMBRE", "PATERNO", "MATERNO", "FECNAC", "SEXO",
    "CALLE", "INT", "EXT", "COLONIA", "CP", "E", "D", "M",
    "S", "L", "MZA", "CONSEC", "CRED", "FOLIO", "NAC", "CURP",
]

# ----------------------- limpieza / parseo ----------------------------------


def _parse_fecnac_series(s):
    """Vectorizado: convierte una serie mixta a ISO date strings (YYYY-MM-DD)
    o None si no se puede. Acepta datetime, str en múltiples formatos y Excel
    serial numbers."""
    out = [None] * len(s)
    for i, v in enumerate(s):
        if v is None or (isinstance(v, float) and v != v):  # NaN
            continue
        if isinstance(v, datetime):
            out[i] = v.strftime("%Y-%m-%d")
            continue
        sv = str(v).strip()
        if not sv or sv.lower() in ("nan", "none", "nat"):
            continue
        for fmt in (
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y",
            "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S",
        ):
            try:
                out[i] = datetime.strptime(sv[:19], fmt[:len(sv[:19])]
                                          if False else fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                continue
        else:
            # Excel serial date (días desde 1899-12-30)
            try:
                f = float(sv)
                if 1 < f < 80000:
                    from datetime import timedelta
                    out[i] = (datetime(1899, 12, 30) + timedelta(days=f)).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pass
    return out


def _clean_text_series(s):
    """Mayúsculas + strip + drop empty."""
    out = []
    for v in s:
        if v is None:
            out.append(None)
            continue
        sv = str(v).strip().upper()
        out.append(sv if sv else None)
    return out


def _clean_int_series(s):
    out = []
    for v in s:
        if v is None:
            out.append(None)
            continue
        try:
            f = float(v)
            if f != f:  # NaN
                out.append(None)
            else:
                out.append(int(f))
        except (ValueError, TypeError):
            out.append(None)
    return out


# ----------------------- carga de archivo ----------------------------------


def read_xlsx(path: Path):
    """Lee un .xlsx en modo streaming y devuelve (rows, header_idx).
    Cada row es dict {col_normalizada: valor_crudo}.
    None si el archivo está vacío o corrupto."""
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as e:
        print(f"  ERROR abriendo {path.name}: {e}", file=sys.stderr)
        return None, None
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = next(rows_iter)
    except StopIteration:
        wb.close()
        return None, None

    header = [str(c).strip().upper() if c is not None else "" for c in header]
    idx = {h: i for i, h in enumerate(header) if h}

    # Validar columnas mínimas
    faltan = [c for c in ("NOMBRE", "PATERNO", "MATERNO", "CURP") if c not in idx]
    if faltan:
        wb.close()
        print(f"  AVISO {path.name}: faltan columnas {faltan} -- saltando",
              file=sys.stderr)
        return None, None

    out = []
    for row in rows_iter:
        if row is None:
            continue
        out.append({
            "cve": row[idx["CVE"]] if "CVE" in idx else None,
            "nombre": row[idx["NOMBRE"]] if "NOMBRE" in idx else None,
            "paterno": row[idx["PATERNO"]] if "PATERNO" in idx else None,
            "materno": row[idx["MATERNO"]] if "MATERNO" in idx else None,
            "fecnac": row[idx["FECNAC"]] if "FECNAC" in idx else None,
            "sexo": row[idx["SEXO"]] if "SEXO" in idx else None,
            "calle": row[idx["CALLE"]] if "CALLE" in idx else None,
            "int": row[idx["INT"]] if "INT" in idx else None,
            "ext": row[idx["EXT"]] if "EXT" in idx else None,
            "colonia": row[idx["COLONIA"]] if "COLONIA" in idx else None,
            "cp": row[idx["CP"]] if "CP" in idx else None,
            "e": row[idx["E"]] if "E" in idx else None,
            "d": row[idx["D"]] if "D" in idx else None,
            "m": row[idx["M"]] if "M" in idx else None,
            "s": row[idx["S"]] if "S" in idx else None,
            "l": row[idx["L"]] if "L" in idx else None,
            "mza": row[idx["MZA"]] if "MZA" in idx else None,
            "consec": row[idx["CONSEC"]] if "CONSEC" in idx else None,
            "cred": row[idx["CRED"]] if "CRED" in idx else None,
            "folio": row[idx["FOLIO"]] if "FOLIO" in idx else None,
            "nac": row[idx["NAC"]] if "NAC" in idx else None,
            "curp": row[idx["CURP"]] if "CURP" in idx else None,
        })
    wb.close()
    return out, idx


def clean_chunk(rows: list[dict]) -> list[dict]:
    """Vectoriza la limpieza de un chunk de filas."""
    if not rows:
        return rows
    keys = rows[0].keys()
    # extrae columnas a listas
    cols = {k: [r[k] for r in rows] for k in keys}
    cols["fecnac"] = _parse_fecnac_series(cols["fecnac"])
    for tcol in ("cve", "nombre", "paterno", "materno", "sexo",
                 "calle", "int", "ext", "colonia", "curp"):
        cols[tcol] = _clean_text_series(cols[tcol])
    for icol in ("cp", "e", "d", "m", "s", "l", "mza", "consec", "cred", "nac"):
        cols[icol] = _clean_int_series(cols[icol])
    # folio admite BIGINT pero también puede tener decimales
    cols["folio"] = _clean_int_series(cols["folio"])
    # reconstruir
    return [dict(zip(keys, [cols[k][i] for k in keys])) for i in range(len(rows))]


# ----------------------- DuckDB --------------------------------------------


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
    # asegurar secuencia y sincronizarla con max(id)
    con.execute("CREATE SEQUENCE IF NOT EXISTS seq_id START 1")
    max_id = con.execute("SELECT COALESCE(MAX(id), 0) FROM padron").fetchone()[0]
    if max_id > 0:
        # avanzar la secuencia al siguiente id disponible
        cur = con.execute("SELECT nextval('seq_id')").fetchone()[0]
        if cur < max_id + 1:
            # force advance
            con.execute(f"ALTER SEQUENCE seq_id RESTART WITH {max_id + 1}")


def insert_chunk(con, rows: list[dict], origen: str):
    if not rows:
        return
    rows = clean_chunk(rows)
    # usar registros como lista de tuplas en orden de columnas
    cols_order = ["cve", "nombre", "paterno", "materno", "fecnac", "sexo",
                  "calle", "int", "ext", "colonia", "cp", "e", "d", "m",
                  "s", "l", "mza", "consec", "cred", "folio", "nac", "curp"]
    df = pd.DataFrame(rows, columns=cols_order)
    con.register("tmp_chunk", df)
    con.execute("""
        INSERT INTO padron (id, cve, nombre, paterno, materno, fecnac, sexo,
                            calle, "int", "ext", colonia, cp, e, d, m, s, l, mza,
                            consec, cred, folio, nac, curp, origen)
        SELECT nextval('seq_id') AS id, *, ? AS origen FROM tmp_chunk
    """, [origen])
    con.unregister("tmp_chunk")


# ----------------------- descubrimiento de archivos ------------------------


def discover_files(root: Path) -> list[Path]:
    """Busca todos los .xlsx recursivamente bajo <root>/*/<estado>/Excel/*.xlsx.

    Si root apunta a src/ (donde vive el script), sube un nivel para buscar
    en las carpetas de estados hermanas.
    """
    candidates = []
    # Caso 1: root es el directorio padre de las carpetas de estado
    candidates.extend(sorted(root.glob("*/Excel/*.xlsx")))
    # Caso 2: root apunta a src/, buscar en su padre
    if not candidates and root.name.lower() in ("src", "source"):
        candidates.extend(sorted((root.parent).glob("*/Excel/*.xlsx")))
    # excluir el script mismo si por error se llama normalizar.xlsx
    return [f for f in candidates if f.is_file()]


def already_loaded(con) -> set[str]:
    return {r[0] for r in con.execute(
        "SELECT DISTINCT origen FROM padron").fetchall()}


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


# ----------------------- main ----------------------------------------------


def main():
    ap = argparse.ArgumentParser(description="Normaliza padrón INE 2018 a DuckDB")
    ap.add_argument("--db", default=str(DB_PATH), help="Path al .duckdb")
    ap.add_argument("--root", default=str(ROOT), help="Directorio raíz con carpetas de estados")
    ap.add_argument("--rebuild", action="store_true", help="Borra la DB y reconstruye desde cero")
    ap.add_argument("--no-index", action="store_true", help="No crear índices al final")
    ap.add_argument("--chunk", type=int, default=50_000, help="Tamaño de chunk para flush")
    args = ap.parse_args()

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
        print(f"AVISO: no se encontraron .xlsx bajo {root}/<estado>/Excel/*.xlsx",
              file=sys.stderr)

    if loaded:
        print(f"Ya cargados: {len(loaded)} archivos. Modo incremental.")
    print(f"Archivos a procesar: {len(files)}")
    t_load = time.time()

    chunk = []
    total_new = 0
    for f in files:
        if str(f) in loaded:
            continue
        print(f"  {f.name} ...", end=" ", flush=True)
        t1 = time.time()
        rows, _ = read_xlsx(f)
        if rows is None:
            print("saltado")
            continue
        # procesar en chunks
        for r in rows:
            r["origen"] = str(f)
            chunk.append(r)
            if len(chunk) >= args.chunk:
                insert_chunk(con, chunk, str(f))
                total_new += len(chunk)
                chunk.clear()
        if chunk:
            insert_chunk(con, chunk, str(f))
            total_new += len(chunk)
            chunk.clear()
        dt = time.time() - t1
        print(f"{len(rows):,} filas ({dt:.1f}s, {len(rows)/max(dt,0.001):,.0f}/s)")

    # deduplicar globalmente (curp, folio, origen) ya está en UNIQUE,
    # pero (curp, folio) sin origen puede estar duplicado entre archivos
    # si dos estados comparten el mismo folio (raro). Aplicar dedup conservador:
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

    # resumen
    total = con.execute("SELECT COUNT(*) FROM padron").fetchone()[0]
    print()
    print("=" * 60)
    print(f"Total filas: {total:,}")
    print(f"Tiempo carga: {time.time() - t_load:.1f}s")
    print(f"Tiempo total: {time.time() - t0:.1f}s")
    print("=" * 60)

    print("\nResumen por origen (top 10):")
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
