"""tools/osint.py — OSINT básico: Instagram, GitHub, email-lookup.

Wrapper sobre /api/osint. No gasta créditos de Singula.
"""
from __future__ import annotations
import time, urllib.request, urllib.error, json, os
from . import ToolDef, ToolContext, register


def _osint_query(args: dict, ctx: ToolContext) -> dict:
    t0 = time.time()
    payload = {}
    for k in ("curp", "nombre", "paterno", "materno", "fecnac", "email", "telefono", "rfc"):
        if args.get(k):
            payload[k] = args[k]
    if not payload:
        return {"error": "se requiere al menos un campo (nombre, curp, rfc, email, telefono)", "rows": []}

    session_token = os.environ.get("ORACULO_SESSION_TOKEN", "")
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if session_token:
        headers["Authorization"] = f"Bearer {session_token}"
    req = urllib.request.Request("http://127.0.0.1:8765/api/osint", data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "rows": []}
    except Exception as e:
        return {"error": str(e), "rows": []}

    # Resumir
    ig = data.get("instagram") or data.get("apify_instagram") or []
    gh = data.get("github") or []
    out = {
        "instagram_candidatos": len(ig) if isinstance(ig, list) else 0,
        "github_candidatos": len(gh) if isinstance(gh, list) else 0,
        "instagram_sample": ig[:3] if isinstance(ig, list) else [],
        "github_sample": [g.get("login") if isinstance(g, dict) else g for g in (gh[:3] if isinstance(gh, list) else [])],
        "brechas": data.get("brechas_filtradas") or [],
    }
    dur = (time.time()-t0)*1000
    ctx.record("osint_query", args, len(ig)+len(gh), dur)
    return {"rows": [out], "count": 1, "duration_ms": round(dur,1)}


register(ToolDef(
    name="osint_query",
    description=(
        "OSINT básico: busca Instagram (Apify), GitHub, email-lookup. NO cuesta "
        "créditos de Singula. Útil para huella digital una vez que hay un candidato "
        "con nombre+apellidos. Devuelve hasta 15 candidatos de Instagram con username+fullName."
    ),
    parameters={
        "type": "object",
        "properties": {
            "curp":    {"type": "string"},
            "nombre":  {"type": "string"},
            "paterno": {"type": "string"},
            "materno": {"type": "string"},
            "fecnac":  {"type": "string"},
            "email":   {"type": "string"},
            "telefono":{"type": "string"},
            "rfc":     {"type": "string"},
        },
        "required": [],
    },
    fn=_osint_query,
    cost_hint="medium",
))
