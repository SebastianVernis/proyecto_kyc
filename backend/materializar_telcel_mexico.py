"""
materializar_telcel_mexico.py — Materializa TELCEL_MEXICO.7z (9 archivos
TELCEL 1-9.txt, formato IDENTICO a telcel_v1.duckdb: 46 cols con comillas
simples). Union directo sin transformación de esquema.
"""
import time
from pathlib import Path
import duckdb

BASES_DIR = Path(__file__).resolve().parent.parent / "bases"
SRC_DIR = Path("/tmp/telcel_mexico_7z")

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
    out = BASES_DIR / "telcel_v4.duckdb"
    if out.exists(): out.unlink()
    con = duckdb.connect(str(out))
    con.execute("SET memory_limit='4GB';")
    con.execute(f"SET temp_directory='{BASES_DIR / '_duckdb_tmp'}';")
    con.execute("PRAGMA threads=4;")

    cols = ', '.join(f'"{c}" VARCHAR' for c in TELCEL_COLS) + ', curp VARCHAR'
    con.execute(f"CREATE TABLE main.personas ({cols})")

    t0 = time.time()
    total = 0
    for i in range(1, 10):
        src = SRC_DIR / f"TELCEL {i}.txt"
        if not src.exists():
            print(f"  SKIP: {src} no existe")
            continue
        print(f"Procesando TELCEL {i}.txt...")
        tstart = time.time()
        try:
            con.execute(f"""
            INSERT INTO main.personas
            SELECT {', '.join(f'"{c}"' for c in TELCEL_COLS)}, CAST(NULL AS VARCHAR) AS curp
            FROM read_csv(
                '{src}',
                header=true,
                quote=chr(39),
                delim=',',
                sample_size=-1,
                all_varchar=true,
                ignore_errors=true,
                skip=2,
                max_line_size=50000000
            )
            """)
            n_after = con.execute("SELECT COUNT(*) FROM main.personas").fetchone()[0]
            print(f"  OK: total acumulado={n_after:,}  ({time.time()-tstart:.0f}s)")
            total = n_after
        except Exception as e:
            print(f"  ERROR en TELCEL {i}: {e}")
            continue

    print(f"\nTotal insertado: {total:,} en {time.time()-t0:.0f}s")

    print("Creando índice idx_rfc...")
    t1 = time.time()
    con.execute("""
    CREATE INDEX idx_rfc ON main.personas(
        (UPPER(TRIM(REGEXP_REPLACE(rfc, '[^A-Za-z0-9]', '', 'g'))))
    )
    """)
    print(f"  {time.time()-t1:.0f}s")

    # Cobertura
    n_total = con.execute("SELECT COUNT(*) FROM main.personas").fetchone()[0]
    n_rfc = con.execute("SELECT COUNT(*) FROM main.personas WHERE rfc IS NOT NULL AND LENGTH(TRIM(rfc)) >= 10").fetchone()[0]
    n_rfc_uniq = con.execute("""
        SELECT COUNT(DISTINCT UPPER(TRIM(REGEXP_REPLACE(rfc, '[^A-Za-z0-9]', '', 'g'))))
        FROM main.personas WHERE rfc IS NOT NULL AND LENGTH(TRIM(rfc)) >= 10
    """).fetchone()[0]
    print(f"Total={n_total:,}  RFCs válidos={n_rfc:,}  RFCs únicos={n_rfc_uniq:,}")

    con.execute("VACUUM")
    con.close()
    sz = out.stat().st_size / 1024 / 1024
    print(f"Archivo: {out} ({sz:.0f} MB)")
