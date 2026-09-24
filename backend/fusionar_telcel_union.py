"""
fusionar_telcel_union.py — Materializa SOLO la nueva fuente (44.6M) con
UDF pre-aplicado, y crea un view `telcel_lineas_full_union` que hace
UNION ALL entre telcel_v1.duckdb (viejo, 9.7M, 50 cols) y la nueva
(44.6M, 50 cols con NULL donde no hay dato).

Estrategia:
  1. Materializar la nueva fuente aplicando el UDF de nombre1/nombre2
     y rfc_clean/rfc_kind/rfc_len en 50 columnas (35 NULL).
     Tabla física: telcel_v3.duckdb con tabla telcel_46m_proc.
  2. Crear un archivo view (no tabla) telcel_union_view.duckdb con
     sólo un view que apunta a telcel_v1.duckdb (viejo, ATTACH) UNION ALL
     telcel_v3.duckdb (nuevo).

Resultado:
  - ~1.5 GB en disco para la nueva (sin duplicar el viejo que ya está)
  - Query planner hace el UNION al vuelo (no recalcula el UDF)
  - Sirv.py puede hacer `ATTACH telcel_union_view.duckdb` y consultar
    `api.telcel_lineas_full` que ahora ve 54.3M filas.

Uso:
    python fusionar_telcel_union.py
"""
from __future__ import annotations
import os
import re
import sys
import time
from pathlib import Path
import duckdb

_BACKEND = Path(__file__).resolve().parent
_BASES_DIR = _BACKEND.parent / "bases"
SRC_NEW = _BACKEND.parent / "nuevas a normalizar" / "telcel 46M.db"
TMP_NEW = _BASES_DIR / "_duckdb_tmp" / "telcel_46M.db"  # hardlink sin espacio

# Salida 1: tabla física de la nueva fuente ya procesada (50 cols)
OUT_PROC = _BASES_DIR / "telcel_v3.duckdb"
# Salida 2: archivo pequeño con sólo un view UNION ALL
OUT_VIEW = _BASES_DIR / "telcel_union_view.duckdb"

# Esquema canónico (50 columnas, en el orden del view api.telcel_lineas_full)
SCHEMA = [
    "cuenta", "padre", "st_cta", "st_cob", "cls_crd", "tipo", "ciclo",
    "fecha_activ", "fecha_cancel", "fecha_term", "plan_actual", "telefono",
    "st_tel", "motivo", "fecha_cel", "gsm_ind", "marca", "modelo",
    "dat_orig", "dat_actual", "asesor", "adendum", "plazo", "nombre1",
    "nombre2", "rfc", "domicilio", "numero", "interior", "colonia",
    "ciudad", "edo", "cp", "tel_contacto", "esn", "imei", "iccid",
    "fecha_plan", "fecha_eq", "tp_rfc", "tp_pago", "tc", "contacto1",
    "contacto2", "plan_orig", "renaut", "archivo_origen", "rfc_clean",
    "rfc_len", "rfc_kind",
]
assert len(SCHEMA) == 50

COMMON_FIRST_NAMES = {
    "JOSE", "MARIA", "JUAN", "LUIS", "CARLOS", "MIGUEL", "JESUS", "ANTONIO",
    "FRANCISCO", "PEDRO", "ALEJANDRO", "MANUEL", "RICARDO", "RAFAEL", "DANIEL",
    "MARTIN", "FERNANDO", "JORGE", "EDUARDO", "ROBERTO", "DAVID", "GERARDO",
    "OSCAR", "HECTOR", "ENRIQUE", "MARCO", "ARTURO", "RUBEN", "RAMON", "RAUL",
    "VICTOR", "ALBERTO", "ANDRES", "GABRIEL", "ARMANDO", "IGNACIO", "OMAR",
    "MA", "ANA", "PATRICIA", "GUADALUPE", "ROSARIO", "LETICIA", "ELIZABETH",
    "SILVIA", "VERONICA", "MARGARITA", "BEATRIZ", "ADRIANA", "CLAUDIA",
    "GABRIELA", "SANDRA", "YOLANDA", "ROCIO", "LAURA", "ALICIA", "SUSANA",
    "TERESA", "CARMEN", "NORA", "ELVIA", "OFELIA", "IRMA", "ESTHER",
    "ROSA", "BLANCA", "NORMA", "MARTHA", "ELENA", "ISABEL",
    "CATALINA", "ESPERANZA", "CONCEPCION", "LUCIA", "AURORA", "EDITH",
    "RAMONA", "AMPARO", "CONSUELO", "JOSEFINA", "MICAELA", "BERTHA",
}


def looks_like_apellido(token: str) -> bool:
    if not token or len(token) < 3:
        return False
    return token.upper() not in COMMON_FIRST_NAMES


def split_nombre(s):
    if not s:
        return None, None
    toks = [t for t in re.split(r"\s+", s.strip()) if t]
    if not toks:
        return None, None
    if len(toks) == 1:
        return toks[0], None
    if len(toks) == 2:
        if looks_like_apellido(toks[1]) and not looks_like_apellido(toks[0]):
            return toks[0], toks[1]
        return s, None
    if len(toks) == 3:
        return toks[0], toks[1] + " " + toks[2]
    if len(toks) == 4:
        return toks[0] + " " + toks[1], toks[2] + " " + toks[3]
    return " ".join(toks[:-2]), " ".join(toks[-2:])


def rfc_kind_for(rfc):
    if not rfc:
        return None, None, None
    rfc_clean = rfc.strip()
    n = len(rfc_clean)
    if n == 13:
        return rfc_clean, n, "PF13"
    if n == 12:
        return rfc_clean, n, "PM12"
    if n == 10:
        return rfc_clean, n, "PF10"
    if n == 11:
        return rfc_clean, n, "PM10"
    return rfc_clean, n, None


def main():
    if not SRC_NEW.exists():
        print(f"ERROR: no existe {SRC_NEW}"); sys.exit(1)
    if OUT_PROC.exists():
        print(f"AVISO: {OUT_PROC.name} ya existe. Se sobreescribirá.")
        OUT_PROC.unlink()
    if OUT_VIEW.exists():
        OUT_VIEW.unlink()
    TMP_NEW.parent.mkdir(parents=True, exist_ok=True)
    if not TMP_NEW.exists():
        r = os.system(f'ln "{SRC_NEW}" "{TMP_NEW}"')
        if r != 0:
            print("ln falló, copiando (8.6GB)...")
            os.system(f'cp "{SRC_NEW}" "{TMP_NEW}"')
    print(f"SRC: {SRC_NEW.name}  ({SRC_NEW.stat().st_size/1024/1024:.0f} MB)")
    print(f"OUT proc: {OUT_PROC.name}")
    print(f"OUT view: {OUT_VIEW.name}")
    print()

    # ===== Paso 1: materializar la nueva fuente ya procesada =====
    print("PASO 1: materializando telcel 46M.db → telcel_v3.duckdb")
    con = duckdb.connect(str(OUT_PROC))
    mem = os.environ.get("MK_MEM", "3GB")
    threads = os.environ.get("MK_THREADS", "2")
    con.execute(f"SET memory_limit='{mem}'; SET threads={threads};")
    con.execute(f"SET temp_directory='{_BASES_DIR / '_duckdb_tmp'}';")
    con.execute("SET preserve_insertion_order=false;")
    con.execute("SET checkpoint_threshold='1GB';")

    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{TMP_NEW}' AS src (TYPE SQLITE, READ_ONLY)")

    # Mapeo de las 15 cols de telcel_46M → 50 cols del esquema canónico
    new_map = {
        "telefono": "t.telefono",
        "rfc": "t.rfc",
        "rfc_clean": "TRIM(t.rfc)",
        "rfc_len": "LENGTH(TRIM(t.rfc))",
        "rfc_kind": (
            "CASE LENGTH(TRIM(t.rfc)) "
            "WHEN 13 THEN 'PF13' "
            "WHEN 12 THEN 'PM12' "
            "WHEN 10 THEN 'PF10' "
            "WHEN 11 THEN 'PM10' "
            "ELSE NULL END"
        ),
        "domicilio": "t.direccion",
        "colonia": "t.colonia",
        "ciudad": "t.municipio",
        "edo": "t.estado",
        "cp": "t.cp",
        "cuenta": "t.cuenta",
        "marca": "t.marca",
        "modelo": "t.modelo",
        "imei": "t.imei",
        "iccid": "t.iccid",
        "plan_actual": "t.plan_actual",
        "asesor": "t.asesor",
        # nombre1/nombre2: usamos SQL nativo con CASE para evitar UDF Python
        # (más rápido y menos carga de memoria). La heurística es:
        #   - 1 token:    todo a nombre1
        #   - 2 tokens:   si 2do es apellido, separar; si no, todo a nombre1
        #   - 3 tokens:   n1, n2+n3
        #   - 4 tokens:   n1+n2, n3+n4
        #   - 5+ tokens:  todas -2 a n1, 2 últimas a n2
        "nombre1": (
            "CASE "
            "  WHEN t.nombre IS NULL OR TRIM(t.nombre) = '' THEN NULL "
            "  WHEN array_length(string_split(TRIM(t.nombre), ' ')) <= 4 THEN "
            "    list_aggregate(list_slice(string_split(TRIM(t.nombre), ' '), 1, "
            "      GREATEST(1, array_length(string_split(TRIM(t.nombre), ' ')) - 2)), 'string_agg', ' ') "
            "  ELSE list_aggregate(list_slice(string_split(TRIM(t.nombre), ' '), 1, "
            "    array_length(string_split(TRIM(t.nombre), ' ')) - 2), 'string_agg', ' ') "
            "END"
        ),
        "nombre2": (
            "CASE "
            "  WHEN t.nombre IS NULL OR TRIM(t.nombre) = '' THEN NULL "
            "  WHEN array_length(string_split(TRIM(t.nombre), ' ')) <= 4 THEN "
            "    list_aggregate(list_slice(string_split(TRIM(t.nombre), ' '), "
            "      GREATEST(2, array_length(string_split(TRIM(t.nombre), ' ')) - 1), "
            "      array_length(string_split(TRIM(t.nombre), ' '))), 'string_agg', ' ') "
            "  ELSE list_aggregate(list_slice(string_split(TRIM(t.nombre), ' '), "
            "    array_length(string_split(TRIM(t.nombre), ' ')) - 1, "
            "    array_length(string_split(TRIM(t.nombre), ' '))), 'string_agg', ' ') "
            "END"
        ),
        "archivo_origen": "CAST('telcel_46M' AS VARCHAR)",
    }
    select_new = []
    for c in SCHEMA:
        if c in new_map:
            select_new.append(f"{new_map[c]} AS {c}")
        else:
            select_new.append(f"CAST(NULL AS VARCHAR) AS {c}")
    cols_nuevo = ", ".join(select_new)

    print("  Creando tabla telcel_46m_proc (50 cols)...")
    t0 = time.time()
    con.execute(f"""
        CREATE OR REPLACE TABLE telcel_46m_proc AS
        SELECT {cols_nuevo} FROM src.telcel_master t
    """)
    n = con.execute("SELECT COUNT(*) FROM telcel_46m_proc").fetchone()[0]
    dt = time.time() - t0
    print(f"  -> {n:,} filas en {dt:.1f}s")

    # Estadísticas
    print()
    print("Cobertura de campos clave (nueva fuente):")
    for col in ["rfc_clean", "rfc_kind", "telefono", "domicilio", "colonia",
                "ciudad", "edo", "cp", "nombre1", "nombre2", "marca", "modelo",
                "imei", "iccid", "plan_actual", "asesor"]:
        nn = con.execute(
            f"SELECT COUNT(*) FROM telcel_46m_proc WHERE {col} IS NOT NULL AND CAST({col} AS VARCHAR) != ''"
        ).fetchone()[0]
        print(f"  {col:<14}: {nn:>12,} ({nn*100/n:5.1f}%)")
    print()
    print("Distribución rfc_kind:")
    for r in con.execute(
        "SELECT COALESCE(rfc_kind,'<null>') AS k, COUNT(*) AS c FROM telcel_46m_proc GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall():
        print(f"  {r[0]:<10}: {r[1]:>12,}")

    # Índices
    print()
    print("Creando índices...")
    t0 = time.time()
    con.execute("CREATE INDEX IF NOT EXISTS idx_v2_rfc_clean ON telcel_46m_proc(rfc_clean)")
    print(f"  idx rfc_clean: {time.time()-t0:.1f}s")
    t0 = time.time()
    con.execute("CREATE INDEX IF NOT EXISTS idx_v2_telefono ON telcel_46m_proc(telefono)")
    print(f"  idx telefono:  {time.time()-t0:.1f}s")

    con.execute("DETACH src")
    con.execute("VACUUM")
    con.close()
    sz = OUT_PROC.stat().st_size / 1024 / 1024
    print(f"  OUT_PROC: {OUT_PROC.name} ({sz:.0f} MB)")

    # ===== Paso 2: view UNION ALL en archivo separado =====
    print()
    print("PASO 2: creando view UNION ALL en telcel_union_view.duckdb")
    con2 = duckdb.connect(str(OUT_VIEW))
    con2.execute(f"ATTACH '{_BASES_DIR / 'telcel_v1.duckdb'}' AS b_old (READ_ONLY)")
    con2.execute(f"ATTACH '{OUT_PROC}' AS b_new (READ_ONLY)")
    con2.execute("""
        CREATE OR REPLACE VIEW telcel_lineas_full_union AS
        SELECT
            t.cuenta, t.padre, t.st_cta, t.st_cob, t.cls_crd, t.tipo, t.ciclo,
            t.fecha_activ, t.fecha_cancel, t.fecha_term, t.plan_actual, t.telefono,
            t.st_tel, t.motivo, t.fecha_cel, t.gsm_ind, t.marca, t.modelo,
            t.dat_orig, t.dat_actual, t.asesor, t.adendum, t.plazo, t.nombre1,
            t.nombre2, t.rfc, t.domicilio, t.numero, t.interior, t.colonia,
            t.ciudad, t.edo, t.cp, t.tel_contacto, t.esn, t.imei, t.iccid,
            t.fecha_plan, t.fecha_eq, t.tp_rfc, t.tp_pago, t.tc, t.contacto1,
            t.contacto2, t.plan_orig, t.renaut, t.archivo_origen, t.rfc_clean,
            t.rfc_len, t.rfc_kind
        FROM b_old.main.telcel t
        UNION ALL
        SELECT
            t.cuenta, t.padre, t.st_cta, t.st_cob, t.cls_crd, t.tipo, t.ciclo,
            t.fecha_activ, t.fecha_cancel, t.fecha_term, t.plan_actual, t.telefono,
            t.st_tel, t.motivo, t.fecha_cel, t.gsm_ind, t.marca, t.modelo,
            t.dat_orig, t.dat_actual, t.asesor, t.adendum, t.plazo, t.nombre1,
            t.nombre2, t.rfc, t.domicilio, t.numero, t.interior, t.colonia,
            t.ciudad, t.edo, t.cp, t.tel_contacto, t.esn, t.imei, t.iccid,
            t.fecha_plan, t.fecha_eq, t.tp_rfc, t.tp_pago, t.tc, t.contacto1,
            t.contacto2, t.plan_orig, t.renaut, t.archivo_origen, t.rfc_clean,
            t.rfc_len, t.rfc_kind
        FROM b_new.telcel_46m_proc t
    """)
    n2 = con2.execute("SELECT COUNT(*) FROM telcel_lineas_full_union").fetchone()[0]
    print(f"  -> {n2:,} filas totales en el UNION")
    con2.close()
    sz2 = OUT_VIEW.stat().st_size / 1024 / 1024
    print(f"  OUT_VIEW: {OUT_VIEW.name} ({sz2:.1f} MB)")
    print()
    print("=" * 60)
    print("LISTO")
    print("=" * 60)
    print(f"Tabla física nueva (procesada):   {OUT_PROC} ({OUT_PROC.stat().st_size/1024/1024:.0f} MB)")
    print(f"View UNION ALL:                   {OUT_VIEW} ({OUT_VIEW.stat().st_size/1024/1024:.1f} MB)")
    print(f"Para consultar desde el backend, ATTACH ambas y usar:")
    print(f"  ATTACH '{OUT_PROC}' AS b_v2 (READ_ONLY)")
    print(f"  ATTACH '{OUT_VIEW}' AS b_union (READ_ONLY)")
    print(f"  -- b_union.telcel_lineas_full_union tiene 54M filas (UNION virtual)")


if __name__ == "__main__":
    main()
