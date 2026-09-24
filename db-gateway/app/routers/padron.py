"""Router de Padrón Electoral — lookup por CURP y búsqueda por dirección."""
from fastapi import APIRouter, HTTPException, Query

from ..pools import pool

router = APIRouter(prefix="/q/padron", tags=["padron"])


@router.get("/by-curp/{curp}")
async def by_curp(curp: str):
    """Lookup exacto por CURP (18 caracteres)."""
    curp = curp.strip().upper()
    if len(curp) != 18:
        raise HTTPException(status_code=400, detail="CURP debe tener 18 caracteres")

    sql = """
        SELECT curp, nombre, paterno, materno, sexo, fecnac,
               calle, ext, colonia, cp, e, d, m, s, l, mza, cred, fuente
        FROM padron
        WHERE curp = ?
        LIMIT 50
    """
    rows = await pool.execute("padron_v1.duckdb", sql, [curp])
    return {"curp": curp, "count": len(rows), "rows": rows}


@router.get("/search")
async def search(
    cp: str | None = Query(None, min_length=5, max_length=5),
    colonia: str | None = Query(None, min_length=2),
    calle: str | None = Query(None, min_length=2),
    ext: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    """Búsqueda por dirección (cp + colonia + calle + ext)."""
    conditions = []
    params = []

    if cp:
        conditions.append("cp = ?")
        params.append(cp)
    if colonia:
        conditions.append("colonia LIKE '%' || ? || '%'")
        params.append(colonia.upper())
    if calle:
        conditions.append("calle LIKE '%' || ? || '%'")
        params.append(calle.upper())
    if ext:
        conditions.append("ext LIKE '%' || ? || '%'")
        params.append(ext.upper())

    if not conditions:
        raise HTTPException(status_code=400, detail="Proporciona al menos un filtro")

    where = " AND ".join(conditions)
    sql = f"""
        SELECT curp, nombre, paterno, materno, sexo, fecnac,
               calle, ext, colonia, cp, e, d, m, s
        FROM padron
        WHERE {where}
        LIMIT ?
    """
    params.append(limit)
    rows = await pool.execute("padron_v1.duckdb", sql, params)
    return {"count": len(rows), "rows": rows}
