#!/usr/bin/env python3
"""Crea índices en ine.duckdb para los filtros de búsqueda.

Estimación basada en audit-2026-08.md: ~3-5 minutos total sobre 88.4M rows.
Orden de creación: del más selectivo al menos selectivo, así los primeros
beneficios se ven antes si hay que abortar.
"""
import sys
import time
import duckdb

DB = "/root/Descargas MEGA/BASE INE 2021/BASE INE 2018/src/ine.duckdb"

INDICES = [
    # (nombre, columnas, comentario)
    ("idx_padron_curp",    "(curp)",          "exact match, 1 row por CURP"),
    ("idx_padron_folio",   "(folio)",         "exact match, 1 row por folio"),
    ("idx_padron_cp",      "(cp)",            "exact match, ~50-2000 rows por CP"),
    ("idx_padron_seccion", "(e, s)",          "compuesto estado+sección, ~1-3k rows"),
    ("idx_padron_colonia", "(colonia)",       "ILIKE contains sobre 88M rows"),
    ("idx_padron_calle",   "(calle)",         "ILIKE contains sobre 88M rows (la más lenta)"),
]


def fmt(t):
    return f"{t:.1f}s" if t < 60 else f"{t/60:.1f}min"


def main():
    con = duckdb.connect(DB, read_only=False)
    t_total = time.time()
    for nombre, cols, nota in INDICES:
        t0 = time.time()
        print(f"[{fmt(time.time()-t_total)}] creando {nombre} ON padron {cols} ...", flush=True)
        try:
            con.execute(f"CREATE INDEX IF NOT EXISTS {nombre} ON padron {cols}")
            elapsed = time.time() - t0
            print(f"             ✓ {nombre} listo en {fmt(elapsed)} — {nota}", flush=True)
        except Exception as e:
            print(f"             ✗ ERROR en {nombre}: {e}", flush=True)
            sys.exit(1)
    total = time.time() - t_total
    print()
    print(f"TOTAL: {fmt(total)}")
    print()
    # verificar
    print("índices creados:")
    for r in con.execute("SELECT index_name, table_name, sql FROM duckdb_indexes()").fetchall():
        print(f"  {r[0]} on {r[1]}: {r[2]}")
    con.close()


if __name__ == "__main__":
    main()
