"""
Endpoints de búsqueda manual para validación humana de sujetos KYC.

2026-08-25: Refactor del flujo de creación del sujeto. Hasta el inciso 3b el
backend ejecuta validaciones automáticas (padrón, SEPOMEX, CheckID, Tlaloc,
bases externas). A partir del 3c, el sistema **NO** consulta automáticamente:
el operador decide qué bases cruzar y valida manualmente cada candidato.

Este módulo expone endpoints individuales por base:

  /api/v1/cfe/buscar_avanzado   — bag of words sobre dirección
  /api/v1/<banco>/buscar        — bag of words sobre nombre+paterno+materno
  /api/v1/telcel/buscar_avanzado — búsqueda exacta por RFC+curp
  /api/v1/issste/buscar_avanzado — bag of words + cargo/sexo
  /api/v1/att/buscar_avanzado   — bag of words por paterno+materno+nombres

Cada endpoint devuelve una lista de CANDIDATOS con score bag-of-words para
que el operador decida cuáles son válidos.
"""
from __future__ import annotations

import os
import re
import time
import urllib.parse as _up
from pathlib import Path
from typing import Optional

import duckdb

from inteligencia_completa import (
    _normalizar_basico,
    _normalizar_nombre,
    _normalizar_calle,
    _normalizar_colonia,
    _normalizar_cp,
    _tokenizar,
    _bow_score,
    _regex_match_multi,
    _is_us_address,
)

# Bases dir: backend/ -> ../bases/
BASES_DIR = Path(__file__).resolve().parent.parent / "bases"

# Bases aliases
BANCOS_DIR = BASES_DIR  # *_v*.duckdb viven aquí


# ============================================================================
# Helpers de candidato
# ============================================================================

def _make_candidate(row_dict: dict, score: float, score_components: dict,
                     fuente: str, base_alias: str, **extras) -> dict:
    """Envuelve un row de DB en un dict de candidato con score y metadata.

    2026-08-31: agrega alias `score_total` (además de `_score`) para que
    integradores externos que esperan el campo documentado no se confundan.
    Mantener `_score` para retrocompatibilidad con frontend existente.
    """
    cand = {
        **row_dict,
        "_score": round(score, 4),
        "score_total": round(score, 4),  # alias documentado
        "_score_components": score_components,
        "_fuente": fuente,
        "_base": base_alias,
    }
    cand.update(extras)
    return cand


def _rank_candidates(cands: list, max_results: int = 50) -> list:
    """Ordena candidatos por score descendente y limita."""
    cands = sorted(cands, key=lambda c: c.get("_score", 0), reverse=True)
    return cands[:max_results]


# ============================================================================
# CFE — bag of words sobre dirección
# ============================================================================

def buscar_cfe_avanzado(calle: str = "", numero: str = "", colonia: str = "",
                          cp: str = "", limit: int = 200) -> dict:
    """Búsqueda avanzada en CFE usando bag of words multi-parámetro.

    2026-08-25: el operador decide qué candidatos son match válido. Esta
    función devuelve TODOS los registros que tengan al menos 1 token
    compartido o match por CP. No aplica un score mínimo estricto: cada
    candidato lleva su score para que el operador lo evalúe.

    Lógica:
      1. Normalizar cada parámetro (calle, número, colonia, CP)
      2. Tokenizar cada uno
      3. Score = bow_score por campo, ponderado (calle 0.5, colonia 0.3, cp 0.2)
      4. Un candidato se incluye si tiene ≥1 token compartido O match por CP

    Args:
        calle, numero, colonia, cp: filtros del usuario (pueden estar vacíos)
        limit: máximo de candidatos a devolver (default 200, más alto que los
               demás endpoints para CFE porque es la base más usada)

    Returns:
        dict con query, candidates (ordenados por score desc), count
    """
    t0 = time.time()
    # Normalizar todos los parámetros
    calle_norm = _normalizar_calle(calle)
    calle_tokens = _tokenizar(calle_norm)
    numero_norm = re.sub(r'[^\w]', '', numero or '').upper()
    colonia_norm = _normalizar_colonia(colonia)
    colonia_tokens = _tokenizar(colonia_norm)
    cp_norm = _normalizar_cp(cp)

    if not any([calle_tokens, numero_norm, colonia_tokens, cp_norm]):
        return {
            "error": "se requiere al menos uno: calle, numero, colonia o cp",
            "candidates": [],
            "count": 0,
            "elapsed_s": 0,
        }

    # Pesos por campo (calle es lo más importante)
    pesos = {"calle": 0.5, "colonia": 0.3, "cp": 0.2}

    # Construir WHERE clause dinámicamente según los campos presentes
    where_clauses = []
    where_params = []
    if cp_norm and len(cp_norm) == 5:
        # CP es el match más fuerte
        where_clauses.append("cp = ?")
        where_params.append(cp_norm)
    elif calle_tokens:
        # Si hay tokens de calle, hacer LIKE con los tokens principales
        where_clauses.append("(UPPER(direccion) LIKE ? OR UPPER(calle_adicional_1) LIKE ?)")
        # Tomar el primer token (la palabra principal de la calle)
        first_token = calle_tokens[0] if calle_tokens else ""
        where_params.extend([f"%{first_token}%", f"%{first_token}%"])

    if not where_clauses:
        return {
            "error": "no hay suficientes parámetros para búsqueda",
            "candidates": [],
            "count": 0,
            "elapsed_s": 0,
        }

    where_sql = " OR ".join(where_clauses) if len(where_clauses) > 1 else where_clauses[0]

    con = duckdb.connect(str(BASES_DIR / "cfe_v1.duckdb"), read_only=True)
    try:
        # Traer hasta 2000 candidatos potenciales (más permisivo para
        # que el operador tenga todas las opciones)
        rows = con.execute(f"""
            SELECT numero_servicio, nombre, direccion, colonia, cp,
                   division, zona_nombre, agencia_nombre,
                   calle_adicional_1, calle_adicional_2
            FROM main.medidores
            WHERE {where_sql}
            LIMIT 2000
        """, where_params).fetchall()
    finally:
        con.close()

    cols = ["numero_servicio", "titular", "direccion", "colonia", "cp",
            "division", "zona", "agencia", "calle_adicional_1", "calle_adicional_2"]

    candidates = []
    for r in rows:
        record = dict(zip(cols, r))

        # Filtrar USA
        if any(_is_us_address(record.get(k) or "") for k in
               ("direccion", "calle_adicional_1", "calle_adicional_2", "colonia")):
            continue

        # Normalizar campos del registro
        dir_tokens = _tokenizar(_normalizar_calle(
            f"{record.get('direccion', '')} {record.get('calle_adicional_1', '')} "
            f"{record.get('calle_adicional_2', '')}"))
        col_record_tokens = _tokenizar(_normalizar_colonia(record.get("colonia", "")))
        cp_record = _normalizar_cp(record.get("cp", ""))

        # Score por campo
        score_calle = _bow_score(calle_tokens, dir_tokens) if calle_tokens else 0
        score_colonia = _bow_score(colonia_tokens, col_record_tokens) if colonia_tokens else 0
        score_cp = 1.0 if cp_norm and cp_norm == cp_record else 0

        # Score compuesto (ponderado)
        if cp_norm and cp_norm == cp_record:
            # CP match es señal fuerte, dar bonus
            score_total = min(1.0, 0.6 + 0.3 * (score_calle + score_colonia) / 2)
        elif cp_norm:
            # CP no coincide pero el usuario pasó CP → descartar fuerte
            score_total = (score_calle * pesos["calle"] +
                           score_colonia * pesos["colonia"]) * 0.3
        else:
            score_total = (score_calle * pesos["calle"] +
                           score_colonia * pesos["colonia"])

        # Validar match multi-parámetro: 2026-08-25 el usuario pidió TODOS los
        # registros (sin filtrar por score mínimo). Solo descartamos los que
        # NO tienen ningún token compartido NI match por CP (esos son
        # claramente no relacionados). El resto va al output con su score
        # real para que el operador decida.
        all_query_tokens = calle_tokens + colonia_tokens
        all_record_tokens = list(set(dir_tokens + col_record_tokens))
        if not _regex_match_multi(all_query_tokens, all_record_tokens, min_hits=1):
            # Si NO hay tokens compartidos, descartar (salvo match por CP)
            if not (cp_norm and cp_norm == cp_record):
                continue

        # Verificar número si el usuario lo pasó
        if numero_norm:
            dir_completo = _normalizar_basico(
                f"{record.get('direccion', '')} {record.get('calle_adicional_1', '')}")
            if numero_norm not in dir_completo:
                score_total *= 0.5  # penalizar (no descarta)

        # 2026-08-25: ya NO descartamos por score mínimo. El operador decide.
        # Solo excluimos los que claramente no tienen NADA que ver (score=0
        # y CP no coincide).
        if score_total == 0 and not (cp_norm and cp_norm == cp_record):
            continue
        candidates.append(_make_candidate(
            record,
            score=score_total,
            score_components={
                "score_calle": round(score_calle, 4),
                "score_colonia": round(score_colonia, 4),
                "score_cp": round(score_cp, 4),
            },
            fuente="cfe",
            base_alias="cfe",
        ))

    candidates = _rank_candidates(candidates, max_results=limit)
    elapsed = time.time() - t0
    return {
        "query": {
            "calle_input": calle, "numero_input": numero,
            "colonia_input": colonia, "cp_input": cp,
            "calle_normalizada": calle_norm,
            "colonia_normalizada": colonia_norm,
            "cp_normalizado": cp_norm,
        },
        "candidates": candidates,
        "count": len(candidates),
        "elapsed_s": round(elapsed, 3),
        "nota": "TODOS los registros con >=1 token compartido o match por CP. "
                 "El operador decide cuáles son match válido.",
    }


# ============================================================================
# Bancos — bag of words sobre paterno/materno/nombre(s)
# ============================================================================

# Mapeo de entidad → lista de vistas en el extended DB.
# Cada banco puede tener 1 o varios sub-archivos (santander_1, ..., santander_7).
# En el extended DB, cada vista es api.<entidad>_<n>_cuentas_full.
# Para bancos con un solo archivo (bancoppel, amex, bancomer, etc.) la vista
# sigue siendo api.<entidad>_cuentas_full (sin sufijo numérico).
BANCO_VISTAS = {
    # Santander: 7 vistas (1,2,3,5,6,7 — falta 4)
    "santander": [
        "api.santander_1_cuentas_full",
        "api.santander_2_cuentas_full",
        "api.santander_3_cuentas_full",
        "api.santander_5_cuentas_full",
        "api.santander_6_cuentas_full",
        "api.santander_7_cuentas_full",
    ],
    "hsbc": [
        # 2026-08-25: hsbc_v1.duckdb NO crea vista api (solo b_hsbc).
        # Solo existen hsbc_1_cuentas_full y hsbc_2_cuentas_full.
        "api.hsbc_1_cuentas_full",
        "api.hsbc_2_cuentas_full",
    ],
    # Bancos con un solo archivo (vista sin sufijo numérico)
    "banorte":      ["api.banorte_cuentas_full"],
    "bancomer":     ["api.bancomer_cuentas_full"],
    "citibanamex":  ["api.citibanamex_cuentas_full"],
    "bancoppel":    ["api.bancoppel_cuentas_full"],
    "amex":         ["api.amex_cuentas_full"],
    # 2026-08-25: clavijero usa _personas_full, NO _cuentas_full.
    "clavijero":    ["api.clavijero_personas_full"],
}

# Alias legacy para compatibilidad hacia con
BANCO_TABLAS = {k: (v[0], "", []) for k, v in BANCO_VISTAS.items()}

# Lista exportada para iterar en servir.py
BUSQUEDA_MANUAL_ENTIDADES = list(BANCO_TABLAS.keys())


def buscar_banco(entidad: str, paterno: str = "", materno: str = "",
                  nombre: str = "", nombre_completo: str = "",
                  rfc: str = "", curp: str = "",
                  cp: str = "", telefono: str = "",
                  limit: int = 100) -> dict:
    """Búsqueda en una base bancaria usando bag of words multi-parámetro.

    2026-08-25: el operador decide qué candidatos son match válido. Esta
    función devuelve TODOS los registros que tengan tokens compartidos en
    al menos 1 parámetro (paterno/materno/nombre), sin score mínimo
    estricto.

    Modos de búsqueda (se evalúan en orden, AND entre ellos):

      1. RFC exacto (si rfc tiene 12-13 chars)
      2. CURP exacta (si curp tiene 18 chars)
      3. Nombre completo (LIKE sobre titular_nombre_completo + tokens)
      4. paterno / materno / nombre(s) por separado (bag-of-words)
      5. CP exacto
      6. Teléfono (10 dígitos, normalizado)

    Args:
      entidad: santander, hsbc, banorte, bancomer, citibanamex, bancoppel,
               amex, clavijero
      paterno, materno, nombre: al menos uno debe estar presente (o
                                 nombre_completo, rfc, curp)
      nombre_completo: nombre completo a buscar (LIKE tokens)
      rfc, curp: búsqueda exacta
      cp: código postal exacto
      telefono: número de teléfono (10 dígitos)
      limit: máximo de candidatos

    Returns:
        dict con query, candidates (ordenados por score), count
    """
    t0 = time.time()
    if entidad not in BANCO_TABLAS:
        return {
            "error": f"entidad desconocida: {entidad}",
            "entidades_validas": sorted(BANCO_TABLAS.keys()),
            "candidates": [],
            "count": 0,
        }

    # Normalizar inputs
    nombre_norm = _normalizar_nombre(nombre)
    paterno_norm = _normalizar_nombre(paterno)
    materno_norm = _normalizar_nombre(materno)
    nc_norm = _normalizar_nombre(nombre_completo)
    rfc_clean = re.sub(r'[^A-Z0-9]', '', (rfc or '').upper())
    curp_clean = (curp or '').upper().strip()
    cp_norm = _normalizar_cp(cp)
    tel_norm = re.sub(r'\D', '', telefono or '')

    nombre_tokens = _tokenizar(nombre_norm)
    paterno_tokens = _tokenizar(paterno_norm)
    materno_tokens = _tokenizar(materno_norm)
    nc_tokens = _tokenizar(nc_norm)

    # Modo especial: si solo se pasan RFC o CURP exactos, hacemos búsqueda
    # directa y devolvemos score=1.0
    exact_mode = False
    if rfc_clean and len(rfc_clean) >= 12 and not (paterno_tokens or materno_tokens or nombre_tokens or nc_tokens):
        exact_mode = True
    if curp_clean and len(curp_clean) == 18 and not (paterno_tokens or materno_tokens or nombre_tokens or nc_tokens or rfc_clean):
        exact_mode = True

    # Si no hay nada que buscar, error
    if not any([paterno_tokens, materno_tokens, nombre_tokens, nc_tokens,
                rfc_clean, curp_clean, cp_norm, tel_norm]):
        return {
            "error": "se requiere al menos uno: nombre_completo, paterno, materno, "
                     "nombre, rfc, curp, cp o telefono",
            "candidates": [],
            "count": 0,
            "elapsed_s": 0,
        }

    # WHERE clause: combinamos los criterios con AND
    where_clauses = []
    where_params = []

    # RFC exacto (preferencia)
    if rfc_clean and len(rfc_clean) >= 12:
        where_clauses.append("REGEXP_REPLACE(UPPER(TRIM(rfc)), '[^A-Z0-9]', '', 'g') = ?")
        where_params.append(rfc_clean)

    # CURP exacta
    if curp_clean and len(curp_clean) == 18:
        where_clauses.append("UPPER(TRIM(curp)) = ?")
        where_params.append(curp_clean)

    # Nombre completo: split en tokens. Cada token debe aparecer en el nombre
    # del titular (AND entre tokens). Buscamos en titular_nombre1 || ' ' ||
    # titular_nombre2 para cubrir cualquier orden de los tokens.
    if nc_tokens:
        nc_clauses = []
        nc_params = []
        # 2026-08-25: NO envolver concat en () porque se inserta dentro de
        # LIKE ?, y los () romperían el balance. DuckDB necesita el patrón
        # "<concat> LIKE ?" (sin paréntesis alrededor del concat).
        concat = "UPPER(COALESCE(titular_nombre1,'') || ' ' || COALESCE(titular_nombre2,''))"
        for tok in nc_tokens:
            nc_clauses.append(f"({concat} LIKE ?)")
            nc_params.append(f"%{tok}%")
        # 2026-08-25: AND entre tokens para que "JUAN PEREZ" no devuelva filas
        # con solo "JUAN" o solo "PEREZ" en cualquier campo.
        where_clauses.append(" AND ".join(nc_clauses))
        where_params.extend(nc_params)

    # paterno / materno / nombre(s) por separado (OR entre ellos)
    indiv_clauses = []
    indiv_params = []
    if paterno_tokens:
        indiv_clauses.append("UPPER(titular_nombre2) LIKE ?")
        indiv_params.append(f"%{paterno_tokens[0]}%")
        if len(paterno_tokens) > 1:
            indiv_clauses.append("UPPER(titular_nombre2) LIKE ?")
            indiv_params.append(f"%{paterno_tokens[1]}%")
    if materno_tokens:
        indiv_clauses.append("UPPER(titular_nombre2) LIKE ?")
        indiv_params.append(f"%{materno_tokens[0]}%")
    if nombre_tokens:
        indiv_clauses.append("UPPER(titular_nombre1) LIKE ?")
        indiv_params.append(f"%{nombre_tokens[0]}%")
    if indiv_clauses:
        where_clauses.append("(" + " OR ".join(indiv_clauses) + ")")
        where_params.extend(indiv_params)

    # CP exacto (5 dígitos)
    if cp_norm and len(cp_norm) == 5:
        where_clauses.append("REGEXP_REPLACE(TRIM(titular_cp), '[^0-9]', '', 'g') = ?")
        where_params.append(cp_norm)

    # Teléfono normalizado (últ10 dígitos)
    if tel_norm and len(tel_norm) >= 7:
        tel10 = tel_norm[-10:] if len(tel_norm) >= 10 else tel_norm
        where_clauses.append("(REGEXP_REPLACE(TRIM(telefono), '[^0-9]', '', 'g') = ? OR REGEXP_REPLACE(TRIM(telefono), '[^0-9]', '', 'g') LIKE ?)")
        where_params.extend([tel10, f"%{tel10}%"])

    if not where_clauses:
        return {
            "error": "no hay criterios válidos para buscar",
            "candidates": [],
            "count": 0,
            "elapsed_s": 0,
        }

    where_sql = " AND ".join(where_clauses)

    tablas = BANCO_VISTAS[entidad]
    base_alias = entidad  # alias para _fuente

    # 2026-09-06: DuckDB NO es thread-safe en conexiones :memory: compartidas.
    # Usar el context manager `_extended_con_ctx()` para serializar el acceso
    # a la conexión global. Eso elimina las race conditions en DESCRIBE.
    used_extended = False
    con_local = None
    candidates = []
    try:
        try:
            from servir import _extended_con_ctx
            con_ctx = _extended_con_ctx()
        except Exception:
            con_ctx = None

        if con_ctx is not None:
            with con_ctx as con:
                used_extended = True
                # Detectar columnas de la primera vista
                cols_db = []
                for tabla in tablas:
                    try:
                        cols_db = [c[0].lower() for c in con.execute(
                            f"DESCRIBE {tabla}").fetchall()]
                        if cols_db:
                            break
                    except Exception:
                        continue
                if not cols_db:
                    return {
                        "error": f"ninguna vista de {entidad} es accesible",
                        "candidates": [],
                        "count": 0,
                        "elapsed_s": round(time.time() - t0, 3),
                    }
                # Construir SELECT con campos disponibles
                select_fields = []
                for col in ["rfc", "curp", "cuenta", "telefono",
                            "titular_nombre1", "titular_nombre2",
                            "titular_domicilio", "titular_numero",
                            "titular_interior", "titular_colonia",
                            "titular_ciudad", "titular_estado", "titular_cp",
                            "archivo_origen"]:
                    if col in cols_db:
                        select_fields.append(col)
                if not select_fields:
                    select_fields = [c for c in cols_db][:10]
                if "rfc" not in select_fields:
                    select_fields.insert(0, "rfc")
                union_parts = []
                for tabla in tablas:
                    union_parts.append(f"""
                        SELECT {', '.join(select_fields)},
                               '{tabla}' AS _vista_origen
                        FROM {tabla}
                        WHERE {where_sql}
                    """)
                n_tablas = len(tablas)
                replicated_params = where_params * n_tablas
                union_sql = " UNION ALL ".join(union_parts)
                sql = f"""
                    SELECT * FROM ({union_sql})
                    LIMIT 2000
                """
                rows = con.execute(sql, replicated_params).fetchall()
                select_fields_with_vista = select_fields + ["_vista_origen"]
        else:
            archivo = next((a for a in [] if (BANCOS_DIR / a).exists()),
                           None)
            if archivo is None:
                return {
                    "error": f"no se pudo conectar a {entidad}",
                    "candidates": [],
                    "count": 0,
                    "elapsed_s": 0,
                }
            con = duckdb.connect(str(BANCOS_DIR / archivo), read_only=True)
            con_local = con
            tablas = ["personas"]
            try:
                cols_db = []
                for tabla in tablas:
                    try:
                        cols_db = [c[0].lower() for c in con.execute(
                            f"DESCRIBE {tabla}").fetchall()]
                        if cols_db:
                            break
                    except Exception:
                        continue
                if not cols_db:
                    return {
                        "error": f"ninguna vista de {entidad} es accesible",
                        "candidates": [],
                        "count": 0,
                        "elapsed_s": round(time.time() - t0, 3),
                    }
                select_fields = []
                for col in ["rfc", "curp", "cuenta", "telefono",
                            "titular_nombre1", "titular_nombre2",
                            "titular_domicilio", "titular_numero",
                            "titular_interior", "titular_colonia",
                            "titular_ciudad", "titular_estado", "titular_cp",
                            "archivo_origen"]:
                    if col in cols_db:
                        select_fields.append(col)
                if not select_fields:
                    select_fields = [c for c in cols_db][:10]
                if "rfc" not in select_fields:
                    select_fields.insert(0, "rfc")
                union_parts = []
                for tabla in tablas:
                    union_parts.append(f"""
                        SELECT {', '.join(select_fields)},
                               '{tabla}' AS _vista_origen
                        FROM {tabla}
                        WHERE {where_sql}
                    """)
                n_tablas = len(tablas)
                replicated_params = where_params * n_tablas
                union_sql = " UNION ALL ".join(union_parts)
                sql = f"""
                    SELECT * FROM ({union_sql})
                    LIMIT 2000
                """
                rows = con.execute(sql, replicated_params).fetchall()
                select_fields_with_vista = select_fields + ["_vista_origen"]
            finally:
                if con_local is not None:
                    con_local.close()
    except Exception as e:
        return {
            "error": f"error accediendo a {entidad}: {str(e)[:200]}",
            "candidates": [],
            "count": 0,
            "elapsed_s": round(time.time() - t0, 3),
        }

    for r in rows:
        record = dict(zip(select_fields_with_vista, r))
        # Filtrar USA
        if _is_us_address(record.get("titular_domicilio") or ""):
            continue

        # Normalizar nombre completo del titular para bow_score
        # En la vista api.<entidad>_1_cuentas_full:
        #   titular_nombre1 = nombre(s) o razón social
        #   titular_nombre2 = apellidos concatenados (PATERNO MATERNO)
        titular_completo = _normalizar_nombre(
            (record.get("titular_nombre1") or "") + " " +
            (record.get("titular_nombre2") or ""))
        titular_tokens = _tokenizar(titular_completo)

        # Score por parámetro
        score_paterno = _bow_score(paterno_tokens, titular_tokens) if paterno_tokens else 0
        score_materno = _bow_score(materno_tokens, titular_tokens) if materno_tokens else 0
        score_nombre = _bow_score(nombre_tokens, titular_tokens) if nombre_tokens else 0

        # 2026-08-25: si la búsqueda es exacta (RFC/CURP/CP/teléfono), el WHERE
        # SQL ya garantizó que el match es válido → aceptamos con score=1.0 sin
        # exigir bag-of-words. Bug histórico: el `if not scores_reales: continue`
        # descartaba TODOS los candidatos encontrados por RFC exacto, dejando
        # count=0 aunque el SQL devolviera rows.
        if exact_mode:
            candidates.append(_make_candidate(
                record,
                score=1.0,
                score_components={
                    "score_paterno": round(score_paterno, 4),
                    "score_materno": round(score_materno, 4),
                    "score_nombre": round(score_nombre, 4),
                    "params_con_match": 1,
                    "exact_match": True,
                },
                fuente=entidad,
                base_alias=base_alias,
                titular_completo=titular_completo,
            ))
            continue

        # 2026-08-25: si hay tokens de nombre_completo, los usamos para el score
        # aunque no haya paterno/materno/nombre individuales. Bug histórico:
        # "nombre_completo=JUAN PEREZ" → scores_reales=[] → continue → count=0.
        score_nc = _bow_score(nc_tokens, titular_tokens) if nc_tokens else 0

        # Score compuesto: promedio de los scores de los parámetros proporcionados
        scores_reales = []
        if paterno_tokens:
            scores_reales.append(score_paterno)
        if materno_tokens:
            scores_reales.append(score_materno)
        if nombre_tokens:
            scores_reales.append(score_nombre)
        if nc_tokens:
            scores_reales.append(score_nc)
        if not scores_reales:
            continue
        score_total = sum(scores_reales) / len(scores_reales)

        # Validar match multi-parámetro: al menos 1 parámetro con ≥1 token
        # compartido. El operador decide el resto.
        params_con_match = sum(1 for s in scores_reales if s > 0)
        if params_con_match < 1:
            continue

        # 2026-08-25: ya NO descartamos por score mínimo. El operador decide.

        candidates.append(_make_candidate(
            record,
            score=score_total,
            score_components={
                "score_paterno": round(score_paterno, 4),
                "score_materno": round(score_materno, 4),
                "score_nombre": round(score_nombre, 4),
                "score_nombre_completo": round(score_nc, 4),
                "params_con_match": params_con_match,
            },
            fuente=entidad,
            base_alias=base_alias,
            titular_completo=titular_completo,
        ))

    candidates = _rank_candidates(candidates, max_results=limit)
    elapsed = time.time() - t0
    return {
        "query": {
            "entidad": entidad,
            "paterno_input": paterno,
            "materno_input": materno,
            "nombre_input": nombre,
            "nombre_completo_input": nombre_completo,
            "rfc_input": rfc,
            "curp_input": curp,
            "cp_input": cp,
            "telefono_input": telefono,
            "paterno_norm": paterno_norm,
            "materno_norm": materno_norm,
            "nombre_norm": nombre_norm,
            "rfc_clean": rfc_clean,
            "cp_norm": cp_norm,
            "tel_norm": tel_norm,
            "exact_mode": exact_mode,
        },
        "candidates": candidates,
        "count": len(candidates),
        "elapsed_s": round(elapsed, 3),
        "nota": "Búsqueda multi-modo: RFC/CURP/CP/teléfono exactos + bag-of-words sobre paterno/materno/nombre(s) y nombre_completo",
    }


# ============================================================================
# ISSSTE — bag of words + cargo/sexo
# ============================================================================

def buscar_issste_avanzado(paterno: str = "", materno: str = "", nombre: str = "",
                              cargo: str = "", sexo: str = "", limit: int = 50) -> dict:
    """Búsqueda en ISSSTE con bag of words + filtros adicionales (cargo/sexo)."""
    t0 = time.time()
    paterno_norm = _normalizar_nombre(paterno)
    materno_norm = _normalizar_nombre(materno)
    nombre_norm = _normalizar_nombre(nombre)

    paterno_tokens = _tokenizar(paterno_norm)
    materno_tokens = _tokenizar(materno_norm)
    nombre_tokens = _tokenizar(nombre_norm)
    cargo_norm = _normalizar_basico(cargo)
    cargo_tokens = _tokenizar(cargo_norm)

    if not any([paterno_tokens, materno_tokens, nombre_tokens]):
        return {
            "error": "se requiere al menos uno: paterno, materno o nombre",
            "candidates": [],
            "count": 0,
            "elapsed_s": 0,
        }

    # WHERE con LIKE por paterno (mayoría de los casos)
    where_clauses = []
    where_params = []
    if paterno_tokens:
        where_clauses.append("UPPER(paterno) LIKE ?")
        where_params.append(f"%{paterno_tokens[0]}%")
    elif materno_tokens:
        where_clauses.append("UPPER(materno) LIKE ?")
        where_params.append(f"%{materno_tokens[0]}%")
    elif nombre_tokens:
        where_clauses.append("UPPER(nombres) LIKE ?")
        where_params.append(f"%{nombre_tokens[0]}%")

    where_sql = " AND ".join(where_clauses)

    candidates = []
    try:
        con = duckdb.connect(str(BASES_DIR / "issste_v1.duckdb"), read_only=True)
        try:
            rows = con.execute(f"""
                SELECT id, paterno, materno, nombres, cargo, sexo,
                       sueldo, ramo_id, entidad_id, sector_id
                FROM main.empleados
                WHERE {where_sql}
                LIMIT 2000
            """, where_params).fetchall()
        finally:
            con.close()
    except Exception as e:
        return {
            "error": f"error accediendo a issste: {str(e)[:200]}",
            "candidates": [],
            "count": 0,
            "elapsed_s": round(time.time() - t0, 3),
        }

    cols = ["paterno", "materno", "nombres", "cargo", "sexo",
            "sueldo", "ramo_id", "entidad_id", "sector_id"]
    # Renombrar id → id_issste en cada record
    for r in rows:
        record = dict(zip(["id_issste"] + cols, r))
        # Filtrar por sexo si el usuario lo pasó
        if sexo and record.get("sexo", "").upper() != sexo.upper():
            continue

        # Tokenizar nombre completo del ISSSTE
        issste_completo = _normalizar_nombre(
            f"{record.get('nombres', '')} {record.get('paterno', '')} "
            f"{record.get('materno', '')}")
        issste_tokens = _tokenizar(issste_completo)

        score_paterno = _bow_score(paterno_tokens, issste_tokens) if paterno_tokens else 0
        score_materno = _bow_score(materno_tokens, issste_tokens) if materno_tokens else 0
        score_nombre = _bow_score(nombre_tokens, issste_tokens) if nombre_tokens else 0

        scores_reales = []
        if paterno_tokens:
            scores_reales.append(score_paterno)
        if materno_tokens:
            scores_reales.append(score_materno)
        if nombre_tokens:
            scores_reales.append(score_nombre)
        if not scores_reales:
            continue
        score_total = sum(scores_reales) / len(scores_reales)

        # Bonus por cargo si coincide
        if cargo_tokens:
            cargo_record = _tokenizar(_normalizar_basico(record.get("cargo", "")))
            score_cargo = _bow_score(cargo_tokens, cargo_record)
            if score_cargo > 0.5:
                score_total = min(1.0, score_total + 0.1)

        # 2026-08-25: ya NO descartamos por score mínimo.

        candidates.append(_make_candidate(
            record,
            score=score_total,
            score_components={
                "score_paterno": round(score_paterno, 4),
                "score_materno": round(score_materno, 4),
                "score_nombre": round(score_nombre, 4),
            },
            fuente="issste",
            base_alias="issste",
        ))

    candidates = _rank_candidates(candidates, max_results=limit)
    elapsed = time.time() - t0
    return {
        "query": {
            "paterno_input": paterno,
            "materno_input": materno,
            "nombre_input": nombre,
            "cargo_input": cargo,
            "sexo_input": sexo,
        },
        "candidates": candidates,
        "count": len(candidates),
        "elapsed_s": round(elapsed, 3),
        "nota": "bag-of-words multi-parametro sobre nombres + cargo + sexo",
    }


# ============================================================================
# ATT — bag of words por paterno/materno/nombres
# ============================================================================

def buscar_att_avanzado(paterno: str = "", materno: str = "", nombres: str = "",
                          limit: int = 50) -> dict:
    """Búsqueda en ATT por paterno/materno/nombres con bag of words."""
    t0 = time.time()
    paterno_norm = _normalizar_nombre(paterno)
    materno_norm = _normalizar_nombre(materno)
    nombres_norm = _normalizar_nombre(nombres)

    paterno_tokens = _tokenizar(paterno_norm)
    materno_tokens = _tokenizar(materno_norm)
    nombres_tokens = _tokenizar(nombres_norm)

    if not any([paterno_tokens, materno_tokens, nombres_tokens]):
        return {
            "error": "se requiere al menos uno: paterno, materno o nombres",
            "candidates": [],
            "count": 0,
            "elapsed_s": 0,
        }

    # WHERE por paterno
    where_clauses = []
    where_params = []
    if paterno_tokens:
        where_clauses.append("UPPER(pat) LIKE ?")
        where_params.append(f"%{paterno_tokens[0]}%")
    elif materno_tokens:
        where_clauses.append("UPPER(may) LIKE ?")
        where_params.append(f"%{materno_tokens[0]}%")
    elif nombres_tokens:
        where_clauses.append("UPPER(nombres) LIKE ?")
        where_params.append(f"%{nombres_tokens[0]}%")

    where_sql = " AND ".join(where_clauses)

    candidates = []
    try:
        con = duckdb.connect(str(BASES_DIR / "att_v1.duckdb"), read_only=True)
        try:
            rows = con.execute(f"""
                SELECT rfc, pat, may, nombres, tel1, celular,
                       direccion, interior, exterior,
                       colonia, municipio, estado_origen,
                       archivo_origen
                FROM main.att
                WHERE {where_sql}
                LIMIT 2000
            """, where_params).fetchall()
        finally:
            con.close()
    except Exception as e:
        return {
            "error": f"error accediendo a att: {str(e)[:200]}",
            "candidates": [],
            "count": 0,
            "elapsed_s": round(time.time() - t0, 3),
        }

    cols = ["rfc", "paterno", "materno", "nombres", "telefono_fijo", "celular",
            "direccion", "num_interior", "num_exterior",
            "colonia", "municipio", "estado", "archivo_origen"]

    for r in rows:
        record = dict(zip(cols, r))
        # Filtrar USA
        if _is_us_address(record.get("direccion") or ""):
            continue

        att_completo = _normalizar_nombre(
            f"{record.get('nombres', '')} {record.get('paterno', '')} "
            f"{record.get('materno', '')}")
        att_tokens = _tokenizar(att_completo)

        score_paterno = _bow_score(paterno_tokens, att_tokens) if paterno_tokens else 0
        score_materno = _bow_score(materno_tokens, att_tokens) if materno_tokens else 0
        score_nombres = _bow_score(nombres_tokens, att_tokens) if nombres_tokens else 0

        scores_reales = []
        if paterno_tokens:
            scores_reales.append(score_paterno)
        if materno_tokens:
            scores_reales.append(score_materno)
        if nombres_tokens:
            scores_reales.append(score_nombres)
        if not scores_reales:
            continue
        score_total = sum(scores_reales) / len(scores_reales)
        # 2026-08-25: ya NO descartamos por score mínimo.

        candidates.append(_make_candidate(
            record,
            score=score_total,
            score_components={
                "score_paterno": round(score_paterno, 4),
                "score_materno": round(score_materno, 4),
                "score_nombres": round(score_nombres, 4),
            },
            fuente="att",
            base_alias="att",
        ))

    candidates = _rank_candidates(candidates, max_results=limit)
    elapsed = time.time() - t0
    return {
        "query": {
            "paterno_input": paterno,
            "materno_input": materno,
            "nombres_input": nombres,
        },
        "candidates": candidates,
        "count": len(candidates),
        "elapsed_s": round(elapsed, 3),
        "nota": "bag-of-words multi-parametro sobre nombres",
    }


# ============================================================================
# Telcel — bag of words por nombre + RFC opcional
# ============================================================================

def buscar_telcel_avanzado(rfc: str = "", paterno: str = "", materno: str = "",
                              nombre: str = "", limit: int = 50) -> dict:
    """Búsqueda en Telcel por RFC (exacto) o por nombre (bag of words)."""
    t0 = time.time()
    rfc_norm = (rfc or "").strip().upper()

    if rfc_norm:
        # Búsqueda exacta por RFC
        con = duckdb.connect(str(BASES_DIR / "telcel_v1.duckdb"), read_only=True)
        try:
            rows = con.execute("""
                SELECT rfc_clean, telefono, TRIM(nombre1), TRIM(nombre2),
                       plan_actual, marca, modelo,
                       domicilio, colonia, ciudad, edo, cp,
                       archivo_origen
                FROM main.telcel
                WHERE REGEXP_REPLACE(TRIM(rfc_clean), '[^A-Z0-9]', '', 'g') = ?
                   OR REGEXP_REPLACE(TRIM(rfc_clean), '[^A-Z0-9]', '', 'g') LIKE ?
                LIMIT ?
            """, [rfc_norm, f"%{rfc_norm}%", limit]).fetchall()
        finally:
            con.close()

        cols = ["rfc", "telefono", "nombre1", "nombre2", "plan", "marca",
                "modelo", "domicilio", "colonia", "ciudad", "estado", "cp",
                "archivo_origen"]
        candidates = []
        for r in rows:
            record = dict(zip(cols, r))
            if _is_us_address(record.get("domicilio") or ""):
                continue
            candidates.append(_make_candidate(
                record, score=1.0, score_components={"exact_match_rfc": 1.0},
                fuente="telcel", base_alias="b_telcel"))
        elapsed = time.time() - t0
        return {
            "query": {"rfc_input": rfc, "modo": "exacto"},
            "candidates": candidates,
            "count": len(candidates),
            "elapsed_s": round(elapsed, 3),
        }

    # Modo bag of words por nombre
    paterno_norm = _normalizar_nombre(paterno)
    materno_norm = _normalizar_nombre(materno)
    nombre_norm = _normalizar_nombre(nombre)

    paterno_tokens = _tokenizar(paterno_norm)
    materno_tokens = _tokenizar(materno_norm)
    nombre_tokens = _tokenizar(nombre_norm)

    if not any([paterno_tokens, materno_tokens, nombre_tokens]):
        return {
            "error": "se requiere rfc o al menos uno: paterno, materno o nombre",
            "candidates": [],
            "count": 0,
            "elapsed_s": 0,
        }

    # Telcel guarda: nombre1 (nombre/razón social), nombre2 (apellidos concatenados)
    # Como la estructura es distinta, usamos bag of words simple
    where_clauses = []
    where_params = []
    if paterno_tokens:
        where_clauses.append("UPPER(nombre2) LIKE ?")
        where_params.append(f"%{paterno_tokens[0]}%")
    elif nombre_tokens:
        where_clauses.append("(UPPER(nombre1) LIKE ? OR UPPER(nombre2) LIKE ?)")
        where_params.extend([f"%{nombre_tokens[0]}%", f"%{nombre_tokens[0]}%"])

    where_sql = " AND ".join(where_clauses)

    candidates = []
    try:
        con = duckdb.connect(str(BASES_DIR / "telcel_v1.duckdb"), read_only=True)
        try:
            rows = con.execute(f"""
                SELECT rfc_clean, telefono, TRIM(nombre1), TRIM(nombre2),
                       plan_actual, marca, modelo,
                       domicilio, colonia, ciudad, edo, cp,
                       archivo_origen
                FROM main.telcel
                WHERE {where_sql}
                LIMIT 2000
            """, where_params).fetchall()
        finally:
            con.close()
    except Exception as e:
        return {
            "error": f"error: {str(e)[:200]}",
            "candidates": [],
            "count": 0,
            "elapsed_s": round(time.time() - t0, 3),
        }

    cols = ["rfc", "telefono", "nombre1", "nombre2", "plan", "marca",
            "modelo", "domicilio", "colonia", "ciudad", "estado", "cp",
            "archivo_origen"]

    for r in rows:
        record = dict(zip(cols, r))
        if _is_us_address(record.get("domicilio") or ""):
            continue

        # Tokenizar nombre completo
        nombre_completo = _normalizar_nombre(
            f"{record.get('nombre2', '')} {record.get('nombre1', '')}")
        nombre_tokens_db = _tokenizar(nombre_completo)

        score_paterno = _bow_score(paterno_tokens, nombre_tokens_db) if paterno_tokens else 0
        score_materno = _bow_score(materno_tokens, nombre_tokens_db) if materno_tokens else 0
        score_nombre = _bow_score(nombre_tokens, nombre_tokens_db) if nombre_tokens else 0

        scores_reales = []
        if paterno_tokens:
            scores_reales.append(score_paterno)
        if materno_tokens:
            scores_reales.append(score_materno)
        if nombre_tokens:
            scores_reales.append(score_nombre)
        if not scores_reales:
            continue
        score_total = sum(scores_reales) / len(scores_reales)
        if score_total < 0.4:
            continue

        candidates.append(_make_candidate(
            record,
            score=score_total,
            score_components={
                "score_paterno": round(score_paterno, 4),
                "score_materno": round(score_materno, 4),
                "score_nombre": round(score_nombre, 4),
            },
            fuente="telcel",
            base_alias="b_telcel",
        ))

    candidates = _rank_candidates(candidates, max_results=limit)
    elapsed = time.time() - t0
    return {
        "query": {
            "paterno_input": paterno,
            "materno_input": materno,
            "nombre_input": nombre,
        },
        "candidates": candidates,
        "count": len(candidates),
        "elapsed_s": round(elapsed, 3),
        "nota": "bag-of-words multi-parametro",
    }


# ============================================================================
# Handlers HTTP para servir.py
# ============================================================================

def _parse_query(path: str) -> dict:
    """Parsea query string de una URL."""
    qs = _up.urlparse(path).query
    parsed = _up.parse_qs(qs)
    # Devolver primer valor de cada key
    return {k: (v[0] if v else "") for k, v in parsed.items()}


def handle_cfe_buscar_avanzado(handler):
    """Handler para GET /api/v1/cfe/buscar_avanzado."""
    q = _parse_query(handler.path)
    result = buscar_cfe_avanzado(
        calle=q.get("calle", ""),
        numero=q.get("numero", ""),
        colonia=q.get("colonia", ""),
        cp=q.get("cp", ""),
        limit=int(q.get("limit", "200") or "200"),
    )
    handler._json(200, result)


def handle_banco_buscar(handler, entidad: str):
    """Handler para GET /api/v1/<banco>/buscar.

    Query params:
      paterno, materno, nombre   — bag-of-words individual
      nombre_completo             — LIKE tokens sobre el nombre completo
      rfc                         — exacto (12-13 chars, normalizado)
      curp                        — exacto (18 chars)
      cp                          — exacto (5 dígitos)
      telefono                    — exacto (10 dígitos normalizados)
      limit                       — máximo de candidatos (default 100)
    """
    q = _parse_query(handler.path)
    result = buscar_banco(
        entidad=entidad,
        paterno=q.get("paterno", ""),
        materno=q.get("materno", ""),
        nombre=q.get("nombre", ""),
        nombre_completo=q.get("nombre_completo", ""),
        rfc=q.get("rfc", ""),
        curp=q.get("curp", ""),
        cp=q.get("cp", ""),
        telefono=q.get("telefono", ""),
        limit=int(q.get("limit", "100") or "100"),
    )
    status = 200 if "error" not in result or "candidates" in result else 400
    handler._json(status, result)


def handle_issste_buscar_avanzado(handler):
    """Handler para GET /api/v1/issste/buscar_avanzado."""
    q = _parse_query(handler.path)
    result = buscar_issste_avanzado(
        paterno=q.get("paterno", ""),
        materno=q.get("materno", ""),
        nombre=q.get("nombre", ""),
        cargo=q.get("cargo", ""),
        sexo=q.get("sexo", ""),
        limit=int(q.get("limit", "100") or "100"),
    )
    handler._json(200, result)


def handle_att_buscar_avanzado(handler):
    """Handler para GET /api/v1/att/buscar_avanzado."""
    q = _parse_query(handler.path)
    result = buscar_att_avanzado(
        paterno=q.get("paterno", ""),
        materno=q.get("materno", ""),
        nombres=q.get("nombres", ""),
        limit=int(q.get("limit", "100") or "100"),
    )
    handler._json(200, result)


def handle_telcel_buscar_avanzado(handler):
    """Handler para GET /api/v1/telcel/buscar_avanzado."""
    q = _parse_query(handler.path)
    result = buscar_telcel_avanzado(
        rfc=q.get("rfc", ""),
        paterno=q.get("paterno", ""),
        materno=q.get("materno", ""),
        nombre=q.get("nombre", ""),
        limit=int(q.get("limit", "100") or "100"),
    )
    handler._json(200, result)