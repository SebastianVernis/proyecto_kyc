"""
materializar_covid23.py — Materializa COVID23.rar -> Covid.csv (19.6M
filas, 130 columnas) COMPLETO: identidad + datos clinicos.

El usuario pidio explicitamente integrar TODOS los datos (no solo
identidad). Se crean DOS tablas:

  1. main.covid_clinico — TODAS las 130 columnas originales, tal cual
     vienen en la fuente (sintomas, comorbilidades, vacunacion, etc.)
  2. main.personas — subset compatible con el esquema comun (47 cols)
     para el lookup uniforme por CURP/RFC en el backend, con
     referencia cruzada al ID_REGISTRO para JOIN con covid_clinico
     si se necesitan los datos clinicos completos.
"""
import time
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parent.parent
BASES_DIR = ROOT / "bases"
SRC = ROOT / "nuevas a normalizar" / "_extraidos" / "COVID23" / "Covid_utf8.csv"

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
    out = BASES_DIR / "covid_v1.duckdb"
    if out.exists(): out.unlink()
    con = duckdb.connect(str(out))
    con.execute("SET memory_limit='6GB';")
    con.execute(f"SET temp_directory='{BASES_DIR / '_duckdb_tmp'}';")
    con.execute("PRAGMA threads=4;")

    t0 = time.time()
    print("=" * 60)
    print("PASO 1: main.covid_clinico (TODAS las 130 columnas)")
    print("=" * 60)
    con.execute(f"""
    CREATE TABLE main.covid_clinico AS
    SELECT * FROM read_csv(
        '{SRC}',
        delim='|', header=true, sample_size=-1,
        all_varchar=true, ignore_errors=true, max_line_size=5000000,
        strict_mode=false
    )
    """)
    n1 = con.execute("SELECT COUNT(*) FROM main.covid_clinico").fetchone()[0]
    print(f"  Insertadas: {n1:,} filas en {time.time()-t0:.0f}s")

    print("Creando indices en covid_clinico (CURP + ID_REGISTRO)...")
    t1 = time.time()
    con.execute('CREATE INDEX idx_covid_curp ON main.covid_clinico("CURP")')
    con.execute('CREATE INDEX idx_covid_idreg ON main.covid_clinico("ID_REGISTRO")')
    print(f"  {time.time()-t1:.0f}s")

    print()
    print("=" * 60)
    print("PASO 2: main.personas (subset compatible, 47 cols + curp)")
    print("=" * 60)
    t2 = time.time()
    cols = ', '.join(f'"{c}" VARCHAR' for c in TELCEL_COLS) + ', curp VARCHAR, id_registro VARCHAR'
    con.execute(f"CREATE TABLE main.personas ({cols})")

    con.execute("""
    INSERT INTO main.personas
    SELECT
        CAST(NULL AS VARCHAR) AS cuenta, CAST(NULL AS VARCHAR) AS padre,
        CAST(NULL AS VARCHAR) AS st_cta, CAST(NULL AS VARCHAR) AS st_cob,
        CAST(NULL AS VARCHAR) AS cls_crd, CAST(NULL AS VARCHAR) AS tipo,
        CAST(NULL AS VARCHAR) AS ciclo, CAST(NULL AS VARCHAR) AS fecha_activ,
        CAST(NULL AS VARCHAR) AS fecha_cancel, CAST(NULL AS VARCHAR) AS fecha_term,
        CAST(NULL AS VARCHAR) AS plan_actual,
        TRIM("TELEFONO") AS telefono,
        CAST(NULL AS VARCHAR) AS st_tel, CAST(NULL AS VARCHAR) AS motivo,
        CAST(NULL AS VARCHAR) AS fecha_cel, CAST(NULL AS VARCHAR) AS gsm_ind,
        CAST(NULL AS VARCHAR) AS marca, CAST(NULL AS VARCHAR) AS modelo,
        CAST(NULL AS VARCHAR) AS dat_orig, CAST(NULL AS VARCHAR) AS dat_actual,
        CAST(NULL AS VARCHAR) AS asesor, CAST(NULL AS VARCHAR) AS adendum,
        CAST(NULL AS VARCHAR) AS plazo,
        TRIM("NOMBRE") AS nombre1,
        CASE WHEN TRIM(COALESCE("APEPATER",'')) = '' AND TRIM(COALESCE("APEMATER",'')) = '' THEN NULL
             WHEN TRIM(COALESCE("APEPATER",'')) = '' THEN TRIM("APEMATER")
             WHEN TRIM(COALESCE("APEMATER",'')) = '' THEN TRIM("APEPATER")
             ELSE TRIM("APEPATER") || ' ' || TRIM("APEMATER")
        END AS nombre2,
        CAST(NULL AS VARCHAR) AS rfc,
        TRIM("DOMICILIO") AS domicilio,
        CAST(NULL AS VARCHAR) AS numero,
        CAST(NULL AS VARCHAR) AS interior,
        CAST(NULL AS VARCHAR) AS colonia,
        CAST(NULL AS VARCHAR) AS ciudad,
        TRIM("ENTIDAD") AS edo,
        TRIM("CP") AS cp,
        CAST(NULL AS VARCHAR) AS tel_contacto,
        CAST(NULL AS VARCHAR) AS esn, CAST(NULL AS VARCHAR) AS imei,
        CAST(NULL AS VARCHAR) AS iccid, CAST(NULL AS VARCHAR) AS fecha_plan,
        CAST(NULL AS VARCHAR) AS fecha_eq, CAST(NULL AS VARCHAR) AS tp_rfc,
        CAST(NULL AS VARCHAR) AS tp_pago, CAST(NULL AS VARCHAR) AS tc,
        CAST(NULL AS VARCHAR) AS contacto1, CAST(NULL AS VARCHAR) AS contacto2,
        CAST(NULL AS VARCHAR) AS plan_orig, CAST(NULL AS VARCHAR) AS renaut,
        UPPER(TRIM("CURP")) AS curp,
        "ID_REGISTRO" AS id_registro
    FROM main.covid_clinico
    """)
    n2 = con.execute("SELECT COUNT(*) FROM main.personas").fetchone()[0]
    print(f"  Insertadas: {n2:,} filas en {time.time()-t2:.0f}s")

    print("Creando indice idx_curp en personas...")
    t3 = time.time()
    con.execute("CREATE INDEX idx_curp ON main.personas(curp)")
    print(f"  {time.time()-t3:.0f}s")

    n_curp = con.execute("SELECT COUNT(*) FROM main.personas WHERE curp IS NOT NULL AND LENGTH(curp)=18").fetchone()[0]
    n_curp_u = con.execute("SELECT COUNT(DISTINCT curp) FROM main.personas WHERE curp IS NOT NULL AND LENGTH(curp)=18").fetchone()[0]
    print(f"CURPs válidos: {n_curp:,}  únicos: {n_curp_u:,}")

    print()
    print("VACUUM...")
    con.execute("VACUUM")
    con.close()
    sz = out.stat().st_size / 1024 / 1024 / 1024
    print(f"Archivo: {out} ({sz:.2f} GB)")
