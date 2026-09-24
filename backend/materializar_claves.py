"""
materializar_claves.py — Materializa las claves de match de cada base en un
índice unificado `bases/match_index.duckdb`, usando los adapters de
normalizar_direccion como UDF de DuckDB (lógica idéntica al adapter).

Cada base produce una tabla `dir_<base>` con:
    ref            rowid en la base origen
    cp, via, ext, col, ent   campos canónicos mínimos para el cruce
    k_via_ext, k_col_via_ext, k_via_cp, k_col_via   claves de match

Uso:
    python materializar_claves.py [base1 base2 ...]   # o todas si no se pasa
"""
from __future__ import annotations
import sys, time
from pathlib import Path
import duckdb

_BACKEND = Path(__file__).resolve().parent
_BASES_DIR = _BACKEND.parent / "bases"
sys.path.insert(0, str(_BACKEND))
import normalizar_direccion as N

INDEX_DB = str(_BASES_DIR / "match_index.duckdb")

# base -> (ruta, tabla, [columnas necesarias], adapter)
BASES = {
    "padron": (str(_BASES_DIR / "padron_v1.duckdb"), "main.padron",
               ["calle", "int", "ext", "colonia", "cp", "e", "d", "m", "s", "l", "mza", "nac"],
               lambda r: N.from_padron(r)),
    "cfe": (str(_BASES_DIR / "cfe_v1.duckdb"), "main.medidores",
            ["direccion", "calle_adicional_1", "calle_adicional_2", "colonia", "cp"],
            lambda r: N.from_cfe(r)),
    "telcel": (str(_BASES_DIR / "telcel_v1.duckdb"), "main.telcel",
               ["domicilio", "numero", "interior", "colonia", "ciudad", "edo", "cp"],
               lambda r: N.from_telcel(r)),
    "att": (str(_BASES_DIR / "att_v1.duckdb"), "main.att",
            ["direccion", "exterior", "interior", "colonia", "municipio", "estado"],
            lambda r: N.from_att(r)),
    "empleadores": (str(_BASES_DIR / "empleadores_v1.duckdb"), "main.empleadores",
                    ["ubicacion.calle", "ubicacion.numero_exterior", "ubicacion.numero_interior",
                     "ubicacion.colonia", "ubicacion.municipio", "ubicacion.entidad",
                     "ubicacion.codigopostal"],
                    lambda r: N.from_empleadores(r)),
    "imss_patron": (str(_BASES_DIR / "imss_asegurados_v1.duckdb"), "main.imss_2025",
                    ["domicilio_patron", "ciudad_estado", "cp5"],
                    lambda r: N.from_imss_patron(r)),
    "repuve": (str(_BASES_DIR / "repuve_v1.duckdb"), "main.repuve",
               ["dir_prop_fix"],
               lambda r: N.from_repuve(r)),
}

_STRUCT = ("STRUCT(cp VARCHAR, via VARCHAR, ext VARCHAR, col VARCHAR, ent INTEGER, "
           "k_via_ext VARCHAR, k_col_via_ext VARCHAR, k_via_cp VARCHAR, k_col_via VARCHAR)")


def _make_udf(adapter, cols):
    """Devuelve una función con aridad fija (= len(cols)) que DuckDB llama por
    fila -> struct de claves. DuckDB introspecciona la firma, así que no sirve
    *args: generamos un wrapper con parámetros explícitos.
    """
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


def materializar(con, base: str):
    path, tbl, cols, adapter = BASES[base]
    fname = f"mk_{base}"
    # (re)registrar UDF
    try:
        con.remove_function(fname)
    except Exception:
        pass
    con.create_function(fname, _make_udf(adapter, cols),
                        ["VARCHAR"] * len(cols), _STRUCT, type="native",
                        null_handling="special")
    col_sql = ", ".join(f'b."{c}"::VARCHAR' for c in cols)
    t0 = time.time()
    con.execute(f"ATTACH IF NOT EXISTS '{path}' AS src_{base} (READ_ONLY)")
    con.execute(f"""
        CREATE OR REPLACE TABLE dir_{base} AS
        WITH s AS (
            SELECT b.rowid AS ref, {fname}({col_sql}) AS k
            FROM src_{base}.{tbl} b
        )
        SELECT ref, k.cp, k.via, k.ext, k.col, k.ent,
               k.k_via_ext, k.k_col_via_ext, k.k_via_cp, k.k_col_via
        FROM s
    """)
    con.execute(f"DETACH src_{base}")
    n = con.execute(f"SELECT COUNT(*) FROM dir_{base}").fetchone()[0]
    dt = time.time() - t0
    print(f"  [{base}] {n:,} filas materializadas en {dt:.1f}s "
          f"({n/max(dt,0.001):,.0f} filas/s)")


def main(argv):
    # arg opcional out=/ruta.duckdb para escribir a un archivo propio (permite
    # correr varias bases en paralelo, cada proceso único-escritor de su file).
    out = INDEX_DB
    bases = []
    for a in argv:
        if a.startswith("out="):
            out = a[4:]
        else:
            bases.append(a)
    targets = bases or list(BASES)
    import os as _os
    mem = _os.environ.get("MK_MEM", "2GB")       # en server 32GB: MK_MEM=8GB
    threads = _os.environ.get("MK_THREADS", "2")  # y MK_THREADS=4
    con = duckdb.connect(out)
    con.execute(f"SET memory_limit='{mem}'; SET threads={threads};")
    con.execute(f"SET temp_directory='{_BASES_DIR / '_duckdb_tmp'}';")
    con.execute("SET preserve_insertion_order=false;")
    for base in targets:
        if base not in BASES:
            print(f"  [!] base desconocida: {base}"); continue
        materializar(con, base)
    con.close()
    print("Listo:", out)


if __name__ == "__main__":
    main(sys.argv[1:])
