#!/usr/bin/env python3
"""Normaliza los CSVs de CFE en DuckDB + Parquet (v2).

- No toca originales.
- Cada Hoja tiene 2 líneas de "header" (corrupta + decorativa).
  Saltamos ambas y leemos desde línea 3.
- Encabezado canónico = nombres descriptivos por posición (max global observado).
- Cadenas se rellenan con NULL hasta 17 columnas. Cada CSV se guarda
  en su PROPIO parquet (no consolidado por carpeta) dentro de
  parquet/<carpeta>/<archivo>.parquet, así garantizamos que cada archivo
  conserva SU propio ancho.
- DuckDB: usamos read_parquet con union de esquemas. La tabla medidores
  tiene 19 columnas (max + 2 metadata).
"""

from __future__ import annotations

import os
import sys
import time
import gc
from pathlib import Path

import polars as pl
import duckdb

BASE = Path("/home/sebastianvernis/Descargas/CFE")
OUT = BASE / "_normalized"
DUCK = OUT / "cfe.duckdb"
PARQUET_DIR = OUT / "parquet"
META_DIR = OUT / "_meta"

OUT.mkdir(parents=True, exist_ok=True)
PARQUET_DIR.mkdir(parents=True, exist_ok=True)
META_DIR.mkdir(parents=True, exist_ok=True)

# Limpiar runs previos
for ext in ["", ".wal"]:
    p = OUT / f"cfe.duckdb{ext}"
    if p.exists():
        p.unlink()
if PARQUET_DIR.exists():
    import shutil
    shutil.rmtree(PARQUET_DIR)
PARQUET_DIR.mkdir(parents=True, exist_ok=True)
for p in META_DIR.glob("*"):
    p.unlink()

# Esquema canónico: cols de datos + 2 metadata
# MAX_COLS se detecta dinámicamente en PASADA 1
MAX_COLS = 0  # se ajusta abajo

# Los CSV no comparten exactamente el mismo ancho: hay hojas con columnas
# opcionales/`Unnamed` entre los campos de ubicación y los del medidor. Se
# conserva el orden físico y se nombran esas posiciones explícitamente para
# no desplazar valores al intentar adivinar su significado.
DATA_COLUMN_NAMES = [
    "division",
    "zona_codigo",
    "zona_nombre",
    "zona_nombre_2",
    "zona_nombre_3",
    "agencia_codigo",
    "agencia_nombre",
    "agencia_nombre_2",
    "agencia_nombre_3",
    "codigo_medidor",
    "numero_medidor",
    "numero_servicio",
    "nombre",
    "direccion",
    "calle_adicional_1",
    "calle_adicional_2",
    "colonia",
    "campo_adicional_1",
    "hilos",
]


def column_name(index: int) -> str:
    """Devuelve el nombre estable de una columna física normalizada."""
    if index <= len(DATA_COLUMN_NAMES):
        return DATA_COLUMN_NAMES[index - 1]
    return f"campo_adicional_{index - len(DATA_COLUMN_NAMES):02d}"


def detect_service_index(df: pl.DataFrame, n_cols: int) -> int:
    """Detecta la posición del servicio usando su formato numérico estable."""
    scores = [0] * n_cols
    for row in df.head(1000).iter_rows():
        for index, value in enumerate(row):
            text = "" if value is None else str(value).strip()
            if text.isdigit() and 8 <= len(text) <= 13:
                scores[index] += 1
    best = max(range(n_cols), key=lambda index: scores[index])
    if scores[best] == 0:
        raise ValueError("No se pudo detectar la columna NUMERO DE SERVICIO")
    return best


def detect_columns(csv_path: Path) -> int:
    """Cuenta pipes en la línea 3 (primera fila de datos) + 1."""
    with csv_path.open("rb") as f:
        f.readline()  # header corrupto
        f.readline()  # decorativa
        data_line = f.readline()
    return data_line.count(b"|") + 1


def expected_rows_from_name(folder_name: str) -> int | None:
    """Concatena tokens numéricos finales (separador de miles = '_').
       '1_BC_Tij_Mexic_Etc_2_333_954' -> '2333954' = 2,333,954."""
    parts = folder_name.split("_")
    digitos_rev = []
    for p in reversed(parts):
        if p.isdigit():
            digitos_rev.append(p)
        else:
            break
    if not digitos_rev:
        return None
    s = "".join(reversed(digitos_rev))
    return int(s) if s.isdigit() else None


def normalize_one(
    csv_path: Path, folder_name: str, n_cols: int, max_cols: int
) -> pl.DataFrame:
    """Lee un CSV y devuelve un DF con nombres canónicos por posición."""
    actual_cols = [f"_raw_{i+1}" for i in range(n_cols)]
    schema_overrides = {c: pl.Utf8 for c in actual_cols}

    df = pl.read_csv(
        str(csv_path),
        separator="|",
        skip_rows=2,
        has_header=False,
        new_columns=actual_cols,
        schema_overrides=schema_overrides,
        null_values=["", "nan", "NaN", "NULL"],
        ignore_errors=True,
        low_memory=False,
    )

    # Filtrar separadores '---'
    sep_pat = r"^-+$"
    is_sep_row = pl.lit(True)
    for c in actual_cols:
        is_sep_row = is_sep_row & (
            pl.col(c).fill_null("").cast(pl.Utf8).str.contains(sep_pat)
        )
    df = df.filter(~is_sep_row)

    # Filtrar filas totalmente vacías
    non_null_count = pl.sum_horizontal(
        [
            pl.col(c).is_not_null() & (pl.col(c).cast(pl.Utf8) != "")
            for c in actual_cols
        ]
    )
    df = df.filter(non_null_count > 0)

    service_index = detect_service_index(df, n_cols)
    service_column = service_index + 1
    prefix = [f"_raw_{i}" for i in range(1, service_column)]
    suffix = [f"_raw_{i}" for i in range(service_column + 1, n_cols + 1)]

    # Los campos de código/medidor preceden al servicio; los campos de
    # ubicación variable se conservan al principio y los opcionales ocupan
    # las posiciones intermedias disponibles.
    prefix_location = prefix[:5]
    prefix_meter = prefix[-2:] if len(prefix) >= 2 else []
    prefix_extra = prefix[5:-2] if len(prefix) >= 7 else []
    output_exprs = []
    for raw in prefix_location:
        output_exprs.append(pl.col(raw))
    output_exprs += [pl.lit(None).cast(pl.Utf8)] * (5 - len(prefix_location))
    output_exprs += [pl.col(raw) for raw in prefix_extra[:4]]
    output_exprs += [pl.lit(None).cast(pl.Utf8)] * (4 - min(len(prefix_extra), 4))
    output_exprs += [pl.col(raw) for raw in prefix_meter]
    output_exprs += [pl.lit(None).cast(pl.Utf8)] * (2 - len(prefix_meter))
    output_exprs.append(pl.col(f"_raw_{service_column}"))

    # El último campo de cada CSV es hilos; el resto es la dirección
    # desglosada en las columnas que existan en esa hoja.
    suffix_values = suffix[:-1] if suffix else []
    output_exprs += [pl.col(raw) for raw in suffix_values[:6]]
    output_exprs += [pl.lit(None).cast(pl.Utf8)] * (6 - min(len(suffix_values), 6))
    output_exprs.append(pl.col(suffix[-1]) if suffix else pl.lit(None).cast(pl.Utf8))

    names = [column_name(i) for i in range(1, max_cols + 1)]
    df = df.select(
        [expression.alias(name) for expression, name in zip(output_exprs, names)]
    )

    df = df.with_columns(
        pl.lit(csv_path.name).alias("__source_file"),
        pl.lit(folder_name).alias("__source_folder"),
    )
    return df


def main():
    folders = sorted([p for p in BASE.iterdir() if p.is_dir()])
    folders = [p for p in folders if not p.name.startswith("_")]

    print(f"Carpetas a procesar: {len(folders)}")
    print("=" * 70)

    stats_rows = []
    grand_total = 0
    t_total = time.time()

    # ============== PASADA 1: verificación de ncols por archivo ==============
    print("\n[PASADA 1] Detectando ncols por archivo...")
    file_info = {}  # (folder, file) -> ncols
    for folder in folders:
        for csv in sorted(folder.glob("*.csv")):
            n = detect_columns(csv)
            file_info[(folder.name, csv.name)] = n
    global MAX_COLS
    MAX_COLS = max(file_info.values())
    print(f"  max observado: {MAX_COLS}")

    # ============== PASADA 2: normalizar y guardar parquet individual ==============
    print(f"\n[PASADA 2] Normalizando y escribiendo parquet...")
    for folder in folders:
        name = folder.name
        print(f"\n[{name}]")
        expected = expected_rows_from_name(name)
        folder_out = PARQUET_DIR / name
        folder_out.mkdir(exist_ok=True)
        csvs = sorted(folder.glob("*.csv"))
        print(f"  hojas: {len(csvs)}  esperado: {expected}")

        folder_total = 0
        folder_t = time.time()
        for csv in csvs:
            n = file_info[(name, csv.name)]
            t = time.time()
            try:
                df = normalize_one(csv, name, n, MAX_COLS)
            except Exception as e:
                print(f"  ERROR {csv.name}: {e}")
                continue
            dt = time.time() - t
            out = folder_out / f"{csv.stem}.parquet"
            df.write_parquet(str(out), compression="snappy")
            folder_total += df.shape[0]
            grand_total += df.shape[0]
            del df
            gc.collect()
            size_mb = out.stat().st_size / 1024 / 1024
            print(f"  {csv.name}: {folder_total:,} (acum)  cols={n}/{MAX_COLS}  {dt:.1f}s  -> {size_mb:.1f}MB")

        match = "OK" if expected and folder_total == expected else (
            f"DIF ({folder_total - expected if expected else '?'})"
        )
        dt = time.time() - folder_t
        print(f"  -> TOTAL: {folder_total:,}  esperado {expected}  [{match}]  ({dt:.0f}s)")
        stats_rows.append({
            "folder": name,
            "expected_rows": expected,
            "actual_rows": folder_total,
            "csv_files": len(csvs),
            "elapsed_s": round(dt, 1),
        })

    # ============== PASADA 3: consolidar en DuckDB ==============
    print(f"\n[PASADA 3] Cargando parquet -> DuckDB...")
    con = duckdb.connect(str(DUCK))
    con.execute("SET memory_limit='6GB';")
    con.execute("SET threads=2;")
    con.execute("SET preserve_insertion_order=false;")

    # Definir tabla con esquema explícito
    data_columns = [column_name(i) for i in range(1, MAX_COLS + 1)]
    col_defs = ",\n            ".join([f'"{c}" VARCHAR' for c in data_columns])
    create_sql = f"""
        CREATE TABLE medidores (
            {col_defs},
            __source_file VARCHAR,
            __source_folder VARCHAR
        )
    """
    con.execute(create_sql)
    print(f"  tabla creada con {MAX_COLS + 2} columnas")

    # Insertar desde cada parquet
    t_db = time.time()
    all_parquets = sorted(PARQUET_DIR.rglob("*.parquet"))
    print(f"  {len(all_parquets)} archivos parquet a insertar")

    # Lo más rápido: registrar todos los parquet y hacer INSERT FROM SELECT
    # Pero DuckDB requiere que TODOS los parquet tengan el mismo esquema.
    # Como ya normalizamos a 17 cols + 2 metadata, debe estar OK.
    pq_glob = str(PARQUET_DIR / "*" / "*.parquet")
    con.execute(
        f"INSERT INTO medidores SELECT * FROM read_parquet('{pq_glob}')"
    )
    dt = time.time() - t_db
    print(f"  INSERT en {dt:.0f}s")

    n_total = con.execute("SELECT COUNT(*) FROM medidores").fetchone()[0]
    print(f"\nTOTAL en DuckDB: {n_total:,}  (esperado {grand_total:,})")

    # Resumen
    import polars as pl
    stats_path = META_DIR / "folder_stats.csv"
    pl.DataFrame(stats_rows).write_csv(str(stats_path))
    print(f"Stats: {stats_path}")

    # Muestreo
    sample = con.execute("""
        SELECT __source_folder, __source_file,
               division, zona_codigo, zona_nombre, zona_nombre_2,
               agencia_codigo, agencia_nombre, agencia_nombre_2,
               agencia_nombre_3, codigo_medidor, numero_medidor,
               numero_servicio, nombre, direccion
        FROM medidores
        USING SAMPLE 5
    """).fetchdf()
    print("\nMuestra aleatoria (5 filas):")
    print(sample.to_string())

    print(f"\nDuckDB: {DUCK} ({DUCK.stat().st_size/1024/1024:.1f} MB)")
    elapsed_total = time.time() - t_total
    print(f"\nTiempo total: {elapsed_total:.0f}s")
    con.close()


if __name__ == "__main__":
    main()
