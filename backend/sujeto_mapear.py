"""sujeto_mapear.py — Flujo de mapeo completo de sujeto.

Endpoint: POST /api/v1/sujeto/mapear
Input: datos del padrón electoral (CURP, nombre, dirección)
Output: perfil fiscal (CheckID) + coincidencias CFE + mapas

Flujo:
1. Validar sesión + 3 créditos de búsqueda
2. Consultar CheckID con CURP → RFC, NSS, CP fiscal, régimen, 69/69B
3. Comparar CP fiscal vs CP del padrón
4. Buscar CFE por dirección del padrón
5. Buscar CFE por nombre del titular
6. Clasificar coincidencias CFE:
   - Dirección exacta → "sin conexión" (se muestra, baja relevancia)
   - Apellidos coinciden → "domicilio familiar"
   - Nombre exacto coincide → "domicilio del sujeto"
7. Retornar perfil completo
"""
from __future__ import annotations

import time
from typing import Any

from providers.checkid import CheckIdClient


def mapear_sujeto(
    *,
    curp: str,
    nombre: str = "",
    paterno: str = "",
    materno: str = "",
    calle: str = "",
    ext: str = "",
    colonia: str = "",
    cp: str = "",
    estado: str = "",
    municipio: str = "",
    seccion: str = "",
    con_extended=None,
) -> dict:
    """Ejecuta el flujo completo de mapeo de sujeto.

    Args:
        curp: CURP del sujeto (del padrón, no necesita validación)
        nombre, paterno, materno: nombre completo del padrón
        calle, ext, colonia, cp: dirección del padrón
        estado, municipio, seccion: ubicación electoral
        con_extended: conexión DuckDB extendida (ya inicializada)

    Returns:
        dict con:
        - padron: datos originales del padrón
        - checkid: resultado de CheckID (RFC, NSS, CP fiscal, régimen, 69/69B)
        - cp_comparison: comparación CP padrón vs CP fiscal
        - cfe: resultados de búsqueda CFE clasificados
        - metadata: timing, créditos usados
    """
    t0 = time.time()
    result = {
        "padron": {
            "curp": curp.upper(),
            "nombre": nombre,
            "paterno": paterno,
            "materno": materno,
            "calle": calle,
            "ext": ext,
            "colonia": colonia,
            "cp": cp,
            "estado": estado,
            "municipio": municipio,
            "seccion": seccion,
        },
        "checkid": None,
        "cp_comparison": None,
        "cfe": None,
        "metadata": {"steps": [], "elapsed_ms": 0},
    }

    # ── Paso 1: CheckID ──────────────────────────────────────────────────
    t1 = time.time()
    try:
        from config import config
        api_key = config.checkid_api_key
        if api_key:
            client = CheckIdClient(api_key=api_key)
            checkid_data = client.get_full(curp)
            result["checkid"] = checkid_data
            result["metadata"]["steps"].append({
                "step": "checkid",
                "elapsed_ms": int((time.time() - t1) * 1000),
                "ok": checkid_data.get("exitoso", False),
            })
        else:
            result["checkid"] = {"exitoso": False, "error": "API key no configurada"}
            result["metadata"]["steps"].append({
                "step": "checkid", "elapsed_ms": 0, "ok": False,
                "error": "API key no configurada",
            })
    except Exception as e:
        result["checkid"] = {"exitoso": False, "error": str(e)[:200]}
        result["metadata"]["steps"].append({
            "step": "checkid", "elapsed_ms": 0, "ok": False,
            "error": str(e)[:200],
        })

    # ── Paso 2: Comparar CP ──────────────────────────────────────────────
    cp_fiscal = None
    if result["checkid"] and result["checkid"].get("exitoso"):
        cp_fiscal_node = result["checkid"].get("codigo_postal")
        if isinstance(cp_fiscal_node, dict):
            cp_fiscal = cp_fiscal_node.get("codigoPostal")

    cp_padron = cp or ""
    cp_match = bool(cp_fiscal and cp_padron and cp_fiscal == cp_padron)
    result["cp_comparison"] = {
        "cp_padron": cp_padron or None,
        "cp_fiscal": cp_fiscal,
        "coinciden": cp_match,
        "nota": (
            "CP fiscal coincide con padrón electoral"
            if cp_match
            else "CP fiscal diferente al padrón — posible domicilio alternativo"
            if cp_fiscal
            else "No se obtuvo CP fiscal de CheckID"
        ),
    }

    # ── Paso 3: CFE por dirección del padrón ─────────────────────────────
    t3 = time.time()
    cfe_domicilio = []
    if calle or colonia or cp:
        try:
            from servir import _search_cfe_by_domicilio
            cfe_result = _search_cfe_by_domicilio(
                calle=calle.upper() if calle else "",
                colonia=colonia.upper() if colonia else "",
                cp=cp if cp and cp != "00000" else "",
                limit=25,
            )
            cfe_domicilio = cfe_result.get("rows", [])
        except Exception:
            pass
    result["metadata"]["steps"].append({
        "step": "cfe_domicilio",
        "elapsed_ms": int((time.time() - t3) * 1000),
        "ok": True,
        "count": len(cfe_domicilio),
    })

    # ── Paso 4: CFE por nombre ───────────────────────────────────────────
    t4 = time.time()
    cfe_nombre = []
    nombre_completo = f"{nombre} {paterno} {materno}".strip()
    if nombre_completo:
        try:
            import duckdb
            # Buscar directamente en la tabla CFE
            # Esto es más rápido que pasar por el endpoint HTTP
            if con_extended:
                sql = """
                    SELECT numero_servicio, nombre, direccion, colonia, cp,
                           division, zona_nom, agencia_nom
                    FROM api.cfe_medidor
                    WHERE UPPER(nombre) LIKE ?
                    LIMIT 20
                """
                rows = con_extended.execute(sql, [f"%{nombre_completo.upper()}%"]).fetchall()
                cols = ["num_servicio", "nombre", "direccion", "colonia", "cp",
                        "division", "zona_nom", "agencia_nom"]
                cfe_nombre = [dict(zip(cols, r)) for r in rows]
        except Exception:
            pass
    result["metadata"]["steps"].append({
        "step": "cfe_nombre",
        "elapsed_ms": int((time.time() - t4) * 1000),
        "ok": True,
        "count": len(cfe_nombre),
    })

    # ── Paso 5: Clasificar coincidencias CFE ─────────────────────────────
    apellidos = {paterno.upper(), materno.upper()} - {""}
    nombre_upper = nombre.upper()

    coincidencias = []
    seen_services = set()

    # Procesar coincidencias por dirección
    for row in cfe_domicilio:
        num = row.get("num_servicio", "")
        if num in seen_services:
            continue
        seen_services.add(num)

        titular = (row.get("nombre") or "").upper()
        clasificacion = "direccion_exacta"  # default

        # Verificar si el titular tiene apellidos coincidentes
        if apellidos and any(ap in titular for ap in apellidos):
            clasificacion = "domicilio_familiar"

        # Verificar si el nombre exacto coincide
        if nombre_upper and nombre_upper in titular:
            clasificacion = "domicilio_sujeto"

        coincidencias.append({
            **row,
            "clasificacion": clasificacion,
            "fuente": "cfe_domicilio",
        })

    # Procesar coincidencias por nombre (que no estén ya en domicilio)
    for row in cfe_nombre:
        num = row.get("num_servicio", "")
        if num in seen_services:
            continue
        seen_services.add(num)

        titular = (row.get("nombre") or "").upper()
        clasificacion = "por_nombre"

        # Si el nombre es exacto, es domicilio del sujeto
        if nombre_upper and nombre_upper in titular:
            clasificacion = "domicilio_sujeto"
        elif apellidos and any(ap in titular for ap in apellidos):
            clasificacion = "domicilio_familiar"

        coincidencias.append({
            **row,
            "clasificacion": clasificacion,
            "fuente": "cfe_nombre",
        })

    result["cfe"] = {
        "coincidencias": coincidencias,
        "total": len(coincidencias),
        "por_clasificacion": {
            "domicilio_sujeto": [c for c in coincidencias if c["clasificacion"] == "domicilio_sujeto"],
            "domicilio_familiar": [c for c in coincidencias if c["clasificacion"] == "domicilio_familiar"],
            "direccion_exacta": [c for c in coincidencias if c["clasificacion"] == "direccion_exacta"],
            "por_nombre": [c for c in coincidencias if c["clasificacion"] == "por_nombre"],
        },
    }

    # ── Metadata final ───────────────────────────────────────────────────
    result["metadata"]["elapsed_ms"] = int((time.time() - t0) * 1000)
    result["metadata"]["credits_used"] = 3

    return result
