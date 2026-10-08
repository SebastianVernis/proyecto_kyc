"""coherencia.py — compuerta que decide si un dato local es concluyente.

Principio del flujo v2: el barrido local es gratis y va primero. La consulta
externa (ConsultaÚnica, 1 crédito por servicio) es una **excepción** que solo se
dispara cuando el dato local falta o es incoherente.

Este módulo no consulta nada: solo clasifica lo que ya se tiene.

Estados:
    CONCLUYENTE  el dato está completo y consistente -> no se gasta
    DUDOSO       falta, está incompleto, o hay conflicto -> escalar
    INCOHERENTE  el dato contradice la identidad -> escalar y avisar

Todo es lógica pura (sin red, sin BD) para poder probarse sin gastar créditos.
"""
from __future__ import annotations

from typing import Any, Optional

CONCLUYENTE, DUDOSO, INCOHERENTE = "concluyente", "dudoso", "incoherente"

# Meses válidos del bloque de fecha de la CURP (posiciones 4-9: AAMMDD).
_MESES = {f"{m:02d}" for m in range(1, 13)}
_DIAS = {f"{d:02d}" for d in range(1, 32)}


def fecnac_desde_curp(curp: str) -> Optional[str]:
    """Extrae la fecha de nacimiento (YYYY-MM-DD) del bloque de la CURP.

    La CURP codifica AAMMDD en las posiciones 5-10 (0-indexed 4:10). El siglo
    se deduce del carácter 17 (index 16): dígito => 1900s, letra => 2000s.
    Devuelve None si la CURP no tiene forma válida.
    """
    c = (curp or "").strip().upper()
    if len(c) != 18:
        return None
    aa, mm, dd = c[4:6], c[6:8], c[8:10]
    if mm not in _MESES or dd not in _DIAS:
        return None
    siglo = 1900 if c[16].isdigit() else 2000
    return f"{siglo + int(aa):04d}-{mm}-{dd}"


def _norm_fecha(f: str) -> str:
    """Normaliza fecnac a YYYY-MM-DD. Acepta YYYY-MM-DD, DD/MM/YYYY, YYYYMMDD."""
    f = (f or "").strip()
    if not f:
        return ""
    if len(f) == 10 and f[4] in "-/":
        return f"{f[0:4]}-{f[5:7]}-{f[8:10]}"      # YYYY-MM-DD o YYYY/MM/DD
    if len(f) == 10 and f[2] in "-/":
        return f"{f[6:10]}-{f[3:5]}-{f[0:2]}"      # DD/MM/YYYY
    if len(f) == 8 and f.isdigit():
        return f"{f[0:4]}-{f[4:6]}-{f[6:8]}"       # YYYYMMDD
    return f


def clasificar_nss(local: dict, *,
                  fuentes: Optional[list[str]] = None) -> dict:
    """Clasifica el NSS local.

    Args:
        local: {"nss": str, "nss_fuentes": [str, ...], "nombre_ok": bool}
               `nss_fuentes` permite detectar conflicto entre imss_asegurados
               e imss_segmentacion (mismo CURP, NSS distinto).
        fuentes: si se pasan varios NSS candidatos, se contrastan.

    Returns: {"estado": ..., "valor": str, "motivo": str, "escalar": bool}
    """
    nss = str((local or {}).get("nss") or "").strip()
    nss_fuentes = (local or {}).get("nss_fuentes") or []

    # Conflicto entre fuentes: mismo sujeto, NSS distintos -> dudoso fuerte.
    distintos = {str(n).strip() for n in nss_fuentes if str(n).strip()}
    if len(distintos) > 1:
        return _r(DUDOSO, nss,
                  f"NSS distinto entre fuentes locales: {sorted(distintos)}")

    if not nss:
        return _r(DUDOSO, "", "sin NSS en las bases locales")
    if not nss.isdigit() or len(nss) != 11:
        return _r(DUDOSO, nss, f"NSS con formato inválido ({len(nss)} dígitos, se esperan 11)")
    if (local or {}).get("nombre_ok") is False:
        return _r(INCOHERENTE, nss, "el nombre asociado al NSS no cuadra")
    return _r(CONCLUYENTE, nss, "NSS completo y consistente en las bases locales")


def clasificar_rfc(local: dict) -> dict:
    """Clasifica el RFC local.

    Args:
        local: {"rfc": str, "rfc_fuentes": [str,...], "nombre_ok": bool}
               Se necesita homoclave real: 13 chars (PF) — 12 (PM). Un RFC de
               10 chars es CURP truncada, sin homoclave -> dudoso.

    Returns: {"estado": ..., "valor": str, "motivo": str, "escalar": bool}
    """
    rfc = str((local or {}).get("rfc") or "").strip().upper()
    fuentes = {str(r).strip().upper() for r in ((local or {}).get("rfc_fuentes") or [])
               if str(r).strip()}

    distintos_13 = {r for r in fuentes if len(r) >= 12}
    if len(distintos_13) > 1:
        return _r(DUDOSO, rfc, f"varios RFC con homoclave distintos: {sorted(distintos_13)}")

    if not rfc:
        return _r(DUDOSO, "", "sin RFC en las bases locales")
    if len(rfc) < 12:
        return _r(DUDOSO, rfc,
                  f"RFC de {len(rfc)} chars: sin homoclave real (se requieren 12-13)")
    if (local or {}).get("nombre_ok") is False:
        return _r(INCOHERENTE, rfc, "el nombre asociado al RFC no cuadra")
    return _r(CONCLUYENTE, rfc, "RFC con homoclave presente en las bases locales")


def clasificar_afore(local: dict) -> dict:
    """AFOR no tiene fuente local: siempre dudoso (se consulta solo si se pide)."""
    val = str((local or {}).get("afore") or "").strip()
    if val:
        return _r(CONCLUYENTE, val, "AFOR ya cacheada")
    return _r(DUDOSO, "", "AFOR no existe en ninguna base local")


def fecha_coherente(curp: str, fecnac_local: str) -> dict:
    """Compara la fecha de nacimiento del padrón con la embebida en la CURP.

    Es una incoherencia dura y gratis: no requiere red. Si difieren, algo no
    cuadra entre la CURP y los datos del padrón -> dispara revisión.
    """
    de_curp = fecnac_desde_curp(curp)
    de_local = _norm_fecha(fecnac_local)
    if not de_curp:
        return _r(DUDOSO, de_local, "CURP sin bloque de fecha parseable")
    if not de_local:
        return _r(DUDOSO, de_curp, "padrón sin fecha de nacimiento")
    if de_curp != de_local:
        return _r(INCOHERENTE, de_local,
                  f"fecha del padrón ({de_local}) != fecha en la CURP ({de_curp})")
    return _r(CONCLUYENTE, de_local, "fecha del padrón coincide con la CURP")


def evaluar(dp: dict) -> dict:
    """Evalúa el perfil local completo y decide qué escalar.

    Args:
        dp: {
          "curp": str, "fecnac": str, "en_padron": bool,
          "nss": {...}, "rfc": {...}, "afore": {...},
          "hint_estrategia": str,     # de resolver_desde_hint
          "hint_score": float,
          "permitir_afore": bool,     # plan corporativo + validación previa
        }

    Returns: {
          "datos": {nss,rfc,afore,fecha: {...}},
          "escalar": [ {"dato","servicio","costo_creditos","motivo"} ],
          "costo_estimado": int,
          "motivos_escalada": [str],
          "avisos": [str],
        }
    """
    dp = dp or {}
    curp = dp.get("curp", "")
    res: dict[str, Any] = {}

    # 0) El sujeto ni siquiera está en el padrón -> dudoso global.
    if dp.get("en_padron") is False:
        res["_global"] = _r(DUDOSO, "", "el sujeto no está en el padrón (huérfano)")

    # 1) Coherencia de fecha (gratis, no escala a nada: es aviso).
    res["fecha"] = fecha_coherente(curp, dp.get("fecnac", ""))

    # 2) Dato por dato.
    res["nss"] = clasificar_nss(dp.get("nss") or {})
    res["rfc"] = clasificar_rfc(dp.get("rfc") or {})
    res["afore"] = clasificar_afore(dp.get("afore") or {})

    # 3) Ambigüedad de identidad: el resolver local devolvió varios candidatos.
    est = (dp.get("hint_estrategia") or "").lower()
    ambiguo = any(s in est for s in ("ambiguo", "multiples", "sin_match"))
    if ambiguo or (dp.get("hint_score") is not None and float(dp.get("hint_score") or 0) < 0.6):
        res["_identidad"] = _r(DUDOSO, "",
                               f"resolución de identidad ambigua (estrategia={est or 'n/d'})")

    # 4) Escalada: solo datos DUDOSOS/INCOHERENTES, el más barato primero.
    escalar = []
    if res["rfc"]["escalar"]:
        # Si falta el RFC, o existe sin homoclave: se reconstruye por
        # nombre+fecha y se valida en el mismo servicio `sat`.
        escalar.append({"dato": "rfc", "servicio": "sat/rfc_search",
                        "costo_creditos": 1, "motivo": res["rfc"]["motivo"]})
    if res["nss"]["escalar"]:
        escalar.append({"dato": "nss", "servicio": "imss/nss_fast",
                        "costo_creditos": 1, "motivo": res["nss"]["motivo"]})
    # AFOR: solo plan corporativo, y solo con la identidad ya validada.
    # Nunca se gasta un crédito de AFOR en un sujeto cuya identidad no cuadra
    # (fecha incoherente o resolución ambigua): primero hay que resolver quién es.
    identificado = (res.get("fecha", {}).get("estado") != INCOHERENTE
                    and res.get("_identidad", {}).get("estado") != DUDOSO
                    and res.get("_global", {}).get("estado") != DUDOSO)
    afore_habilitado = bool(dp.get("permitir_afore"))
    if res["afore"]["escalar"] and afore_habilitado and identificado:
        escalar.append({"dato": "afore", "servicio": "afore/detalles",
                        "costo_creditos": 1, "motivo": res["afore"]["motivo"]})

    # Avisos gratuitos: no cuestan pero el operador debe verlos.
    avisos = []
    for k in ("fecha", "_identidad", "_global"):
        v = res.get(k)
        if isinstance(v, dict) and v["estado"] != CONCLUYENTE:
            avisos.append(f"{k.lstrip('_')}: {v['motivo']}")
    if res["afore"]["escalar"]:
        if not afore_habilitado:
            avisos.append("afore: no existe local — solo se consulta en plan corporativo")
        elif not identificado:
            avisos.append("afore: omitido hasta validar la identidad del sujeto")

    return {
        "datos": res,
        "escalar": escalar,
        "costo_estimado": sum(e["costo_creditos"] for e in escalar),
        "motivos_escalada": [f"{e['dato']}: {e['motivo']}" for e in escalar],
        "avisos": avisos,
    }


def _r(estado: str, valor: str, motivo: str) -> dict:
    return {"estado": estado, "valor": valor, "motivo": motivo,
            "escalar": estado in (DUDOSO, INCOHERENTE)}
