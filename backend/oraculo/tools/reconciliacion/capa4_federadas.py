"""Capa 4: Búsqueda en las 29 bases federadas por RFC base.

Toma un CURP o un set de (paterno, materno, nombre, fecnac) y:
  1. Calcula RFC base de 10 chars
  2. Recorre las 29 bases federadas buscando por RFC
  3. Devuelve para cada base el nº de hits y muestra
"""
from __future__ import annotations
import time
from .. import ToolDef, ToolContext, register


def _federadas(args: dict, ctx: ToolContext) -> dict:
    t0 = time.time()
    try:
        from servir import _init_extended_con, ENTITY_TO_BASES, TABLE_FOR_BASE
    except Exception as e:
        return {"error": f"servir no importable: {e}", "rows": []}

    con = _init_extended_con()
    if con is None:
        return {"error": "extended DB no inicializado", "rows": []}

    # Calcular RFC
    rfc = ""
    if args.get("curp") and len(args["curp"]) == 18:
        rfc = args["curp"][:10].upper()
    elif args.get("rfc"):
        rfc = args["rfc"][:10].upper()
    else:
        # calcular desde nombre
        try:
            from rfc_utils import calcular_rfc_desde_curp
            r = calcular_rfc_desde_curp(
                curp="",
                nombre=args.get("nombre", ""),
                paterno=args.get("paterno", ""),
                materno=args.get("materno", ""),
                fecnac=args.get("fecnac", ""),
            )
            rfc = (r.get("rfc_10") or "")[:10].upper()
        except Exception:
            pass

    if not rfc or len(rfc) < 10:
        return {"error": "no se pudo calcular RFC base", "rows": []}

    # Detectar columnas de cada base (cachear)
    schema_cache = {}
    out_rows: list = []
    por_entidad: dict = {}

    for ent, aliases in ENTITY_TO_BASES.items():
        for alias in aliases:
            if alias not in TABLE_FOR_BASE:
                continue
            tbl = TABLE_FOR_BASE[alias]
            if alias not in schema_cache:
                try:
                    cols = {r[0].lower() for r in con.execute(f"DESCRIBE {alias}.{tbl}").fetchall()}
                except Exception:
                    cols = set()
                rfc_col = next((c for c in ("rfc_clean","rfc","rfc_sin_dv","rfc10") if c in cols), None)
                schema_cache[alias] = (cols, rfc_col)
            else:
                cols, rfc_col = schema_cache[alias]
            if not rfc_col:
                continue
            try:
                cur = con.execute(
                    f"SELECT * FROM {alias}.{tbl} WHERE UPPER(COALESCE({rfc_col}, '')) = ? LIMIT 3",
                    [rfc]
                )
                desc = cur.description
                rs = cur.fetchall()
                if rs:
                    cols_names = [d[0] for d in desc]
                    for r in rs:
                        rec = dict(zip(cols_names, r))
                        rec["__base"] = alias
                        rec["__entidad"] = ent
                        rec["__match_field"] = rfc_col
                        out_rows.append(rec)
                por_entidad[ent] = por_entidad.get(ent, 0) + len(rs)
            except Exception as e:
                por_entidad[f"{ent}_error"] = str(e)[:200]

    # Score: +0.1 por cada base con hit
    bases_con_hit = sum(1 for v in por_entidad.values() if isinstance(v, int) and v > 0)
    score_boost = min(bases_con_hit * 0.10, 1.0)

    dur = (time.time()-t0)*1000
    ctx.record("recon_federadas", args, len(out_rows), dur)
    return {
        "rows": out_rows,
        "count": len(out_rows),
        "rfc_base": rfc,
        "por_entidad": por_entidad,
        "bases_con_hit": bases_con_hit,
        "score_boost": round(score_boost, 2),
        "duration_ms": round(dur, 1),
    }


register(ToolDef(
    name="recon_federadas",
    description=(
        "CAPA 4: Búsqueda en las 29 bases federadas (ATT, CFE, Telcel×4, IMSS×2, "
        "Santander×6, HSBC×2, Citibanamex, Banorte, Bancomer, Bancoppel, AMEX, "
        "Clavijero, REPUVE, ISSSTE, Docentes, COVID23, Hospital Angeles, Empleadores) "
        "usando RFC base de 10 chars. Calcula el RFC desde CURP o desde nombre+fechanac. "
        "Devuelve los hits por base, score_boost = 0.10 × #bases con hit. "
        "Llamar después de tener un candidato sólido del padrón."
    ),
    parameters={
        "type": "object",
        "properties": {
            "curp":   {"type": "string", "description": "preferido: 18 chars"},
            "rfc":    {"type": "string"},
            "paterno":{"type": "string"},
            "materno":{"type": "string"},
            "nombre": {"type": "string"},
            "fecnac": {"type": "string"},
        },
        "required": [],
    },
    fn=_federadas,
    cost_hint="medium",
))
