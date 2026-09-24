"""Router de IMSS — lookup por CURP en asegurados y segmentación."""
from fastapi import APIRouter, HTTPException

from ..pools import pool

router = APIRouter(prefix="/q/imss", tags=["imss"])


@router.get("/by-curp/{curp}")
async def by_curp(curp: str):
    """Lookup por CURP en IMSS asegurados + salud."""
    curp = curp.strip().upper()
    if len(curp) != 18:
        raise HTTPException(status_code=400, detail="CURP debe tener 18 caracteres")

    # IMSS Asegurados (57M filas, vista filtra por curp_kind='PF18')
    sql_aseg = """
        SELECT curp_clean AS curp, nss_clean AS nss, nombre AS nombre_patron,
               registro_patron, nombre_patron AS empresa_nombre,
               domicilio_patron, ciudad_estado, cp5 AS empresa_cp,
               empresa_giro, sueldo_raw AS sueldo
        FROM imss_2025
        WHERE curp_clean = ? AND curp_kind = 'PF18'
        LIMIT 100
    """
    asegurados = await pool.execute("imss_asegurados_v1.duckdb", sql_aseg, [curp])

    # IMSS Salud (23M filas, vista filtra por curp_kind='PF18')
    sql_salud = """
        SELECT curp_clean AS curp, rfc_clean AS rfc, nss_clean AS nss,
               id_persona, unidad_medica, ooad, modalidad,
               segmentacion_diabetes_mellitus, segmento_hipertension,
               segmentacion_cancer_de_mama, segmentacion_cancer_de_prostata,
               nombre, apellido_paterno, apellido_materno,
               edad, genero, fecha_de_nacimiento
        FROM imss_personas
        WHERE curp_clean = ? AND curp_kind = 'PF18'
        LIMIT 100
    """
    salud = await pool.execute("imss_segmentacion_v1.duckdb", sql_salud, [curp])

    return {
        "curp": curp,
        "asegurados": {"count": len(asegurados), "rows": asegurados},
        "salud": {"count": len(salud), "rows": salud},
    }
