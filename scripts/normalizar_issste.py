#!/usr/bin/env python3
"""normalizar_issste.py — renombrar columnas de issste.duckdb a snake_case corto.

Renombrado elegido (coherente con el resto del proyecto):
  empleado_id      → id
  apellido_paterno → paterno
  apellido_materno → materno
  nombre           → nombres
  nombramiento     → cargo
  sueldo_issste    → sueldo
  *_id en catalogos → id

Crea un backup en /tmp antes de modificar.
Uso:
  python3 normalizar_issste.py [--base /path/to/issste.duckdb]
"""
import shutil, sys, argparse
from pathlib import Path

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="/root/proyecto_kyc/bases/issste.duckdb")
    args = ap.parse_args()
    base = Path(args.base)
    bak = Path("/tmp") / (base.name + ".bak")
    shutil.copy(base, bak)
    print(f"Backup: {bak}")

    import duckdb
    con = duckdb.connect(str(base))

    renames_empleados = {
        "empleado_id": "id",
        "apellido_paterno": "paterno",
        "apellido_materno": "materno",
        "nombre": "nombres",
        "nombramiento": "cargo",
        "sueldo_issste": "sueldo",
    }
    print("\n=== empleados ===")
    for old, new in renames_empleados.items():
        con.execute(f"ALTER TABLE empleados RENAME COLUMN {old} TO {new}")
        print(f"  {old} -> {new}")

    catalog_renames = {
        "cat_ramos":       ("ramo_id", "id"),
        "cat_estados":     ("estado_id", "id"),
        "cat_entidades":   ("entidad_id", "id"),
        "cat_modalidades": ("modalidad_id", "id"),
        "cat_sectores":    ("sector_id", "id"),
    }
    for tbl, (old, new) in catalog_renames.items():
        print(f"\n=== {tbl} ===")
        con.execute(f"ALTER TABLE {tbl} RENAME COLUMN {old} TO {new}")
        print(f"  {old} -> {new}")

    con.execute("CHECKPOINT")
    con.close()
    print("\n=== OK ===")

if __name__ == "__main__":
    main()