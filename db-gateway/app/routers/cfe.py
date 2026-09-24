"""Router de CFE — lookup por número de servicio y búsqueda fuzzy."""
from fastapi import APIRouter, HTTPException, Query

from ..pools import pool

router = APIRouter(prefix="/q/cfe", tags=["cfe"])


@router.get("/by-num-servicio/{num_servicio}")
async def by_num_servicio(num_servicio: str):
    """Lookup exacto por número de servicio CFE (10-12 dígitos)."""
    num = num_servicio.strip()
    if not num.isdigit() or len(num) < 8:
        raise HTTPException(
            status_code=400, detail="Número de servicio inválido (mínimo 8 dígitos)"
        )

    sql = """
        SELECT division, zona_codigo AS zona_cod, zona_nombre AS zona_nom,
               agencia_codigo AS agencia_cod, agencia_nombre AS agencia_nom,
               codigo_medidor, numero_medidor, numero_servicio,
               cp, nombre, direccion, calle_adicional_1, calle_adicional_2,
               colonia, hilos
        FROM medidores
        WHERE numero_servicio = ?
        LIMIT 10
    """
    rows = await pool.execute("cfe_v1.duckdb", sql, [num])
    return {"numero_servicio": num, "count": len(rows), "rows": rows}


@router.get("/buscar")
async def buscar(
    nombre: str | None = Query(None, min_length=2),
    cp: str | None = Query(None, min_length=5, max_length=5),
    colonia: str | None = Query(None, min_length=2),
    direccion: str | None = Query(None, min_length=2),
    limit: int = Query(20, ge=1, le=200),
):
    """Búsqueda fuzzy por nombre + dirección."""
    conditions = []
    params = []

    if nombre:
        conditions.append("UPPER(nombre) LIKE '%' || ? || '%'")
        params.append(nombre.upper())
    if cp:
        conditions.append("cp = ?")
        params.append(cp)
    if colonia:
        conditions.append("colonia LIKE '%' || ? || '%'")
        params.append(colonia.upper())
    if direccion:
        conditions.append("(direccion LIKE '%' || ? || '%' OR calle_adicional_1 LIKE '%' || ? || '%')")
        params.append(direccion.upper())
        params.append(direccion.upper())

    if not conditions:
        raise HTTPException(status_code=400, detail="Proporciona al menos un filtro")

    where = " AND ".join(conditions)
    sql = f"""
        SELECT numero_servicio, nombre, direccion, colonia, cp,
               division, zona_nombre AS zona_nom, agencia_nombre AS agencia_nom, hilos
        FROM medidores
        WHERE {where}
        LIMIT ?
    """
    params.append(limit)
    rows = await pool.execute("cfe_v1.duckdb", sql, params)
    return {"count": len(rows), "rows": rows}
