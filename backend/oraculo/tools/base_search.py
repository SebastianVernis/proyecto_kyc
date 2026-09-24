"""tools/base_search.py — Búsqueda federada en las 29 bases extendidas por RFC/teléfono.

Aprende automáticamente el esquema de cada base (qué columna de RFC tiene:
rfc, rfc_clean, curp_clean, etc.) y construye el WHERE adecuado. Cachea el
mapeo para no re-DESCRIBIR en cada query.
"""
from __future__ import annotations
import time
from . import ToolDef, ToolContext, register


_SCHEMA_CACHE: dict[str, dict] = {}


def _detect_columns(con, alias: str, table: str) -> dict:
    """DESCRIBE la tabla y devuelve mapeo de campos lógicos a columnas reales."""
    if alias in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[alias]
    try:
        rows = con.execute(f"DESCRIBE {alias}.{table}").fetchall()
        cols = {r[0].lower() for r in rows}
    except Exception as e:
        _SCHEMA_CACHE[alias] = {"_error": str(e), "_cols": set()}
        return _SCHEMA_CACHE[alias]

    # Mapeo lógico: preferimos columnas con datos limpios
    rfc_col = None
    for cand in ("rfc_clean", "rfc", "rfc_sin_dv", "rfc10"):
        if cand in cols:
            rfc_col = cand
            break

    curp_col = None
    for cand in ("curp_clean", "curp", "curp18"):
        if cand in cols:
            curp_col = cand
            break

    tel_col = None
    for cand in ("telefono", "tel", "celular", "telefono_fijo", "tel1"):
        if cand in cols:
            tel_col = cand
            break

    nombre_cols = [c for c in ("nombre", "nombre_completo", "nombres", "titular_nombre1",
                               "nombre1", "name") if c in cols]
    paterno_cols = [c for c in ("paterno", "apellido_paterno", "pat", "titular_paterno") if c in cols]
    materno_cols = [c for c in ("materno", "apellido_materno", "may", "titular_materno") if c in cols]

    info = {
        "_cols": cols,
        "rfc": rfc_col,
        "curp": curp_col,
        "telefono": tel_col,
        "nombre": nombre_cols,
        "paterno": paterno_cols,
        "materno": materno_cols,
    }
    _SCHEMA_CACHE[alias] = info
    return info


def _base_search(args: dict, ctx: ToolContext) -> dict:
    t0 = time.time()
    try:
        from servir import _init_extended_con, ENTITY_TO_BASES, TABLE_FOR_BASE
    except Exception as e:
        return {"error": f"servir no importable: {e}", "rows": []}

    con = _init_extended_con()
    if con is None:
        return {"error": "extended DB no inicializado", "rows": []}

    rfc = (args.get("rfc") or "").strip().upper()
    telefono = "".join(c for c in str(args.get("telefono") or "") if c.isdigit())[-10:]
    nombre = (args.get("nombre") or "").strip()
    limit = min(int(args.get("limit", 50)), 200)
    entidad = (args.get("entidad") or "all").lower()
    if entidad == "all":
        entidades = list(ENTITY_TO_BASES.keys())
    elif entidad in ENTITY_TO_BASES:
        entidades = [entidad]
    else:
        return {"error": f"entidad '{entidad}' no existe; válidas: {list(ENTITY_TO_BASES.keys())}", "rows": []}

    if not rfc and not telefono and not nombre:
        return {"error": "rfc, telefono o nombre requerido", "rows": []}

    out_rows: list = []
    by_base: dict = {}

    for ent in entidades:
        for alias in ENTITY_TO_BASES[ent]:
            if alias not in TABLE_FOR_BASE:
                continue
            tbl = TABLE_FOR_BASE[alias]
            schema = _detect_columns(con, alias, tbl)
            if schema.get("_error"):
                by_base[f"{ent}_error"] = schema["_error"][:200]
                continue

            where = []
            params: list = []
            rfc_col = schema.get("rfc")
            if rfc and rfc_col:
                where.append(f"UPPER(COALESCE({rfc_col}, '')) = ?")
                params.append(rfc)
            if telefono and schema.get("telefono"):
                tcol = schema["telefono"]
                where.append(f"REGEXP_REPLACE(COALESCE({tcol},''), '[^0-9]', '', 'g') = ?")
                params.append(telefono)
            if nombre and schema.get("paterno"):
                pat_col = schema["paterno"][0]
                where.append(f"UPPER(COALESCE({pat_col}, '')) LIKE UPPER(?)")
                params.append(f"%{nombre}%")
            if not where:
                continue
            sql = f"SELECT * FROM {alias}.{tbl} WHERE " + " AND ".join(where) + f" LIMIT {limit}"
            try:
                rs = con.execute(sql, params).fetchall()
                cols = [d[0] for d in con.description] if con.description else []
                for r in rs:
                    rec = dict(zip(cols, r))
                    rec["__base"] = alias
                    rec["__entidad"] = ent
                    out_rows.append(rec)
                by_base[ent] = by_base.get(ent, 0) + len(rs)
            except Exception as e:
                by_base[f"{ent}_error"] = str(e)[:200]

    dur = (time.time()-t0)*1000
    ctx.record("base_search", args, len(out_rows), dur)
    return {
        "rows": out_rows[:limit],
        "total": len(out_rows),
        "por_entidad": by_base,
        "duration_ms": round(dur, 1),
    }


register(ToolDef(
    name="base_search",
    description=(
        "Busca un sujeto en las 29 bases federadas (ATT, CFE, Telcel×4, IMSS×2, "
        "Santander×6, HSBC×2, Citibanamex, Banorte, Bancomer, Bancoppel, AMEX, "
        "Clavijero, REPUVE, ISSSTE, Docentes, COVID23, Hospital Angeles, Empleadores) "
        "usando RFC base de 10 chars, teléfono (10 dígitos exactos) o nombre. Detecta "
        "automáticamente qué columna tiene cada base (rfc_clean, rfc, curp_clean, etc.). "
        "SIEMPRE llamar después de calcular RFC con rfc_calc."
    ),
    parameters={
        "type": "object",
        "properties": {
            "entidad":  {"type": "string", "description": "santander|telcel|cfe|...|all (default all)"},
            "rfc":      {"type": "string"},
            "telefono": {"type": "string"},
            "nombre":   {"type": "string"},
            "limit":    {"type": "integer", "default": 50},
        },
        "required": [],
    },
    fn=_base_search,
    cost_hint="medium",
))
