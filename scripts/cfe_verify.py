#!/usr/bin/env python3
"""Verificación exhaustiva de legibilidad de cfe.duckdb + parquet."""
import sys, time
from pathlib import Path
import duckdb
import polars as pl

BASE = Path("/home/sebastianvernis/Descargas/CFE/_normalized")
DUCK = BASE / "cfe.duckdb"
PARQUET_DIR = BASE / "parquet"

print("=" * 70)
print("VERIFICACIÓN DE LEGIBILIDAD - CFE normalizado")
print("=" * 70)

# ---------------------------------------------------------
# 1. Integridad física de los archivos
# ---------------------------------------------------------
print("\n[1] INTEGRIDAD FÍSICA")
print(f"  cfe.duckdb: {DUCK.stat().st_size/1024/1024:.1f} MB")
n_parquet = sum(1 for _ in PARQUET_DIR.rglob("*.parquet"))
size_parquet = sum(p.stat().st_size for p in PARQUET_DIR.rglob("*.parquet"))
print(f"  parquet: {n_parquet} archivos, {size_parquet/1024/1024:.1f} MB")

# ---------------------------------------------------------
# 2. Apertura y conteos en DuckDB
# ---------------------------------------------------------
print("\n[2] DUCKDB")
con = duckdb.connect(str(DUCK), read_only=True)

t = time.time()
n_total = con.execute("SELECT COUNT(*) FROM medidores").fetchone()[0]
dt = time.time() - t
print(f"  Total filas: {n_total:,}  (count(*) en {dt:.2f}s)")

t = time.time()
n_carpetas = con.execute("SELECT COUNT(DISTINCT __source_folder) FROM medidores").fetchone()[0]
n_archivos = con.execute("SELECT COUNT(DISTINCT (__source_folder || '/' || __source_file)) FROM medidores").fetchone()[0]
dt = time.time() - t
print(f"  Carpetas distintas: {n_carpetas}")
print(f"  Archivos distintos: {n_archivos}  (en {dt:.3f}s)")

# ---------------------------------------------------------
# 3. Esquema de la tabla
# ---------------------------------------------------------
print("\n[3] ESQUEMA")
schema = con.execute("""
    SELECT column_name, data_type
    FROM information_schema.columns
    WHERE table_name='medidores'
    ORDER BY ordinal_position
""").fetchall()
for col, dt in schema:
    print(f"  {col:20s} {dt}")

# ---------------------------------------------------------
# 4. Conteo por carpeta (debe coincidir con stats)
# ---------------------------------------------------------
print("\n[4] CONTEO POR CARPETA")
by_folder = con.execute("""
    SELECT __source_folder, COUNT(*) AS n
    FROM medidores
    GROUP BY __source_folder
    ORDER BY __source_folder
""").fetchdf()
print(by_folder.to_string(index=False))
total_check = by_folder['n'].sum()
print(f"  SUMA: {total_check:,}")

# ---------------------------------------------------------
# 5. Distribución de NULLs por columna
# ---------------------------------------------------------
print("\n[5] DISTRIBUCIÓN DE NULLs (columnas de datos)")
nulls = con.execute("""
    SELECT
        SUM(CASE WHEN division IS NULL THEN 1 ELSE 0 END) AS division_n,
        SUM(CASE WHEN zona_codigo IS NULL THEN 1 ELSE 0 END) AS zona_codigo_n,
        SUM(CASE WHEN zona_nombre IS NULL THEN 1 ELSE 0 END) AS zona_nombre_n,
        SUM(CASE WHEN codigo_medidor IS NULL THEN 1 ELSE 0 END) AS codigo_medidor_n,
        SUM(CASE WHEN numero_medidor IS NULL THEN 1 ELSE 0 END) AS numero_medidor_n,
        SUM(CASE WHEN numero_servicio IS NULL THEN 1 ELSE 0 END) AS numero_servicio_n,
        SUM(CASE WHEN nombre IS NULL THEN 1 ELSE 0 END) AS nombre_n,
        SUM(CASE WHEN direccion IS NULL THEN 1 ELSE 0 END) AS direccion_n
    FROM medidores
""").fetchdf().iloc[0].to_dict()
for col in ["division", "zona_codigo", "zona_nombre", "codigo_medidor", "numero_medidor", "numero_servicio", "nombre", "direccion"]:
    n = nulls[f'{col}_n']
    pct = 100 * n / n_total
    bar = '#' * int(pct/2)
    print(f"  {col:20s}: {n:>12,} NULL  ({pct:5.2f}%) {bar}")

# ---------------------------------------------------------
# 6. Cardinalidad de columnas clave
# ---------------------------------------------------------
print("\n[6] CARDINALIDAD DE COLUMNAS IDENTIFICADAS")
for label, col, top in [
    ("división",        "division",  8),
    ("zona (código)",   "zona_codigo",  8),
    ("zona (nombre)",   "zona_nombre",  8),
    ("agencia (código)", "agencia_codigo",  8),
    ("agencia (nombre)", "agencia_nombre",  8),
    ("codigo medidor",  "codigo_medidor",  8),
]:
    card = con.execute(f"SELECT COUNT(DISTINCT {col}) FROM medidores").fetchone()[0]
    print(f"  {label:18s} ({col}): {card:,} valores únicos")
    tops = con.execute(f"""
        SELECT {col} AS val, COUNT(*) AS n
        FROM medidores
        WHERE {col} IS NOT NULL
        GROUP BY {col}
        ORDER BY n DESC
        LIMIT {top}
    """).fetchdf()
    for _, r in tops.iterrows():
        v = r['val'][:40] if isinstance(r['val'], str) else r['val']
        print(f"      {str(v):42s} {r['n']:>12,}")

# ---------------------------------------------------------
# 7. Queries de muestreo
# ---------------------------------------------------------
print("\n[7] QUERIES DE MUESTREO")
print("  7a) 5 filas aleatorias:")
sample = con.execute("""
    SELECT __source_folder, __source_file,
            division AS div, zona_codigo AS zona_cod,
            zona_nombre AS zona_nom, agencia_codigo AS age_cod,
            agencia_nombre AS age_nom, codigo_medidor AS cod_med,
            numero_medidor AS medidor, numero_servicio AS servicio,
            nombre, direccion
    FROM medidores USING SAMPLE 5
""").fetchdf()
print(sample.to_string(index=False))

print("\n  7b) Top 10 divisiones por # de cuentas:")
print(con.execute("""
     SELECT division, COUNT(*) AS n_cuentas,
            COUNT(DISTINCT zona_nombre) AS n_zonas
    FROM medidores
     WHERE division IS NOT NULL
     GROUP BY division
    ORDER BY n_cuentas DESC
    LIMIT 10
""").fetchdf().to_string(index=False))

# ---------------------------------------------------------
# 8. Validación cruzada: contar parquet y compararlo con DuckDB
# ---------------------------------------------------------
print("\n[8] VALIDACIÓN CRUZADA DuckDB vs Parquet")
print("  Conteo de filas por parquet individual:")
desfase_total = 0
for carpeta_dir in sorted(PARQUET_DIR.iterdir()):
    if not carpeta_dir.is_dir():
        continue
    carpeta = carpeta_dir.name
    filas_parquet = 0
    archivos = sorted(carpeta_dir.glob("*.parquet"))
    for p in archivos:
        try:
            n = pl.scan_parquet(str(p)).select(pl.len()).collect(engine='streaming').item()
            filas_parquet += n
        except Exception as e:
            print(f"    ERROR leyendo {p}: {e}")
    filas_db = con.execute(
        "SELECT COUNT(*) FROM medidores WHERE __source_folder = ?", [carpeta]
    ).fetchone()[0]
    diff = filas_parquet - filas_db
    marcador = "OK" if diff == 0 else f"DIF={diff}"
    print(f"    {carpeta:50s} parquet={filas_parquet:>12,}  db={filas_db:>12,}  [{marcador}]")
    desfase_total += abs(diff)
print(f"  Desfase total absoluto: {desfase_total:,}")

con.close()
print("\n" + "=" * 70)
print(f"VERIFICACIÓN COMPLETADA - base {'LEGIBLE' if desfase_total == 0 and total_check == n_total else 'CON INCONSISTENCIAS'}")
print("=" * 70)
