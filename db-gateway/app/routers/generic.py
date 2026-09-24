"""Router genérico — lookup por RFC o CURP en cualquier base."""
from fastapi import APIRouter, HTTPException, Query

from ..pools import pool

router = APIRouter(prefix="/q", tags=["generic"])

# Mapeo de alias → (db_file, tabla, columna_pk)
BASE_MAP = {
    "att": ("att_v1.duckdb", "att", "rfc_clean"),
    "empleadores": ("empleadores_v1.duckdb", "empleadores", "rfc_clean"),
    "repuve": ("repuve_v1.duckdb", "repuve", "rfc_clean"),
    "telcel": ("telcel_v1.duckdb", "telcel", "rfc_clean"),
    "issste": ("issste_v1.duckdb", "empleados", None),
}


@router.get("/{base}/by-rfc/{rfc}")
async def by_rfc(base: str, rfc: str, limit: int = Query(50, ge=1, le=500)):
    """Lookup genérico por RFC en la base indicada."""
    if base not in BASE_MAP:
        raise HTTPException(
            status_code=404,
            detail=f"Base '{base}' no soportada. Disponibles: {list(BASE_MAP.keys())}",
        )

    db_file, table, pk_col = BASE_MAP[base]
    if not pk_col:
        raise HTTPException(status_code=400, detail=f"Base '{base}' no tiene lookup por RFC")

    rfc = rfc.strip().upper()
    sql = f"SELECT * FROM {table} WHERE {pk_col} = ? LIMIT ?"
    rows = await pool.execute(db_file, sql, [rfc, limit])
    return {"base": base, "rfc": rfc, "count": len(rows), "rows": rows}


@router.get("/{base}/by-curp/{curp}")
async def by_curp(base: str, curp: str, limit: int = Query(50, ge=1, le=500)):
    """Lookup genérico por CURP (solo bases con columna curp_clean)."""
    curp = curp.strip().upper()
    if len(curp) != 18:
        raise HTTPException(status_code=400, detail="CURP debe tener 18 caracteres")

    if base not in BASE_MAP:
        raise HTTPException(
            status_code=404,
            detail=f"Base '{base}' no soportada. Disponibles: {list(BASE_MAP.keys())}",
        )

    db_file, table, _ = BASE_MAP[base]
    sql = f"SELECT * FROM {table} WHERE curp_clean = ? LIMIT ?"
    rows = await pool.execute(db_file, sql, [curp, limit])
    return {"base": base, "curp": curp, "count": len(rows), "rows": rows}
