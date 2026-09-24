"""
materializar_docentes.py — Materializa docentes_edomex.duckdb al
esquema comun main.personas para attachear al backend con el mismo
patron que las bases bancarias.

Fuente: nuevas a normalizar/docentes_edomex.duckdb, tabla `docentes`
(51,538 filas, ya en DuckDB). Cols: clave_ct, tipo_plaza, rfc, curp,
nombre, funcion, funcion_detalle, horas, sueldo.
"""
import time
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parent.parent
BASES_DIR = ROOT / "bases"
SRC = ROOT / "nuevas a normalizar" / "docentes_edomex.duckdb"

TELCEL_COLS = [
    "cuenta","padre","st_cta","st_cob","cls_crd","tipo","ciclo",
    "fecha_activ","fecha_cancel","fecha_term","plan_actual","telefono",
    "st_tel","motivo","fecha_cel","gsm_ind","marca","modelo",
    "dat_orig","dat_actual","asesor","adendum","plazo","nombre1",
    "nombre2","rfc","domicilio","numero","interior","colonia",
    "ciudad","edo","cp","tel_contacto","esn","imei","iccid",
    "fecha_plan","fecha_eq","tp_rfc","tp_pago","tc","contacto1",
    "contacto2","plan_orig","renaut",
]

if __name__ == "__main__":
    out = BASES_DIR / "docentes_v1.duckdb"
    if out.exists(): out.unlink()
    con = duckdb.connect(str(out))
    con.execute("SET memory_limit='2GB';")
    con.execute(f"SET temp_directory='{BASES_DIR / '_duckdb_tmp'}';")

    cols = ', '.join(f'"{c}" VARCHAR' for c in TELCEL_COLS) + ', curp VARCHAR, sueldo VARCHAR, funcion VARCHAR, funcion_detalle VARCHAR, clave_ct VARCHAR'
    con.execute(f"CREATE TABLE main.personas ({cols})")

    con.execute(f"ATTACH '{SRC}' AS src (READ_ONLY)")

    t0 = time.time()
    con.execute("""
    INSERT INTO main.personas
    SELECT
        clave_ct AS cuenta,
        CAST(NULL AS VARCHAR) AS padre, CAST(NULL AS VARCHAR) AS st_cta,
        CAST(NULL AS VARCHAR) AS st_cob, CAST(NULL AS VARCHAR) AS cls_crd,
        CAST(tipo_plaza AS VARCHAR) AS tipo, CAST(NULL AS VARCHAR) AS ciclo,
        CAST(NULL AS VARCHAR) AS fecha_activ, CAST(NULL AS VARCHAR) AS fecha_cancel,
        CAST(NULL AS VARCHAR) AS fecha_term, CAST(NULL AS VARCHAR) AS plan_actual,
        CAST(NULL AS VARCHAR) AS telefono,
        CAST(NULL AS VARCHAR) AS st_tel, CAST(NULL AS VARCHAR) AS motivo,
        CAST(NULL AS VARCHAR) AS fecha_cel, CAST(NULL AS VARCHAR) AS gsm_ind,
        CAST(NULL AS VARCHAR) AS marca, CAST(NULL AS VARCHAR) AS modelo,
        CAST(NULL AS VARCHAR) AS dat_orig, CAST(NULL AS VARCHAR) AS dat_actual,
        CAST(NULL AS VARCHAR) AS asesor, CAST(NULL AS VARCHAR) AS adendum,
        CAST(horas AS VARCHAR) AS plazo,
        CASE
          WHEN array_length(string_split(TRIM(nombre), ' ')) <= 1 THEN TRIM(nombre)
          WHEN array_length(string_split(TRIM(nombre), ' ')) = 2 THEN string_split(TRIM(nombre), ' ')[1]
          WHEN array_length(string_split(TRIM(nombre), ' ')) = 3 THEN string_split(TRIM(nombre), ' ')[1] || ' ' || string_split(TRIM(nombre), ' ')[2]
          ELSE string_split(TRIM(nombre), ' ')[1] || ' ' || string_split(TRIM(nombre), ' ')[2]
        END AS nombre1,
        CASE
          WHEN array_length(string_split(TRIM(nombre), ' ')) <= 2 THEN NULL
          WHEN array_length(string_split(TRIM(nombre), ' ')) = 3 THEN string_split(TRIM(nombre), ' ')[3]
          ELSE list_aggregate(list_slice(string_split(TRIM(nombre), ' '), 3, 100), 'string_agg', ' ')
        END AS nombre2,
        UPPER(TRIM(rfc)) AS rfc,
        CAST(NULL AS VARCHAR) AS domicilio,
        CAST(NULL AS VARCHAR) AS numero,
        CAST(NULL AS VARCHAR) AS interior,
        CAST(NULL AS VARCHAR) AS colonia,
        CAST(NULL AS VARCHAR) AS ciudad,
        'MEX' AS edo,
        CAST(NULL AS VARCHAR) AS cp,
        CAST(NULL AS VARCHAR) AS tel_contacto,
        CAST(NULL AS VARCHAR) AS esn, CAST(NULL AS VARCHAR) AS imei,
        CAST(NULL AS VARCHAR) AS iccid, CAST(NULL AS VARCHAR) AS fecha_plan,
        CAST(NULL AS VARCHAR) AS fecha_eq, CAST(NULL AS VARCHAR) AS tp_rfc,
        CAST(NULL AS VARCHAR) AS tp_pago, CAST(NULL AS VARCHAR) AS tc,
        CAST(NULL AS VARCHAR) AS contacto1, CAST(NULL AS VARCHAR) AS contacto2,
        CAST(NULL AS VARCHAR) AS plan_orig, CAST(NULL AS VARCHAR) AS renaut,
        UPPER(TRIM(curp)) AS curp,
        CAST(sueldo AS VARCHAR) AS sueldo,
        funcion,
        funcion_detalle,
        clave_ct
    FROM src.docentes
    """)
    n = con.execute("SELECT COUNT(*) FROM main.personas").fetchone()[0]
    print(f"Insertadas: {n:,} filas en {time.time()-t0:.0f}s")

    con.execute("CREATE INDEX idx_rfc ON main.personas(rfc)")
    con.execute("CREATE INDEX idx_curp ON main.personas(curp)")

    n_rfc = con.execute("SELECT COUNT(DISTINCT rfc) FROM main.personas WHERE rfc IS NOT NULL").fetchone()[0]
    n_curp = con.execute("SELECT COUNT(DISTINCT curp) FROM main.personas WHERE curp IS NOT NULL").fetchone()[0]
    print(f"RFCs únicos: {n_rfc:,}  CURPs únicos: {n_curp:,}")

    con.execute("VACUUM")
    con.close()
    sz = out.stat().st_size / 1024 / 1024
    print(f"Archivo: {out} ({sz:.1f} MB)")
