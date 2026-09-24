"""tools/padron.py — Búsqueda flexible en el padrón electoral (88.4M).

El padrón es SIEMPRE el primer punto de búsqueda. Tiene cobertura casi
universal de adultos mexicanos. El LLM debe llamarlo con cualquier nombre
+ opcional fecnac y/o estado.
"""
from __future__ import annotations
import time, duckdb
from . import ToolDef, ToolContext, register
from pathlib import Path

PADRON_PATH = Path(__file__).resolve().parent.parent.parent.parent / "bases" / "padron_v1.duckdb"


def _padron_query(args: dict, ctx: ToolContext) -> dict:
    """SELECT en padron con WHERE dinámico.

    Args esperados: paterno, materno?, nombre, fecnac?, estado?, cp?, limit?
    """
    where = []
    params: list = []
    if args.get("paterno"):
        where.append("UPPER(paterno) = UPPER(?)")
        params.append(args["paterno"].strip())
    if args.get("materno"):
        where.append("UPPER(materno) = UPPER(?)")
        params.append(args["materno"].strip())
    if args.get("nombre"):
        where.append("UPPER(nombre) LIKE UPPER(?)")
        params.append(f"%{args['nombre'].strip()}%")
    if args.get("fecnac"):
        where.append("fecnac = ?")
        params.append(args["fecnac"])
    if args.get("estado"):
        # en padron el estado está como clave numérica; aceptamos nombre o número
        where.append("estado = ?")
        params.append(args["estado"])
    if args.get("cp"):
        where.append("cp = ?")
        params.append(str(args["cp"]).zfill(5))

    if not where:
        return {"error": "se requiere al menos paterno o nombre", "rows": []}

    limit = min(int(args.get("limit", 50)), 200)
    sql = (
        "SELECT curp, paterno, materno, nombre, fecnac, sexo, "
        "calle, ext, colonia, cp, e AS estado, d AS distrito, m AS municipio, "
        "folio, cred, consec "
        "FROM padron WHERE " + " AND ".join(where) +
        f" LIMIT {limit}"
    )

    t0 = time.time()
    try:
        con = duckdb.connect(str(PADRON_PATH), read_only=True)
        try:
            cur = con.execute(sql, params)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            con.close()
    except Exception as e:
        ctx.record("padron_search", args, 0, (time.time()-t0)*1000, error=str(e))
        return {"error": str(e), "rows": []}
    dur = (time.time()-t0)*1000
    ctx.record("padron_search", args, len(rows), dur)
    return {"rows": rows, "count": len(rows), "sql": sql, "duration_ms": round(dur,1)}


register(ToolDef(
    name="padron_search",
    description=(
        "Busca en el padrón electoral (88.4M registros, cobertura universal de adultos "
        "mexicanos). SIEMPRE primera opción para localizar un sujeto. Soporta filtrado "
        "combinado por apellidos, nombre parcial, fecnac exacta, estado (clave 1-32) o CP."
    ),
    parameters={
        "type": "object",
        "properties": {
            "paterno": {"type": "string"},
            "materno": {"type": "string"},
            "nombre":  {"type": "string"},
            "fecnac":  {"type": "string", "description": "YYYY-MM-DD"},
            "estado":  {"type": "string", "description": "clave numérica 1-32 o nombre"},
            "cp":      {"type": "string"},
            "limit":   {"type": "integer", "default": 50},
        },
        "required": [],
    },
    fn=_padron_query,
    cost_hint="low",
))
