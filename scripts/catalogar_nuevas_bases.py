#!/usr/bin/env python3
"""
Catalogar archivos de "nuevas a normalizar/" sin materializar nada en RAM.

Estrategia:
  - SQLite (.db) → cursor sobre cada tabla, LIMIT/OFFSET paginado.
  - DuckDB       → mismo, vía conexión read-only.
  - CSV/TSV/TXT  → generator de líneas (csv.reader), peek de las primeras 5k.
  - XLSX         → openpyxl read_only=True.
  - PDF          → PyPDF2 page-by-page, primeras 5 páginas o hasta N chars.
  - .rar/.7z/.zip → solo listar contenido con `unrar`/`7z`/`unzip` (sin extraer).

Para cada archivo: nombre, motor detectado, tamaño MB, #filas, #columnas,
lista de columnas (nombre, tipo, ejemplo, heurística PII), muestra
anonimizada de 1 fila, alertas (lock? error? formato raro?).

Salida: un .txt por archivo en /home/sebastianvernis/proyectos/kyc/proyecto_kyc/bases/_catalog_nuevas/
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Iterable, Iterator

# --- Configuración ---------------------------------------------------------
SRC = Path("/home/sebastianvernis/proyectos/kyc/proyecto_kyc/nuevas a normalizar")
OUT = Path("/home/sebastianvernis/proyectos/kyc/proyecto_kyc/bases/_catalog_nuevas")
OUT.mkdir(parents=True, exist_ok=True)

# Límite duro: NO cargar más de 5 MB en memoria para muestras. Las muestras
# se mantienen como tuplas pequeñas, no como DataFrames.
MAX_SAMPLE_BYTES = 5 * 1024 * 1024
SAMPLE_ROWS = 5
# Chunks de filas para bases grandes: nunca más de 50k filas en memoria
CHUNK_ROWS = 50_000
# Páginas a leer de PDFs (al menos las primeras para detectar contenido)
PDF_PAGES = 5
PDF_MAX_CHARS = 4000


# --- Heurísticas de PII ----------------------------------------------------
PII_PATTERNS = [
    # (regex_keywords_en_nombre_de_columna, placeholder, descripción)
    (("curp",), "<CURP>", "CURP"),
    (("rfc", "rfc_clean"), "<RFC>", "RFC"),
    (("nss",), "<NSS>", "NSS"),
    (("telefono", "tel1", "celular", "tel"), "<TEL>", "teléfono"),
    (("email", "correo", "mail"), "<EMAIL>", "email"),
    (("nombre", "nombres", "pat", "may", "materno", "paterno", "razon_social", "nombre_patron"), "<NOMBRE>", "nombre"),
    (("calle", "direccion", "dir", "domicilio", "colonia"), "<DIRECCION>", "dirección"),
    (("cp", "codigo_postal", "zip"), "<CP>", "CP"),
    (("placa", "vin", "serie", "num_servicio", "medidor"), "<ID>", "ID vehículo/servicio"),
    (("folio", "credencial", "ine", "ife"), "<FOLIO>", "folio"),
    (("username", "user_id", "usuario"), "<USER>", "usuario"),
    (("password", "passwd", "pwd", "hash", "token", "public_key", "credential_id", "secret", "salt"), "<SECRETO>", "secreto"),
    (("ip", "ip_address"), "<IP>", "IP"),
]


def pii_for_column(col_name: str) -> tuple[str, str]:
    cl = col_name.lower()
    for keys, placeholder, desc in PII_PATTERNS:
        for k in keys:
            if k in cl:
                return placeholder, desc
    return "<TEXTO>", "texto"


def is_pii(col_name: str) -> bool:
    cl = col_name.lower()
    for keys, _, _ in PII_PATTERNS:
        for k in keys:
            if k in cl:
                return True
    return False


# --- Anonimización ---------------------------------------------------------
def anon(value: Any) -> str:
    """Anonimiza un valor: trunca, enmascara, nunca devuelve el original completo."""
    if value is None:
        return "<NULL>"
    s = str(value)
    if not s:
        return "<VACIO>"
    # Si parece email, enmascar local-part
    if "@" in s and "." in s and " " not in s:
        local, _, dom = s.partition("@")
        if local and dom:
            return f"<EMAIL: {'*' * min(8, len(local))}@{dom}>"
    # Si parece teléfono (10+ dígitos)
    digits = "".join(c for c in s if c.isdigit())
    if len(digits) >= 10 and len(digits) <= 15 and len(s) <= 20:
        return "<TEL: ****" + digits[-4:] + ">"
    # Truncar y reemplazar contenido
    if len(s) > 30:
        return s[:8] + "...<TRUNC>"
    return s


# --- Detectores de motor ----------------------------------------------------
def detect_engine(path: Path) -> str:
    s = str(path).lower()
    if s.endswith((".db", ".sqlite", ".sqlite3")):
        return "sqlite"
    if s.endswith(".duckdb"):
        return "duckdb"
    if s.endswith(".csv"):
        return "csv"
    if s.endswith(".tsv"):
        return "tsv"
    if s.endswith(".txt"):
        return "txt"
    if s.endswith(".xlsx"):
        return "xlsx"
    if s.endswith(".xls"):
        return "xls"
    if s.endswith(".pdf"):
        return "pdf"
    if s.endswith(".rar"):
        return "rar"
    if s.endswith(".7z"):
        return "7z"
    if s.endswith(".zip"):
        return "zip"
    if s.endswith(".sql"):
        return "sql"
    return "desconocido"


# --- Catalogadores por motor -----------------------------------------------
def catalog_sqlite(path: Path) -> dict:
    """Abre SQLite en modo URI read-only, itera tablas."""
    info: dict = {"motor": "sqlite", "archivo": path.name, "tablas": []}
    try:
        uri = f"file:{path}?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=10)
        try:
            cur = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = [r[0] for r in cur.fetchall()]
        finally:
            con.close()
    except sqlite3.Error as e:
        info["error"] = f"sqlite open: {e}"
        return info

    for tbl in tables:
        try:
            uri2 = f"file:{path}?mode=ro"
            con = sqlite3.connect(uri2, uri=True, timeout=10)
            try:
                # Tamaño
                try:
                    n_rows = con.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
                except sqlite3.Error:
                    n_rows = None
                # Columnas + tipos
                cols_info = con.execute(f'PRAGMA table_info("{tbl}")').fetchall()
                # cid, name, type, notnull, dflt_value, pk
                columns = []
                for cid, name, ctype, notnull, dflt, pk in cols_info:
                    columns.append({
                        "nombre": name,
                        "tipo": ctype or "TEXT",
                        "pk": bool(pk),
                        "notnull": bool(notnull),
                    })
                # Muestra: primeras 5 filas usando iterator (no carga todo)
                sample = []
                try:
                    cur2 = con.execute(f'SELECT * FROM "{tbl}" LIMIT {SAMPLE_ROWS}')
                    col_names = [d[0] for d in cur2.description]
                    for row in cur2:
                        sample.append(dict(zip(col_names, row)))
                except sqlite3.Error as e:
                    info.setdefault("warnings", []).append(f"{tbl}: muestra: {e}")
            finally:
                con.close()
        except sqlite3.Error as e:
            info["tablas"].append({"nombre": tbl, "error": str(e)})
            continue

        # Detectar columnas PII
        for c in columns:
            placeholder, desc = pii_for_column(c["nombre"])
            c["pii"] = is_pii(c["nombre"])
            c["placeholder"] = placeholder
            c["descripcion"] = desc

        # Anonimizar muestra
        anon_sample = []
        for row in sample:
            ar = {}
            for c in columns:
                v = row.get(c["nombre"])
                ar[c["nombre"]] = anon(v) if c["pii"] else (str(v)[:60] if v is not None else "<NULL>")
            anon_sample.append(ar)

        info["tablas"].append({
            "nombre": tbl,
            "filas": n_rows,
            "columnas": columns,
            "muestra_anonimizada": anon_sample,
        })

    return info


def catalog_duckdb(path: Path) -> dict:
    """Abre DuckDB en read-only. Si no está disponible, marca error."""
    info: dict = {"motor": "duckdb", "archivo": path.name, "tablas": [], "warnings": []}
    try:
        import duckdb
    except ImportError:
        info["error"] = "duckdb Python module no instalado"
        return info
    try:
        con = duckdb.connect(str(path), read_only=True)
    except Exception as e:
        info["error"] = f"duckdb open: {e}"
        return info
    try:
        tables = [r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='main' ORDER BY table_name"
        ).fetchall()]
    except Exception as e:
        info["error"] = f"list tables: {e}"
        con.close()
        return info

    for tbl in tables:
        try:
            n_rows = con.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
            cols_info = con.execute(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema='main' AND table_name=? ORDER BY ordinal_position",
                [tbl]
            ).fetchall()
            columns = []
            for cname, ctype, nullable in cols_info:
                columns.append({
                    "nombre": cname,
                    "tipo": ctype,
                    "nullable": nullable == "YES",
                    "pk": False,  # DuckDB no expone PK trivialmente
                })
            sample = []
            try:
                cur = con.execute(f'SELECT * FROM "{tbl}" LIMIT {SAMPLE_ROWS}')
                col_names = [d[0] for d in cur.description]
                for row in cur.fetchall():
                    sample.append(dict(zip(col_names, row)))
            except Exception as e:
                info["warnings"].append(f"{tbl}: muestra: {e}")
        except Exception as e:
            info["tablas"].append({"nombre": tbl, "error": str(e)})
            continue

        for c in columns:
            placeholder, desc = pii_for_column(c["nombre"])
            c["pii"] = is_pii(c["nombre"])
            c["placeholder"] = placeholder
            c["descripcion"] = desc

        anon_sample = []
        for row in sample:
            ar = {}
            for c in columns:
                v = row.get(c["nombre"])
                ar[c["nombre"]] = anon(v) if c["pii"] else (str(v)[:60] if v is not None else "<NULL>")
            anon_sample.append(ar)

        info["tablas"].append({
            "nombre": tbl,
            "filas": n_rows,
            "columnas": columns,
            "muestra_anonimizada": anon_sample,
        })
    con.close()
    return info


def _line_count_fast(path: Path) -> int | None:
    """Cuenta líneas sin cargar el archivo. Para archivos gigantes es O(1) en RAM."""
    try:
        with subprocess.Popen(
            ["wc", "-l", str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        ) as p:
            out, _ = p.communicate(timeout=60)
        if p.returncode != 0:
            return None
        return int(out.split()[0])
    except (subprocess.TimeoutExpired, ValueError):
        return None


def _detect_separator(path: Path, max_bytes: int = 65536) -> tuple[str | None, list[str]]:
    """Lee los primeros N bytes, intenta detectar separador."""
    seps = [",", "|", "\t", ";"]
    try:
        with open(path, "rb") as f:
            data = f.read(max_bytes)
    except OSError:
        return None, []
    text = data.decode("utf-8", errors="replace")
    # primera línea
    first = text.split("\n", 1)[0] if text else ""
    counts = [(s, first.count(s)) for s in seps]
    counts.sort(key=lambda x: -x[1])
    if counts and counts[0][1] >= 2:
        return counts[0][0], [c[0] for c in counts if c[1] >= 1]
    return None, [c[0] for c in counts if c[1] >= 1]


def catalog_delimited(path: Path, motor: str) -> dict:
    """CSV/TSV/TXT: usa wc -l para contar líneas, luego samplea sin cargar todo."""
    info: dict = {"motor": motor, "archivo": path.name, "warnings": []}
    n_lines = _line_count_fast(path)
    info["filas_aprox"] = n_lines

    sep, sep_counts = _detect_separator(path)
    info["separador_detectado"] = repr(sep) if sep else "no detectado"
    info["separadores_vistos"] = [repr(s) for s in sep_counts]

    if sep is None:
        info["warnings"].append("No se detectó separador (¿archivo sin estructura tabular? muestra ilegible)")
        return info

    # Samplear primeras 5 filas como dict
    sample = []
    header = None
    data_start = 0
    try:
        with open(path, "rb") as f:
            data = f.read(2 * 1024 * 1024)  # hasta 2MB para el header + 5 filas
        # Quitar BOM UTF-8
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
        text = data.decode("utf-8", errors="replace")
        reader = csv.reader(io.StringIO(text), delimiter=sep)
        rows = []
        for i, row in enumerate(reader):
            if i >= 1 + SAMPLE_ROWS:
                break
            rows.append(row)
        if not rows:
            info["warnings"].append("Archivo vacío")
            return info
        first = rows[0]
        # Detectar si la primera fila es header o dato.
        # Criterios para "es header":
        #  - La primera fila tiene strings no-numéricos en su mayoría
        #  - Los valores son únicos entre sí (no se repiten como datos)
        #  - La segunda fila no se parece a la primera (no son datos gemelos)
        def looks_like_header(candidate: list[str], all_rows: list[list[str]]) -> bool:
            cs = [s.strip() for s in candidate]
            non_empty = [s for s in cs if s]
            if not non_empty:
                return False
            # % no-numérico
            str_ratio = sum(1 for s in non_empty
                            if not s.replace(".", "").replace("-", "").replace(",", "").isdigit()) / len(non_empty)
            # Unicidad
            unique_ratio = len(set(non_empty)) / len(non_empty)
            # ¿La segunda fila se parece? Si tiene misma cantidad de celdas y
            # valores numéricos donde la 1ra tiene strings, es header.
            if len(all_rows) > 1:
                second = all_rows[1]
                # Si la primera tiene strings y la segunda tiene números, es header
                first_strs = sum(1 for s in non_empty if any(c.isalpha() for c in s))
                second_strs = sum(1 for s in second
                                  if s and any(c.isalpha() for c in s))
                if first_strs > len(non_empty) * 0.5 and second_strs < len(second) * 0.3:
                    return True
            return str_ratio > 0.5 and unique_ratio > 0.7
        if looks_like_header(first, rows):
            header = [s.strip() if s else f"col_{i}" for i, s in enumerate(first)]
            # Quitar BOM residual del primer header
            if header and header[0].startswith("\ufeff"):
                header[0] = header[0].lstrip("\ufeff")
            data_start = 1
        else:
            # No hay header. Generar col_0..col_N usando el ancho de la primera fila
            ncols = max(len(r) for r in rows) if rows else 0
            header = [f"col_{i}" for i in range(ncols)]
            data_start = 0
            info.setdefault("warnings", []).append(
                f"Sin header detectable ({len(rows[0])} columnas inferidas del ancho de la primera fila)"
            )
        for i, row in enumerate(rows[data_start:data_start + SAMPLE_ROWS]):
            if len(row) < len(header):
                row = row + [""] * (len(header) - len(row))
            elif len(row) > len(header):
                row = row[:len(header)]
            sample.append(dict(zip(header, row)))
    except OSError as e:
        info["error"] = f"read: {e}"
        return info

    info["columnas"] = []
    for h in header:
        placeholder, desc = pii_for_column(h)
        info["columnas"].append({
            "nombre": h,
            "tipo": "TEXT",
            "pk": False,
            "notnull": False,
            "pii": is_pii(h),
            "placeholder": placeholder,
            "descripcion": desc,
        })
    info["muestra_anonimizada"] = [
        {c["nombre"]: anon(row.get(c["nombre"])) if c["pii"] else str(row.get(c["nombre"], ""))[:60] for c in info["columnas"]}
        for row in sample
    ]
    return info


def catalog_xlsx(path: Path) -> dict:
    info: dict = {"motor": "xlsx", "archivo": path.name, "hojas": [], "warnings": []}
    try:
        from openpyxl import load_workbook
    except ImportError:
        info["error"] = "openpyxl no instalado"
        return info
    try:
        wb = load_workbook(str(path), read_only=True, data_only=True, keep_links=False)
    except Exception as e:
        info["error"] = f"xlsx open: {e}"
        return info
    for ws in wb.worksheets:
        sheet_info = {"nombre": ws.title, "filas_aprox": ws.max_row, "columnas": []}
        # Leer las primeras 2+SAMPLE_ROWS filas para distinguir header de datos
        all_rows = []
        for r in ws.iter_rows(min_row=1, max_row=2 + SAMPLE_ROWS, values_only=True):
            all_rows.append(r)
        if not all_rows:
            wb.close()
            info["hojas"].append(sheet_info)
            continue
        first = all_rows[0]
        # Heurística: si la primera fila parece header (mayoría strings no-numéricos
        # o todos únicos), usarla como header. Si parece datos (mayoría numérica o
        # valores repetidos), generar header genérico col_N.
        first_strs = [str(c) if c is not None else "" for c in first]
        # % de celdas que son strings no-vacíos
        non_empty = [s for s in first_strs if s.strip()]
        str_ratio = sum(1 for s in non_empty if not s.replace(".", "").replace("-", "").isdigit()) / max(1, len(non_empty))
        # Unicidad
        unique_ratio = len(set(non_empty)) / max(1, len(non_empty))
        if str_ratio > 0.6 and unique_ratio > 0.7:
            header = [s.strip() if s else f"col_{i}" for i, s in enumerate(first_strs)]
            data_start = 1
        else:
            ncols = len(first) if first else 0
            header = [f"col_{i}" for i in range(ncols)]
            data_start = 0
            sheet_info.setdefault("warnings", []).append(
                "Primera fila parece dato (no header). Header generado como col_0..col_N"
            )
        for h in header:
            placeholder, desc = pii_for_column(h)
            sheet_info["columnas"].append({
                "nombre": h,
                "tipo": "CELL",
                "pii": is_pii(h),
                "placeholder": placeholder,
                "descripcion": desc,
            })
        sample_rows = []
        for r in all_rows[data_start:data_start + SAMPLE_ROWS]:
            d = {}
            for i, h in enumerate(header):
                v = r[i] if i < len(r) else None
                d[h] = v
            sample_rows.append(d)
        anon_sample = []
        for row in sample_rows:
            ar = {}
            for c in sheet_info["columnas"]:
                v = row.get(c["nombre"])
                ar[c["nombre"]] = anon(v) if c["pii"] else (str(v)[:60] if v is not None else "<NULL>")
            anon_sample.append(ar)
        sheet_info["muestra_anonimizada"] = anon_sample
        info["hojas"].append(sheet_info)
    wb.close()
    return info


def catalog_pdf(path: Path) -> dict:
    info: dict = {"motor": "pdf", "archivo": path.name, "warnings": []}
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader
        except ImportError:
            info["error"] = "pypdf/PyPDF2 no instalado"
            return info
    try:
        reader = PdfReader(str(path), strict=False)
    except Exception as e:
        info["error"] = f"pdf open: {e}"
        return info
    info["paginas"] = len(reader.pages)
    chunks = []
    chars = 0
    for i, p in enumerate(reader.pages):
        if i >= PDF_PAGES or chars >= PDF_MAX_CHARS:
            break
        try:
            t = p.extract_text() or ""
            chunks.append(f"[pag {i+1}]\n{t}")
            chars += len(t)
        except Exception as e:
            info["warnings"].append(f"page {i}: {e}")
    info["extracto"] = "\n\n".join(chunks)[:PDF_MAX_CHARS]
    info["nota"] = "PDF: solo se extrajeron las primeras páginas como muestra. No es base de datos."
    return info


def catalog_archive(path: Path, motor: str) -> dict:
    info: dict = {"motor": motor, "archivo": path.name, "warnings": []}
    if motor == "zip":
        try:
            with zipfile.ZipFile(path, "r") as z:
                infos = z.infolist()
                info["entradas"] = len(infos)
                info["tamano_descomprimido_bytes"] = sum(i.file_size for i in infos)
                info["muestra_archivos"] = [
                    {"nombre": i.filename, "tamano": i.file_size, "comprimido": i.compress_size}
                    for i in infos[:30]
                ]
        except (zipfile.BadZipFile, OSError) as e:
            info["error"] = f"zip: {e}"
    elif motor == "rar":
        if shutil.which("unrar"):
            try:
                out = subprocess.run(
                    ["unrar", "l", str(path)],
                    capture_output=True, text=True, timeout=60
                )
                if out.returncode == 0:
                    info["contenido_resumen"] = out.stdout[-4000:]
                else:
                    info["error"] = out.stderr[:500]
            except (subprocess.TimeoutExpired, OSError) as e:
                info["error"] = f"unrar: {e}"
        else:
            info["error"] = "unrar no instalado (no se puede listar contenido)"
    elif motor == "7z":
        if shutil.which("7z"):
            try:
                out = subprocess.run(
                    ["7z", "l", str(path)],
                    capture_output=True, text=True, timeout=60
                )
                if out.returncode == 0:
                    info["contenido_resumen"] = out.stdout[-4000:]
                else:
                    info["error"] = out.stderr[:500]
            except (subprocess.TimeoutExpired, OSError) as e:
                info["error"] = f"7z: {e}"
        else:
            info["error"] = "7z no instalado (no se puede listar contenido)"
    info["nota"] = "Archivo comprimido: no se extrajo. Solo se inspeccionó metadata/contenido."
    return info


def catalog_sql(path: Path) -> dict:
    info: dict = {"motor": "sql", "archivo": path.name, "warnings": []}
    n_lines = _line_count_fast(path)
    info["filas_aprox"] = n_lines
    # Contar CREATE TABLE / INSERT INTO
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = ""
            for _ in range(2000):  # primeras 2000 líneas
                line = f.readline()
                if not line:
                    break
                text += line
        info["create_tables"] = text.lower().count("create table")
        info["inserts"] = text.lower().count("insert into")
        info["extracto_head"] = text[:2500]
    except OSError as e:
        info["error"] = f"read: {e}"
    return info


# --- Escritura de reporte ---------------------------------------------------
def write_report(path: Path, info: dict) -> None:
    """Escribe un .txt estilo 'KYC Layout' — sin HTML, todo texto plano."""
    out = []
    out.append("=" * 64)
    out.append(f"CATALOGO: {info.get('archivo', '?')}")
    out.append(f"Motor: {info.get('motor', '?')}")
    out.append(f"Tamano: {info.get('tamano_mb', 0):.2f} MB")
    if "filas_aprox" in info:
        out.append(f"Filas aprox: {info.get('filas_aprox', '?'):,}")
    if "filas" in info:
        out.append(f"Filas: {info.get('filas', '?'):,}")
    if "paginas" in info:
        out.append(f"Paginas: {info.get('paginas', '?')}")
    if "entradas" in info:
        out.append(f"Entradas en archivo: {info.get('entradas', '?')}")
    if "separador_detectado" in info:
        out.append(f"Separador: {info.get('separador_detectado')}")
    out.append("=" * 64)
    if info.get("error"):
        out.append(f"ERROR: {info['error']}")
    if info.get("warnings"):
        out.append("WARNINGS:")
        for w in info["warnings"]:
            out.append(f"  - {w}")
    if info.get("nota"):
        out.append(f"NOTA: {info['nota']}")

    # Tablas / hojas
    # Compatibilidad: catalog_delimited llena info["columnas"] y
    # info["muestra_anonimizada"] directamente (sin info["tablas"]).
    # Lo envolvemos para que write_report las procese.
    if not info.get("tablas") and info.get("columnas"):
        info["tablas"] = [{
            "nombre": info.get("archivo", "?"),
            "filas_aprox": info.get("filas_aprox"),
            "columnas": info["columnas"],
            "muestra_anonimizada": info.get("muestra_anonimizada", []),
            "filas": info.get("filas"),
        }]
    for tbl in info.get("tablas", []):
        out.append("")
        out.append(f"--- Tabla: {tbl.get('nombre', '?')} ---")
        if "filas" in tbl and tbl["filas"] is not None:
            out.append(f"Filas:        {tbl['filas']:,}" if isinstance(tbl['filas'], int) else f"Filas:        {tbl['filas']}")
        if "filas_aprox" in tbl and tbl["filas_aprox"] is not None:
            out.append(f"Filas aprox:  {tbl['filas_aprox']:,}")
        cols = tbl.get("columnas", [])
        out.append(f"Columnas:     {len(cols)}")
        if cols:
            out.append("")
            out.append(f"  {'#':<4}{'COLUMNA':<35}{'TIPO':<18}{'PK':<4}{'PII':<6}{'PLACEHOLDER':<14}")
            out.append(f"  {'-'*4}{'-'*35}{'-'*18}{'-'*4}{'-'*6}{'-'*14}")
            for i, c in enumerate(cols, 1):
                out.append(
                    f"  {i:<4}{c.get('nombre','?')[:34]:<35}"
                    f"{str(c.get('tipo','?'))[:17]:<18}"
                    f"{'SI' if c.get('pk') else 'no':<4}"
                    f"{'SI' if c.get('pii') else 'no':<6}"
                    f"{c.get('placeholder','<TEXTO>'):<14}"
                )
            if tbl.get("muestra_anonimizada"):
                out.append("")
                out.append("  Ejemplo (anonimizado, 1 fila):")
                first = tbl["muestra_anonimizada"][0]
                for c in cols:
                    v = first.get(c.get("nombre", "?"), "<N/A>")
                    if v is None:
                        v = "<NULL>"
                    out.append(f"    {c.get('nombre','?'):<35} = {str(v)[:70]}")
        if tbl.get("error"):
            out.append(f"  ERROR: {tbl['error']}")

    for hoja in info.get("hojas", []):
        out.append("")
        out.append(f"--- Hoja: {hoja.get('nombre', '?')} ---")
        out.append(f"Filas aprox:  {hoja.get('filas_aprox', '?')}")
        cols = hoja.get("columnas", [])
        out.append(f"Columnas:     {len(cols)}")
        for i, c in enumerate(cols, 1):
            out.append(
                f"  {i:<4}{c.get('nombre','?')[:34]:<35}"
                f"{c.get('placeholder','<TEXTO>'):<14}"
                f"{'PII' if c.get('pii') else '   '}"
            )
        if hoja.get("muestra_anonimizada"):
            out.append("")
            out.append("  Ejemplo (anonimizado, 1 fila):")
            first = hoja["muestra_anonimizada"][0]
            for c in cols:
                v = first.get(c.get("nombre", "?"), "<N/A>")
                if v is None:
                    v = "<NULL>"
                out.append(f"    {c.get('nombre','?'):<35} = {str(v)[:70]}")

    if "extracto" in info:
        out.append("")
        out.append("--- Extracto (primeras páginas) ---")
        out.append(info["extracto"])

    if "contenido_resumen" in info:
        out.append("")
        out.append("--- Contenido del archivo comprimido ---")
        out.append(info["contenido_resumen"])

    if "extracto_head" in info:
        out.append("")
        out.append("--- Extracto SQL (head) ---")
        out.append(info["extracto_head"])

    if "muestra_archivos" in info:
        out.append("")
        out.append("--- Archivos dentro (muestra) ---")
        for a in info["muestra_archivos"]:
            out.append(f"  {a['nombre']:<60} {a['tamano']:>12,}  (comp: {a['comprimido']:>12,})")

    out.append("")
    out_text = "\n".join(out)
    path.write_text(out_text, encoding="utf-8")
    return len(out_text)


# --- MAIN ------------------------------------------------------------------
def catalog_one(path: Path) -> None:
    motor = detect_engine(path)
    size_mb = path.stat().st_size / 1024 / 1024
    print(f"[{motor:>11}] {path.name}  ({size_mb:.1f} MB) ...", flush=True)
    t0 = time.time()
    if motor == "sqlite":
        info = catalog_sqlite(path)
    elif motor == "duckdb":
        info = catalog_duckdb(path)
    elif motor in ("csv", "tsv", "txt"):
        info = catalog_delimited(path, motor)
    elif motor == "xlsx":
        info = catalog_xlsx(path)
    elif motor == "pdf":
        info = catalog_pdf(path)
    elif motor in ("zip", "rar", "7z"):
        info = catalog_archive(path, motor)
    elif motor == "sql":
        info = catalog_sql(path)
    else:
        info = {"motor": motor, "archivo": path.name, "error": "motor no soportado"}
    info["tamano_mb"] = size_mb
    elapsed = time.time() - t0
    safe_name = path.name.replace("/", "_").replace(" ", "_")
    out_path = OUT / f"{safe_name}.catalog.txt"
    n_bytes = write_report(out_path, info)
    print(f"            -> {out_path.relative_to(OUT.parent.parent)}  ({n_bytes:,} chars, {elapsed:.1f}s)", flush=True)


def main():
    if not SRC.exists():
        print(f"ERROR: {SRC} no existe", file=sys.stderr)
        sys.exit(1)
    files = sorted(p for p in SRC.iterdir()
                   if p.is_file()
                   and not p.name.endswith(("-shm", "-wal", "-journal")))
    if not files:
        print(f"AVISO: {SRC} está vacío")
        return
    print(f"Catalogando {len(files)} archivos en {SRC}")
    print(f"Salida: {OUT}/")
    print()
    t0 = time.time()
    for p in files:
        try:
            catalog_one(p)
        except Exception as e:
            print(f"EXCEPCION con {p.name}: {e}", file=sys.stderr)
    print()
    print(f"Total: {len(files)} archivos en {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
