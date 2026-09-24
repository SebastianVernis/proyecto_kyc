"""tools/kyc_broker.py — Broker KYC completo: RENAPO + Singula + blacklist + RFC.

Wrapper sobre /api/kyc del backend. Hace:
  - Tlaloc/RENAPO: validación de CURP
  - Apify: huella digital
  - Singula: antecedentes judiciales (PJF/CJF) + lista negra (OFAC/UE/ONU/SAT 69B)
  - Costo: ~$42 judicial + $10 blacklist por sujeto

CUIDADO: cada llamada gasta créditos. Solo invocar cuando hay un CANDIDATO SÓLIDO.
"""
from __future__ import annotations
import time, urllib.request, urllib.error, urllib.parse, json, os
from . import ToolDef, ToolContext, register


def _kyc_broker(args: dict, ctx: ToolContext) -> dict:
    """Llama al broker KYC del backend.

    Args: curp? (preferido), o (paterno+materno+nombre+fecnac)
    """
    t0 = time.time()
    # Llamamos al backend local por HTTP (estamos en el mismo proceso pero por
    # simplicidad usamos HTTP)
    payload = {}
    if args.get("curp"):
        payload["curp"] = args["curp"].strip().upper()
    else:
        for k in ("paterno", "materno", "nombre", "fecnac"):
            if args.get(k):
                payload[k] = args[k]
    if not payload:
        return {"error": "se requiere curp o (paterno+materno+nombre+fecnac)", "rows": []}

    # Auth por cookie: reusamos el session del caller
    # Para simplificar, asumimos usuario admin en el mismo backend
    # (en producción: el motor recibe el session token del user)
    session_token = os.environ.get("ORACULO_SESSION_TOKEN", "")
    url = "http://127.0.0.1:8765/api/kyc"
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if session_token:
        headers["Authorization"] = f"Bearer {session_token}"

    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read()[:300].decode(errors='ignore')}", "rows": []}
    except Exception as e:
        return {"error": str(e), "rows": []}

    # Extraer campos clave
    out = {
        "tlaloc_ok": data.get("tlaloc", {}).get("ok") or bool(data.get("tlaloc")),
        "singula_judicial": bool(data.get("singula", {}).get("judicial")),
        "singula_blacklist": data.get("singula", {}).get("blacklist", {}).get("hit", False),
        "rfc": data.get("rfc") or data.get("rfc_sat"),
        "apify_results": len(data.get("apify", {}).get("instagram", []) or []),
        "raw_keys": list(data.keys()),
    }
    dur = (time.time()-t0)*1000
    ctx.record("kyc_broker", args, 1, dur)
    return {"rows": [out], "count": 1, "raw": {k: data[k] for k in list(data.keys())[:5]}, "duration_ms": round(dur,1)}


register(ToolDef(
    name="kyc_broker",
    description=(
        "Broker KYC completo: RENAPO (Tlaloc) + Apify (redes) + Singula (antecedentes "
        "judiciales + lista negra OFAC/UE/ONU/SAT 69B). Cuesta ~$52 por sujeto. "
        "SOLO invocar cuando hay un candidato sólido (no en cada iteración). "
        "Si el sujeto tiene hits en padrón, este tool confirma identidad y "
        "detecta si tiene antecedentes."
    ),
    parameters={
        "type": "object",
        "properties": {
            "curp":    {"type": "string"},
            "paterno": {"type": "string"},
            "materno": {"type": "string"},
            "nombre":  {"type": "string"},
            "fecnac":  {"type": "string"},
        },
        "required": [],
    },
    fn=_kyc_broker,
    cost_hint="high",
))
