"""
imputar_cp_cfe.py — Puente sin-cp de CFE (pendiente #1 del handoff).

CFE trae cp solo en ~17% de filas, pero la geografía comercial
(division|zona|agencia) + colonia cruda determina el cp con alta confianza
en muchos casos. Este script:

  1. Construye el mapa (geo, colonia_cruda) -> cp dominante usando las filas
     que SÍ tienen cp (del índice, ya normalizado), exigiendo share >= 80%
     y soporte >= 3 medidores.
  2. Reescribe dir_cfe en mkidx_cfe_v1.duckdb imputando ese cp a las filas sin
     cp y recomputando las claves k_via_cp (cp|via) y k_via_ext (cp|via|ext).
     Las filas imputadas quedan marcadas con cp_imp = TRUE.

Idempotente: si dir_cfe ya tiene la columna cp_imp, aborta.

Uso:
    python imputar_cp_cfe.py            # MK_MEM / MK_THREADS por env como siempre
"""
from __future__ import annotations
import os, sys, time
from pathlib import Path
import duckdb

_BASES_DIR = Path(__file__).resolve().parent.parent / "bases"
IDX_DB = str(_BASES_DIR / "mkidx_cfe_v1.duckdb")
CFE_DB = str(_BASES_DIR / "cfe_v1.duckdb")

MIN_SHARE = 0.80   # el cp dominante debe cubrir >= 80% del grupo
MIN_SOPORTE = 3    # y tener >= 3 medidores con cp


def main():
    t0 = time.time()
    mem = os.environ.get("MK_MEM", "8GB")
    threads = os.environ.get("MK_THREADS", "6")
    con = duckdb.connect(IDX_DB)
    con.execute(f"SET memory_limit='{mem}'; SET threads={threads};")
    con.execute(f"SET temp_directory='{_BASES_DIR / '_duckdb_tmp'}';")
    con.execute("SET preserve_insertion_order=false;")
    con.execute(f"ATTACH '{CFE_DB}' AS cfe (READ_ONLY)")

    cols = [r[0] for r in con.execute("DESCRIBE dir_cfe").fetchall()]
    if "cp_imp" in cols:
        print("dir_cfe ya tiene cp imputado (columna cp_imp presente) — nada que hacer.")
        return

    # geografía + colonia cruda de cada fila, alineada al índice por rowid=ref
    print("[1/3] geografía por fila…", flush=True)
    con.execute("""
        CREATE TEMP TABLE geo_fila AS
        SELECT rowid AS ref,
               division || '|' || zona_codigo || '|' || agencia_codigo AS geo,
               upper(trim(colonia)) AS col_raw
        FROM cfe.medidores
        WHERE colonia IS NOT NULL AND trim(colonia) <> ''
    """)

    # mapa (geo, colonia) -> cp dominante, con el cp YA normalizado del índice
    print("[2/3] mapa (geo, colonia) -> cp dominante…", flush=True)
    con.execute(f"""
        CREATE TEMP TABLE mapa AS
        SELECT geo, col_raw, arg_max(cp, n) AS cp_top
        FROM (
            SELECT g.geo, g.col_raw, d.cp, count(*) AS n
            FROM dir_cfe d JOIN geo_fila g USING (ref)
            WHERE d.cp IS NOT NULL AND d.cp <> ''
            GROUP BY 1, 2, 3
        )
        GROUP BY geo, col_raw
        HAVING max(n) >= {MIN_SOPORTE}
           AND max(n)::DOUBLE / sum(n) >= {MIN_SHARE}
    """)
    n_mapa = con.execute("SELECT count(*) FROM mapa").fetchone()[0]
    print(f"      grupos confiables: {n_mapa:,}")

    # reescritura: cp imputado + claves cp recomputadas solo donde faltaba cp
    print("[3/3] reescribiendo dir_cfe con cp imputado…", flush=True)
    con.execute("""
        CREATE TABLE dir_cfe_new AS
        SELECT d.ref,
               COALESCE(nullif(d.cp, ''), m.cp_top) AS cp,
               d.via, d.ext, d.col, d.ent,
               CASE WHEN nullif(d.cp, '') IS NOT NULL THEN d.k_via_ext
                    WHEN m.cp_top IS NOT NULL AND d.via IS NOT NULL AND d.ext IS NOT NULL
                         THEN m.cp_top || '|' || d.via || '|' || d.ext
                    ELSE d.k_via_ext END AS k_via_ext,
               d.k_col_via_ext,
               CASE WHEN nullif(d.cp, '') IS NOT NULL THEN d.k_via_cp
                    WHEN m.cp_top IS NOT NULL AND d.via IS NOT NULL
                         THEN m.cp_top || '|' || d.via
                    ELSE d.k_via_cp END AS k_via_cp,
               d.k_col_via,
               (nullif(d.cp, '') IS NULL AND m.cp_top IS NOT NULL) AS cp_imp
        FROM dir_cfe d
        LEFT JOIN geo_fila g USING (ref)
        LEFT JOIN mapa m ON m.geo = g.geo AND m.col_raw = g.col_raw
    """)
    con.execute("DROP TABLE dir_cfe")
    con.execute("ALTER TABLE dir_cfe_new RENAME TO dir_cfe")

    r = con.execute("""
        SELECT count(*),
               count(*) FILTER (cp_imp),
               count(*) FILTER (cp_imp AND k_via_cp IS NOT NULL),
               count(*) FILTER (cp_imp AND k_via_ext IS NOT NULL)
        FROM dir_cfe""").fetchone()
    print(f"filas: {r[0]:,} | cp imputado: {r[1]:,} | "
          f"nuevas k_via_cp: {r[2]:,} | nuevas k_via_ext: {r[3]:,} | "
          f"{time.time()-t0:,.0f}s")
    con.close()


if __name__ == "__main__":
    main()
