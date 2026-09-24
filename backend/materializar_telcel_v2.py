"""
materializar_telcel_v2.py — Materializa el índice de match para `telcel 46M.db`
(esquema nuevo: telefono, nombre, rfc, direccion, colonia, municipio, estado, cp, ...)
sin tocar la base `telcel` original (que tiene esquema viejo: domicilio, numero, ...).

El adapter `from_telcel_v2` se define inline aquí (no modifico
normalizar_direccion.py para no romper compatibilidad con mkidx_telcel_v1.duckdb
ya generado para la base vieja).

Uso:
    python materializar_telcel_v2.py

Salida: bases/mkidx_telcel_v2.duckdb con tabla `dir_telcel_v2` (10 cols)
"""
from __future__ import annotations
import sys
import time
from pathlib import Path
import duckdb

_BACKEND = Path(__file__).resolve().parent
_BASES_DIR = _BACKEND.parent / "bases"
sys.path.insert(0, str(_BACKEND))
import normalizar_direccion as N  # noqa: E402

SRC = _BASES_DIR.parent / "nuevas a normalizar" / "telcel 46M.db"
OUT = _BASES_DIR / "mkidx_telcel_v2.duckdb"
TBL = "telcel_master"  # verificada en catalogación

# Esquema nuevo de telcel 46M.db
COLS = ["direccion", "colonia", "municipio", "estado", "cp"]


def from_telcel_v2(row: dict) -> dict:
    """Adapter para telcel 46M.db.

    Columnas disponibles: direccion, colonia, municipio, estado, cp
    Diferencias vs. telcel original:
      - No hay `numero` separado → hay que parsearlo de la dirección
        (formato típico: "CALLE 123", "CALLE EXT 123 INT 4", "AV X 45-A")
      - `estado` viene como nombre completo ("BAJA CALIFORNIA", "JALISCO")
        en lugar de abrev 3-letra
      - `direccion` puede incluir entrecalles con "/"
    """
    dom = N._clean_str(row.get("direccion"))
    calle, entre = dom, None
    ext = None
    if dom and "/" in dom:
        izq, der = dom.split("/", 1)
        calle = izq.strip() or None
        entre = der.strip().strip("/").strip() or None
        if entre and (len(entre) <= 4 or entre.upper() in ("Y", "ENTR", "ESQ", "ENTRE")):
            entre = None
    # Intentar extraer número exterior del final de la calle
    if calle:
        import re
        m = re.search(r"\s+(\d+\s*[A-Z]?(?:\s*-\s*\d+)?)$", calle, re.I)
        if m:
            ext_candidate = m.group(1).strip()
            # Solo tomar como ext si no es ruido (p.ej. "LOTE 5" sí, "MZ 12" sí)
            calle = calle[:m.start()].strip() or calle
            ext = ext_candidate
    # Si estado es nombre completo, resolver a clave
    estado = N._clean_str(row.get("estado"))
    entidad_clave = None
    if estado:
        c, _ = N._resolver_entidad(estado)
        entidad_clave = c
    return N.normalizar_direccion(
        calle=calle, ext=ext, int_=None,
        colonia=row.get("colonia"), cp=row.get("cp"),
        municipio=row.get("municipio"), entidad=estado,
        entrecalles=entre, fuente="telcel_v2",
    )


_STRUCT = ("STRUCT(cp VARCHAR, via VARCHAR, ext VARCHAR, col VARCHAR, ent INTEGER, "
           "k_via_ext VARCHAR, k_col_via_ext VARCHAR, k_via_cp VARCHAR, k_col_via VARCHAR)")


def _make_udf(adapter, cols):
    def _inner(vals):
        row = {c: v for c, v in zip(cols, vals)}
        c = adapter(row)
        ks = N.clave_match(c)
        col = N._norm_texto(c["nombre_asentamiento"])
        return {
            "cp": c["cp"], "via": N._norm_vialidad(c["nombre_vialidad"]),
            "ext": c["num_ext"], "col": (col[:15] if col else None),
            "ent": c["entidad_clave"],
            "k_via_ext": ks["k_via_ext"], "k_col_via_ext": ks["k_col_via_ext"],
            "k_via_cp": ks["k_via_cp"], "k_col_via": ks["k_col_via"],
        }
    n = len(cols)
    params = ", ".join(f"a{i}" for i in range(n))
    args = ", ".join(f"a{i}" for i in range(n))
    ns = {"_inner": _inner}
    exec(f"def _w({params}):\n    return _inner([{args}])", ns)
    return ns["_w"]


def materializar():
    if not SRC.exists():
        print(f"ERROR: no existe {SRC}")
        sys.exit(1)
    if OUT.exists():
        print(f"AVISO: {OUT.name} ya existe. Se sobreescribirá.")
        OUT.unlink()
    # DuckDB sqlite_scanner no acepta rutas con espacios; hardlink a path limpio
    tmp_db = _BASES_DIR / "_duckdb_tmp" / "telcel_46M.db"
    if not tmp_db.exists():
        import subprocess
        r = subprocess.run(["ln", str(SRC), str(tmp_db)], capture_output=True)
        if r.returncode != 0:
            # Si ln falla (cross-device), copiar
            print(f"  [!] ln falló: {r.stderr.decode()[:200]}. Copiando (8.6GB)...")
            subprocess.run(["cp", str(SRC), str(tmp_db)], check=True)
    con = duckdb.connect(str(OUT))
    import os as _os
    mem = _os.environ.get("MK_MEM", "3GB")        # 7.7GB RAM, dejar 3GB al UDF
    threads = _os.environ.get("MK_THREADS", "2")
    con.execute(f"SET memory_limit='{mem}'; SET threads={threads};")
    con.execute(f"SET temp_directory='{_BASES_DIR / '_duckdb_tmp'}';")
    con.execute("SET preserve_insertion_order=false;")
    con.execute("SET checkpoint_threshold='1GB';")  # checkpoint frecuente para no perder todo
    fname = "mk_telcel_v2"
    try:
        con.remove_function(fname)
    except Exception:
        pass
    con.create_function(fname, _make_udf(from_telcel_v2, COLS),
                        ["VARCHAR"] * len(COLS), _STRUCT, type="native",
                        null_handling="special")
    try:
        con.execute("INSTALL sqlite; LOAD sqlite;")
        con.execute(f"ATTACH '{tmp_db}' AS src_v2 (TYPE SQLITE, READ_ONLY)")
    except Exception as e:
        print(f"  [!] sqlite_scanner no disponible: {e}")
        con.close()
        sys.exit(1)
    col_inner = ", ".join(f'"{c}"::VARCHAR' for c in COLS)
    col_outer = ", ".join(COLS)
    print(f"  Materializando dir_telcel_v2 desde {SRC.name}...")
    t0 = time.time()
    # Patrón: UDF escalar en SELECT, devuelve STRUCT, se desempaqueta abajo
    con.execute(f"""
        CREATE OR REPLACE TABLE dir_telcel_v2 AS
        SELECT ref,
               k.cp AS cp, k.via AS via, k.ext AS ext, k.col AS col, k.ent AS ent,
               k.k_via_ext AS k_via_ext, k.k_col_via_ext AS k_col_via_ext,
               k.k_via_cp AS k_via_cp, k.k_col_via AS k_col_via
        FROM (
            SELECT b.rowid AS ref, {fname}({col_outer}) AS k
            FROM src_v2.{TBL} b
        )
    """)
    con.execute("DETACH src_v2")
    n = con.execute("SELECT COUNT(*) FROM dir_telcel_v2").fetchone()[0]
    dt = time.time() - t0
    print(f"  [telcel_v2] {n:,} filas materializadas en {dt:.1f}s "
          f"({n/max(dt,0.001):,.0f} filas/s)")
    con.execute("VACUUM")
    con.close()
    print(f"Listo: {OUT} ({OUT.stat().st_size/1024/1024:.1f} MB)")


if __name__ == "__main__":
    materializar()
