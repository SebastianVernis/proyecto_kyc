#!/usr/bin/env python3
"""Crea la base perfil_completo.duckdb con la tabla de sujetos consolidados.

Uso:
  cd /home/sebastianvernis/proyectos/kyc/proyecto_kyc
  python3 backend/scripts/init_perfil_completo.py

O:
  python3 backend/scripts/init_perfil_completo.py --path /ruta/a/perfil_completo.duckdb
"""

import sys
from pathlib import Path

# defaults
BASE_DIR = Path(__file__).parent.parent.parent
PERFIL_DB = BASE_DIR / "bases" / "perfil_completo.duckdb"
SQL_FILE = Path(__file__).parent / "migrate_perfil_completo.sql"

def main():
    import duckdb

    # parse simple --path
    path = PERFIL_DB
    if "--path" in sys.argv:
        i = sys.argv.index("--path")
        path = Path(sys.argv[i + 1])

    print(f"Creando base en: {path}")

    # leer SQL de migración
    sql = SQL_FILE.read_text()
    print(f"Aplicando: {SQL_FILE.relative_to(BASE_DIR)}")

    con = duckdb.connect(str(path))
    con.execute(sql)

    # verificar
    tablas = con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()
    print(f"✓ Tablas creadas: {[t[0] for t in tablas]}")

    vistas = con.execute("SELECT table_name FROM information_schema.tables WHERE table_type='VIEW'").fetchall()
    print(f"✓ Vistas creadas: {[v[0] for v in vistas]}")

    # mostrar estructura de la tabla
    print("\n=== Estructura de perfil_completo ===")
    for row in con.execute("DESCRIBE perfil_completo").fetchall():
        print(f"  {row[0]:30s} {row[1]:20s}")

    con.close()
    print(f"\n✓ Base lista: {path}")

if __name__ == "__main__":
    main()
