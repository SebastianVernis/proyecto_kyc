#!/usr/bin/env python3
"""
Healthcheck del proyecto KYC consolidado.

Verifica que cada base en bases/ abre correctamente
en modo read-only y reporta conteo de filas.

Uso:
    # este servidor:
    /mnt/disco2/projects/kyc/.venv/bin/python healthcheck.py

    # en dev (cualquier path):
    PROYECTO_KYC_ROOT=/path/to/proyecto_kyc python3 healthcheck.py

Salida: JSON con estado por base + total de filas consultables.
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

import duckdb

ROOT = Path(os.environ.get("PROYECTO_KYC_ROOT", Path(__file__).resolve().parent))

BASES = [
    ("padron",           ROOT / "bases" / "padron.duckdb",          "duckdb"),
    ("telcel",           ROOT / "bases" / "telcel.duckdb",          "duckdb"),
    ("att",              ROOT / "bases" / "att.duckdb",             "duckdb"),
    ("empleadores",      ROOT / "bases" / "empleadores.duckdb",     "duckdb"),
    ("imss_asegurados",  ROOT / "bases" / "imss_asegurados.duckdb", "duckdb"),
    ("imss_segmentacion",ROOT / "bases" / "imss_segmentacion.duckdb","duckdb"),
    ("repuve",           ROOT / "bases" / "repuve.duckdb",          "duckdb"),
    ("fotos",            ROOT / "bases" / "fotos.duckdb",           "duckdb"),
    ("cfe",              ROOT / "bases" / "cfe.duckdb",             "duckdb"),
    ("auth",             ROOT / "bases" / "auth.db",                "sqlite"),
    ("geo",              ROOT / "bases" / "geo.db",                 "sqlite"),
    ("sepomex",          ROOT / "bases" / "sepomex.db",             "sqlite"),
    # 2026-08-24: store local del modulo Singula (no se consulta en runtime
    # pero vive en bases/ como el resto de infra).
    ("singula_cache",    ROOT / "bases" / "singula_cache.db",       "sqlite"),
]


def check_symlink(path: Path) -> dict:
    info = {"path": str(path), "exists": path.exists(), "is_symlink": path.is_symlink()}
    if path.is_symlink():
        target = os.readlink(path)
        info["target"] = target
        info["target_exists"] = Path(target).exists()
    return info


def check_duckdb(path: Path) -> dict:
    try:
        con = duckdb.connect(str(path), read_only=True)
        tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
        counts = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        con.close()
        return {"ok": True, "tables": tables, "rows": counts, "size_gb": round(path.stat().st_size / 1024**3, 3)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def check_sqlite(path: Path) -> dict:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        counts = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        con.close()
        return {"ok": True, "tables": tables, "rows": counts, "size_gb": round(path.stat().st_size / 1024**3, 3)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def main():
    print(f"=== Healthcheck proyecto_kyc ===\n")
    overall_ok = True
    summary = []
    total_rows = 0
    total_gb = 0.0

    for name, path, kind in BASES:
        if not path.exists():
            print(f"  [MISS] {name:20s} -> {path} (missing)")
            overall_ok = False
            summary.append({"name": name, "ok": False, "reason": "missing"})
            continue

        if kind == "duckdb":
            result = check_duckdb(path)
        else:
            result = check_sqlite(path)

        if result["ok"]:
            rows = sum(result["rows"].values())
            total_rows += rows
            total_gb += result["size_gb"]
            print(f"  [ OK ] {name:20s} {result['size_gb']:>7.3f} GB  {rows:>14,} filas  ({len(result['tables'])} tablas)")
            summary.append({"name": name, "ok": True, "size_gb": result["size_gb"], "rows": rows, "tables": result["tables"]})
        else:
            print(f"  [FAIL] {name:20s} {result['error']}")
            overall_ok = False
            summary.append({"name": name, "ok": False, "error": result["error"]})

    # HTMLs y backend existen?
    other = [
        ("buscar.html", ROOT / "frontend" / "buscar.html"),
        ("sujeto.html", ROOT / "frontend" / "sujeto.html"),
        ("login.html",  ROOT / "frontend" / "login.html"),
        ("admin.html",  ROOT / "frontend" / "admin.html"),
        ("servir.py",   ROOT / "backend" / "servir.py"),
        ("config.py",   ROOT / "backend" / "config.py"),
        ("providers/",  ROOT / "backend" / "providers"),
    ]
    print("\n--- Interfaz / Backend ---")
    for label, p in other:
        ok = p.exists()
        marker = "[ OK ]" if ok else "[MISS]"
        print(f"  {marker} {label:15s} {p}")
        if not ok:
            overall_ok = False

    print(f"\n=== TOTAL: {total_gb:,.2f} GB, {total_rows:,} filas consultables ===")
    print(f"=== Estado global: {'PASS' if overall_ok else 'FAIL'} ===")

    out = ROOT / "healthcheck.last.json"
    out.write_text(json.dumps({
        "overall_ok": overall_ok,
        "total_size_gb": round(total_gb, 3),
        "total_rows": total_rows,
        "bases": summary,
    }, indent=2, ensure_ascii=False))
    print(f"\nReporte guardado en: {out}")

    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
