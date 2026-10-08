"""flujo_busqueda_v2.py — orquestador: local primero, ConsultaÚnica solo si duda.

Orden:
  1. Recolectar local (gratis) — lo hace el caller (servir.py) y lo pasa aquí.
  2. coherencia.evaluar() decide, dato por dato, qué está dudoso.
  3. Escalar a ConsultaÚnica SOLO por los datos dudosos (1 crédito por servicio).
  4. Cachear el resultado externo para no volver a pagarlo.

No implementa el barrido local (vive en servir.py, ya existe). Este módulo es la
decisión de gasto + la ejecución de la escalada, con tope de créditos y modo
auditoría.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import coherencia


# Tope duro por corrida: ninguna llamada externa puede exceder esto.
MAX_CREDITOS_POR_CORRIDA = 3


def _cu_client(mock: bool = False):
    """Cliente de ConsultaÚnica desde config. None si no hay key."""
    try:
        from providers.consultaunica import make_client_from_config
        return make_client_from_config(mock=mock)
    except Exception:
        return None


def resolver(*, local: dict, pedir_afore: bool = False,
             mock: bool = False, dry_run: bool = False,
             cliente=None) -> dict:
    """Flujo completo: evalúa lo local y escala lo dudoso.

    Args:
        local: datos ya recolectados del barrido local. Forma esperada:
            {
              "curp": str, "nombre": str, "paterno": str, "materno": str,
              "fecnac": str, "en_padron": bool,
              "nss": {"nss": str, "nss_fuentes": [...]},
              "rfc": {"rfc": str, "rfc_fuentes": [...]},
              "afore": {},
              "hint_estrategia": str, "hint_score": float,
            }
        pedir_afore: si True, AFOR también se consulta cuando no haya local.
        mock: usar rutas /v3/mock (no cobra). Para pruebas.
        dry_run: no llama nada; solo reporta qué se consultaría y cuánto costaría.
        cliente: cliente inyectable (tests).

    Returns:
        {
          "decision": {...salida de coherencia.evaluar...},
          "consultado": [ {"dato","servicio","ok","costo_creditos","resultado"} ],
          "costo_creditos": int,
          "valores": {"nss","rfc","afore"},
          "errores": [str],
        }
    """
    local = local or {}
    decision = coherencia.evaluar({**local, "pedir_afore": pedir_afore})

    out = {
        "decision": decision,
        "consultado": [],
        "costo_creditos": 0,
        "valores": {
            "nss": (local.get("nss") or {}).get("nss", ""),
            "rfc": (local.get("rfc") or {}).get("rfc", ""),
            "afore": (local.get("afore") or {}).get("afore", ""),
        },
        "errores": [],
    }

    if dry_run or not decision["escalar"]:
        return out

    # Tope de gasto.
    plan = decision["escalar"][:MAX_CREDITOS_POR_CORRIDA]
    if len(decision["escalar"]) > len(plan):
        out["errores"].append(
            f"se truncó la escalada a {MAX_CREDITOS_POR_CORRIDA} créditos "
            f"(se omitieron {len(decision['escalar']) - len(plan)})")

    cu = cliente or _cu_client(mock=mock)
    if cu is None:
        out["errores"].append("ConsultaÚnica no configurada (sin API key)")
        return out

    for item in plan:
        dato, servicio = item["dato"], item["servicio"]
        t0 = time.time()
        entrada = {"dato": dato, "servicio": servicio, "ok": False,
                   "costo_creditos": 0, "resultado": None}
        try:
            if servicio == "sat/rfc_search":
                # Reconstruye el RFC desde nombre + fecha de nacimiento.
                from coherencia import fecnac_desde_curp
                bd = (local.get("fecnac") or "").strip() or fecnac_desde_curp(local.get("curp", ""))
                r = cu.rfc_search(local.get("nombre", ""), local.get("paterno", ""),
                                  local.get("materno", ""), bd or "")
                if r.get("rfc"):
                    out["valores"]["rfc"] = r["rfc"]
                    entrada.update(ok=True, costo_creditos=1, resultado=r)
                else:
                    # no localizado: la API no cobra dato
                    entrada.update(ok=False, costo_creditos=0, resultado=r)

            elif servicio == "imss/nss_fast":
                r = cu.nss_lookup(local.get("curp", ""),
                                  user_email=local.get("email", ""))
                if r.get("nss"):
                    out["valores"]["nss"] = r["nss"]
                    entrada.update(ok=True, costo_creditos=1, resultado=r)
                else:
                    entrada.update(ok=False, costo_creditos=0, resultado=r)

            elif servicio == "afore/detalles":
                r = cu.afore_details(local.get("curp", ""))
                if r.get("afore"):
                    out["valores"]["afore"] = r["afore"]
                    entrada.update(ok=True, costo_creditos=1, resultado=r)
                else:
                    entrada.update(ok=False, costo_creditos=0, resultado=r)

            else:
                out["errores"].append(f"servicio desconocido: {servicio}")
                continue

            out["costo_creditos"] += entrada["costo_creditos"]
        except Exception as e:
            out["errores"].append(f"{servicio}: {type(e).__name__}: {str(e)[:200]}")
        entrada["elapsed_ms"] = int((time.time() - t0) * 1000)
        out["consultado"].append(entrada)

    return out


def formatear_para_ui(res: dict) -> dict:
    """Reduce el resultado a lo que la UI necesita mostrar (sin el `raw`)."""
    return {
        "costo_creditos": res["costo_creditos"],
        "valores": res["valores"],
        "consultado": [
            {"dato": c["dato"], "servicio": c["servicio"], "ok": c["ok"],
             "costo_creditos": c["costo_creditos"]}
            for c in res["consultado"]
        ],
        "avisos": res["decision"]["avisos"],
        "motivos_escalada": res["decision"]["motivos_escalada"],
        "errores": res["errores"],
    }
