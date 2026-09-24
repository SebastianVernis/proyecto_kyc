"""
materializar_pendientes.py — Materializa las bases "nuevas a normalizar/"
a .duckdb usando DuckDB SQL nativo (read_csv + transformaciones en SQL).
~30x más rápido que loop Python.

Bases: telcel1, CITIBANAMEX, BANORTE, HSBC, SANTANDER×7, Bancoppel,
       Amex, Bancomer, Clavijero.
"""
import sys
import time
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parent.parent
BASES_DIR = ROOT / "bases"
SRC_DIR = ROOT / "nuevas a normalizar"

# Esquema viejo de telcel (46 cols) — target
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

TELCEL_COLS_SQL = ', '.join(f'"{c}"' for c in TELCEL_COLS)


def init_db(out_path):
    """Crea db nueva con tabla personas de 46 cols VARCHAR."""
    out_path = Path(out_path)
    if out_path.exists(): out_path.unlink()
    con = duckdb.connect(str(out_path))
    con.execute(f"SET memory_limit='4GB';")
    con.execute(f"SET temp_directory='{BASES_DIR / '_duckdb_tmp'}';")
    con.execute(f"PRAGMA threads=2;")
    cols = ', '.join(f'"{c}" VARCHAR' for c in TELCEL_COLS) + ', curp VARCHAR'
    con.execute(f"CREATE TABLE main.personas ({cols})")
    return con


def make_split_nombre_sql(col_in, n1_col, n2_col):
    """SQL para split NOMBRE COMPLETO → (nombre1, nombre2)."""
    return f"""
    TRIM({col_in}) AS {n1_col}_raw,
    CASE
      WHEN TRIM({col_in}) IS NULL OR TRIM({col_in}) = '' THEN NULL
      WHEN array_length(string_split(TRIM({col_in}), ' ')) <= 1 THEN TRIM({col_in})
      WHEN array_length(string_split(TRIM({col_in}), ' ')) = 2 THEN string_split(TRIM({col_in}), ' ')[1]
      WHEN array_length(string_split(TRIM({col_in}), ' ')) = 3 THEN string_split(TRIM({col_in}), ' ')[1] || ' ' || string_split(TRIM({col_in}), ' ')[2]
      ELSE string_split(TRIM({col_in}), ' ')[1] || ' ' || string_split(TRIM({col_in}), ' ')[2]
    END AS {n1_col},
    CASE
      WHEN TRIM({col_in}) IS NULL OR TRIM({col_in}) = '' THEN NULL
      WHEN array_length(string_split(TRIM({col_in}), ' ')) <= 2 THEN NULL
      WHEN array_length(string_split(TRIM({col_in}), ' ')) = 3 THEN string_split(TRIM({col_in}), ' ')[3]
      ELSE list_aggregate(list_slice(string_split(TRIM({col_in}), ' '), 3, 100), 'string_agg', ' ')
    END AS {n2_col}
    """


def make_split_nombre_sql_v2(col_n, col_p, col_m, c1, c2):
    """Usa cols separadas para nombre, paterno, materno → (c1, c2)."""
    return f"""
    TRIM({col_n}) AS {c1},
    CASE
      WHEN TRIM({col_p}) IS NULL AND TRIM({col_m}) IS NULL THEN NULL
      WHEN TRIM({col_p}) IS NULL THEN TRIM({col_m})
      WHEN TRIM({col_m}) IS NULL THEN TRIM({col_p})
      ELSE TRIM({col_p}) || ' ' || TRIM({col_m})
    END AS {c2}
    """


def report(con, label):
    n_total = con.execute("SELECT COUNT(*) FROM main.personas").fetchone()[0]
    n_rfc = con.execute(f"SELECT COUNT(*) FROM main.personas WHERE rfc IS NOT NULL AND LENGTH(TRIM(rfc)) >= 10").fetchone()[0]
    n_rfc_uniq = con.execute(f"SELECT COUNT(DISTINCT UPPER(TRIM(rfc))) FROM main.personas WHERE rfc IS NOT NULL AND LENGTH(TRIM(rfc)) >= 10").fetchone()[0]
    n_curp = con.execute(f"SELECT COUNT(*) FROM main.personas WHERE curp IS NOT NULL AND LENGTH(TRIM(curp)) = 18").fetchone()[0]
    n_curp_uniq = con.execute(f"SELECT COUNT(DISTINCT UPPER(TRIM(curp))) FROM main.personas WHERE curp IS NOT NULL AND LENGTH(TRIM(curp)) = 18").fetchone()[0]
    print(f"  {label}: total={n_total:,}  RFCs válidos={n_rfc:,} ({n_rfc_uniq:,} únicos)  CURPs={n_curp:,} ({n_curp_uniq:,} únicos)")


# ============== telcel1.csv (header, 16 cols, RFC+tel) ==============
def mat_telcel1():
    print("=" * 60)
    print("telcel1.csv (7.3M, header 16 cols)")
    print("=" * 60)
    t0 = time.time()
    src = SRC_DIR / "telcel1.csv"
    out = BASES_DIR / "telcel_v2.duckdb"
    con = init_db(out)

    # Read CSV directo, mapear cols al esquema telcel
    split_sql = make_split_nombre_sql('nombre_rs', '_nombre1', '_nombre2')
    con.execute(f"""
    INSERT INTO main.personas
    SELECT
        CAST(NULL AS VARCHAR) AS cuenta, CAST(NULL AS VARCHAR) AS padre,
        CAST(NULL AS VARCHAR) AS st_cta, CAST(NULL AS VARCHAR) AS st_cob,
        CAST(NULL AS VARCHAR) AS cls_crd, CAST(NULL AS VARCHAR) AS tipo,
        CAST(NULL AS VARCHAR) AS ciclo, CAST(NULL AS VARCHAR) AS fecha_activ,
        CAST(NULL AS VARCHAR) AS fecha_cancel, CAST(NULL AS VARCHAR) AS fecha_term,
        CAST(NULL AS VARCHAR) AS plan_actual,
        TRIM(msisdn) AS telefono,
        CAST(NULL AS VARCHAR) AS st_tel, CAST(NULL AS VARCHAR) AS motivo,
        CAST(NULL AS VARCHAR) AS fecha_cel, CAST(NULL AS VARCHAR) AS gsm_ind,
        t_red AS marca, CAST(NULL AS VARCHAR) AS modelo,
        CAST(NULL AS VARCHAR) AS dat_orig, CAST(NULL AS VARCHAR) AS dat_actual,
        r_social AS asesor, CAST(NULL AS VARCHAR) AS adendum,
        CAST(NULL AS VARCHAR) AS plazo,
        {_split_nombre_sql_inline('nombre_rs', 'nombre1', 'nombre2')},
        UPPER(TRIM(rfc)) AS rfc,
        dir_calle AS domicilio, CAST(NULL AS VARCHAR) AS numero,
        CAST(NULL AS VARCHAR) AS interior, dir_col AS colonia,
        dir_ciudad AS ciudad, CAST(NULL AS VARCHAR) AS edo,
        TRIM(cp) AS cp, CAST(NULL AS VARCHAR) AS tel_contacto,
        CAST(NULL AS VARCHAR) AS esn, imai AS imei, iccid AS iccid,
        CAST(NULL AS VARCHAR) AS fecha_plan, CAST(NULL AS VARCHAR) AS fecha_eq,
        CAST(NULL AS VARCHAR) AS tp_rfc, CAST(NULL AS VARCHAR) AS tp_pago,
        CAST(NULL AS VARCHAR) AS tc, CAST(NULL AS VARCHAR) AS contacto1,
        CAST(NULL AS VARCHAR) AS contacto2, CAST(NULL AS VARCHAR) AS plan_orig,
        CAST(NULL AS VARCHAR) AS renaut,
        CAST(NULL AS VARCHAR) AS curp
    FROM read_csv_auto('{src}', header=true, sample_size=-1, all_varchar=true, ignore_errors=true)
    """)
    print(f"  Insertadas: {time.time()-t0:.0f}s")
    con.execute("CREATE INDEX idx_rfc ON main.personas(rfc)")
    report(con, "telcel1")
    con.close()
    sz = out.stat().st_size / 1024 / 1024
    print(f"  Archivo: {sz:.0f} MB")


def _split_nombre_sql_inline(col_in, c1, c2):
    """SQL inline para split NOMBRE COMPLETO → (c1, c2). Retorna 2 columnas."""
    return f"""
    CASE
      WHEN TRIM({col_in}) IS NULL OR TRIM({col_in}) = '' THEN NULL
      WHEN array_length(string_split(TRIM({col_in}), ' ')) <= 1 THEN TRIM({col_in})
      WHEN array_length(string_split(TRIM({col_in}), ' ')) = 2 THEN string_split(TRIM({col_in}), ' ')[1]
      WHEN array_length(string_split(TRIM({col_in}), ' ')) = 3 THEN string_split(TRIM({col_in}), ' ')[1] || ' ' || string_split(TRIM({col_in}), ' ')[2]
      ELSE string_split(TRIM({col_in}), ' ')[1] || ' ' || string_split(TRIM({col_in}), ' ')[2]
    END AS {c1},
    CASE
      WHEN TRIM({col_in}) IS NULL OR TRIM({col_in}) = '' THEN NULL
      WHEN array_length(string_split(TRIM({col_in}), ' ')) <= 2 THEN NULL
      WHEN array_length(string_split(TRIM({col_in}), ' ')) = 3 THEN string_split(TRIM({col_in}), ' ')[3]
      ELSE list_aggregate(list_slice(string_split(TRIM({col_in}), ' '), 3, 100), 'string_agg', ' ')
    END AS {c2}
    """


def _split_nombre_sql_inline_old(col_in, c1, c2):
    """DEPRECATED: versión con columna extra raw_n (3 cols)."""
    return f"""
    TRIM({col_in}) AS raw_n,
    CASE
      WHEN TRIM({col_in}) IS NULL OR TRIM({col_in}) = '' THEN NULL
      WHEN array_length(string_split(TRIM({col_in}), ' ')) <= 1 THEN TRIM({col_in})
      WHEN array_length(string_split(TRIM({col_in}), ' ')) = 2 THEN string_split(TRIM({col_in}), ' ')[1]
      WHEN array_length(string_split(TRIM({col_in}), ' ')) = 3 THEN string_split(TRIM({col_in}), ' ')[1] || ' ' || string_split(TRIM({col_in}), ' ')[2]
      ELSE string_split(TRIM({col_in}), ' ')[1] || ' ' || string_split(TRIM({col_in}), ' ')[2]
    END AS {c1},
    CASE
      WHEN TRIM({col_in}) IS NULL OR TRIM({col_in}) = '' THEN NULL
      WHEN array_length(string_split(TRIM({col_in}), ' ')) <= 2 THEN NULL
      WHEN array_length(string_split(TRIM({col_in}), ' ')) = 3 THEN string_split(TRIM({col_in}), ' ')[3]
      ELSE list_aggregate(list_slice(string_split(TRIM({col_in}), ' '), 3, 100), 'string_agg', ' ')
    END AS {c2}
    """


# ============== CITIBANAMEX.csv (NO header, 29 cols) ==============
def mat_citibanamex():
    print("=" * 60)
    print("CITIBANAMEX.csv (2.6M, no header, 29 cols)")
    print("=" * 60)
    t0 = time.time()
    src = SRC_DIR / "BANCO CITIBANAMEX.csv"
    out = BASES_DIR / "citibanamex_v1.duckdb"
    con = init_db(out)
    # CITIBANAMEX cols por catálogo+muestra:
    # 1:paterno 2:materno 3:nombre 4:nombre2 5:num_sucursal
    # 6:fechanac 7:tel1 8:tel2 9:empresa_trabajo 10:tipo_empresa
    # 12:num_ext 13:vialidad 16:municipio 18:cp 19:colonia
    # 22:accion_alt 23:genero 24:num_cliente 25:num_cuenta 26:status
    # 27:fecha_apertura 28:pais 29:rfc
    con.execute(f"""
    INSERT INTO main.personas
    WITH raw AS (
        SELECT * FROM read_csv('{src}', header=false, sample_size=-1, all_varchar=true, ignore_errors=true,
            columns={{'c01':'VARCHAR','c02':'VARCHAR','c03':'VARCHAR','c04':'VARCHAR','c05':'VARCHAR',
                'c06':'VARCHAR','c07':'VARCHAR','c08':'VARCHAR','c09':'VARCHAR','c10':'VARCHAR',
                'c11':'VARCHAR','c12':'VARCHAR','c13':'VARCHAR','c14':'VARCHAR','c15':'VARCHAR',
                'c16':'VARCHAR','c17':'VARCHAR','c18':'VARCHAR','c19':'VARCHAR','c20':'VARCHAR',
                'c21':'VARCHAR','c22':'VARCHAR','c23':'VARCHAR','c24':'VARCHAR','c25':'VARCHAR',
                'c26':'VARCHAR','c27':'VARCHAR','c28':'VARCHAR','c29':'VARCHAR'}})
    )
    SELECT
        CAST(c25 AS VARCHAR) AS cuenta,
        CAST(NULL AS VARCHAR) AS padre, CAST(NULL AS VARCHAR) AS st_cta,
        CAST(NULL AS VARCHAR) AS st_cob, CAST(NULL AS VARCHAR) AS cls_crd,
        CAST(NULL AS VARCHAR) AS tipo, CAST(NULL AS VARCHAR) AS ciclo,
        CAST(NULL AS VARCHAR) AS fecha_activ, CAST(NULL AS VARCHAR) AS fecha_cancel,
        CAST(NULL AS VARCHAR) AS fecha_term, CAST(NULL AS VARCHAR) AS plan_actual,
        CAST(c07 AS VARCHAR) AS telefono,
        CAST(NULL AS VARCHAR) AS st_tel, CAST(NULL AS VARCHAR) AS motivo,
        CAST(NULL AS VARCHAR) AS fecha_cel, CAST(NULL AS VARCHAR) AS gsm_ind,
        CAST(NULL AS VARCHAR) AS marca, CAST(NULL AS VARCHAR) AS modelo,
        CAST(NULL AS VARCHAR) AS dat_orig, CAST(NULL AS VARCHAR) AS dat_actual,
        CAST(c09 AS VARCHAR) AS asesor, CAST(NULL AS VARCHAR) AS adendum,
        CAST(NULL AS VARCHAR) AS plazo,
        {make_split_nombre_sql_v2('CAST(c03 AS VARCHAR)', 'CAST(c01 AS VARCHAR)', 'CAST(c02 AS VARCHAR)', 'nombre1', 'nombre2')},
        UPPER(TRIM(CAST(c29 AS VARCHAR))) AS rfc,
        CAST(c13 AS VARCHAR) AS domicilio,
        CAST(c12 AS VARCHAR) AS numero,
        CAST(NULL AS VARCHAR) AS interior,
        CAST(c19 AS VARCHAR) AS colonia,
        CAST(c16 AS VARCHAR) AS ciudad,
        CAST(NULL AS VARCHAR) AS edo,
        CAST(c18 AS VARCHAR) AS cp,
        CAST(NULL AS VARCHAR) AS tel_contacto,
        CAST(NULL AS VARCHAR) AS esn, CAST(NULL AS VARCHAR) AS imei,
        CAST(NULL AS VARCHAR) AS iccid, CAST(NULL AS VARCHAR) AS fecha_plan,
        CAST(NULL AS VARCHAR) AS fecha_eq, CAST(NULL AS VARCHAR) AS tp_rfc,
        CAST(NULL AS VARCHAR) AS tp_pago, CAST(NULL AS VARCHAR) AS tc,
        CAST(NULL AS VARCHAR) AS contacto1, CAST(NULL AS VARCHAR) AS contacto2,
        CAST(NULL AS VARCHAR) AS plan_orig, CAST(NULL AS VARCHAR) AS renaut,
        CAST(NULL AS VARCHAR) AS curp
    FROM raw
    """)
    print(f"  Insertadas: {time.time()-t0:.0f}s")
    con.execute("CREATE INDEX idx_rfc ON main.personas(rfc)")
    report(con, "citibanamex")
    con.close()
    sz = out.stat().st_size / 1024 / 1024
    print(f"  Archivo: {sz:.0f} MB")


# ============== BANORTE Data Base.csv (header, 22 cols) ==============
def mat_banorte():
    print("=" * 60)
    print("BANORTE Data Base.csv (2M, header 22 cols)")
    print("=" * 60)
    t0 = time.time()
    src = SRC_DIR / "Banorte Data Base (1).csv"
    out = BASES_DIR / "banorte_v1.duckdb"
    con = init_db(out)
    con.execute(f"""
    INSERT INTO main.personas
    SELECT
        CAST(NULL AS VARCHAR) AS cuenta,
        CAST(NULL AS VARCHAR) AS padre, CAST(NULL AS VARCHAR) AS st_cta,
        CAST(NULL AS VARCHAR) AS st_cob, CAST(NULL AS VARCHAR) AS cls_crd,
        CAST(NULL AS VARCHAR) AS tipo, CAST(NULL AS VARCHAR) AS ciclo,
        CAST(NULL AS VARCHAR) AS fecha_activ, CAST(NULL AS VARCHAR) AS fecha_cancel,
        CAST(NULL AS VARCHAR) AS fecha_term, CAST(NULL AS VARCHAR) AS plan_actual,
        CAST(TEL1 AS VARCHAR) AS telefono,
        CAST(NULL AS VARCHAR) AS st_tel, CAST(NULL AS VARCHAR) AS motivo,
        CAST(NULL AS VARCHAR) AS fecha_cel, CAST(NULL AS VARCHAR) AS gsm_ind,
        CAST(NULL AS VARCHAR) AS marca, CAST(NULL AS VARCHAR) AS modelo,
        CAST(NULL AS VARCHAR) AS dat_orig, CAST(NULL AS VARCHAR) AS dat_actual,
        CAST(E_MAIL AS VARCHAR) AS asesor, CAST(NULL AS VARCHAR) AS adendum,
        CAST(NULL AS VARCHAR) AS plazo,
        {make_split_nombre_sql_v2('CAST(NOMBRE AS VARCHAR)', 'CAST(A_PATERNO AS VARCHAR)', 'CAST(A_MATERNO AS VARCHAR)', 'nombre1', 'nombre2')},
        UPPER(TRIM(RFC)) AS rfc,
        CAST(CALLE AS VARCHAR) AS domicilio,
        CAST(NUM AS VARCHAR) AS numero,
        CAST(NULL AS VARCHAR) AS interior,
        CAST(COLONIA AS VARCHAR) AS colonia,
        CAST(DES_DELMUN AS VARCHAR) AS ciudad,
        CAST(DESC_EDO AS VARCHAR) AS edo,
        CAST(CP AS VARCHAR) AS cp,
        CAST(NULL AS VARCHAR) AS tel_contacto,
        CAST(NULL AS VARCHAR) AS esn, CAST(NULL AS VARCHAR) AS imei,
        CAST(NULL AS VARCHAR) AS iccid, CAST(NULL AS VARCHAR) AS fecha_plan,
        CAST(NULL AS VARCHAR) AS fecha_eq, CAST(NULL AS VARCHAR) AS tp_rfc,
        CAST(NULL AS VARCHAR) AS tp_pago, CAST(NULL AS VARCHAR) AS tc,
        CAST(NULL AS VARCHAR) AS contacto1, CAST(NULL AS VARCHAR) AS contacto2,
        CAST(NULL AS VARCHAR) AS plan_orig, CAST(NULL AS VARCHAR) AS renaut,
        CAST(NULL AS VARCHAR) AS curp
    FROM read_csv_auto('{src}', header=true, sample_size=-1, all_varchar=true, ignore_errors=true)
    """)
    print(f"  Insertadas: {time.time()-t0:.0f}s")
    con.execute("CREATE INDEX idx_rfc ON main.personas(rfc)")
    report(con, "banorte")
    con.close()
    sz = out.stat().st_size / 1024 / 1024
    print(f"  Archivo: {sz:.0f} MB")


# ============== HSBC + SANTANDER (COBOL, header, ~7M) ==============
def mat_cobol(src_path, out_path, label, sep=','):
    print("=" * 60)
    print(f"{label} (COBOL)")
    print("=" * 60)
    t0 = time.time()
    src = Path(src_path)
    out = Path(out_path)
    con = init_db(out)
    # COBOL cols: U6CVEREG, U6NUMCTO, DMSSNUM, DMADDR1, DMADDR2, U6DELOMU,
    # U6ESTADO, DMCITY, DMZIP, U6LADTE1, U6TEL1, U6LADTE2, U6TEL2, DMNAME,
    # U6RFC, U6LICREA (+ U6ACCT para Santander)
    con.execute(f"""
    INSERT INTO main.personas
    SELECT
        CAST(U6NUMCTO AS VARCHAR) AS cuenta,
        CAST(NULL AS VARCHAR) AS padre, CAST(NULL AS VARCHAR) AS st_cta,
        CAST(NULL AS VARCHAR) AS st_cob, CAST(NULL AS VARCHAR) AS cls_crd,
        CAST(NULL AS VARCHAR) AS tipo, CAST(NULL AS VARCHAR) AS ciclo,
        CAST(NULL AS VARCHAR) AS fecha_activ, CAST(NULL AS VARCHAR) AS fecha_cancel,
        CAST(NULL AS VARCHAR) AS fecha_term, CAST(NULL AS VARCHAR) AS plan_actual,
        CAST(U6TEL1 AS VARCHAR) AS telefono,
        CAST(NULL AS VARCHAR) AS st_tel, CAST(NULL AS VARCHAR) AS motivo,
        CAST(NULL AS VARCHAR) AS fecha_cel, CAST(NULL AS VARCHAR) AS gsm_ind,
        CAST(NULL AS VARCHAR) AS marca, CAST(NULL AS VARCHAR) AS modelo,
        CAST(NULL AS VARCHAR) AS dat_orig, CAST(NULL AS VARCHAR) AS dat_actual,
        CAST(NULL AS VARCHAR) AS asesor, CAST(NULL AS VARCHAR) AS adendum,
        CAST(NULL AS VARCHAR) AS plazo,
        {_split_nombre_sql_inline('DMNAME', 'nombre1', 'nombre2')},
        UPPER(TRIM(U6RFC)) AS rfc,
        CAST(DMADDR1 AS VARCHAR) AS domicilio,
        CAST(NULL AS VARCHAR) AS numero,
        CAST(NULL AS VARCHAR) AS interior,
        CAST(DMADDR2 AS VARCHAR) AS colonia,
        CAST(DMCITY AS VARCHAR) AS ciudad,
        CAST(U6ESTADO AS VARCHAR) AS edo,
        CAST(DMZIP AS VARCHAR) AS cp,
        CAST(NULL AS VARCHAR) AS tel_contacto,
        CAST(NULL AS VARCHAR) AS esn, CAST(NULL AS VARCHAR) AS imei,
        CAST(NULL AS VARCHAR) AS iccid, CAST(NULL AS VARCHAR) AS fecha_plan,
        CAST(NULL AS VARCHAR) AS fecha_eq, CAST(NULL AS VARCHAR) AS tp_rfc,
        CAST(NULL AS VARCHAR) AS tp_pago, CAST(NULL AS VARCHAR) AS tc,
        CAST(NULL AS VARCHAR) AS contacto1, CAST(NULL AS VARCHAR) AS contacto2,
        CAST(NULL AS VARCHAR) AS plan_orig, CAST(NULL AS VARCHAR) AS renaut,
        CAST(NULL AS VARCHAR) AS curp
    FROM read_csv_auto('{src}', header=true, sep='{sep}', sample_size=-1, all_varchar=true, ignore_errors=true)
    """)
    print(f"  Insertadas: {time.time()-t0:.0f}s")
    con.execute("CREATE INDEX idx_rfc ON main.personas(rfc)")
    report(con, label)
    con.close()
    sz = out.stat().st_size / 1024 / 1024
    print(f"  Archivo: {sz:.0f} MB")


# ============== XLSX pequeños ==============
def mat_xlsx(src_path, out_path, label, header_row, adapter_name):
    print("=" * 60)
    print(f"{label}")
    print("=" * 60)
    t0 = time.time()
    import openpyxl
    src = SRC_DIR / src_path
    out = BASES_DIR / out_path
    con = init_db(out)

    wb = openpyxl.load_workbook(src, read_only=True)
    sh = wb["Hoja1"]

    rows = []
    n = 0
    for row in sh.iter_rows(values_only=True):
        n += 1
        if n == header_row: continue  # skip header
        rows.append(row)
    print(f"  {len(rows):,} filas leídas")

    # Insertar via Python con DuckDB executemany
    def _to_str(v):
        if v is None: return None
        if isinstance(v, (int, float)):
            if isinstance(v, float) and v.is_integer(): return str(int(v))
            return str(v)
        return str(v).strip()

    batch = []
    n_ins = 0
    for row in rows:
        try:
            if adapter_name == 'bancoppel':
                rec = _adapter_bancoppel(row)
            elif adapter_name == 'amex':
                rec = _adapter_amex(row)
            elif adapter_name == 'bancomer':
                rec = _adapter_bancomer(row)
            elif adapter_name == 'clavijero':
                rec = _adapter_clavijero(row)
            else:
                continue
            if not rec: continue
            vals = [rec.get(c) if rec.get(c) != '' else None for c in TELCEL_COLS]
            vals.append(rec.get('curp'))
            batch.append(vals)
            if len(batch) >= 5000:
                con.executemany(f"INSERT INTO main.personas VALUES ({','.join(['?']*(len(TELCEL_COLS)+1))})", batch)
                n_ins += len(batch)
                batch = []
        except Exception as e:
            continue
    if batch:
        con.executemany(f"INSERT INTO main.personas VALUES ({','.join(['?']*(len(TELCEL_COLS)+1))})", batch)
        n_ins += len(batch)
    print(f"  Insertadas: {n_ins:,} en {time.time()-t0:.0f}s")

    con.execute("CREATE INDEX idx_rfc ON main.personas(rfc)")
    con.execute("CREATE INDEX idx_curp ON main.personas(curp)")
    report(con, label)
    con.close()
    sz = out.stat().st_size / 1024 / 1024
    print(f"  Archivo: {sz:.0f} MB")


def _adapter_bancoppel(row):
    """BBDBancoppel.xlsx sin header. col[0]=tel, [1]=nombre, [2]=rfc, [3]=calle,
    [4]=col, [5]=cp, [6]=ciudad, [7]=estado, [8-9]=tel2/3, [10]=monto,
    [11]=tipo, [12]=plazo, [13]=tasa, [14]=banco
    """
    if len(row) < 3: return None
    nombre = str(row[1] or '').strip()
    n1, n2 = None, None
    parts = nombre.split()
    if parts:
        n1 = parts[0]
        n2 = ' '.join(parts[1:]) if len(parts) > 1 else None
    tel = str(row[0] or '').strip()
    cp = str(int(row[5])).strip() if row[5] else None
    return {
        "cuenta": None, "telefono": tel,
        "nombre1": n1, "nombre2": n2,
        "rfc": str(row[2] or '').strip().upper() if row[2] else None,
        "domicilio": str(row[3] or '').strip() if row[3] else None,
        "colonia": str(row[4] or '').strip() if row[4] else None,
        "ciudad": str(row[6] or '').strip() if row[6] else None,
        "edo": str(row[7] or '').strip() if row[7] else None,
        "cp": cp, "archivo_origen": "BBDBancoppel.xlsx", "curp": None,
    }


def _adapter_amex(row):
    """BDDAmex Hoja1: RFC, NOMBRE, CALLE, COL, EDO, CP, TEL, CEL, TDC, BANCO, col_10"""
    if len(row) < 8: return None
    nombre = str(row[1] or '').strip()
    parts = nombre.split()
    n1 = parts[0] if parts else None
    n2 = ' '.join(parts[1:]) if len(parts) > 1 else None
    return {
        "cuenta": str(row[8] or '').strip() if row[8] else None,
        "telefono": str(int(row[6])).strip() if row[6] else None,
        "nombre1": n1, "nombre2": n2,
        "rfc": str(row[0] or '').strip().upper() if row[0] else None,
        "domicilio": str(row[2] or '').strip() if row[2] else None,
        "colonia": str(row[3] or '').strip() if row[3] else None,
        "ciudad": str(row[4] or '').strip() if row[4] else None,
        "cp": str(int(row[5])).strip() if row[5] else None,
        "archivo_origen": "BDDAmex.xlsx", "curp": None,
    }


def _adapter_bancomer(row):
    """BDDBancomer Hoja1: Nombre, Paterno, Materno, Colonia, Producto, CP, Ciudad,
    Tarjeta, Vencimiento, Telefono, Estado, Municipio, Red, Telefono 2,
    Estado, Municipio, Red 2"""
    if len(row) < 11: return None
    pat = str(row[1] or '').strip()
    mat = str(row[2] or '').strip()
    nombre = str(row[0] or '').strip()
    n1 = pat or nombre
    n2 = mat
    return {
        "cuenta": str(row[7] or '').strip() if row[7] else None,
        "telefono": str(int(row[9])).strip() if row[9] else None,
        "nombre1": n1, "nombre2": n2,
        "rfc": None,
        "domicilio": str(row[3] or '').strip() if row[3] else None,
        "colonia": str(row[3] or '').strip() if row[3] else None,
        "ciudad": str(row[6] or '').strip() if row[6] else None,
        "edo": str(row[10] or '').strip() if row[10] else None,
        "cp": str(int(row[5])).strip() if row[5] else None,
        "archivo_origen": "BDDBancomer.xlsx", "curp": None,
    }


def _adapter_clavijero(row):
    """Clavijero con header DictReader."""
    nombre = row.get('nombre_completo', '').strip()
    parts = nombre.split()
    n1 = parts[0] if parts else None
    n2 = ' '.join(parts[1:]) if len(parts) > 1 else None
    return {
        "cuenta": None, "telefono": None,
        "nombre1": n1, "nombre2": n2,
        "rfc": None,
        "domicilio": None,
        "ciudad": row.get('municipio', '').strip(),
        "archivo_origen": "Clavijero.csv",
        "curp": row.get('curp', '').strip().upper(),
    }


def mat_hsbc2():
    """HSBC 2: 17 cols SIN header.
    1:source, 2:cta, 3:DMSSNUM, 4:dir1, 5:dir2, 6:municipio,
    7:estado, 8:ciudad, 9:cp, 10:?, 11:?, 12:lada, 13:tel,
    14:nombre, 15:rfc, 16:monto, 17:source(dup)
    """
    print("=" * 60)
    print("HSBC 2 (35.3k, no header, 17 cols)")
    print("=" * 60)
    t0 = time.time()
    src = SRC_DIR / "BANCO HSBC 2.csv"
    out = BASES_DIR / "hsbc_v2.duckdb"
    con = init_db(out)
    con.execute(f"""
    INSERT INTO main.personas
    WITH raw AS (
        SELECT * FROM read_csv('{src}', header=false, sample_size=-1, all_varchar=true, ignore_errors=true,
            columns={{'c01':'VARCHAR','c02':'VARCHAR','c03':'VARCHAR','c04':'VARCHAR','c05':'VARCHAR',
                'c06':'VARCHAR','c07':'VARCHAR','c08':'VARCHAR','c09':'VARCHAR','c10':'VARCHAR',
                'c11':'VARCHAR','c12':'VARCHAR','c13':'VARCHAR','c14':'VARCHAR','c15':'VARCHAR',
                'c16':'VARCHAR','c17':'VARCHAR'}})
    )
    SELECT
        CAST(c02 AS VARCHAR) AS cuenta,
        CAST(NULL AS VARCHAR) AS padre, CAST(NULL AS VARCHAR) AS st_cta,
        CAST(NULL AS VARCHAR) AS st_cob, CAST(NULL AS VARCHAR) AS cls_crd,
        CAST(NULL AS VARCHAR) AS tipo, CAST(NULL AS VARCHAR) AS ciclo,
        CAST(NULL AS VARCHAR) AS fecha_activ, CAST(NULL AS VARCHAR) AS fecha_cancel,
        CAST(NULL AS VARCHAR) AS fecha_term, CAST(NULL AS VARCHAR) AS plan_actual,
        CAST(c13 AS VARCHAR) AS telefono,
        CAST(NULL AS VARCHAR) AS st_tel, CAST(NULL AS VARCHAR) AS motivo,
        CAST(NULL AS VARCHAR) AS fecha_cel, CAST(NULL AS VARCHAR) AS gsm_ind,
        CAST(NULL AS VARCHAR) AS marca, CAST(NULL AS VARCHAR) AS modelo,
        CAST(NULL AS VARCHAR) AS dat_orig, CAST(NULL AS VARCHAR) AS dat_actual,
        CAST(NULL AS VARCHAR) AS asesor, CAST(NULL AS VARCHAR) AS adendum,
        CAST(NULL AS VARCHAR) AS plazo,
        {_split_nombre_sql_inline('c14', 'nombre1', 'nombre2')},
        UPPER(TRIM(c15)) AS rfc,
        CAST(c04 AS VARCHAR) AS domicilio,
        CAST(NULL AS VARCHAR) AS numero,
        CAST(NULL AS VARCHAR) AS interior,
        CAST(c05 AS VARCHAR) AS colonia,
        CAST(c08 AS VARCHAR) AS ciudad,
        CAST(c07 AS VARCHAR) AS edo,
        CAST(c09 AS VARCHAR) AS cp,
        CAST(NULL AS VARCHAR) AS tel_contacto,
        CAST(NULL AS VARCHAR) AS esn, CAST(NULL AS VARCHAR) AS imei,
        CAST(NULL AS VARCHAR) AS iccid, CAST(NULL AS VARCHAR) AS fecha_plan,
        CAST(NULL AS VARCHAR) AS fecha_eq, CAST(NULL AS VARCHAR) AS tp_rfc,
        CAST(NULL AS VARCHAR) AS tp_pago, CAST(NULL AS VARCHAR) AS tc,
        CAST(NULL AS VARCHAR) AS contacto1, CAST(NULL AS VARCHAR) AS contacto2,
        CAST(NULL AS VARCHAR) AS plan_orig, CAST(NULL AS VARCHAR) AS renaut,
        CAST(NULL AS VARCHAR) AS curp
    FROM raw
    """)
    print(f"  Insertadas: {time.time()-t0:.0f}s")
    con.execute("CREATE INDEX idx_rfc ON main.personas(rfc)")
    report(con, "hsbc_2")
    con.close()
    sz = out.stat().st_size / 1024 / 1024
    print(f"  Archivo: {sz:.0f} MB")


# ============== MAIN ==============
def mat_clavijero():
    print("=" * 60)
    print("Clavijero.csv (13.4k, header)")
    print("=" * 60)
    t0 = time.time()
    src = SRC_DIR / "Instituto Consorcio Clavijero dataleak by Z3r00.csv"
    out = BASES_DIR / "clavijero_v1.duckdb"
    con = init_db(out)
    con.execute(f"""
    INSERT INTO main.personas
    SELECT
        CAST(NULL AS VARCHAR) AS cuenta,
        CAST(NULL AS VARCHAR) AS padre, CAST(NULL AS VARCHAR) AS st_cta,
        CAST(NULL AS VARCHAR) AS st_cob, CAST(NULL AS VARCHAR) AS cls_crd,
        CAST(NULL AS VARCHAR) AS tipo, CAST(NULL AS VARCHAR) AS ciclo,
        CAST(NULL AS VARCHAR) AS fecha_activ, CAST(NULL AS VARCHAR) AS fecha_cancel,
        CAST(NULL AS VARCHAR) AS fecha_term, CAST(NULL AS VARCHAR) AS plan_actual,
        CAST(NULL AS VARCHAR) AS telefono,
        CAST(NULL AS VARCHAR) AS st_tel, CAST(NULL AS VARCHAR) AS motivo,
        CAST(NULL AS VARCHAR) AS fecha_cel, CAST(NULL AS VARCHAR) AS gsm_ind,
        CAST(NULL AS VARCHAR) AS marca, CAST(NULL AS VARCHAR) AS modelo,
        CAST(NULL AS VARCHAR) AS dat_orig, CAST(NULL AS VARCHAR) AS dat_actual,
        CAST(NULL AS VARCHAR) AS asesor, CAST(NULL AS VARCHAR) AS adendum,
        CAST(NULL AS VARCHAR) AS plazo,
        {_split_nombre_sql_inline('nombre_completo', 'nombre1', 'nombre2')},
        CAST(NULL AS VARCHAR) AS rfc,
        CAST(NULL AS VARCHAR) AS domicilio,
        CAST(NULL AS VARCHAR) AS numero,
        CAST(NULL AS VARCHAR) AS interior,
        CAST(NULL AS VARCHAR) AS colonia,
        CAST(municipio AS VARCHAR) AS ciudad,
        CAST(NULL AS VARCHAR) AS edo,
        CAST(NULL AS VARCHAR) AS cp,
        CAST(NULL AS VARCHAR) AS tel_contacto,
        CAST(NULL AS VARCHAR) AS esn, CAST(NULL AS VARCHAR) AS imei,
        CAST(NULL AS VARCHAR) AS iccid, CAST(NULL AS VARCHAR) AS fecha_plan,
        CAST(NULL AS VARCHAR) AS fecha_eq, CAST(NULL AS VARCHAR) AS tp_rfc,
        CAST(NULL AS VARCHAR) AS tp_pago, CAST(NULL AS VARCHAR) AS tc,
        CAST(NULL AS VARCHAR) AS contacto1, CAST(NULL AS VARCHAR) AS contacto2,
        CAST(NULL AS VARCHAR) AS plan_orig, CAST(NULL AS VARCHAR) AS renaut,
        UPPER(TRIM(curp)) AS curp
    FROM read_csv_auto('{src}', header=true, sample_size=-1, all_varchar=true, ignore_errors=true)
    """)
    print(f"  Insertadas: {time.time()-t0:.0f}s")
    con.execute("CREATE INDEX idx_curp ON main.personas(curp)")
    report(con, "clavijero")
    con.close()
    sz = out.stat().st_size / 1024 / 1024
    print(f"  Archivo: {sz:.0f} MB")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    start = time.time()
    if target in ("telcel1", "all"): mat_telcel1()
    if target in ("citibanamex", "all"): mat_citibanamex()
    if target in ("banorte", "all"): mat_banorte()
    if target in ("hsbc", "all"):
        mat_cobol(SRC_DIR / "BANCO HSBC 1.csv", BASES_DIR / "hsbc_v1.duckdb", "HSBC 1", sep=',')
        mat_hsbc2()
    if target in ("santander", "all"):
        for i in range(1, 8):
            src = SRC_DIR / f"BANCO SANTANDER ({i}).txt"
            if not src.exists(): continue
            mat_cobol(src, BASES_DIR / f"santander_v{i}.duckdb", f"SANTANDER {i}", sep='\t')
    if target in ("xlsx", "all"):
        mat_xlsx("BBDBancoppel.xlsx", "bancoppel_v1.duckdb", "Bancoppel", header_row=1, adapter_name='bancoppel')
        mat_xlsx("BDDAmex.xlsx", "amex_v1.duckdb", "Amex", header_row=1, adapter_name='amex')
        mat_xlsx("BDDBancomer.xlsx", "bancomer_v1.duckdb", "Bancomer", header_row=1, adapter_name='bancomer')
    if target in ("clavijero", "all"): mat_clavijero()
    print(f"\n=== TOTAL: {time.time()-start:.0f}s ===")