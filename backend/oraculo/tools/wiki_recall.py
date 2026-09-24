"""tools/wiki_recall.py — Búsqueda en la wiki gitnexus para layouts y heurísticas.

Permite al LLM "recordar" qué columna de RFC tiene cada base, qué partículas
manejar, qué gotchas de direcciones USA hay, sin tener que re-DESCRIBIR.
"""
from __future__ import annotations
import time, os
from pathlib import Path
from . import ToolDef, ToolContext, register

WIKI = Path(__file__).resolve().parents[3].parent / "wiki"


def _wiki_recall(args: dict, ctx: ToolContext) -> dict:
    t0 = time.time()
    query = (args.get("query") or "").strip().lower()
    if not query:
        return {"error": "se requiere query", "rows": []}

    # Búsqueda simple: leer todas las entities y concepts, matchear por tokens
    matches: list[dict] = []
    tokens = [t for t in query.split() if len(t) > 2]
    for subdir in ("entities", "concepts"):
        d = WIKI / subdir
        if not d.exists(): continue
        for f in d.glob("*.md"):
            try:
                content = f.read_text(errors="ignore")
            except Exception:
                continue
            content_l = content.lower()
            score = sum(1 for t in tokens if t in content_l)
            if score > 0:
                # Tomar las primeras 30 líneas significativas
                lines = [l for l in content.split("\n") if l.strip() and not l.startswith("---")]
                snippet = "\n".join(lines[:30])
                matches.append({
                    "page": str(f.relative_to(WIKI)),
                    "score": score,
                    "snippet": snippet[:1500],
                })
    matches.sort(key=lambda x: -x["score"])
    matches = matches[:5]
    dur = (time.time()-t0)*1000
    ctx.record("wiki_recall", args, len(matches), dur)
    return {"rows": matches, "count": len(matches), "duration_ms": round(dur,1)}


register(ToolDef(
    name="wiki_recall",
    description=(
        "Busca en la wiki gitnexus páginas relevantes por tokens. Sirve para "
        "recordar layouts de las 29 bases federadas, heurísticas de matching, "
        "gotchas de direcciones USA, manejo de partículas (DE LA, SAN, etc.). "
        "Llamar cuando hay duda sobre qué columna o filtro usar en otra tool."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "términos a buscar, ej. 'cfe columna rfc'"},
        },
        "required": ["query"],
    },
    fn=_wiki_recall,
    cost_hint="low",
))
