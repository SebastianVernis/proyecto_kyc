"""Capa 7: Síntesis y dedupe.

Toma los resultados de las 6 capas anteriores y:
  1. Dedupea por CURP/RFC
  2. Calcula score compuesto:
     - 0.50 si match EXACTO en padrón
     - 0.30 si match VARIANTES con score alto
     - 0.10 × bases federadas con hit (hasta +1.0)
     - 0.10 si titular CFE = sujeto
     - 0.05 si familiares cohabitantes
     - 0.05 si RENAPO confirma
     - 0.15 si Singula limpio
     -0.50 si Singula blacklist
  3. Genera el veredicto final
"""
from __future__ import annotations
import json
from .. import ToolDef, ToolContext, register


def _sintetizar(args: dict, ctx: ToolContext) -> dict:
    """Args esperados: un JSON con los resultados de cada capa:
    {
        "capa2_padron_exact": {...},
        "capa3_padron_variants": {...},
        "capa4_federadas": {...},
        "capa5_cohabitacion": {...},
        "capa6_broker": {...},
    }
    """
    capa2 = args.get("capa2_padron_exact") or {}
    capa3 = args.get("capa3_padron_variants") or {}
    capa4 = args.get("capa4_federadas") or {}
    capa5 = args.get("capa5_cohabitacion") or {}
    capa6 = args.get("capa6_broker") or {}

    # Dedupe: agrupar por CURP/RFC base
    candidatos: dict = {}  # curp -> dict con evidencia

    # Capa 2: padrón exacto
    for r in capa2.get("rows", []) or []:
        curp = r.get("curp")
        if not curp: continue
        c = candidatos.setdefault(curp, {
            "id": curp, "curp": curp, "evidencia": [], "score": 0.0,
            "direccion": "", "estado_probable": "", "nombre_completo": "",
        })
        c["score"] += 0.50
        c["evidencia"].append({
            "fuente": "padron_exact",
            "match": "EXACTO",
            "detalle": f"CURP {curp}, {r.get('paterno','')} {r.get('materno','')} {r.get('nombre','')}, fecnac {r.get('fecnac','')}, CP {r.get('cp','')}",
            "registro": r,
        })
        c["nombre_completo"] = f"{r.get('paterno','')} {r.get('materno','')} {r.get('nombre','')}".strip()
        c["direccion"] = ", ".join(filter(None, [r.get("calle"), r.get("colonia"), r.get("cp", ""), f"estado {r.get('estado','')}"]))

    # Capa 3: padrón variantes (solo si capa2 vacío)
    if not candidatos and capa3.get("rows"):
        for r in capa3.get("rows", [])[:5]:  # top 5
            curp = r.get("curp")
            if not curp: continue
            sc = capa3.get("score_por_match", [0])[capa3.get("rows",[]).index(r)] if capa3.get("score_por_match") else 0.3
            c = candidatos.setdefault(curp, {
                "id": curp, "curp": curp, "evidencia": [], "score": 0.0,
                "direccion": "", "estado_probable": "", "nombre_completo": "",
            })
            c["score"] += 0.30 * (sc if sc else 0.5)
            c["evidencia"].append({
                "fuente": "padron_variants",
                "match": f"PARCIAL score={sc}",
                "detalle": f"{r.get('paterno','')} {r.get('materno','')} {r.get('nombre','')} fecnac {r.get('fecnac','')}",
                "registro": r,
            })
            c["nombre_completo"] = f"{r.get('paterno','')} {r.get('materno','')} {r.get('nombre','')}".strip()

    # Capa 4: federadas → boost por cada base con hit
    bases_boost = capa4.get("score_boost", 0) or 0
    for r in capa4.get("rows", []) or []:
        curp = (r.get("curp") or r.get("rfc_clean") or r.get("rfc") or "").strip()[:18] or r.get("__base", "")
        c = candidatos.setdefault(curp if curp else f"rfc_{r.get('rfc_clean') or r.get('rfc') or '?'}", {
            "id": curp or r.get("rfc"), "evidencia": [], "score": 0.0,
            "direccion": "", "estado_probable": "", "nombre_completo": "",
        })
        c["score"] += 0.10
        c["evidencia"].append({
            "fuente": f"base_{r.get('__base')}",
            "match": "RFC",
            "detalle": f"rfc={r.get('rfc_clean') or r.get('rfc')}",
        })

    # Capa 5: cohabitación → boost titular + familiares
    if capa5.get("titular_es_sujeto"):
        # aplicar a todos los candidatos (no sabemos cuál)
        for c in candidatos.values():
            c["score"] += 0.10
            c["evidencia"].append({"fuente": "cfe_titular", "match": "TITULAR", "detalle": "titular CFE = sujeto"})
    if capa5.get("familiares_detectados"):
        for c in candidatos.values():
            c["score"] += 0.05
            c["evidencia"].append({"fuente": "cfe_familiares", "match": "FAMILIAR",
                                   "detalle": f"{len(capa5['familiares_detectados'])} familiar(es) cohabitante(s)"})

    # Capa 6: broker
    boost6 = capa6.get("score_boost", 0) or 0
    blacklist = capa6.get("blacklist_hit", False)
    for c in candidatos.values():
        c["score"] += boost6
        if capa6.get("tlaloc") and isinstance(capa6["tlaloc"], dict) and capa6["tlaloc"].get("ok"):
            c["evidencia"].append({"fuente": "renapo", "match": "VALIDA", "detalle": "RENAPO confirma CURP"})
        if capa6.get("singula", {}).get("judicial"):
            c["evidencia"].append({"fuente": "singula_judicial", "match": "LIMPIO", "detalle": "sin antecedentes"})
        if blacklist:
            c["evidencia"].append({"fuente": "singula_blacklist", "match": "ALERTA", "detalle": "match en lista negra"})

    # Normalizar score a 0-1
    out_list = []
    for c in candidatos.values():
        c["score"] = round(max(0, min(c["score"], 1.0)), 3)
        out_list.append(c)

    # ordenar por score
    out_list.sort(key=lambda x: -x["score"])

    # veredicto
    if not out_list:
        veredicto = "NO_LOCALIZADO"
        confianza = 0.0
    elif out_list[0]["score"] >= 0.5:
        if len([c for c in out_list if c["score"] >= 0.5]) > 1:
            veredicto = "MULTIPLE"
        else:
            veredicto = "LOCALIZADO"
        confianza = min(0.99, out_list[0]["score"] + 0.05)
    else:
        veredicto = "INCIERTO"
        confianza = out_list[0]["score"]

    # descartar los de score muy bajo
    descartados = [{"nombre": c.get("nombre_completo","?"), "razon": f"score {c['score']:.2f} < 0.3"} for c in out_list if c["score"] < 0.3]
    candidatos_finales = [c for c in out_list if c["score"] >= 0.3]

    return {
        "veredicto_global": veredicto,
        "candidatos": candidatos_finales[:5],
        "descartados": descartados[:10],
        "confianza": round(confianza, 3),
        "resumen": f"{veredicto}: top score {candidatos_finales[0]['score'] if candidatos_finales else 0:.2f}, {len(candidatos_finales)} candidato(s) sobre umbral",
        "scoring_detalle": {
            "umbral_descartado": 0.3,
            "formula": "0.50_exacto + 0.30_variantes + 0.10*N_bases + 0.10_titular_cfe + 0.05_familiares + 0.05_renapo + 0.15_singula_limpio - 0.50_blacklist",
        }
    }


register(ToolDef(
    name="recon_sintetizar",
    description=(
        "CAPA 7: Síntesis final. Recibe los resultados de las 6 capas anteriores como "
        "un dict JSON y devuelve el veredicto con score compuesto, dedupe por CURP, "
        "y separación entre candidatos sólidos y descartados. SIEMPRE llamar al final "
        "de la cadena de reconciliación. NO modifica datos, solo agrega."
    ),
    parameters={
        "type": "object",
        "properties": {
            "capa2_padron_exact":  {"type": "object"},
            "capa3_padron_variants": {"type": "object"},
            "capa4_federadas":     {"type": "object"},
            "capa5_cohabitacion":  {"type": "object"},
            "capa6_broker":        {"type": "object"},
        },
        "required": [],
    },
    fn=_sintetizar,
    cost_hint="low",
))
