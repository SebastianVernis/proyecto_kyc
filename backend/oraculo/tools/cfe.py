"""tools/cfe.py — Búsqueda por domicilio en CFE (medidores eléctricos).

Oro para descubrimiento familiar: si el sujeto vive con familia, el titular
del contrato CFE suele ser pariente. Acepta fuzzy match en calle+CP.
"""
from __future__ import annotations
import time
from . import ToolDef, ToolContext, register


def _cfe_search(args: dict, ctx: ToolContext) -> dict:
    """Busca medidores CFE por calle, número exterior, colonia, CP o nombre.

    Args: calle?, numero?, colonia?, cp?, nombre? (titular), limit?
    """
    t0 = time.time()
    try:
        from servir import _init_extended_con, TABLE_FOR_BASE
    except Exception as e:
        return {"error": f"servir no importable: {e}", "rows": []}

    con = _init_extended_con()
    if con is None:
        return {"error": "extended DB no inicializado", "rows": []}

    where = []
    params: list = []
    if args.get("calle"):
        # Fuzzy: UPPER LIKE en direccion
        where.append("UPPER(COALESCE(direccion, '')) LIKE UPPER(?)")
        params.append(f"%{args['calle']}%")
    if args.get("numero"):
        where.append("COALESCE(direccion, '') LIKE ?")
        params.append(f"%{args['numero']}%")
    if args.get("colonia"):
        where.append("UPPER(COALESCE(colonia, '')) LIKE UPPER(?)")
        params.append(f"%{args['colonia']}%")
    if args.get("cp"):
        where.append("cp = ?")
        params.append(str(args["cp"]).zfill(5))
    if args.get("nombre"):
        where.append("UPPER(COALESCE(nombre, '')) LIKE UPPER(?)")
        params.append(f"%{args['nombre']}%")
    if not where:
        return {"error": "calle, cp, colonia o nombre requerido", "rows": []}

    limit = min(int(args.get("limit", 30)), 100)
    sql = f"SELECT numero_servicio, nombre, direccion, colonia, cp, division, zona_nombre, agencia_nombre, calle_adicional_1 FROM b_cfe.main.medidores WHERE " + " AND ".join(where) + f" LIMIT {limit}"
    try:
        cur = con.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as e:
        return {"error": str(e), "rows": []}
    dur = (time.time()-t0)*1000
    ctx.record("cfe_search", args, len(rows), dur)
    return {"rows": rows, "count": len(rows), "duration_ms": round(dur,1)}


register(ToolDef(
    name="cfe_search",
    description=(
        "Busca contratos de luz (CFE, 66M medidores) por calle, número exterior, "
        "colonia, CP o nombre del titular. ORO para descubrimiento familiar: si el "
        "sujeto vive con parientes, el titular del contrato CFE suele ser familiar. "
        "Usar después de localizar domicilio del padrón para encontrar cohabitantes."
    ),
    parameters={
        "type": "object",
        "properties": {
            "calle":   {"type": "string"},
            "numero":  {"type": "string"},
            "colonia": {"type": "string"},
            "cp":      {"type": "string"},
            "nombre":  {"type": "string", "description": "nombre del titular del contrato"},
            "limit":   {"type": "integer", "default": 30},
        },
        "required": [],
    },
    fn=_cfe_search,
    cost_hint="low",
))
