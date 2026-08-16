"""
fuzzy_direccion.py — Candidatos de domicilio en el padrón para decisión manual.

Cuando una dirección (p.ej. de CFE) no cruza por clave exacta, este módulo
devuelve las opciones más parecidas del padrón, rankeadas por score, para que
el usuario decida con cuál enriquecer. Siempre que exista alguna opción
razonable, se devuelve — el criterio final es del usuario.

Estrategia:
  1. EXACTO: cascada de claves (k_via_ext → k_col_via) contra mkidx_padron.
  2. FUZZY: pool restringido (mismo cp; o misma entidad + prefijo de colonia)
     y score jaro_winkler sobre vialidad + colonia, con bonos por ext y cp.

Uso programático:
    from fuzzy_direccion import candidatos
    r = candidatos(calle="IGNACIO SARAGOSA", ext="5", cp="98400")
    # r = {"consulta": {...}, "candidatos": [{score, nivel, cp, via, ext, col,
    #       entidad, municipio, n_personas, refs}, ...]}
"""
from __future__ import annotations
import threading
from pathlib import Path
from typing import Optional

import duckdb

import normalizar_direccion as N

_BASES_DIR = Path(__file__).resolve().parent.parent / "bases"
IDX_PADRON = _BASES_DIR / "mkidx_padron.duckdb"

NIVELES = ["k_via_ext", "k_col_via_ext", "k_via_cp", "k_col_via"]

_lock = threading.Lock()
_con: Optional[duckdb.DuckDBPyConnection] = None


def _conexion() -> Optional[duckdb.DuckDBPyConnection]:
    """Conexión perezosa (read-only) al índice del padrón. None si no existe
    o está bloqueado por el escritor (materialización en curso)."""
    global _con
    with _lock:
        if _con is not None:
            return _con
        if not IDX_PADRON.exists():
            return None
        try:
            con = duckdb.connect(":memory:")
            con.execute("SET memory_limit='2GB'; SET threads=4;")
            con.execute(f"ATTACH '{IDX_PADRON}' AS ixp (READ_ONLY)")
            _con = con
        except duckdb.Error:
            return None
        return _con


def _normalizar(calle=None, ext=None, colonia=None, cp=None,
                municipio=None, entidad=None) -> dict:
    canon = N.normalizar_direccion(calle=calle, ext=ext, colonia=colonia,
                                   cp=cp, municipio=municipio, entidad=entidad)
    claves = N.clave_match(canon)
    via = N._norm_vialidad(canon.get("nombre_vialidad"))
    col_full = N._norm_texto(canon.get("nombre_asentamiento"))
    return {
        "cp": canon.get("cp"),
        "via": via,
        "ext": canon.get("num_ext"),
        "col": col_full[:15] if col_full else None,
        "ent": canon.get("entidad_clave"),
        "claves": {k: claves.get(k) for k in NIVELES},
    }


def _agrupar(con, where_sql: str, params: list, score_sql: str,
             score_params: list, limit: int) -> list[dict]:
    """Candidatos agrupados por domicilio (cp|via|ext|col), con conteo de
    personas y refs de muestra para drill-down."""
    rows = con.execute(f"""
        SELECT cp, via, ext, col, ent, {score_sql} AS score,
               count(*) AS n_personas, list_slice(list(ref), 1, 50) AS refs
        FROM ixp.dir_padron
        WHERE {where_sql}
        GROUP BY cp, via, ext, col, ent, score
        ORDER BY score DESC, n_personas DESC
        LIMIT {int(limit)}
    """, score_params + params).fetchall()
    out = []
    for cp, via, ext, col, ent, score, n, refs in rows:
        out.append({
            "score": round(float(score), 3), "cp": cp, "via": via, "ext": ext,
            "col": col, "entidad": N.ENTIDAD.get(ent) if ent else None,
            "n_personas": n, "refs": refs,
        })
    return out


def candidatos(calle=None, ext=None, colonia=None, cp=None, municipio=None,
               entidad=None, limit: int = 10) -> dict:
    """Devuelve candidatos del padrón para la dirección dada.

    Primero intenta las claves exactas en cascada; si ninguna pega, hace
    fuzzy sobre un pool restringido. Nunca lanza: si no hay índice o datos
    suficientes, devuelve candidatos=[] con el motivo en `nota`.
    """
    q = _normalizar(calle=calle, ext=ext, colonia=colonia, cp=cp,
                    municipio=municipio, entidad=entidad)
    res = {"consulta": q, "match_exacto": None, "candidatos": [], "nota": None}

    con = _conexion()
    if con is None:
        res["nota"] = "índice del padrón no disponible (¿materializando?)"
        return res

    # 1) cascada exacta
    for nivel in NIVELES:
        k = q["claves"].get(nivel)
        if not k:
            continue
        cands = _agrupar(con, f"{nivel} = ?", [k], "1.0", [], limit)
        if cands:
            for c in cands:
                c["nivel"] = f"exacto:{nivel}"
            res["match_exacto"] = nivel
            res["candidatos"] = cands
            return res

    # 2) fuzzy: pool restringido + jaro_winkler
    if not q["via"] and not q["col"]:
        res["nota"] = "sin vialidad ni colonia utilizables para fuzzy"
        return res

    score_parts, score_params = [], []
    if q["via"]:
        score_parts.append("0.6 * jaro_winkler_similarity(via, ?)")
        score_params.append(q["via"])
    if q["col"]:
        score_parts.append("0.25 * jaro_winkler_similarity(col, ?)")
        score_params.append(q["col"])
    if q["ext"]:
        score_parts.append("0.15 * CASE WHEN ext = ? THEN 1.0 ELSE 0.0 END")
        score_params.append(q["ext"])
    score_sql = " + ".join(score_parts)

    where, params = [], []
    if q["cp"]:
        where.append("cp = ?")
        params.append(q["cp"])
    elif q["col"]:
        # bloque por prefijo de colonia (3 chars) para no barrer 88M filas
        where.append("substr(col, 1, 3) = substr(?, 1, 3)")
        params.append(q["col"])
        if q["ent"]:
            where.append("ent = ?")
            params.append(q["ent"])
    else:
        res["nota"] = "fuzzy requiere cp o colonia como bloque"
        return res

    try:
        cands = _agrupar(con, " AND ".join(where), params,
                         score_sql, score_params, limit)
    except duckdb.Error as e:
        res["nota"] = f"error fuzzy: {str(e)[:120]}"
        return res
    for c in cands:
        c["nivel"] = "fuzzy"
    res["candidatos"] = cands
    if not cands:
        res["nota"] = "sin candidatos en el bloque consultado"
    return res


def personas_de_refs(refs: list[int], db_padron: Optional[str] = None) -> list[dict]:
    """Datos del padrón para los refs (rowids) de un candidato elegido."""
    if not refs:
        return []
    db = db_padron or str(_BASES_DIR / "padron.duckdb")
    con = duckdb.connect(db, read_only=True)
    try:
        marks = ",".join("?" * len(refs))
        cols = ["curp", "nombre", "paterno", "materno", "calle", "ext",
                "colonia", "cp", "e", "m"]
        rows = con.execute(
            f"SELECT {', '.join(cols)} FROM padron WHERE rowid IN ({marks})",
            list(refs)).fetchall()
        return [dict(zip(cols, r)) for r in rows]
    finally:
        con.close()
