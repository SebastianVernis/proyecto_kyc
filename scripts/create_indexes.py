#!/usr/bin/env python3
"""Crea índices en las bases DuckDB del proyecto KYC para acelerar queries de búsqueda.

Fase 0.5 del Plan de Escalabilidad — prerrequisito antes de cualquier otra optimización.

Uso:
    python3 scripts/create_indexes.py --dry-run    # solo muestra plan, no ejecuta
    python3 scripts/create_indexes.py              # crea índices reales
    python3 scripts/create_indexes.py --benchmark  # ejecuta benchmark antes/después
    python3 scripts/create_indexes.py --only padron # solo una DB específica

Bases que necesitan índices (sin índices actuales en campos de búsqueda):
  - padron_v1.duckdb  (88.4M rows) → curp
  - cfe_v1.duckdb     (66.0M rows) → numero_servicio
  - imss_asegurados_v1.duckdb (57.7M rows) → curp_clean
  - att_v1.duckdb     (1.0M rows)  → rfc_clean
  - empleadores_v1.duckdb (162K rows) → rfc_clean

Bases que YA tienen índices adecuados (no tocar):
  - telcel_v1..v4     → rfc_clean, telefono_clean
  - imss_segmentacion → curp_clean, nss_clean, rfc_clean, telefono_clean
  - issste_v1         → paterno, materno, nombres, sueldo, entidad, modalidad, sector
  - repuve_v1         → rfc_clean, nom_prop_fix
"""
import argparse
import os
import shutil
import sys
import time

import duckdb

BASES_DIR = os.path.join(os.path.dirname(__file__), "..", "bases")


def fmt(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{seconds / 60:.1f}min"


def get_db_path(name: str) -> str:
    return os.path.join(BASES_DIR, name)


def estimate_index_size(con, table: str, column: str) -> dict:
    """Estima el tamaño del índice basándose en cardinalidad y tamaño de la tabla."""
    try:
        row_count = con.execute(f"SELECT COUNT(*) FROM main.{table}").fetchone()[0]
    except Exception:
        return {"rows": "?", "cardinality": "?", "estimated_mb": "?"}

    try:
        cardinality = con.execute(
            f"SELECT COUNT(DISTINCT {column}) FROM main.{table}"
        ).fetchone()[0]
    except Exception:
        cardinality = "?"

    # Estimación rough: un ART index en DuckDB usa ~30-100 bytes por entrada
    if isinstance(cardinality, int) and isinstance(row_count, int) and row_count > 0:
        selectivity = cardinality / row_count
        # Menor selectividad = más entradas en el árbol
        estimated_bytes = cardinality * 60  # ~60 bytes por entrada ART
        estimated_mb = estimated_bytes / (1024 * 1024)
    else:
        estimated_mb = "?"

    return {
        "rows": row_count,
        "cardinality": cardinality,
        "estimated_mb": estimated_mb,
    }


def list_existing_indexes(con) -> list:
    """Retorna índices existentes en la DB."""
    try:
        rows = con.execute(
            "SELECT index_name, table_name, sql FROM duckdb_indexes()"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]
    except Exception:
        return []


def get_disk_usage(db_path: str) -> float:
    """Retorna tamaño del archivo en MB."""
    if os.path.exists(db_path):
        return os.path.getsize(db_path) / (1024 * 1024)
    return 0.0


def run_benchmark(con, table: str, column: str, label: str, iterations: int = 20):
    """Ejecuta un benchmark simple midiendo latencia de lookup."""
    # Obtener un valor de ejemplo
    try:
        sample = con.execute(
            f"SELECT {column} FROM main.{table} WHERE {column} IS NOT NULL LIMIT 1"
        ).fetchone()
        if not sample or not sample[0]:
            print(f"  ⚠ {label}: no hay datos para benchmark")
            return None
        sample_val = sample[0]
    except Exception as e:
        print(f"  ⚠ {label}: no se pudo obtener sample — {e}")
        return None

    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        con.execute(f"SELECT * FROM main.{table} WHERE {column} = ?", [sample_val])
        times.append((time.perf_counter() - t0) * 1000)

    times.sort()
    p50 = times[len(times) // 2]
    p95 = times[int(len(times) * 0.95)]
    avg = sum(times) / len(times)
    return {"p50_ms": p50, "p95_ms": p95, "avg_ms": avg, "sample": str(sample_val)[:30]}


# ── Definición de índices a crear ──────────────────────────────────────────────
# Cada entrada: (db_file, table, column, index_name, description)
#
# CRITERIO: solo índices en campos que aparecen en WHERE de servir.py y que
# actualmente hacen full scan. Índices en tablas pequeñas (<1M rows) o con
# baja cardinalidad se excluyen porque el beneficio es marginal.
#
# Prioridad 1 (CRÍTICO): tablas >10M rows con queries por PK
# Prioridad 2 (ALTO): tablas >1M rows con queries por campo de búsqueda
# Prioridad 3 (MEDIO): tablas <1M rows, bajo costo pero beneficio moderado
INDEX_DEFS = [
    # ═══ PRIORIDAD 1 — CRÍTICO ═══════════════════════════════════════════════
    # 88.4M rows, CADA query KYC pasa por aquí (WHERE curp = ?)
    ("padron_v1.duckdb", "padron", "curp", "idx_padron_curp",
     "CURP exact match — prioridad #1, cada lookup KYC"),

    # 66M rows, CFE lookups por número de servicio (WHERE numero_servicio = ?)
    ("cfe_v1.duckdb", "medidores", "numero_servicio", "idx_cfe_num_servicio",
     "CFE número servicio — 2do más grande, critical para CFE"),

    # 57.7M rows, IMSS asegurados (WHERE curp = ? vía api.imss_asegurado_full)
    # NOTA: la vista consulta imss_2025 (no imss_valid) con WHERE curp_kind='PF18'
    ("imss_asegurados_v1.duckdb", "imss_2025", "curp_clean", "idx_imss_a_curp",
     "IMSS asegurados por CURP — vista filtra por curp_kind='PF18'"),

    # ═══ PRIORIDAD 2 — ALTO ═════════════════════════════════════════════════
    # 1M rows, ATT (WHERE rfc = ? vía api.att_persona) — ya tiene índices de nombre
    # NOTA: att_valid es una vista, el índice va en la tabla base att
    ("att_v1.duckdb", "att", "rfc_clean", "idx_att_rfc",
     "ATT por RFC limpio —Needed for WHERE rfc IN (...) en sujeto handler"),

    # 162K rows, empleadores (WHERE rfc = ? vía api.empleadores)
    ("empleadores_v1.duckdb", "empleadores", "rfc_clean", "idx_emp_rfc",
     "Empleadores por RFC — Needed for WHERE rfc IN (...) en sujeto handler"),

    # ═══ PRIORIDAD 3 — MEDIO (bajo costo, beneficio moderado) ═══════════════
    # 1.7M rows, repuve ya tiene rfc_clean, pero PLACA no tiene índice
    # (las queries por placa son menos frecuentes pero existen en repuve handler)
    # SKIP: repuve ya tiene idx_repuve_rfc_clean; queries por PLACA son <5% del tráfico

    # 2.7M rows, issste ya tiene índices en paterno/materno/nombres — OK
    # SKIP: ya cubierto

    # 9.7M rows, telcel_v1 ya tiene rfc_clean + telefono_clean — OK
    # SKIP: ya cubierto

    # 23.8M rows, imss_segmentacion ya tiene curp_clean + rfc_clean + nss_clean — OK
    # SKIP: ya cubierto
]


def main():
    parser = argparse.ArgumentParser(
        description="Crea índices DuckDB para el proyecto KYC (Fase 0.5)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Solo muestra el plan sin ejecutar nada"
    )
    parser.add_argument(
        "--benchmark", action="store_true",
        help="Ejecuta benchmark antes/después de crear índices"
    )
    parser.add_argument(
        "--only", type=str, default=None,
        help="Solo procesar una DB específica (ej: padron_v1)"
    )
    parser.add_argument(
        "--skip-existing", action="store_true",
        help="Saltar índices que ya existen"
    )
    args = parser.parse_args()

    os.chdir(os.path.join(os.path.dirname(__file__), ".."))
    bases_abs = os.path.abspath(BASES_DIR)

    print("=" * 70)
    print("  FASE 0.5 — Creación de índices DuckDB (Plan Escalabilidad KYC)")
    print("=" * 70)
    print()

    # Verificar espacio en disco
    total_disk_before = 0
    for db_file, *_ in INDEX_DEFS:
        path = os.path.join(bases_abs, db_file)
        if os.path.exists(path):
            total_disk_before += os.path.getsize(path)

    disk_free = shutil.disk_usage(bases_abs).free
    disk_free_gb = disk_free / (1024**3)
    print(f"  Espacio libre: {disk_free_gb:.1f} GB")
    print(f"  Bases a indexar: {total_disk_before / (1024**3):.1f} GB (tamaño actual)")
    print()

    # Filtrar por --only si se especifica
    index_defs = INDEX_DEFS
    if args.only:
        index_defs = [d for d in INDEX_DEFS if args.only in d[0]]
        if not index_defs:
            print(f"  ✗ No se encontraron índices para '{args.only}'")
            sys.exit(1)

    # Agrupar por DB
    dbs = {}
    for db_file, table, column, name, desc in index_defs:
        dbs.setdefault(db_file, []).append((table, column, name, desc))

    # FASE 1: Benchmark ANTES (si se pide)
    benchmarks_before = {}
    if args.benchmark and not args.dry_run:
        print("─" * 70)
        print("  BENCHMARK ANTES de crear índices")
        print("─" * 70)
        for db_file, entries in dbs.items():
            path = os.path.join(bases_abs, db_file)
            if not os.path.exists(path):
                continue
            con = duckdb.connect(path, read_only=True)
            for table, column, name, desc in entries:
                label = f"{db_file}::{column}"
                result = run_benchmark(con, table, column, label)
                if result:
                    benchmarks_before[label] = result
                    print(f"  {label}: p50={result['p50_ms']:.1f}ms p95={result['p95_ms']:.1f}ms")
            con.close()
        print()

    # FASE 2: Dry-run o ejecución
    print("─" * 70)
    if args.dry_run:
        print("  DRY RUN — Plan de creación de índices")
    else:
        print("  EJECUCIÓN — Creando índices")
    print("─" * 70)
    print()

    total_estimated = 0
    created = 0
    skipped = 0
    errors = 0
    t_global = time.time()

    for db_file, entries in dbs.items():
        path = os.path.join(bases_abs, db_file)
        if not os.path.exists(path):
            print(f"  ✗ {db_file}: archivo no encontrado en {path}")
            errors += len(entries)
            continue

        size_before = get_disk_usage(path)
        try:
            con = duckdb.connect(path, read_only=args.dry_run)
        except Exception as e:
            if "Conflicting lock" in str(e) and args.dry_run:
                # Para dry-run, intentar read-only
                con = duckdb.connect(path, read_only=True)
            else:
                print(f"    ✗ No se pudo abrir {db_file}: {e}")
                errors += len(entries)
                continue
        existing = list_existing_indexes(con)
        existing_names = {i[0] for i in existing}

        print(f"  [{db_file}] ({size_before:.0f} MB)")

        for table, column, name, desc in entries:
            if args.skip_existing and name in existing_names:
                print(f"    ⊘ {name} ya existe — saltando")
                skipped += 1
                continue

            info = estimate_index_size(con, table, column)
            est_mb = info["estimated_mb"]
            if isinstance(est_mb, (int, float)):
                total_estimated += est_mb

            rows_str = f"{info['rows']:,}" if isinstance(info['rows'], int) else info['rows']
            card_str = f"{info['cardinality']:,}" if isinstance(info['cardinality'], int) else info['cardinality']

            print(f"    {name} ON {table}({column})")
            print(f"      filas: {rows_str}  |  cardinalidad: {card_str}  |  estimado: ~{est_mb:.0f} MB" if isinstance(est_mb, (int, float)) else f"      filas: {rows_str}  |  cardinalidad: {card_str}")
            print(f"      → {desc}")

            if args.dry_run:
                skipped += 1
                print(f"      [DRY RUN] skip")
            else:
                t0 = time.time()
                try:
                    con.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({column})")
                    elapsed = time.time() - t0
                    print(f"      ✓ creado en {fmt(elapsed)}")
                    created += 1
                except Exception as e:
                    elapsed = time.time() - t0
                    print(f"      ✗ ERROR en {fmt(elapsed)}: {e}")
                    errors += 1

        size_after = get_disk_usage(path)
        delta = size_after - size_before
        if not args.dry_run and delta > 0:
            print(f"    Δ disco: +{delta:.0f} MB ({size_before:.0f} → {size_after:.0f} MB)")

        con.close()
        print()

    total_elapsed = time.time() - t_global

    # Resumen
    print("─" * 70)
    print("  RESUMEN")
    print("─" * 70)
    print(f"  Creados:  {created}")
    print(f"  Saltados: {skipped}")
    print(f"  Errores:  {errors}")
    if not args.dry_run:
        print(f"  Tiempo:   {fmt(total_elapsed)}")
    print(f"  Estimado: +{total_estimated:.0f} MB de disco" if total_estimated > 0 else "")
    print()

    # Verificación post-creación
    if not args.dry_run and created > 0:
        print("─" * 70)
        print("  VERIFICACIÓN POST-CREACIÓN")
        print("─" * 70)
        for db_file, entries in dbs.items():
            path = os.path.join(bases_abs, db_file)
            if not os.path.exists(path):
                continue
            con = duckdb.connect(path, read_only=True)
            idxs = list_existing_indexes(con)
            print(f"  {db_file}: {len(idxs)} índice(s)")
            for iname, itable in idxs:
                print(f"    - {iname} ON {itable}")
            con.close()
        print()

    # FASE 3: Benchmark DESPUÉS (si se pide)
    if args.benchmark and not args.dry_run:
        print("─" * 70)
        print("  BENCHMARK DESPUÉS de crear índices")
        print("─" * 70)
        for db_file, entries in dbs.items():
            path = os.path.join(bases_abs, db_file)
            if not os.path.exists(path):
                continue
            con = duckdb.connect(path, read_only=True)
            for table, column, name, desc in entries:
                label = f"{db_file}::{column}"
                result = run_benchmark(con, table, column, label)
                if result:
                    print(f"  {label}: p50={result['p50_ms']:.1f}ms p95={result['p95_ms']:.1f}ms")
                    if label in benchmarks_before:
                        before = benchmarks_before[label]
                        ratio = before["p50_ms"] / result["p50_ms"] if result["p50_ms"] > 0 else 0
                        print(f"    ↓ {ratio:.1f}x más rápido (antes: {before['p50_ms']:.1f}ms)")
            con.close()
        print()

    if args.dry_run:
        print("  ℹ Para ejecutar realmente: python3 scripts/create_indexes.py")
        print("  ℹ Para benchmark: python3 scripts/create_indexes.py --benchmark")

    print("=" * 70)
    sys.exit(1 if errors > 0 else 0)


if __name__ == "__main__":
    main()
