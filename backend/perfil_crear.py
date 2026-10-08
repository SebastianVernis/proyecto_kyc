"""perfil_crear.py — Flujo de creación de perfil completo.

Orden estricto:
  1. INSERT padrón (datos obligatorios, 2 créditos — incluye TODAS las bases locales)
  2. CheckID (3 créditos, solo si >30 días desde última consulta, se guarda en perfil)
  3. UPDATE IMSS (por CURP, exacto, incluido en 2 créditos locales)
  4. UPDATE ATT (por RFC + 2 pasadas, incluido en 2 créditos locales)
  5. UPDATE Telcel (por RFC + 2 pasadas, incluido en 2 créditos locales)
  6. UPDATE REPUVE (por RFC + 2 pasadas, incluido en 2 créditos locales)
  7. UPDATE Empleadores (por RFC + 2 pasadas, incluido en 2 créditos locales)
  8. UPDATE ISSSTE (por nombre, fuzzy, incluido en 2 créditos locales)
  9. UPDATE CFE (por nombre/CP/dirección, 3 pasadas, incluido en 2 créditos locales)

Sistema de créditos:
  - Bases locales (todas): 2 créditos MÁXIMO por perfil
  - CheckID externo: 3 créditos, se cachea 30 días en perfil_completo
  - Singula/Apify: pendiente de estructurar

Cada UPDATE solo agrega lo nuevo. Devuelve dict con todo lo encontrado.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Any, Optional

from perfil_completo_db import (
    get_con,
    insertar_padron, updatear_checkid, updatear_imss,
    updatear_bases_rfc, updatear_issste, updatear_cfe,
    updatear_consultaunica,
    leer_perfil, existe_en_padron,
)
from providers.checkid import CheckIdClient


# ─── Constantes de créditos ─────────────────────────────────────────────
CREDITOS_BASES_LOCALES = 2   # Máximo por perfil (todas las bases locales combinadas)
CREDITOS_CHECKID = 3          # Costo real de CheckID API
DIAS_CADUCIDAD_CHECKID = 30  # Re-ejecutar CheckID después de 30 días


class _FaseLocal(Exception):
    """Corta la fase externa del perfil cuando solo se pidió la local."""

    pass


def crear_perfil(
    *,
    curp: str = "",
    rfc: str = "",
    nss: str = "",
    nombre: str = "",
    con_extended=None,
    confirmar: bool = False,
    datos_pendientes: dict = None,
    con_perfil=None,  # Conexión a perfil_completo (nuevo)
    con_padron=None,  # Conexión a padrón electoral (nuevo)
    plan: str = "",            # plan del usuario (AFOR solo en "corporativo")
    validacion_previa: bool = False,  # habilita AFOR si el plan es corporativo
    fase: str = "completo",    # "local" (gratis) | "completo" (local + externo pagado)
) -> dict:
    """Crea perfil completo con flujo estricto de 9 pasos.

    Args:
        curp: CURP del sujeto (OBLIGATORIO, 18 chars)
        con_extended: Conexión a bases extendidas (ya inicializada)
        con_perfil: Conexión a perfil_completo.duckdb (lazy singleton)
        ...

    Returns:
        dict con:
        - perfil: datos consolidados de todas las fuentes
        - pasos: metadata de cada paso (timing, estado, resultado)
        - error: si algún paso falla críticamente
    """

    curp = curp.upper().strip()
    if not curp or len(curp) != 18:
        return {"error": "CURP requerida (18 caracteres)"}

    t0 = time.time()
    # Fase "local": construcción preliminar gratuita. No se paga nada: ni
    # CheckID ni ConsultaÚnica. Solo match contra las bases locales ya
    # cacheadas en perfil_completo. La fase externa se hace después, cuando
    # el usuario confirma que quiere gastar créditos.
    solo_local = (fase == "local")
    con_perfil = con_perfil or get_con()  # Conexión a perfil_completo
    # Si con_extended tiene b_perfil ATTACHed, usar esa conexión en su lugar
    # para evitar "already attached" error
    if con_extended:
        try:
            con_extended.execute("SELECT 1 FROM b_perfil.perfil_completo LIMIT 1")
            con_perfil = con_extended  # Usar la conexión ATTACHed
        except Exception:
            pass  # Mantener con_perfil original

    result = {
        "curp": curp,
        "perfil": None,
        "pasos": [],
        "errores": [],
        "estado": "en_progreso",
        "metadata": {"elapsed_ms": 0},
    }

    # ────────────────────────────────────────────────────────────────────
    # PASO 0: Leer perfil existente (si existe)
    # ────────────────────────────────────────────────────────────────────
    t1 = time.time()
    perfil_existente = leer_perfil(con_perfil, curp)
    result["pasos"].append({
        "paso": "leer_existente",
        "tiempo_ms": int((time.time() - t1) * 1000),
        "existe": perfil_existente is not None,
        "estado_anterior": perfil_existente.get("estado") if perfil_existente else None,
    })

    if perfil_existente and perfil_existente.get("estado") == "completo":
        checkid_vencido = False
        if perfil_existente.get("checkid_fecha"):
            try:
                fecha_checkid = perfil_existente["checkid_fecha"]
                if isinstance(fecha_checkid, str):
                    fecha_checkid = datetime.fromisoformat(fecha_checkid)
                checkid_vencido = (datetime.now() - fecha_checkid).days >= DIAS_CADUCIDAD_CHECKID
            except Exception:
                checkid_vencido = True

        # En fase "local" siempre se devuelve lo ya guardado sin re-correr nada
        # (es la lectura rápida para pintar el modal de datos básicos).
        if solo_local or not checkid_vencido:
            result["perfil"] = _parse_perfil_from_db(perfil_existente)
            result["estado"] = "ya_completo"
            result["metadata"]["elapsed_ms"] = int((time.time() - t0) * 1000)
            # El cobro por perfil se mantiene aunque salga de caché; solo la
            # fase local (popup de datos básicos) es gratuita.
            cobro = 0 if solo_local else CREDITOS_CHECKID
            result["metadata"]["creditos"] = {"checkid": cobro, "bases_locales": 0, "total": cobro}
            return result

    # ────────────────────────────────────────────────────────────────────
    # PASO 1: Buscar en Padrón (datos obligatorios)
    # ────────────────────────────────────────────────────────────────────
    t1 = time.time()
    datos_padron = {}
    try:
        # Usar con_padron si está disponible, sino con_extended
        padron_con = con_padron if con_padron else con_extended
        rows = padron_con.execute(
            "SELECT curp, nombre, paterno, materno, fecnac, sexo, calle, ext, int, "
            "       colonia, cp, e, d, m, s, l, mza, consec, fuente "
            "FROM padron WHERE curp = ? LIMIT 1",
            [curp]
        ).fetchall()

        if rows:
            cols = [d[0] for d in padron_con.description]
            datos_padron = dict(zip(cols, rows[0]))
        else:
            result["errores"].append("CURP no encontrado en padrón")
            result["estado"] = "error"
            return result
    except Exception as e:
        result["errores"].append(f"Error en padrón: {str(e)[:200]}")
        result["estado"] = "error"
        return result

    result["pasos"].append({
        "paso": "padron",
        "tiempo_ms": int((time.time() - t1) * 1000),
        "ok": True,
        "datos_encontrados": True,
    })

    # ────────────────────────────────────────────────────────────────────
    # PASO 1b: INSERT padrón en perfil_completo
    # ────────────────────────────────────────────────────────────────────
    if not perfil_existente:
        t1 = time.time()
        padron_json = {
            "nombre": datos_padron.get("nombre", ""),
            "paterno": datos_padron.get("paterno", ""),
            "materno": datos_padron.get("materno", ""),
            "fecnac": datos_padron.get("fecnac", ""),
            "sexo": datos_padron.get("sexo", ""),
            "calle": datos_padron.get("calle", ""),
            "ext": datos_padron.get("ext", ""),
            "int": datos_padron.get("int", ""),
            "colonia": datos_padron.get("colonia", ""),
            "cp": datos_padron.get("cp", ""),
            "estado": datos_padron.get("e", ""),
            "distrito": datos_padron.get("d", ""),
            "municipio": datos_padron.get("m", ""),
            "seccion": datos_padron.get("s", ""),
            "clave_elector": datos_padron.get("curp", ""),
            "folio": datos_padron.get("fuente", ""),
        }
        ok = insertar_padron(con_perfil, curp, padron_json)
        result["pasos"].append({
            "paso": "insert_padron_db",
            "tiempo_ms": int((time.time() - t1) * 1000),
            "ok": ok,
        })

    # ────────────────────────────────────────────────────────────────────
    # PASO 2: CheckID (3 créditos, solo si >30 días o primera vez)
    # ────────────────────────────────────────────────────────────────────
    t1 = time.time()
    checkid_data = {}
    checkid_ok = False
    checkid_reutilizado = False
    # RFC/NSS locales: pueden venir del perfil cacheado o de CheckID. Se
    # inicializan aquí para que los 9 pasos locales corran aunque CheckID
    # esté pausado o falle (antes un fallo de CheckID abortaba el perfil).
    rfc = (perfil_existente or {}).get("rfc", "") or ""
    nss = (perfil_existente or {}).get("nss", "") or ""

    from config import config
    checkid_habilitado = getattr(config, "checkid_enabled", False)
    if solo_local:
        checkid_habilitado = False

    # Verificar si ya tenemos CheckID válido y reciente
    if perfil_existente and perfil_existente.get("checkid_ok") and perfil_existente.get("checkid_fecha"):
        try:
            fecha_checkid = perfil_existente["checkid_fecha"]
            if isinstance(fecha_checkid, str):
                fecha_checkid = datetime.fromisoformat(fecha_checkid)
            dias_desde = (datetime.now() - fecha_checkid).days
            if dias_desde < DIAS_CADUCIDAD_CHECKID:
                # Reutilizar datos cacheados — no hay costo
                checkid_reutilizado = True
                rfc = perfil_existente.get("rfc", "")
                nss = perfil_existente.get("nss", "")
                checkid_data = json.loads(perfil_existente.get("checkid_data", "{}")) if isinstance(perfil_existente.get("checkid_data"), str) else (perfil_existente.get("checkid_data") or {})
                checkid_ok = True
                result["pasos"].append({
                    "paso": "checkid",
                    "tiempo_ms": int((time.time() - t1) * 1000),
                    "ok": True,
                    "reutilizado": True,
                    "dias_desde_ultima": dias_desde,
                    "creditos": 0,
                })
        except Exception:
            pass  # Si hay error parseando fecha, re-ejecutar

    if not checkid_habilitado:
        # CheckID PAUSADO (CHECKID_ENABLED=false): se omite y el flujo sigue
        # con las bases locales. No se gasta y no se aborta.
        result["pasos"].append({
            "paso": "checkid",
            "tiempo_ms": int((time.time() - t1) * 1000),
            "ok": False,
            "omitido": True,
            "motivo": "CHECKID_ENABLED=false",
            "creditos": 0,
        })
    elif not checkid_reutilizado:
        # Ejecutar CheckID (3 créditos) — un fallo NO aborta el perfil.
        try:
            api_key = config.checkid_api_key
            if api_key:
                client = CheckIdClient(api_key=api_key)
                checkid_data = client.get_full(curp)
                checkid_ok = checkid_data.get("exitoso", False)
            else:
                result["errores"].append("CheckID API key no configurada")
        except Exception as e:
            result["errores"].append(f"Error CheckID: {str(e)[:200]}")

        result["pasos"].append({
            "paso": "checkid",
            "tiempo_ms": int((time.time() - t1) * 1000),
            "ok": checkid_ok,
            "rfc_encontrado": checkid_data.get("rfc", "")[:10] if checkid_ok else None,
            "reutilizado": False,
            "creditos": CREDITOS_CHECKID if checkid_ok else 0,
        })

        if not checkid_ok:
            # No se aborta: se registra el error y se continúa con lo local.
            updatear_checkid(con_perfil, curp, {"error": checkid_data.get("error")}, False)
            result["errores"].append(
                f"CheckID sin resultado ({checkid_data.get('codigoError', 'sin código')}): "
                "se continúa con bases locales"
            )
        else:
            rfc = checkid_data.get("rfc", "").upper()
            nss = checkid_data.get("nss", "")

            t1 = time.time()
            updatear_checkid(con_perfil, curp, checkid_data, True)
            result["pasos"].append({
                "paso": "update_checkid_db",
                "tiempo_ms": int((time.time() - t1) * 1000),
                "ok": True,
            })

    nombre_sujeto = f"{datos_padron.get('nombre', '')} {datos_padron.get('paterno', '')} {datos_padron.get('materno', '')}".strip()

    # ────────────────────────────────────────────────────────────────────
    # PASO 3: IMSS (por CURP — match exacto)
    # ────────────────────────────────────────────────────────────────────
    t1 = time.time()
    imss_asegurados = []
    imss_segmentacion = []
    try:
        rows = con_extended.execute(
            "SELECT curp, nss, nss_clean, nombre_patron as nombre_patron, sueldo as sueldo, "
            "       registro_patron, nombre_patron, empresa_domicilio as domicilio_patron, empresa_ciudad_estado as ciudad_estado, "
            "       empresa_cp as cp_patron, empresa_giro as giro_patron "
            "FROM api.imss_asegurado WHERE curp = ? LIMIT 50",
            [curp]
        ).fetchall()
        if rows:
            cols = [d[0] for d in con_extended.description]
            imss_asegurados = [dict(zip(cols, r)) for r in rows]

        rows = con_extended.execute(
            "SELECT curp, nss, genero, edad, "
            "       ooad, unidad_medica, "
            "       segmento_hipertension, segmentacion_diabetes_mellitus, "
            "       segmento_hipertension, segmentacion_diabetes_mellitus "
            "FROM api.imss_salud WHERE curp = ? LIMIT 50",
            [curp]
        ).fetchall()
        if rows:
            cols = [d[0] for d in con_extended.description]
            imss_segmentacion = [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        result["errores"].append(f"Error IMSS: {str(e)[:200]}")

    result["pasos"].append({
        "paso": "imss",
        "tiempo_ms": int((time.time() - t1) * 1000),
        "ok": True,
        "asegurados_count": len(imss_asegurados),
        "salud_count": len(imss_segmentacion),
    })

    # UPDATE IMSS en perfil_completo
    t1 = time.time()
    updatear_imss(con_perfil, curp, imss_asegurados, imss_segmentacion)
    result["pasos"].append({
        "paso": "update_imss_db",
        "tiempo_ms": int((time.time() - t1) * 1000),
        "ok": True,
    })

    # ────────────────────────────────────────────────────────────────────
    # PASOS 4-7: ATT, Telcel, REPUVE, Empleadores (todos por RFC, 2 pasadas)
    # ────────────────────────────────────────────────────────────────────
    t1 = time.time()
    try:
        resultado_rfc = updatear_bases_rfc(con_perfil, curp, con_extended, nombre_sujeto, rfc)
        result["pasos"].append({
            "paso": "bases_rfc",
            "tiempo_ms": int((time.time() - t1) * 1000),
            "ok": resultado_rfc.get("ok", False),
            "att": len(resultado_rfc.get("att", {}).get("pasada1", [])),
            "telcel": len(resultado_rfc.get("telcel", {}).get("pasada1", [])),
            "repuve": len(resultado_rfc.get("repuve", {}).get("pasada1", [])),
            "empleadores": len(resultado_rfc.get("empleadores", {}).get("pasada1", [])),
        })
    except Exception as e:
        result["errores"].append(f"Error bases RFC: {str(e)[:200]}")

    # ────────────────────────────────────────────────────────────────────
    # PASO 8: ISSSTE (por nombre — fuzzy)
    # ────────────────────────────────────────────────────────────────────
    t1 = time.time()
    try:
        resultado_issste = updatear_issste(
            con_perfil, curp,
            datos_padron.get("nombre", ""),
            datos_padron.get("paterno", ""),
            datos_padron.get("materno", ""),
            con_extended=con_extended,
        )
        result["pasos"].append({
            "paso": "issste",
            "tiempo_ms": int((time.time() - t1) * 1000),
            "ok": resultado_issste.get("ok", False),
            "empleos_count": resultado_issste.get("total", 0),
        })
    except Exception as e:
        result["errores"].append(f"Error ISSSTE: {str(e)[:200]}")

    # ────────────────────────────────────────────────────────────────────
    # PASO 9: CFE (por nombre/CP/dirección — 3 pasadas, último)
    # ────────────────────────────────────────────────────────────────────
    t1 = time.time()
    try:
        resultado_cfe = updatear_cfe(
            con_perfil, curp, con_extended,
            nombre_sujeto,
            datos_padron.get("paterno", ""),
            datos_padron.get("materno", ""),
            datos_padron.get("calle", ""),
            datos_padron.get("colonia", ""),
            datos_padron.get("cp", "")
        )
        result["pasos"].append({
            "paso": "cfe",
            "tiempo_ms": int((time.time() - t1) * 1000),
            "ok": resultado_cfe.get("ok", False),
            "servicios_count": resultado_cfe.get("total_encontrados", 0),
        })
    except Exception as e:
        result["errores"].append(f"Error CFE: {str(e)[:200]}")

    # ────────────────────────────────────────────────────────────────────
    # Créditos (se calculan ANTES del paso 10, que suma los de ConsultaÚnica)
    # ────────────────────────────────────────────────────────────────────
    # Créditos (se calculan ANTES del paso 10, que suma los de ConsultaÚnica).
    # En fase local (popup de datos básicos) NADA se cobra: es el vistazo
    # gratuito que se dispara al hacer clic en la fila.
    if solo_local:
        creditos_checkid = 0
        creditos_locales = 0
    else:
        creditos_checkid = CREDITOS_CHECKID if checkid_ok else 0
        creditos_locales = CREDITOS_BASES_LOCALES if not perfil_existente else 0
    result["metadata"]["creditos"] = {
        "checkid": creditos_checkid,
        "bases_locales": creditos_locales,
        "total": creditos_checkid + creditos_locales,
    }

    # ────────────────────────────────────────────────────────────────────
    # PASO 10: Escalada a ConsultaÚnica (flujo v2) — solo lo dudoso.
    # ────────────────────────────────────────────────────────────────────
    # Local primero: los 9 pasos anteriores ya corrieron gratis. Ahora la
    # compuerta decide qué dato quedó dudoso y se paga SOLO eso (1 crédito por
    # servicio, tope 3). AFOR únicamente en plan corporativo con validación
    # previa, y nunca si la identidad no está resuelta.
    # En fase "local" NO se toca ningún proveedor externo.
    t1 = time.time()
    try:
        if solo_local:
            result["pasos"].append({
                "paso": "consultaunica_v2",
                "tiempo_ms": int((time.time() - t1) * 1000),
                "ok": True, "omitido": True,
                "motivo": "fase=local (sin costo externo)",
                "creditos": 0, "consultado": [],
            })
            raise _FaseLocal()
        import flujo_busqueda_v2 as _flujo
        local_v2 = {
            "curp": curp,
            "fecnac": datos_padron.get("fecnac", ""),
            "en_padron": True,
            "nombre": datos_padron.get("nombre", ""),
            "paterno": datos_padron.get("paterno", ""),
            "materno": datos_padron.get("materno", ""),
            "nss": {"nss": nss or ""},
            "rfc": {"rfc": rfc or ""},
            "afore": {"afore": "", "email": "", "telefono": ""},
        }
        # Contacto/AFOR ya cacheados (si el perfil existe) para no re-pagar.
        if perfil_existente:
            cto = perfil_existente.get("cu_contacto") or {}
            if isinstance(cto, str):
                cto = json.loads(cto) if cto else {}
            local_v2["afore"] = {
                "afore": perfil_existente.get("cu_data", {}).get("afore", "") if isinstance(perfil_existente.get("cu_data"), dict) else "",
                "email": cto.get("email", ""),
                "telefono": cto.get("telefono", ""),
            }
            local_v2["hint_estrategia"] = (perfil_existente.get("fuentes_consultadas") or {}).get("hint_estrategia", "") if isinstance(perfil_existente.get("fuentes_consultadas"), dict) else ""

        res_v2 = _flujo.resolver(
            local=local_v2, plan=plan, validacion_previa=validacion_previa,
            mock=bool(getattr(config, "consultaunica_mock", False)),
        )
        ui_v2 = _flujo.formatear_para_ui(res_v2)
        result["consultaunica"] = ui_v2
        result["metadata"]["creditos"]["consultaunica"] = ui_v2["costo_creditos"]
        result["metadata"]["creditos"]["total"] = (
            result["metadata"]["creditos"].get("total", 0) + ui_v2["costo_creditos"]
        )

        if res_v2["costo_creditos"] > 0 and res_v2["consultado"]:
            cu_data = {
                "nss": ui_v2["valores"].get("nss", ""),
                "rfc": ui_v2["valores"].get("rfc", ""),
                "afore": ui_v2["valores"].get("afore", ""),
            }
            updatear_consultaunica(
                con_perfil, curp, cu_data,
                contacto=ui_v2["contacto"], costo_creditos=res_v2["costo_creditos"],
            )
        result["pasos"].append({
            "paso": "consultaunica_v2",
            "tiempo_ms": int((time.time() - t1) * 1000),
            "ok": True,
            "creditos": ui_v2["costo_creditos"],
            "consultado": [c["servicio"] for c in ui_v2["consultado"]],
        })
    except _FaseLocal:
        pass
    except Exception as e:
        result["errores"].append(f"Error flujo v2: {str(e)[:200]}")

    # ────────────────────────────────────────────────────────────────────
    # FINALIZADO
    # ────────────────────────────────────────────────────────────────────
    # Estado según la fase: la local solo cruzó las bases locales (parcial);
    # la completa ya pagó lo externo.
    try:
        from perfil_completo_db import set_estado_perfil
        set_estado_perfil(con_perfil, curp, "parcial" if solo_local else "completo")
    except Exception:
        pass
    perfil_final = leer_perfil(con_perfil, curp)
    result["perfil"] = _parse_perfil_from_db(perfil_final) if perfil_final else {}
    result["estado"] = perfil_final.get("estado", "completo") if perfil_final else "error"
    result["metadata"]["elapsed_ms"] = int((time.time() - t0) * 1000)

    result["metadata"]["pendientes"] = ["singula", "apify"]

    return result


def _parse_perfil_from_db(perfil_db: dict) -> dict:
    """Convierte un row de perfil_completo a dict legible para el frontend."""
    if not perfil_db:
        return {}

    perfil = {
        "curp": perfil_db.get("curp"),
        "rfc": perfil_db.get("rfc"),
        "nss": perfil_db.get("nss"),
        "estado": perfil_db.get("estado"),
        "creado_en": str(perfil_db.get("creado_en", "")),
        "actualizado_en": str(perfil_db.get("actualizado_en", "")),
    }

    # Descomponer JSON fields
    for key in ["padron_data", "checkid_data", "imss_data", "att_data",
                "telcel_data", "repuve_data", "empleadores_data",
                "issste_data", "cfe_data", "cu_data", "cu_contacto"]:
        if perfil_db.get(key):
            try:
                perfil[key] = json.loads(perfil_db[key]) if isinstance(perfil_db[key], str) else perfil_db[key]
            except:
                pass

    # 2026-10-08: contacto de AFOR (flujo v2). Es el ÚNICO dato de contacto del
    # expediente (ninguna base local tiene email), así que se expone plano para
    # que la ficha lo muestre sin que el frontend tenga que descomponer JSON.
    cu = perfil.get("cu_data") or {}
    if isinstance(cu, str):
        try:
            cu = json.loads(cu)
        except Exception:
            cu = {}
    cto = perfil.get("cu_contacto") or {}
    if isinstance(cto, str):
        try:
            cto = json.loads(cto)
        except Exception:
            cto = {}
    perfil["email"] = cto.get("email", "") or ""
    perfil["telefono"] = cto.get("telefono", "") or ""
    perfil["afore"] = cu.get("afore", "") or ""

    return perfil
