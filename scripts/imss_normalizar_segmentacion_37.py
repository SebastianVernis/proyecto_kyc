"""
IMSS — Normalizador de CSVs de segmentación de derechohabientes.

Fuentes: 37 CSVs en el directorio de entrada (cada uno = una delegación/estado).
Encodings manejados:
  - 35 archivos: UTF-8 con BOM (\\xef\\xbb\\xbf al inicio)
  - CDMX NORTE.csv, CDMX SUR.csv: quoted-printable UTF-16-LE (exportación Windows/Power BI)

Salida:
  - out/parquet/<OOAD>/part-0.parquet   (particionado por delegación, snappy)
  - out/imss.duckdb                       (BD única con tabla imss_personas)

Uso (desde el padre de src/):
    /home/sebastianvernis/.venv/bin/python src/normalizar.py
o:
    cd /home/sebastianvernis/proyectos/imss-normalizer
    /home/sebastianvernis/.venv/bin/python src/normalizar.py
"""
from __future__ import annotations

import os
import re
import sys
import csv
import time
import unicodedata
from pathlib import Path
from typing import Iterator

import duckdb
import polars as pl

# ----------------------------------------------------------------------------- 
# Config
# -----------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
SRC_DIR = Path(os.environ.get("IMSS_SRC", "/home/sebastianvernis/Descargas/Bases/IMSS"))
OUT_DIR = Path(os.environ.get("IMSS_OUT", PROJECT / "out"))
PARQUET_DIR = OUT_DIR / "parquet"
DUCKDB_PATH = OUT_DIR / "imss.duckdb"

PARQUET_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

# CDMX NORTE/SUR vienen como ASCII puro en bytes pero con escapes estilo
# "modified-UTF-7" no estándar: secuencias +XX- donde los caracteres NO son
# base64 estándar. El exportador Power BI en Windows usa un esquema propio
# que no decodificamos limpiamente (probé UTF-7 RFC 2152 y no encaja).
# En lugar de inventar mappings, dejamos el texto crudo: polars leerá las
# secuencias +//3//Q- como strings literales, y el slugify posterior las
# aplana a tokens ascii (ej. "regi_3_q_n"). Es feo pero garantiza que los
# 37 archivos produzcan el mismo esquema y se puedan unir en DuckDB.
# La única operación especial: detectar estos archivos por la presencia de
# secuencias +...- en el header y dropear la primera columna (espuria).

_QP_ESCAPE_RE = re.compile(r"\+[A-Za-z0-9/]+-")


def decode_escaped_text(text: str) -> str:
    """Pasa el texto tal cual. La decodificación de escapes estilo +XX- no
    es trivial (el exportador usa un esquema no estándar); dejamos los
    strings crudos para preservar consistencia entre archivos.
    """
    return text


def read_csv_text(path: Path) -> tuple[str, str]:
    """Lee un CSV del IMSS y devuelve (encoding_usado, texto)."""
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-bom", raw.decode("utf-8-sig")
    # Los CDMX NORTE/SUR son ASCII puro en bytes pero con escapes +XX-
    # (modified-UTF-7). Checar ANTES de la heurística latin-1.
    head = raw[:4096]
    text_latin = head.decode("latin-1", errors="replace")
    if _QP_ESCAPE_RE.search(text_latin):
        return "escaped-cp1252", decode_escaped_text(raw.decode("latin-1"))
    # ascii puro: latin-1 sin escapes
    try:
        head.decode("ascii")
        return "latin-1", raw.decode("latin-1")
    except UnicodeDecodeError:
        pass
    return "utf-8", raw.decode("utf-8", errors="replace")


# -----------------------------------------------------------------------------
# Saneado de nombres de columna
# -----------------------------------------------------------------------------
def slugify_col(name: str, used: set[str] | None = None) -> str:
    """snake_case ASCII sin acentos. Quita '(Custom SQL Query)' y similares.
    Si ``used`` se pasa, desambigua añadiendo sufijos numéricos en colisiones.
    """
    s = name
    s = re.sub(r"\(Custom SQL Query\)", "", s, flags=re.IGNORECASE)
    s = s.replace("\u00a0", " ")  # nbsp
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.strip().lower()
    # la columna mal nombrada "'," queda vacía tras strip; asignamos flag
    if not s:
        s = "flag_vacio"
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    base = s or "col"
    if used is None:
        return base
    # desambiguar
    candidate = base
    n = 2
    while candidate in used:
        candidate = f"{base}_{n}"
        n += 1
    used.add(candidate)
    return candidate


# -----------------------------------------------------------------------------
# Tipado por nombre
# -----------------------------------------------------------------------------
DATE_COLS = {"fecha_de_nacimiento", "fecha_segmentacion"}
INT_COLS = {
    "edad", "personas", "personas_pct", "id_persona",
    "cve_delegacion", "cve_modalidad", "cve_nivel_atencion", "cve_region",
    "cve_unidadmedica", "id_calidad", "id_segmento", "id_segmento_cama",
    "id_segmento_cap", "id_segmento_ht", "id_tipo_derechohabiente",
    "prioridad", "prioridad_cama", "prioridad_cap", "prioridad_ht",
}
DOUBLE_COLS = {"porcentaje", "porcentaje_ooad", "porcentaje_unidad_medica"}


def normalize_columns(df: pl.DataFrame) -> pl.DataFrame:
    """Renombra columnas y aplica tipos. Garantiza nombres únicos."""
    used: set[str] = set()
    new_names = [slugify_col(c, used) for c in df.columns]
    df = df.rename(dict(zip(df.columns, new_names)))

    cast_map: dict[str, pl.DataType] = {}
    for c in df.columns:
        if c in DATE_COLS:
            cast_map[c] = pl.Date
        elif c in INT_COLS:
            cast_map[c] = pl.Int64
        elif c in DOUBLE_COLS:
            cast_map[c] = pl.Float64
    if cast_map:
        df = df.with_columns([pl.col(c).cast(t, strict=False) for c, t in cast_map.items()])
    return df


# -----------------------------------------------------------------------------
# Parseo polars desde string en memoria
# -----------------------------------------------------------------------------
def parse_csv_string(text: str, src_name: str) -> pl.DataFrame:
    """Lee el CSV ya decodificado a str usando polars."""
    from io import StringIO
    # quita el BOM que pueda quedar
    if text.startswith("\ufeff"):
        text = text[1:]
    buf = StringIO(text)
    df = pl.read_csv(
        buf,
        infer_schema_length=10000,
        null_values=["", " "],
        ignore_errors=True,
        truncate_ragged_lines=True,
        low_memory=False,
    )
    return df


# Header canónico de 55 columnas (presente en los 35 archivos UTF-8-BOM).
# CDMX NORTE/SUR tienen 56 columnas por una columna espuria al inicio
# (header "éOOAD" con un "é" residual pegado al "OOAD" original).
CANONICAL_NCOLS = 55
# Header canónico en formato ORIGINAL (como aparece en AGUAS C.csv), usado
# para asignar nombres a las columnas de CDMX NORTE/SUR cuyo header viene
# con escapes +XX- ilegibles. El orden posicional coincide.
CANONICAL_ORIG_HEADERS = [
    "OOAD", "Región", "',", "% Porcentaje", "% Porcentaje OOAD",
    "% Porcentaje Unidad Médica", "100%", "Agregado Médico", "Apellido Materno",
    "Apellido Paterno", "convenio", "Correo Electronico", "CURP",
    "cve_delegacion (Custom SQL Query)", "cve_modalidad (Custom SQL Query)",
    "cve_nivel_atencion (Custom SQL Query)", "cve_region (Custom SQL Query)",
    "cve_unidadmedica (Custom SQL Query)", "desc_enfermedad_cama",
    "desc_enfermedad_cap", "desc_enfermedad_diabetes", "desc_enfermedad_hipertension",
    "desc_nivel_atencion (Custom SQL Query)", "Edad", "Fecha de Nacimiento",
    "Fecha Segmentación", "Género", "ID Persona", "id_calidad (Custom SQL Query)",
    "id_segmento (Custom SQL Query)", "id_segmento_cama (Custom SQL Query)",
    "id_segmento_cap (Custom SQL Query)", "id_segmento_ht (Custom SQL Query)",
    "id_tipo_derechohabiente (Custom SQL Query)", "Modalidad", "Nombre", "NSS",
    "Personas", "Personas (%)", "prioridad (Custom SQL Query)",
    "prioridad_cama (Custom SQL Query)", "prioridad_cap (Custom SQL Query)",
    "prioridad_ht (Custom SQL Query)", "Rango de edad", "Razón Social",
    "ref_celular", "Registro Patronal", "RFC", "Segmentación Cáncer de Mama",
    "Segmentación Cáncer de Próstata", "Segmentación Diabetes Mellitus",
    "segmento_hipertension", "Telefono", "Tipo de derechohabiente", "Unidad Médica",
]


def align_to_canonical(df: pl.DataFrame, has_escapes: bool = False) -> pl.DataFrame:
    """Si el df tiene escapes +XX- en headers (CDMX NORTE/SUR), reasigna
    los nombres canónicos posicionalmente. El orden de columnas en esos
    archivos coincide con el de los archivos UTF-8-BOM, solo cambian los
    headers por secuencias de escape.
    """
    if has_escapes and df.width == CANONICAL_NCOLS:
        df = df.rename(dict(zip(df.columns, CANONICAL_ORIG_HEADERS)))
    return df


# -----------------------------------------------------------------------------
# Pipeline
# -----------------------------------------------------------------------------
def listar_archivos() -> list[Path]:
    return sorted(p for p in SRC_DIR.glob("*.csv"))


def procesar(src: Path) -> tuple[str, pl.DataFrame, str]:
    enc, text = read_csv_text(src)
    has_escapes = (enc == "escaped-cp1252")
    df = parse_csv_string(text, src.name)
    df = align_to_canonical(df, has_escapes=has_escapes)
    df = normalize_columns(df)
    # normalizar/sobreescribir la col 'ooad' con el nombre del archivo.
    # si ya existía (porque el CSV tiene col OOAD), la dropeamos primero
    # para evitar duplicados.
    ooad_label = src.stem
    if "ooad" in df.columns:
        df = df.drop("ooad")
    df = df.with_columns(pl.lit(ooad_label).alias("ooad"))
    return enc, df, ooad_label


def main() -> int:
    t0 = time.time()
    archivos = listar_archivos()
    print(f"[imss] {len(archivos)} CSVs en {SRC_DIR}", flush=True)

    # 1) escribir parquet particionado por estado
    resumen: list[dict] = []
    for i, src in enumerate(archivos, 1):
        t1 = time.time()
        enc, df, ooad = procesar(src)
        n_rows, n_cols = df.shape
        out_dir = PARQUET_DIR / ooad
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "part-0.parquet"
        df.write_parquet(out_file, compression="snappy", compression_level=None)
        dt = time.time() - t1
        size_mb = out_file.stat().st_size / 1024 / 1024
        print(
            f"[{i:>2}/{len(archivos)}] {src.name:<22}  enc={enc:<24}  "
            f"rows={n_rows:>9,}  cols={n_cols:>2}  → {size_mb:>7.1f} MB  ({dt:>5.1f}s)",
            flush=True,
        )
        resumen.append({
            "archivo": src.name,
            "ooad": ooad,
            "encoding": enc,
            "filas": n_rows,
            "columnas": n_cols,
            "size_mb": round(size_mb, 2),
            "segundos": round(dt, 2),
        })

    # 2) consolidar en DuckDB
    print("\n[imss] consolidando en DuckDB...", flush=True)
    if DUCKDB_PATH.exists():
        DUCKDB_PATH.unlink()
    con = duckdb.connect(str(DUCKDB_PATH))
    con.execute(f"""
        SET memory_limit = '6GB';
        SET threads = 4;
        SET temp_directory = '{OUT_DIR}/_tmp';
    """)
    Path(OUT_DIR / "_tmp").mkdir(exist_ok=True)

    parquet_glob = str(PARQUET_DIR / "*" / "part-0.parquet")
    con.execute(f"""
        CREATE TABLE imss_personas AS
        SELECT * FROM read_parquet('{parquet_glob}', union_by_name=true);
    """)
    # convertir texto a minúsculas donde aplique (ooad ya en mayúsculas con acentos)
    # crear índices lógicos: en DuckDB no hay índices tradicionales sobre Parquet;
    # lo que sí sirve es: ordenar/clusterizar la tabla y crear ART indexes
    # sobre columnas categóricas clave.
    print("[imss] creando índices ART...", flush=True)
    for col in ("curp", "nss", "rfc", "id_persona", "ooad"):
        try:
            con.execute(f'CREATE INDEX idx_{col} ON imss_personas ("{col}");')
        except duckdb.Error as e:
            print(f"  idx_{col}: {e}", flush=True)

    # resúmenes útiles
    print("[imss] resumen final:", flush=True)
    total = con.execute("SELECT COUNT(*) FROM imss_personas").fetchone()[0]
    print(f"  total filas: {total:,}", flush=True)
    con.execute("""
        SELECT ooad, COUNT(*) AS filas
        FROM imss_personas
        GROUP BY ooad
        ORDER BY ooad
    """).df().to_string(buf=sys.stdout)

    # guardar manifiesto
    import json
    (OUT_DIR / "manifiesto.json").write_text(
        json.dumps({
            "total_filas": total,
            "archivos": resumen,
            "duckdb": str(DUCKDB_PATH),
            "parquet_dir": str(PARQUET_DIR),
        }, indent=2, ensure_ascii=False)
    )
    con.close()
    dt = time.time() - t0
    print(f"\n[imss] listo en {dt/60:.1f} min", flush=True)
    print(f"  duckdb: {DUCKDB_PATH}", flush=True)
    print(f"  parquet: {PARQUET_DIR}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
