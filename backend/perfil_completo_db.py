"""perfil_completo_db.py — Manejo de la base perfil_completo.duckdb.

Mantiene la tabla perfil_completo con datos consolidados por CURP:
  1. Padrón (INSERT inicial, datos obligatorios)
  2. CheckID (UPDATE rfc/nss/cp_fiscal)
  3. IMSS (UPDATE por CURP, match exacto)
  4. ATT/Telcel/REPUVE/Empleadores (UPDATE por RFC, 2 pasadas)
  5. ISSSTE (UPDATE por nombre, fuzzy)
  6. CFE (UPDATE por nombre/CP/dirección, 3 pasadas fuzzy)

Cada UPDATE solo agrega lo nuevo, nunca duplica.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional


PERFIL_COMPLETO_PATH = str(Path(__file__).resolve().parent.parent / "bases" / "perfil_completo.duckdb")


# ══════════════════════════════════════════════════════════════════════════
# UTILIDADES
# ══════════════════════════════════════════════════════════════════════════

def _normalizar_para_regex(texto: str) -> str:
    """Deja solo caracteres válidos para regex (sin especiales)."""
    import re
    import unicodedata
    n = unicodedata.normalize('NFKD', texto or '').encode('ASCII', 'ignore').decode('ASCII')
    return re.sub(r'[^\w\s]', '', n.upper()).strip()


def _regex_nombre(patron: str) -> str:
    """Genera patrón regex seguro para búsqueda de nombres."""
    import re
    safe = re.sub(r'\s+', r'\\s+', _normalizar_para_regex(patron))
    return safe if safe else '^$'  # ^$ no matchea nada si vacío


# ══════════════════════════════════════════════════════════════════════════
# CONEXIÓN Y MIGRACIÓN
# ══════════════════════════════════════════════════════════════════════════

_con = None


def get_con():
    """Conexión lazy a perfil_completo.duckdb.
    Retorna None si el archivo ya está ATTACHed en otra conexión (evita conflicto)."""
    global _con
    # Si ya hay una conexión activa, verificar que el archivo no esté bloqueado
    if _con is not None:
        try:
            _con.execute("SELECT 1 FROM b_perfil.perfil_completo LIMIT 1")
            return _con
        except Exception:
            _con = None  # Reset si hay problema
    import duckdb
    try:
        _con = duckdb.connect(PERFIL_COMPLETO_PATH)
        _migrar(_con)
        return _con
    except Exception:
        # Archivo probablemente ya ATTACHed en otra conexión
        return None


def _migrar(con):
    """Aplica migrate_perfil_completo.sql si la tabla no existe.

    2026-10-08: además aplica las columnas aditivas de ConsultaÚnica (flujo v2)
    a bases ya existentes. Cada ALTER es idempotente (checa information_schema).
    """
    try:
        con.execute("SELECT 1 FROM b_perfil.perfil_completo LIMIT 0")
    except Exception:
        sql = open("scripts/migrate_perfil_completo.sql").read()
        con.execute(sql)
    _migrar_cu(con)


def _columnas(con) -> set:
    try:
        rows = con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'perfil_completo'"
        ).fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()


VIEW_PERFIL_FLAT = """
CREATE OR REPLACE VIEW perfil_completo_flat AS
SELECT
    curp, rfc, nss,
    json_extract_string(padron_data, '$.nombre')      AS nombre,
    json_extract_string(padron_data, '$.paterno')     AS paterno,
    json_extract_string(padron_data, '$.materno')     AS materno,
    json_extract_string(padron_data, '$.fecnac')      AS fecha_nacimiento,
    json_extract_string(padron_data, '$.sexo')        AS sexo,
    json_extract_string(padron_data, '$.calle')       AS calle,
    json_extract_string(padron_data, '$.ext')         AS num_ext,
    json_extract_string(padron_data, '$.int')         AS num_int,
    json_extract_string(padron_data, '$.colonia')     AS colonia,
    json_extract_string(padron_data, '$.cp')          AS cp_padron,
    json_extract_string(padron_data, '$.clave_elector') AS ine_clave,
    json_extract_string(padron_data, '$.folio')       AS ine_folio,
    json_extract_string(checkid_data, '$.codigo_postal_fiscal') AS cp_fiscal,
    json_extract_string(checkid_data, '$.regimen_fiscal')       AS regimen_fiscal,
    json_extract_string(checkid_data, '$.situacion_69b')        AS situacion_69b,
    json_array_length(imss_data->'asegurado')                    AS imss_trabajos,
    json_array_length(att_data->'pasada1_exacto')                AS att_contactos,
    json_array_length(telcel_data->'lineas')                     AS telcel_lineas_count,
    json_array_length(repuve_data->'vehiculos')                  AS vehiculos_count,
    json_array_length(issste_data->'empleos')                    AS issste_empleos,
    json_array_length(cfe_data->'servicios')                     AS cfe_servicios_count,
    estado, creditos_consumidos, creado_en, actualizado_en
FROM perfil_completo;
"""


def _migrar_cu(con):
    """Agrega las columnas de ConsultaÚnica si faltan (idempotente).

    En DuckDB, ALTER TABLE falla si hay una vista o índices que dependen de la
    tabla ("Dependency Error"), así que se sueltan, se altera y se recrean. Las
    columnas son aditivas: las filas existentes quedan con NULL y todo sigue
    funcionando.

    cu_data: JSON con lo que devolvió la escalada (nss/rfc/afore + contacto).
    cu_contacto: JSON con email/teléfono (únicos datos de contacto del flujo).
    cu_fecha / cu_costo_creditos: control de caché y gasto.
    """
    nuevas = {
        "cu_data": "JSON",
        "cu_contacto": "JSON",
        "cu_fecha": "TIMESTAMP",
        "cu_costo_creditos": "INTEGER DEFAULT 0",
    }
    try:
        existentes = _columnas(con)
        if not existentes:
            return
        faltantes = {c: t for c, t in nuevas.items() if c not in existentes}
        if not faltantes:
            return

        # Soltar dependencias (vista + índices), alterar y recrear.
        con.execute("DROP VIEW IF EXISTS perfil_completo_flat")
        for ix in ("idx_perfil_rfc", "idx_perfil_nss", "idx_perfil_estado"):
            con.execute(f"DROP INDEX IF EXISTS {ix}")
        for col, tipo in faltantes.items():
            con.execute(f"ALTER TABLE b_perfil.perfil_completo ADD COLUMN {col} {tipo}")
        con.execute("CREATE INDEX IF NOT EXISTS idx_perfil_rfc ON perfil_completo(rfc)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_perfil_nss ON perfil_completo(nss)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_perfil_estado ON perfil_completo(estado)")
        con.execute(VIEW_PERFIL_FLAT)
        import logging
        logging.info(f"Migración cu_*: agregadas {list(faltantes)}")
    except Exception as e:
        import logging
        logging.error(f"Error migrando columnas cu_*: {e}")


def updatear_consultaunica(con, curp: str, cu_data: dict,
                           contacto: dict = None, costo_creditos: int = 0) -> bool:
    """Persiste el resultado de la escalada a ConsultaÚnica (flujo v2).

    Guarda NSS/RFC/AFOR resueltos, el contacto (email/teléfono) y el costo real.
    Actualiza rfc/nss de la fila si vinieron del externo y no estaban.
    """
    try:
        curp = curp.upper().strip()
        cu_json = json.dumps(cu_data or {}, ensure_ascii=False)
        cto_json = json.dumps(contacto or {}, ensure_ascii=False)
        rfc = (cu_data or {}).get("rfc") or None
        nss = (cu_data or {}).get("nss") or None
        fuentes_update = json.dumps({"consultaunica": True})
        sql = """
            UPDATE b_perfil.perfil_completo SET
                cu_data = ?,
                cu_contacto = ?,
                cu_fecha = NOW(),
                cu_costo_creditos = COALESCE(cu_costo_creditos, 0) + ?,
                rfc = COALESCE(NULLIF(?, ''), rfc),
                nss = COALESCE(NULLIF(?, ''), nss),
                creditos_consumidos = COALESCE(creditos_consumidos, 0) + ?,
                fuentes_consultadas = JSON_MERGE_PATCH(
                    COALESCE(fuentes_consultadas, '{}'), ?
                ),
                actualizado_en = NOW()
            WHERE curp = ?
        """
        con.execute(sql, [cu_json, cto_json, costo_creditos, rfc or "", nss or "",
                          costo_creditos, fuentes_update, curp])
        return True
    except Exception as e:
        import logging
        logging.error(f"Error actualizando ConsultaÚnica para {curp}: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════
# PASO 1: INSERT inicial (Padrón)
# ══════════════════════════════════════════════════════════════════════════

def insertar_padron(con, curp: str, datos_padron: dict) -> bool:
    """INSERT inicial con datos del padrón. Solo datos electorales."""
    try:
        curp = curp.upper().strip()
        sql = """
            INSERT INTO b_perfil.perfil_completo
            (curp, padron_data, estado, fuentes_consultadas, creado_en, actualizado_en)
            VALUES (?, ?, 'padron_solo', ?, NOW(), NOW())
        """
        padron_json = json.dumps(datos_padron, ensure_ascii=False)
        fuentes = json.dumps({"padron": True})
        con.execute(sql, [curp, padron_json, fuentes])
        return True
    except Exception as e:
        import logging
        logging.error(f"Error insertando padrón para {curp}: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════
# PASO 2: UPDATE CheckID (rfc, nss, cp_fiscal)
# ══════════════════════════════════════════════════════════════════════════

def updatear_checkid(con, curp: str, checkid_data: dict, checkid_ok: bool) -> bool:
    """UPDATE con resultado de CheckID. Marca estado como checkid_ok/error."""
    try:
        curp = curp.upper().strip()
        estado = "checkid_ok" if checkid_ok else "checkid_error"

        rfc = checkid_data.get("rfc", "").upper() if checkid_ok else None
        nss = checkid_data.get("nss", "") if checkid_ok else None
        checkid_json = json.dumps(checkid_data, ensure_ascii=False)
        fuentes_update = json.dumps({"checkid": True, "checkid_ok": checkid_ok})

        sql = """
            UPDATE b_perfil.perfil_completo SET
                rfc = ?,
                nss = ?,
                checkid_data = ?,
                checkid_ok = ?,
                estado = ?,
                creditos_consumidos = creditos_consumidos + 3,
                fuentes_consultadas = JSON_MERGE_PATCH(
                    COALESCE(fuentes_consultadas, '{}'),
                    ?
                ),
                actualizado_en = NOW()
            WHERE curp = ?
        """
        con.execute(sql, [rfc, nss, checkid_json, checkid_ok, estado, fuentes_update, curp])
        return True
    except Exception as e:
        import logging
        logging.error(f"Error actualizando CheckID para {curp}: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════
# PASO 3: UPDATE IMSS (por CURP — match exacto)
# ══════════════════════════════════════════════════════════════════════════

def updatear_imss(con, curp: str, imss_asegurados: list, imss_segmentacion: list) -> bool:
    """UPDATE con datos de IMSS (match exacto por CURP)."""
    try:
        curp = curp.upper().strip()
        imss_json = json.dumps({
            "asegurado": imss_asegurados or [],
            "salud": imss_segmentacion or [],
        }, ensure_ascii=False)
        fuentes_update = json.dumps({"imss_asegurados": True, "imss_segmentacion": True})
        sql = """
            UPDATE b_perfil.perfil_completo SET
                imss_data = ?,
                fuentes_consultadas = JSON_MERGE_PATCH(
                    COALESCE(fuentes_consultadas, '{}'),
                    ?
                ),
                actualizado_en = NOW()
            WHERE curp = ?
        """
        con.execute(sql, [imss_json, fuentes_update, curp])
        return True
    except Exception as e:
        import logging
        logging.error(f"Error actualizando IMSS para {curp}: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════
# PASO 4: UPDATE bases por RFC (2 pasadas)
# ══════════════════════════════════════════════════════════════════════════

def _buscar_por_rfc_dos_pasadas(con_extended, query_exacto: str, query_fuzzy: str,
                                rfc_completo: str, nombre_validar: str,
                                base: str) -> tuple[list, list]:
    """Ejecuta 2 pasadas: (1) exacto PF13, (2) fuzzy PF10 + validación nombre.

    Returns:
        (pasada1_exacto, pasada2_fuzzy_validado)
    """
    pasada1 = []
    pasada2 = []

    # PASADA 1: rfc_clean = rfc completo (PF13)
    if len(rfc_completo) == 13:
        try:
            rows = con_extended.execute(query_exacto, [rfc_completo.upper()]).fetchall()
            cols = [d[0] for d in con_extended.execute(query_exacto.replace("WHERE", "LIMIT 0") + " LIMIT 0").description] if rows else []
            pasada1 = [dict(zip(cols, r)) for r in rows]
            for item in pasada1:
                item["rfc_kind"] = "PF13"
                item["confianza"] = "alta"
        except Exception as e:
            print(f"Error en pasada 1 {base}: {e}")

    # PASADA 2: rfc_clean = rfc[:10] (PF10) + validación nombre por regex
    rfc_base = rfc_completo[:10] if rfc_completo else ""
    if len(rfc_base) == 10:
        try:
            regex = _regex_nombre(nombre_validar)
            params = [rfc_base] + [regex] * (query_fuzzy.count("?") - 1)
            rows = con_extended.execute(query_fuzzy, params).fetchall()

            if rows:
                cols = [d[0] for d in con_extended.description] if rows else []
                pasada2 = [dict(zip(cols, r)) for r in rows]

                # Validar por nombre: al menos 50% de palabras compartidas
                import unicodedata
                def _jaccard(set1: set, set2: set) -> float:
                    return len(set1 & set2) / len(set1 | set2) if (set1 | set2) else 0

                nombre_norm = _normalizar_para_regex(nombre_validar)
                nombre_set = set(nombre_norm.split())

                for item in pasada2:
                    nombre_encontrado = _normalizar_para_regex(
                        item.get("nombre", "") or item.get("nombre_completo", "") or
                        item.get("nombres", "") or ""
                    )
                    nombre_encontrado_set = set(nombre_encontrado.split())
                    jacc = _jaccard(nombre_set, nombre_encontrado_set)
                    item["confianza"] = "media" if jacc > 0.5 else "baja"
                    item["jaccard_score"] = round(jacc, 2)
                    item["rfc_kind"] = "PF10"
        except Exception as e:
            print(f"Error en pasada 2 {base}: {e}")

    return pasada1, pasada2


def updatear_bases_rfc(con, curp: str, con_extended, nombre_sujeto: str,
                      rfc_completo: str) -> dict:
    """UPDATE con ATT, Telcel, REPUVE, Empleadores (todas por RFC, 2 pasadas)."""

    if not rfc_completo:
        return {"error": "Sin RFC para búsquedas"}

    resultado = {
        "att": {"pasada1": [], "pasada2": []},
        "telcel": {"pasada1": [], "pasada2": []},
        "repuve": {"pasada1": [], "pasada2": []},
        "empleadores": {"pasada1": [], "pasada2": []},
    }

    # ── Query ATT ──
    q_att_exacto = """
        SELECT rfc, nombre_completo, nombres, apellido_paterno, apellido_materno,
               telefono_fijo, celular, direccion, num_interior, num_exterior,
               colonia, municipio, estado
        FROM api.att_persona
        WHERE rfc = ? AND rfc_kind = 'PF13'
    """
    q_att_fuzzy = """
        SELECT rfc, nombre_completo, nombres, apellido_paterno, apellido_materno,
               telefono_fijo, celular, direccion, num_interior, num_exterior,
               colonia, municipio, estado
        FROM api.att_persona
        WHERE rfc = ? AND rfc_kind = 'PF10'
          AND (regexp_matches(UPPER(nombre_completo), UPPER(?))
           OR regexp_matches(UPPER(apellido_paterno), UPPER(?))
           OR regexp_matches(UPPER(apellido_materno), UPPER(?)))
    """
    att_p1, att_p2 = _buscar_por_rfc_dos_pasadas(
        con_extended, q_att_exacto, q_att_fuzzy, rfc_completo, nombre_sujeto, "ATT")
    resultado["att"] = {"pasada1": att_p1, "pasada2": att_p2}

    # ── Query Telcel ──
    q_telcel_exacto = """
        SELECT rfc, TRIM(COALESCE(nombre1,'') || ' ' || COALESCE(nombre2,'')) as nombre,
               domicilio, colonia, ciudad, estado, cp, telefono,
               marca, modelo, plan
        FROM api.telcel_linea
        WHERE rfc = ? AND rfc_kind = 'PF13'
    """
    q_telcel_fuzzy = """
        SELECT rfc, TRIM(COALESCE(nombre1,'') || ' ' || COALESCE(nombre2,'')) as nombre,
               domicilio, colonia, ciudad, estado, cp, telefono, marca, modelo, plan
        FROM api.telcel_linea
        WHERE rfc = ? AND rfc_kind = 'PF10'
          AND (regexp_matches(UPPER(nombre1), UPPER(?))
           OR regexp_matches(UPPER(nombre2), UPPER(?)))
    """
    telcel_p1, telcel_p2 = _buscar_por_rfc_dos_pasadas(
        con_extended, q_telcel_exacto, q_telcel_fuzzy, rfc_completo, nombre_sujeto, "Telcel")
    resultado["telcel"] = {"pasada1": telcel_p1, "pasada2": telcel_p2}

    # ── Query REPUVE ──
    q_repuve_exacto = """
        SELECT rfc, placa, no_serie, marca, tipo, modelo, color, propietario, direccion_propietario
        FROM api.repuve_vehiculo
        WHERE rfc = ? AND rfc_kind = 'PF13'
    """
    q_repuve_fuzzy = """
        SELECT rfc, placa, no_serie, marca, tipo, modelo, color, propietario, direccion_propietario
        FROM api.repuve_vehiculo
        WHERE rfc = ? AND rfc_kind = 'PF10'
          AND regexp_matches(UPPER(propietario), UPPER(?))
    """
    repuve_p1, repuve_p2 = _buscar_por_rfc_dos_pasadas(
        con_extended, q_repuve_exacto, q_repuve_fuzzy, rfc_completo, nombre_sujeto, "REPUVE")
    resultado["repuve"] = {"pasada1": repuve_p1, "pasada2": repuve_p2}

    # ── Query Empleadores ──
    q_emp_exacto = """
        SELECT rfc, razon_social, nombre_comercial, num_empleados, descripcion,
               correo, dom_calle, dom_colonia, dom_municipio, dom_entidad, dom_cp
        FROM api.empleadores
        WHERE rfc = ? AND rfc_kind = 'PF13'
    """
    q_emp_fuzzy = """
        SELECT rfc, razon_social, nombre_comercial, num_empleados,
               dom_calle, dom_colonia, dom_municipio, dom_entidad
        FROM api.empleadores
        WHERE rfc = ? AND rfc_kind = 'PF10'
          AND (regexp_matches(UPPER(razon_social), UPPER(?))
           OR regexp_matches(UPPER(nombre_comercial), UPPER(?)))
    """
    emp_p1, emp_p2 = _buscar_por_rfc_dos_pasadas(
        con_extended, q_emp_exacto, q_emp_fuzzy, rfc_completo, nombre_sujeto, "Empleadores")
    resultado["empleadores"] = {"pasada1": emp_p1, "pasada2": emp_p2}

    # ── Guardar todos en perfil_completo ──
    fuentes_update = json.dumps({"att": True, "telcel": True, "repuve": True, "empleadores": True})
    sql = """
        UPDATE b_perfil.perfil_completo SET
            att_data = ?, telcel_data = ?, repuve_data = ?, empleadores_data = ?,
            fuentes_consultadas = JSON_MERGE_PATCH(
                COALESCE(fuentes_consultadas, '{}'),
                ?
            ),
            actualizado_en = NOW()
        WHERE curp = ?
    """
    try:
        curp = curp.upper().strip()
        con.execute(sql, [
            json.dumps(resultado["att"], ensure_ascii=False),
            json.dumps(resultado["telcel"], ensure_ascii=False),
            json.dumps(resultado["repuve"], ensure_ascii=False),
            json.dumps(resultado["empleadores"], ensure_ascii=False),
            fuentes_update,
            curp,
        ])
        resultado["ok"] = True
    except Exception as e:
        print(f"Error actualizando bases RFC para {curp}: {e}")
        resultado["ok"] = False
        resultado["error"] = str(e)

    return resultado


# ══════════════════════════════════════════════════════════════════════════
# PASO 5: UPDATE ISSSTE (por nombre — fuzzy)
# ══════════════════════════════════════════════════════════════════════════

def updatear_issste(con, curp: str, nombre: str, paterno: str, materno: str,
                    con_extended=None) -> dict:
    """UPDATE con datos de ISSSTE (por nombre, fuzzy)."""
    if not (nombre and paterno):
        return {"error": "Se requiere nombre y paterno"}

    try:
        if con_extended is None:
            from servir import _init_extended_con
            con_extended = _init_extended_con()
        sql = """
            SELECT nombres, paterno, materno, cargo, sexo, sueldo, ramo, entidad,
                   modalidad, sector, estado
            FROM api.issste_empleado WHERE
                UPPER(paterno) LIKE ?
                AND UPPER(materno) LIKE ?
                AND UPPER(nombres) LIKE ?
            ORDER BY sueldo DESC LIMIT 20
        """
        rows = con_extended.execute(sql, [
            f"%{paterno.upper()}%",
            f"%{materno.upper()}%" if materno else "%",
            f"%{nombre.upper()}%",
        ]).fetchall()

        empleos = []
        if rows:
            cols = [d[0] for d in con_extended.description]
            empleos = [dict(zip(cols, r)) for r in rows]
            for emp in empleos:
                emp["confianza"] = "media"

        issste_json = json.dumps({"empleos": empleos}, ensure_ascii=False)
        fuentes_update = json.dumps({"issste": True})

        con.execute("""
            UPDATE b_perfil.perfil_completo SET
                issste_data = ?,
                fuentes_consultadas = JSON_MERGE_PATCH(
                    COALESCE(fuentes_consultadas, '{}'),
                    ?
                ),
                actualizado_en = NOW()
            WHERE curp = ?
        """, [issste_json, fuentes_update, curp.upper().strip()])

        return {"ok": True, "empleos": empleos, "total": len(empleos)}
    except Exception as e:
        print(f"Error actualizando ISSSTE: {e}")
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════════════
# PASO 6: UPDATE CFE (por nombre/CP/dirección — 3 pasadas fuzzy)
# ══════════════════════════════════════════════════════════════════════════

def updatear_cfe(con, curp: str, con_extended, nombre_completo: str,
                paterno: str, materno: str, calle: str, colonia: str,
                cp: str) -> dict:
    """UPDATE con datos de CFE. 3 pasadas de búsqueda fuzzy."""

    resultado = {
        "servicios": [],
        "por_paso": {"cp_direccion": 0, "nombre_completo": 0, "paterno_materno": 0},
        "total_encontrados": 0,
        "buscado_por": {"cp": False, "direccion": False, "nombre": False},
    }

    if not nombre_completo:
        resultado["error"] = "Se requiere nombre completo"
        return resultado

    # ── PASO 1: Por CP del padrón (más confiable cuando existe) ──
    if cp:
        try:
            sql = """
                SELECT numero_servicio, nombre, direccion, calle_adicional_1,
                       calle_adicional_2, colonia, cp, division
                FROM api.cfe_medidor_kyc
                WHERE cp = ?
            """
            rows = con_extended.execute(sql, [cp]).fetchall()
            if rows:
                cols = [d[0] for d in con_extended.description]
                for row in rows:
                    servicio = dict(zip(cols, row))
                    if _validar_nombre_cfe(nombre_completo, servicio.get("nombre", "")):
                        servicio["match_tipo"] = "cp_direccion"
                        servicio["confianza"] = "alta"
                        resultado["servicios"].append(servicio)
                        resultado["por_paso"]["cp_direccion"] += 1
                resultado["buscado_por"]["cp"] = True
        except Exception as e:
            print(f"Error en paso CFE por CP: {e}")

    # ── PASO 2: Por dirección del padrón ──
    if calle and colonia:
        try:
            sql = """
                SELECT numero_servicio, nombre, direccion, calle_adicional_1,
                       calle_adicional_2, colonia, cp, division
                FROM api.cfe_medidor_kyc
                WHERE UPPER(direccion) LIKE ?
                  AND UPPER(colonia) LIKE ?
            """
            rows = con_extended.execute(sql, [
                f"%{calle.upper()}%",
                f"%{colonia.upper()}%"
            ]).fetchall()
            if rows:
                cols = [d[0] for d in con_extended.description]
                for row in rows:
                    servicio = dict(zip(cols, row))
                    if _validar_nombre_cfe(nombre_completo, servicio.get("nombre", "")):
                        servicio["match_tipo"] = "direccion"
                        servicio["confianza"] = "alta"
                        if not any(s["numero_servicio"] == servicio["numero_servicio"]
                                   for s in resultado["servicios"]):
                            resultado["servicios"].append(servicio)
                            resultado["por_paso"]["cp_direccion"] += 1
                resultado["buscado_por"]["direccion"] = True
        except Exception as e:
            print(f"Error en paso CFE por dirección: {e}")

    # ── PASO 3: Por nombre completo (más lento pero preciso) ──
    if nombre_completo:
        try:
            nombre_regex = nombre_completo.upper().replace(" ", "%")
            sql = """
                SELECT numero_servicio, nombre, direccion, calle_adicional_1,
                       calle_adicional_2, colonia, cp, division
                FROM api.cfe_medidor_kyc
                WHERE UPPER(nombre) LIKE ?
                LIMIT 20
            """
            rows = con_extended.execute(sql, [f"%{nombre_regex}%"]).fetchall()
            if rows:
                cols = [d[0] for d in con_extended.description]
                for row in rows:
                    servicio = dict(zip(cols, row))
                    servicio["match_tipo"] = "nombre_completo"
                    servicio["confianza"] = "alta"
                    if not any(s["numero_servicio"] == servicio["numero_servicio"]
                               for s in resultado["servicios"]):
                        resultado["servicios"].append(servicio)
                        resultado["por_paso"]["nombre_completo"] += 1
                resultado["buscado_por"]["nombre"] = True
        except Exception as e:
            print(f"Error en paso CFE por nombre: {e}")

    # ── Guardar en perfil_completo ──
    resultado["total_encontrados"] = len(resultado["servicios"])
    cfe_json = json.dumps(resultado, ensure_ascii=False)
    fuentes_update = json.dumps({"cfe": True})

    try:
        curp = curp.upper().strip()
        con.execute("""
            UPDATE b_perfil.perfil_completo SET
                cfe_data = ?,
                fuentes_consultadas = JSON_MERGE_PATCH(
                    COALESCE(fuentes_consultadas, '{}'),
                    ?
                ),
                estado = 'completo',
                actualizado_en = NOW()
            WHERE curp = ?
        """, [cfe_json, fuentes_update, curp])
        resultado["ok"] = True
    except Exception as e:
        print(f"Error guardando CFE: {e}")
        resultado["ok"] = False
        resultado["error"] = str(e)

    return resultado


def _validar_nombre_cfe(nombre_sujeto: str, nombre_cfe: str) -> bool:
    """Valida si el titular CFE probablemente es el sujeto."""
    return _normalizar_para_regex(nombre_sujeto) in _normalizar_para_regex(nombre_cfe)


# ══════════════════════════════════════════════════════════════════════════
# UTILIDAD: Leer perfil existente
# ══════════════════════════════════════════════════════════════════════════

def leer_perfil(con, curp: str) -> Optional[dict]:
    """Lee el perfil completo de un CURP."""
    try:
        row = con.execute(
            "SELECT * FROM b_perfil.perfil_completo WHERE curp = ?",
            [curp.upper().strip()]
        ).fetchone()
        if not row:
            return None
        cols = [d[0] for d in con.description]
        return dict(zip(cols, row))
    except Exception as e:
        print(f"Error leyendo perfil: {e}")
        return None


def existe_en_padron(curp: str) -> bool:
    """Verifica rápidamente si el CURP existe en perfil_completo."""
    try:
        row = get_con().execute(
            "SELECT 1 FROM b_perfil.perfil_completo WHERE curp = ?",
            [curp.upper().strip()]
        ).fetchone()
        return row is not None
    except Exception:
        return False


def listar_perfiles(limit: int = 100, offset: int = 0) -> dict:
    """Lista todos los perfiles creados en perfil_completo.

    Returns dict con:
      - perfiles: lista de dicts con curp, nombre, estado, fuentes, fechas
      - total: conteo total de perfiles
    """
    try:
        con = get_con()
        total = con.execute("SELECT COUNT(*) FROM b_perfil.perfil_completo").fetchone()[0]
        rows = con.execute(
            """SELECT curp, padron_data, rfc, nss, estado,
                      fuentes_consultadas, checkid_ok, checkid_fecha,
                      creado_en, actualizado_en
               FROM b_perfil.perfil_completo
               ORDER BY actualizado_en DESC
               LIMIT ? OFFSET ?""",
            [limit, offset]
        ).fetchall()
        cols = [d[0] for d in con.description]
        perfiles = []
        for row in rows:
            r = dict(zip(cols, row))
            # Extraer nombre del padron_data JSON
            padron = {}
            if r.get("padron_data"):
                try:
                    padron = json.loads(r["padron_data"]) if isinstance(r["padron_data"], str) else r["padron_data"]
                except Exception:
                    pass
            nombre = " ".join(filter(None, [
                padron.get("nombre", ""),
                padron.get("paterno", ""),
                padron.get("materno", ""),
            ])).strip()
            fuentes = {}
            if r.get("fuentes_consultadas"):
                try:
                    fuentes = json.loads(r["fuentes_consultadas"]) if isinstance(r["fuentes_consultadas"], str) else r["fuentes_consultadas"]
                except Exception:
                    pass
            perfiles.append({
                "curp": r.get("curp", ""),
                "nombre": nombre,
                "rfc": r.get("rfc", ""),
                "nss": r.get("nss", ""),
                "estado": r.get("estado", ""),
                "fuentes": fuentes,
                "checkid_ok": r.get("checkid_ok", False),
                "creado_en": str(r.get("creado_en", "")),
                "actualizado_en": str(r.get("actualizado_en", "")),
            })
        return {"perfiles": perfiles, "total": total}
    except Exception as e:
        import logging
        logging.error(f"Error listando perfiles: {e}")
        return {"perfiles": [], "total": 0, "error": str(e)}

# Conexión compartida del servidor (ATTACHed in-memory)
_server_con = None

def set_server_con(con):
    """Set the server's ATTACHed connection for perfil_completo reads."""
    global _server_con
    _server_con = con
