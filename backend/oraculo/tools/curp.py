"""tools/curp.py — Lookup exacto por CURP en padrón."""
from __future__ import annotations
import time, duckdb
from . import ToolDef, ToolContext, register
from pathlib import Path

PADRON_PATH = Path(__file__).resolve().parent.parent.parent.parent / "bases" / "padron_v1.duckdb"


def _curp_lookup(args: dict, ctx: ToolContext) -> dict:
    curp = (args.get("curp") or "").strip().upper()
    if len(curp) != 18:
        return {"error": "CURP debe tener 18 caracteres", "rows": []}
    t0 = time.time()
    try:
        con = duckdb.connect(str(PADRON_PATH), read_only=True)
        try:
            cur = con.execute(
                "SELECT curp, paterno, materno, nombre, fecnac, sexo, "
                "calle, ext, int, colonia, cp, e AS estado, d AS distrito, m AS municipio, "
                "folio, cred, consec, s AS seccion, mza, l "
                "FROM padron WHERE curp = ? LIMIT 1", [curp]
            )
            cols = [d[0] for d in cur.description]
            raw = cur.fetchall()
            rows = [dict(zip(cols, r)) for r in raw]
        finally:
            con.close()
    except Exception as e:
        ctx.record("curp_lookup", {"curp": curp}, 0, (time.time()-t0)*1000, error=str(e))
        return {"error": str(e), "rows": []}
    dur = (time.time()-t0)*1000
    ctx.record("curp_lookup", {"curp": curp}, len(rows), dur)
    return {"rows": rows, "count": len(rows), "duration_ms": round(dur,1)}


register(ToolDef(
    name="curp_lookup",
    description=(
        "Lookup exacto por CURP (18 chars) en padrón. Devuelve el registro completo "
        "(domicilio, folio, sección, manzana, credencial, etc.). Usar cuando ya se "
        "tiene un CURP candidato y se quiere confirmar identidad o extraer domicilio."
    ),
    parameters={
        "type": "object",
        "properties": {"curp": {"type": "string"}},
        "required": ["curp"],
    },
    fn=_curp_lookup,
    cost_hint="low",
))
