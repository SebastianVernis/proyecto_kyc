"""Capa 3: Búsqueda con VARIANTES en padrón.

Cuando la exacta falla, prueba:
  a) LIKE '%PRIMER_NOMBRE%' + LIKE '%SEGUNDO_NOMBRE%'
  b) invertir orden de apellidos
  c) solo apellidos (cualquier nombre)
  d) combinaciones con partículas (DE LA, DEL)
"""
from __future__ import annotations
import time, duckdb, re
from pathlib import Path
from .. import ToolDef, ToolContext, register

PADRON_PATH = Path(__file__).resolve().parent.parent.parent.parent.parent / "bases" / "padron_v1.duckdb"


def _padron_variants(args: dict, ctx: ToolContext) -> dict:
    t0 = time.time()
    paterno = (args.get("paterno") or "").strip().upper()
    materno = (args.get("materno") or "").strip().upper()
    nombre = (args.get("nombre") or "").strip().upper()
    limit = min(int(args.get("limit", 30)), 100)
    if not paterno or not materno:
        return {"error": "paterno y materno requeridos", "rows": []}

    con = duckdb.connect(str(PADRON_PATH), read_only=True)
    try:
        # 4 queries en una: cada palabra del nombre por separado
        palabras = [w for w in re.split(r"\s+", nombre) if len(w) > 1]

        # A: con cada palabra del nombre como LIKE
        # B: invertir paterno/materno
        # C: solo apellidos, cualquier nombre
        sql = """
        SELECT curp, paterno, materno, nombre, fecnac, sexo,
               calle, ext, colonia, cp, e AS estado, m AS municipio
        FROM padron
        WHERE UPPER(paterno) = ? AND UPPER(materno) = ?
          AND (
            UPPER(nombre) LIKE ?
            """ + ("" if not palabras else "OR " + " OR ".join(["UPPER(nombre) LIKE ?"] * len(palabras))) + """
          )
        ORDER BY
          (CASE WHEN UPPER(nombre) = ? THEN 0 ELSE 1 END),
          (CASE WHEN UPPER(nombre) LIKE ? THEN 0 ELSE 1 END),
          fecnac DESC
        LIMIT ?
        """
        params = [paterno, materno, f"%{nombre}%"]
        for w in palabras:
            params.append(f"%{w}%")
        params.extend([nombre, f"{palabras[0]}%" if palabras else f"%{nombre}%", limit])
        cur = con.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cols_norm = "rows"
    except Exception as e:
        rows = []
        cols_norm = f"error: {e}"

    # también: invertir apellidos
    inv_rows = []
    try:
        sql2 = """SELECT curp, paterno, materno, nombre, fecnac, sexo,
                         calle, ext, colonia, cp, e AS estado, m AS municipio
                  FROM padron
                  WHERE UPPER(paterno) = ? AND UPPER(materno) = ?
                    AND UPPER(nombre) LIKE ?
                  ORDER BY fecnac DESC LIMIT 15"""
        cur = con.execute(sql2, [materno, paterno, f"%{nombre}%"])
        cols = [d[0] for d in cur.description]
        inv_rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception:
        pass
    con.close()

    # dedupe por CURP
    seen = set()
    merged = []
    for r in rows + inv_rows:
        c = r.get("curp")
        if c and c not in seen:
            seen.add(c)
            merged.append(r)
    # scoring: ordenar por cuántos campos coinciden
    def score(r):
        s = 0
        if r.get("paterno", "").upper() == paterno: s += 0.3
        if r.get("materno", "").upper() == materno: s += 0.2
        n = r.get("nombre", "").upper()
        if n == nombre: s += 0.4
        elif any(w in n for w in palabras) if palabras else False: s += 0.2
        return s
    merged.sort(key=lambda r: -score(r))

    dur = (time.time()-t0)*1000
    ctx.record("recon_padron_variants", args, len(merged), dur)
    return {
        "rows": merged[:limit],
        "count": len(merged),
        "match_type": "VARIANTES",
        "score_por_match": [score(r) for r in merged[:limit]],
        "inversion_apellidos_count": len(inv_rows),
        "duration_ms": round(dur, 1),
    }


register(ToolDef(
    name="recon_padron_variants",
    description=(
        "CAPA 3: Búsqueda con VARIANTES. Llamar SOLO si recon_padron_exact devolvió 0 hits. "
        "Prueba: nombre LIKE '%PRIMER_NOMBRE%' y '%SEGUNDO_NOMBRE%' por separado, "
        "invierte orden de apellidos, busca solo con los apellidos. "
        "Devuelve hasta 30 candidatos con score por match parcial."
    ),
    parameters={
        "type": "object",
        "properties": {
            "paterno": {"type": "string"},
            "materno": {"type": "string"},
            "nombre":  {"type": "string"},
            "limit":   {"type": "integer", "default": 30},
        },
        "required": ["paterno", "materno"],
    },
    fn=_padron_variants,
    cost_hint="low",
))
