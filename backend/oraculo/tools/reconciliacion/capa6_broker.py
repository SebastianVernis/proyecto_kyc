"""Capa 6: Broker KYC (RENAPO + Singula).

Gasta créditos. Solo invocar cuando hay un candidato SÓLIDO (no en cada iteración).
  - Tlaloc/RENAPO: valida CURP, devuelve datos oficiales
  - Singula: antecedentes judiciales + lista negra (OFAC/UE/ONU/SAT 69B)
"""
from __future__ import annotations
import time, json, urllib.request, urllib.error, os
from .. import ToolDef, ToolContext, register


def _broker(args: dict, ctx: ToolContext) -> dict:
    t0 = time.time()
    curp = (args.get("curp") or "").strip().upper()
    if not curp or len(curp) != 18:
        return {"error": "curp 18 chars requerido (candidato sólido del padrón)", "rows": []}

    session_token = os.environ.get("ORACULO_SESSION_TOKEN", "")
    body = json.dumps({"curp": curp}).encode()
    headers = {"Content-Type": "application/json"}
    if session_token:
        headers["Authorization"] = f"Bearer {session_token}"

    out = {"curp": curp, "tlaloc": None, "singula": {"judicial": None, "blacklist": None}, "apify_count": 0}

    # KYC broker: /api/kyc hace Tlaloc + Singula + Apify en un solo request
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8765/api/kyc", data=body, method="POST", headers=headers
        )
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read())
        out["tlaloc"] = data.get("tlaloc")
        out["singula"] = {
            "judicial": data.get("singula", {}).get("judicial"),
            "blacklist": data.get("singula", {}).get("blacklist"),
        }
        out["apify_count"] = len(data.get("apify", {}).get("instagram", []) or [])
    except urllib.error.HTTPError as e:
        out["error"] = f"HTTP {e.code}: {e.read()[:200].decode(errors='ignore')}"
    except Exception as e:
        out["error"] = str(e)

    # scoring
    score_boost = 0.0
    blacklist_hit = False
    if out["tlaloc"] and isinstance(out["tlaloc"], dict) and out["tlaloc"].get("ok"):
        score_boost += 0.05  # RENAPO confirma
    if out["singula"].get("judicial"):
        score_boost += 0.15  # sin antecedentes es buena señal
    if out["singula"].get("blacklist"):
        # si blacklist es un hit
        if isinstance(out["singula"]["blacklist"], dict) and out["singula"]["blacklist"].get("hit"):
            score_boost -= 0.50
            blacklist_hit = True
    score_boost = max(score_boost, -0.50)

    dur = (time.time()-t0)*1000
    ctx.record("recon_broker", args, 1, dur, error=out.get("error", ""))
    out["score_boost"] = round(score_boost, 2)
    out["blacklist_hit"] = blacklist_hit
    out["duration_ms"] = round(dur, 1)
    return out


register(ToolDef(
    name="recon_broker",
    description=(
        "CAPA 6: Broker KYC externo. Llama RENAPO (Tlaloc) + Singula (antecedentes judiciales "
        "+ lista negra OFAC/UE/ONU/SAT 69B) + Apify. GASTA CRÉDITOS (~52 pesos). "
        "Solo invocar cuando hay un candidato SÓLIDO del padrón (1 hit claro). "
        "Score boost: +0.05 si RENAPO confirma, +0.15 si Singula limpio, -0.50 si está en blacklist."
    ),
    parameters={
        "type": "object",
        "properties": {
            "curp": {"type": "string", "description": "18 chars, del candidato del padrón"},
        },
        "required": ["curp"],
    },
    fn=_broker,
    cost_hint="high",
))
