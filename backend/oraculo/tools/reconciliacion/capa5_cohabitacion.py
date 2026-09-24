"""Capa 5: Cohabitación CFE por domicilio.

Toma CP + calle del padrón (capa 2) y busca en CFE:
  1. medidores en ese CP + calle
  2. titular del contrato + cohabitantes
  3. inferencia de parentesco por apellidos
"""
from __future__ import annotations
import time
from .. import ToolDef, ToolContext, register


def _cohabitacion(args: dict, ctx: ToolContext) -> dict:
    t0 = time.time()
    try:
        from servir import _init_extended_con
    except Exception as e:
        return {"error": f"servir no importable: {e}", "rows": []}
    con = _init_extended_con()
    if con is None:
        return {"error": "extended DB no inicializado", "rows": []}

    cp = (args.get("cp") or "").zfill(5)
    calle = (args.get("calle") or "").strip().upper()
    numero = (args.get("numero") or "").strip()
    colonia = (args.get("colonia") or "").strip().upper()
    paterno = (args.get("paterno") or "").strip().upper()
    if not cp or len(cp) != 5:
        return {"error": "cp requerido (5 dígitos)", "rows": []}

    # 1) buscar medidores en el CP
    where = ["cp = ?"]
    params = [cp]
    if calle:
        where.append("UPPER(direccion) LIKE ?")
        params.append(f"%{calle}%")
    if numero:
        where.append("direccion LIKE ?")
        params.append(f"%{numero}%")
    if colonia:
        where.append("UPPER(colonia) LIKE ?")
        params.append(f"%{colonia}%")

    sql = f"""SELECT numero_servicio, nombre, direccion, colonia, cp, division, zona_nombre
             FROM b_cfe.main.medidores WHERE {' AND '.join(where)}
             LIMIT 50"""
    try:
        cur = con.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as e:
        return {"error": str(e), "rows": []}

    # 2) inferir parentesco: titular CFE comparte apellido paterno?
    cohabitantes = []
    titular_match = False
    for r in rows:
        titular = (r.get("nombre") or "").upper()
        # titular parece el sujeto?
        if paterno and paterno in titular:
            titular_match = True
        else:
            cohabitantes.append(r)

    # 3) familiares directos: cualquier cohabitante que comparta paterno
    familiares = []
    for c in cohabitantes:
        titular = (c.get("nombre") or "").upper()
        if paterno and paterno in titular:
            familiares.append(c)

    dur = (time.time()-t0)*1000
    score_boost = 0.0
    if titular_match: score_boost += 0.10
    if familiares:    score_boost += 0.05
    score_boost = min(score_boost, 0.20)

    ctx.record("recon_cohabitacion", args, len(rows), dur)
    return {
        "rows": rows,
        "count": len(rows),
        "titular_es_sujeto": titular_match,
        "familiares_detectados": familiares[:10],
        "cohabitantes_count": len(cohabitantes),
        "score_boost": round(score_boost, 2),
        "duration_ms": round(dur, 1),
    }


register(ToolDef(
    name="recon_cohabitacion",
    description=(
        "CAPA 5: Cohabitación CFE. Toma el domicilio (CP + calle) del candidato del padrón "
        "y busca en CFE los medidores activos. Identifica al titular del contrato y a "
        "familiares que cohabitan (comparte apellido paterno). Score boost: 0.10 si el "
        "titular CFE es el sujeto, +0.05 si hay familiares detectados."
    ),
    parameters={
        "type": "object",
        "properties": {
            "cp":      {"type": "string", "description": "5 dígitos, requerido"},
            "calle":   {"type": "string"},
            "numero":  {"type": "string"},
            "colonia": {"type": "string"},
            "paterno": {"type": "string", "description": "para inferir parentesco"},
        },
        "required": ["cp"],
    },
    fn=_cohabitacion,
    cost_hint="low",
))
