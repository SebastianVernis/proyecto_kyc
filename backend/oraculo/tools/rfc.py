"""tools/rfc.py — Cálculo de RFC base + homoclave provisional desde CURP/nombre+fecnac."""
from __future__ import annotations
import time
from . import ToolDef, ToolContext, register

# Reutilizamos la utilidad del proyecto
try:
    from rfc_utils import calcular_rfc_desde_curp
    HAS_RFC_UTIL = True
except Exception:
    HAS_RFC_UTIL = False


def _rfc_calc(args: dict, ctx: ToolContext) -> dict:
    """Calcula el RFC de 10 chars (base) + homoclave provisional.

    Si recibe CURP, devuelve substr(curp,1,10) como RFC base (que es la
    convención SAT cuando no hay homoclave oficial). Más rápido y siempre
    disponible que pasar por rfc_utils.
    """
    t0 = time.time()
    curp = (args.get("curp") or "").strip().upper()
    if curp and len(curp) == 18:
        rfc_base = curp[:10]
        dur = (time.time()-t0)*1000
        ctx.record("rfc_calc", {"curp": curp}, 1, dur)
        return {
            "rfc_10": rfc_base,
            "dv_provisional": curp[10:13] if len(curp) >= 13 else "",
            "fuente": "substr(CURP,1,10) — convención SAT",
        }

    if HAS_RFC_UTIL and args.get("paterno") and args.get("materno") and args.get("nombre") and args.get("fecnac"):
        try:
            # calcular_rfc_desde_curp: si no pasamos curp, igual funciona con nombre+fechanac
            r = calcular_rfc_desde_curp(
                curp="",
                nombre=args["nombre"], paterno=args["paterno"],
                materno=args["materno"], fecnac=args["fecnac"]
            )
            dur = (time.time()-t0)*1000
            ctx.record("rfc_calc", {k: args.get(k) for k in ("paterno","materno","nombre","fecnac")}, 1, dur)
            return r
        except Exception as e:
            return {"error": str(e), "rfc_10": None}
    return {"error": "proporciona curp O (paterno+materno+nombre+fecnac)", "rfc_10": None}


register(ToolDef(
    name="rfc_calc",
    description=(
        "Calcula el RFC base de 10 caracteres (sin homoclave oficial) a partir de un "
        "CURP (substr 1..10) o de nombre completo + fecha de nacimiento. El RFC base "
        "es lo que se usa para indexar las 29 bases federadas — siempre hay que "
        "calcularlo ANTES de invocar base_search."
    ),
    parameters={
        "type": "object",
        "properties": {
            "curp":    {"type": "string", "description": "preferido: 18 chars"},
            "paterno": {"type": "string"},
            "materno": {"type": "string"},
            "nombre":  {"type": "string"},
            "fecnac":  {"type": "string", "description": "YYYY-MM-DD"},
        },
        "required": [],
    },
    fn=_rfc_calc,
    cost_hint="low",
))
