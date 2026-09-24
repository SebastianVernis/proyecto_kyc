"""P5: Completar telefono_clean + nombre en las bases restantes."""
import duckdb, time, sys

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def add_telefono_clean(path, table, col='telefono', new_col='telefono_clean'):
    log(f"--- {path.split('/')[-1]} :: {table} ---")
    con = duckdb.connect(path, read_only=False)
    schema = con.execute(f"DESCRIBE {table}").fetchall()
    cols = [c[0] for c in schema]
    if new_col not in cols:
        t0 = time.time()
        con.execute(f"ALTER TABLE {table} ADD COLUMN {new_col} VARCHAR")
        con.execute(f"UPDATE {table} SET {new_col} = REGEXP_REPLACE(TRIM(COALESCE({col},'')), '[^0-9]', '', 'g') WHERE {new_col} IS NULL OR {new_col} = ''")
        log(f"  {new_col} poblado en {(time.time()-t0)/60:.1f}min")
        t0 = time.time()
        con.execute(f"CREATE INDEX IF NOT EXISTS idx_{table.replace('.','_')}_{new_col} ON {table}({new_col})")
        log(f"  idx {new_col} en {(time.time()-t0)/60:.1f}min")
    else:
        log(f"  {new_col} ya existe, skip")
    con.close()

def add_nombre_idx(path, table, cols):
    log(f"--- {path.split('/')[-1]} :: {table} (nombre idx) ---")
    con = duckdb.connect(path, read_only=False)
    stem = path.split('/')[-1].replace('.duckdb','')
    for c in cols:
        idx = f"idx_{stem}_{c}"
        t0 = time.time()
        con.execute(f"CREATE INDEX IF NOT EXISTS {idx} ON {table}({c})")
        log(f"  idx {c} en {(time.time()-t0)/60:.1f}min")
    con.close()

# Falta: telcel_master_v2 (44.6M), imss_segmentacion telefono, issste, repuve, att, cfe
add_telefono_clean("/home/sebastianvernis/proyectos/kyc/proyecto_kyc/bases/telcel_master_v2.duckdb",
                   "main.telcel_46m_proc")
add_telefono_clean("/home/sebastianvernis/proyectos/kyc/proyecto_kyc/bases/imss_segmentacion.duckdb",
                   "main.imss_personas")
add_nombre_idx("/home/sebastianvernis/proyectos/kyc/proyecto_kyc/bases/issste.duckdb",
               "main.empleados", ["paterno", "materno", "nombres"])
add_nombre_idx("/home/sebastianvernis/proyectos/kyc/proyecto_kyc/bases/repuve.duckdb",
               "main.repuve", ["nom_prop_fix", "rfc_clean"])
add_nombre_idx("/home/sebastianvernis/proyectos/kyc/proyecto_kyc/bases/att.duckdb",
               "main.att", ["nombres", "pat", "may"])
add_nombre_idx("/home/sebastianvernis/proyectos/kyc/proyecto_kyc/bases/telcel.duckdb",
               "main.telcel", ["nombre1", "nombre2"])
add_nombre_idx("/home/sebastianvernis/proyectos/kyc/proyecto_kyc/bases/cfe.duckdb",
               "main.medidores", ["nombre"])
log("DONE")
