"""Capa 2: Match EXACTO en padrón electoral.

Búsqueda determinística por paterno + materno + nombre.
Si hay 1 hit, ese es el sujeto con score 0.5.
Si hay 2-5 hits, devuelve lista de homónimos para que el LLM pida fecnac/CP al usuario.
Si hay 0 hits, devuelve vacío (debe caer a capa 3).
"""
from __future__ import annotations
import time, duckdb
from pathlib import Path
from .. import ToolDef, ToolContext, register

PADRON_PATH = Path(__file__).resolve().parent.parent.parent.parent.parent / "bases" / "padron_v1.duckdb"


def _padron_exact(args: dict, ctx: ToolContext) -> dict:
    t0 = time.time()
    paterno = (args.get("paterno") or "").strip().upper()
    materno = (args.get("materno") or "").strip().upper()
    nombre = (args.get("nombre") or "").strip().upper()
    if not paterno or not materno or not nombre:
        return {"error": "paterno, materno y nombre son requeridos (usar recon_normalize primero)", "rows": []}

    con = duckdb.connect(str(PADRON_PATH), read_only=True)
    try:
        sql = """SELECT curp, paterno, materno, nombre, fecnac, sexo,
                        calle, ext, int, colonia, cp, e AS estado, d AS distrito, m AS municipio,
                        s AS seccion, mza, l, folio, cred, consec
                 FROM padron
                 WHERE UPPER(paterno) = ? AND UPPER(materno) = ? AND UPPER(nombre) = ?
                 ORDER BY fecnac DESC LIMIT 20"""
        cur = con.execute(sql, [paterno, materno, nombre])
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()

    dur = (time.time()-t0)*1000
    # Score automático: 1 hit = 0.5, múltiples = 0.3 c/u
    score = 0.5 if len(rows) == 1 else (0.3 if rows else 0.0)
    ctx.record("recon_padron_exact", args, len(rows), dur)
    return {
        "rows": rows,
        "count": len(rows),
        "match_type": "EXACTO",
        "score_por_cantidad": score,
        "ambiguo": len(rows) > 1,
        "duration_ms": round(dur, 1),
    }


register(ToolDef(
    name="recon_padron_exact",
    description=(
        "CAPA 2: Match EXACTO por paterno+materno+nombre en el padrón (88.4M). "
        "Devuelve 0, 1 o varios registros. Si count=1, ese es el sujeto. "
        "Si count>1, son homónimos (mismo nombre completo) y el LLM debe pedir fecnac/CP "
        "al usuario para desambiguar. Si count=0, caer a recon_padron_variants."
    ),
    parameters={
        "type": "object",
        "properties": {
            "paterno": {"type": "string"},
            "materno": {"type": "string"},
            "nombre":  {"type": "string"},
        },
        "required": ["paterno", "materno", "nombre"],
    },
    fn=_padron_exact,
    cost_hint="low",
))
