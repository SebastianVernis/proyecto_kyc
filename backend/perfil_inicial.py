"""
perfil_inicial.py — Flujo estándar de búsqueda inicial de un sujeto.

2026-09-06: Estandarización del endpoint /api/v1/sujeto/perfil_inicial.

Este módulo define la **única** secuencia de búsqueda que debe ejecutarse
desde cada punto del frontend que reciba un CURP o RFC como entrada:

  1. Padrón Electoral (db.by_curp / by_rfc)
       → nombres + dirección estructurada del sujeto
  2. CheckID (SAT) directo (sin re-validar CURP)
       → RFC, NSS, CP fiscal, email fiscal, régimen fiscal, 69/69B
  3. Validación CP padrón vs CP CheckID (SEPOMEX)
       → si NO coinciden, hay un domicilio fiscal adicional a renderizar
       con un mapa Leaflet que abarque la colonia del CP del SAT
  4. Búsqueda CFE por la dirección del padrón (calle + número + colonia + CP)
       → sólo se aceptan candidatos con uno de estos factores:
         a) dirección EXACTA del padrón (calle + ext + cp coinciden)
         b) titular con APELLIDOS coincidentes al sujeto (familiar)
         c) titular con NOMBRE EXACTO al sujeto (bow_score >= 0.85)
       → todas las direcciones válidas (familiares o exactas) deben
         renderizar un mapa Leaflet en el frontend

Este módulo NO consulta bases bancarias ni externas. Sólo el trío
Padrón + CheckID + CFE, en ese orden, con la clasificación CFE descrita.

El endpoint que invoca este módulo está en servir.py:
  GET /api/v1/sujeto/perfil_inicial?curp=...&rfc=...

Devuelve un payload JSON con las secciones:
  {
    "curp":            str,
    "padron":          {...} | None,   # fila del padrón + estado/municipio
    "checkid":         {...} | None,   # perfil fiscal completo del SAT
    "direccion": {
        "padron":     {...},
        "fiscal":     {...} | None,
        "cp_coincide": bool,
    },
    "sepomex": {
        "padron":     {...} | None,    # cruce CP padrón
        "fiscal":     {...} | None,    # cruce CP fiscal (si difiere)
    },
    "cfe": {
        "candidatos":  [...],          # candidatos crudos CFE
        "exactos":     [...],          # match por dirección exacta padrón
        "familiares":  [...],          # match por apellidos/nombre familiar
    },
    "mapas": [
        {
            "tipo":    "padron" | "fiscal" | "familiar_cfe" | "exacto_cfe",
            "lat":     float, "lon": float,
            "direccion": str,
            "colonia":   str,
            "cp":        str,
        },
        ...
    ],
    "metadata": {
        "fuente":        "padron" | "checkid-fallback" | "ambos",
        "elapsed_ms":    int,
        "version":       "1.0",
    }
  }
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import duckdb

from inteligencia_completa import (
    _bow_score,
    _is_us_address,
    _normalizar_basico,
    _normalizar_calle,
    _normalizar_colonia,
    _normalizar_cp,
    _tokenizar,
)

logger = logging.getLogger("perfil_inicial")

BASES_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "bases")
)

CFE_DB = os.path.join(BASES_DIR, "cfe_v1.duckdb")
SEPOMEX_DB = os.path.join(BASES_DIR, "sepomex.db")
GEO_DB = os.path.join(BASES_DIR, "geo.db")
PADRON_DB = os.path.join(BASES_DIR, "padron_v1.duckdb")

# Umbral para considerar un titular CFE como match por nombre "exacto"
# (bow_score >= 0.85 → ~85% de los tokens del nombre del sujeto están
# en el nombre del titular CFE).
BOW_NOMBRE_UMBRAL = 0.85

# Umbral mínimo para considerar match por apellido (familiar)
BOW_APELLIDO_UMBRAL = 0.5


# ── Helpers ──────────────────────────────────────────────────────────────────


def _norm_cp(cp: Optional[str]) -> str:
    """Normaliza CP a 5 dígitos."""
    if not cp:
        return ""
    digits = re.sub(r"\D", "", str(cp))
    return digits[:5].zfill(5) if len(digits) >= 5 else ""


def _norm_text(s: Optional[str]) -> str:
    return _normalizar_basico(s or "")


def _split_calle_y_numero(direccion: str) -> Tuple[str, str]:
    """Extrae (calle, número) de una dirección concatenada.

    Ej: "AV INSURGENTES SUR 1234 INT 5" → ("AV INSURGENTES SUR", "1234 INT 5")
    """
    if not direccion:
        return "", ""
    m = re.search(r"\s+(\d+\w*\s*(?:INT\s*\d+)?(?:/[A-Z]?)?)\s*$",
                  direccion.upper().strip())
    if m:
        return direccion[:m.start()].strip(), m.group(1).strip()
    return direccion.strip(), ""


def _normalize_patron_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Convierte fila cruda del padrón en un dict estructurado con campos
    canónicos (calle, numero, colonia, cp, estado_nombre, municipio_nombre).

    El padrón DuckDB tiene las columnas: id, curp, nombre, paterno, materno,
    fecnac, sexo, calle, int, ext, colonia, cp, e, d, m, s, l, mza, consec,
    cred, folio, nac, fuente.
    """
    if not row:
        return {}

    calle = (row.get("calle") or "").strip()
    ext = (row.get("ext") or "").strip()
    interior = (row.get("int") or "").strip()
    colonia = (row.get("colonia") or "").strip()
    cp = _norm_cp(row.get("cp"))

    return {
        "curp":            (row.get("curp") or "").strip().upper(),
        "nombre":          (row.get("nombre") or "").strip().upper(),
        "paterno":         (row.get("paterno") or "").strip().upper(),
        "materno":         (row.get("materno") or "").strip().upper(),
        "nombre_completo": " ".join(filter(None, [
            (row.get("nombre") or "").strip().upper(),
            (row.get("paterno") or "").strip().upper(),
            (row.get("materno") or "").strip().upper(),
        ])),
        "fecnac":          (row.get("fecnac") or "").strip(),
        "sexo":            (row.get("sexo") or "").strip(),
        "calle":           calle,
        "numero":          ext,
        "interior":        interior,
        "colonia":         colonia,
        "cp":              cp,
        "estado":          row.get("e"),  # código numérico
        "estado_codigo":   row.get("e"),
        "distrito":        row.get("d"),
        "municipio":       row.get("m"),  # código numérico
        "municipio_codigo": row.get("m"),
        "seccion":         row.get("s"),
        "localidad":       row.get("l"),
        "manzana":         row.get("mza"),
        "folio":           (row.get("folio") or "").strip(),
        "credencial":      (row.get("cred") or "").strip(),
        "fuente":          (row.get("fuente") or "").strip(),
    }


# ── 1. Padrón ────────────────────────────────────────────────────────────────


def fetch_padron_by_curp(curp: str) -> Optional[Dict[str, Any]]:
    """Busca al sujeto en el padrón electoral por CURP exacto.

    Returns dict normalizado o None si no existe.
    """
    if not curp or len(curp) != 18:
        return None
    curp = curp.upper().strip()
    if not os.path.exists(PADRON_DB):
        logger.warning("PADRON_DB no existe: %s", PADRON_DB)
        return None
    con = duckdb.connect(PADRON_DB, read_only=True)
    try:
        # Mismas columnas que servir.py:97 COLUMNAS
        cols = ("id, curp, nombre, paterno, materno, fecnac, sexo, "
                "calle, int, ext, colonia, cp, e, d, m, s, l, mza, "
                "consec, cred, folio, nac, fuente")
        rows = con.execute(
            f"SELECT {cols} FROM padron WHERE curp = ? LIMIT 1",
            (curp,),
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return None
    col_list = cols.replace(",", "").split()
    raw = dict(zip(col_list, rows[0]))
    return _normalize_patron_row(raw)


def fetch_padron_by_rfc(rfc: str) -> Optional[Dict[str, Any]]:
    """Fallback: busca en padrón por RFC (campo rfc no existe en padrón,
    pero algunos padrones lo tienen en 'fuente' o derivado)."""
    # El padrón base no tiene RFC; este fallback sirve para callers que
    # ya tienen RFC pero quieren datos demográficos. Devuelve None por ahora.
    return None


# ── 2. CheckID ───────────────────────────────────────────────────────────────


def fetch_checkid_by_curp(curp: str, rfc_hint: str = "") -> Optional[Dict[str, Any]]:
    """Llama a CheckID (SAT) para obtener el perfil fiscal completo.

    Usa el cache y los créditos de CheckID (NO consume si no hay créditos).
    Devuelve dict normalizado o None si no se pudo consultar.
    """
    # Importación perezosa para no acoplar este módulo a CheckID
    try:
        from servir import _checkid_lookup, _checkid_creditos_disponibles
    except ImportError:
        return None

    if not _checkid_creditos_disponibles():
        return {
            "exitoso": False,
            "skipped": True,
            "razon": "sin créditos CheckID",
        }

    raw = _checkid_lookup(curp, rfc_hint)
    if raw is None:
        return None

    # Normalizar campos. El shape viene de get_full() en providers/checkid.py.
    resultado = (raw.get("resultado") or raw.get("raw", {}).get("resultado") or {})
    rfc_node = resultado.get("rfc") or {}
    domicilio = resultado.get("domicilio") or {}

    rfc_val = (rfc_node.get("rfc") or raw.get("rfc") or "").strip().upper()
    cp = _norm_cp(
        domicilio.get("codigoPostal")
        or raw.get("codigo_postal")
        or raw.get("cp")
    )

    return {
        "exitoso":         raw.get("exitoso", True),
        "fuente":          raw.get("fuente", "CheckID"),
        "rfc":             rfc_val,
        "rfc_sat":         (rfc_node.get("rfc") or "").strip().upper(),
        "homoclave":       (rfc_node.get("homoclave") or "").strip().upper(),
        "dv":              (rfc_node.get("dv") or "").strip(),
        "razon_social":    (raw.get("razon_social")
                            or resultado.get("nombre")
                            or "").strip(),
        "nss":             (raw.get("nss") or "").strip(),
        "email_fiscal":    (raw.get("email") or domicilio.get("correoElectronico") or "").strip(),
        "regimen_fiscal":  (raw.get("regimen_fiscal")
                            or (resultado.get("regimenesFiscales") or {}).get("regimen")
                            or "").strip(),
        "codigo_postal":   cp,
        "domicilio_fiscal": {
            "calle":      (domicilio.get("calle") or "").strip(),
            "numero":     (domicilio.get("numeroExterior") or "").strip(),
            "interior":   (domicilio.get("numeroInterior") or "").strip(),
            "colonia":    (domicilio.get("colonia") or "").strip(),
            "municipio":  (domicilio.get("municipio") or "").strip(),
            "estado":     (domicilio.get("entidadFederativa") or "").strip(),
            "cp":         cp,
        },
        "estado_69_69b":   raw.get("estado_69_69b") or {},
        "raw_keys":        list((raw or {}).keys()),
    }


# ── 3. SEPOMEX ───────────────────────────────────────────────────────────────


def _sepomex_validate(cp: str, colonia: str = "", estado: str = "",
                      municipio: str = "") -> Dict[str, Any]:
    """Wrapper sobre la lógica SEPOMEX de servir.py (sin HTTP).

    Devuelve dict con score, coincidencias, colonias oficiales, etc.
    """
    cp = _norm_cp(cp)
    if len(cp) != 5:
        return {"error": "cp inválido", "score": 0, "verdict": "cp inválido"}

    if not os.path.exists(SEPOMEX_DB):
        return {"error": "SEPOMEX_DB no encontrada", "score": 0,
                "verdict": "SEPOMEX no disponible"}

    colonia_u = (colonia or "").strip().upper()
    estado_u = (estado or "").strip().upper()
    municipio_u = (municipio or "").strip().upper()

    con = duckdb.connect(SEPOMEX_DB, read_only=True)
    try:
        rows = con.execute(
            "SELECT cp, colonia, tipo, municipio, estado FROM cp WHERE cp=? ORDER BY colonia",
            (cp,),
        ).fetchall()
        cols = ["cp", "colonia", "tipo", "municipio", "estado"]
        rows = [dict(zip(cols, r)) for r in rows]
    finally:
        con.close()

    result = {
        "cp": cp,
        "colonia_input":    colonia_u,
        "estado_input":     estado_u,
        "municipio_input":  municipio_u,
        "matches":          [],
        "coincidencias":    {"colonia": False, "estado": False, "municipio": False},
        "score":            0,
        "verdict":          "sin datos",
    }

    if not rows:
        result["verdict"] = "CP no encontrado en SEPOMEX"
        return result

    oficial_estado = (rows[0]["estado"] or "").upper()
    oficial_municipio = (rows[0]["municipio"] or "").upper()
    result["estado_oficial"]    = rows[0]["estado"]
    result["municipio_oficial"] = rows[0]["municipio"]
    result["colonias_oficiales"] = [
        {"nombre": r["colonia"], "tipo": r["tipo"]} for r in rows
    ]

    result["coincidencias"]["estado"] = bool(estado_u) and (
        estado_u == oficial_estado or estado_u in oficial_estado or oficial_estado in estado_u
    )
    result["coincidencias"]["municipio"] = bool(municipio_u) and (
        municipio_u == oficial_municipio or municipio_u in oficial_municipio
        or oficial_municipio in municipio_u
    )

    colonia_match = None
    for r in rows:
        oficial = (r["colonia"] or "").upper()
        if colonia_u and (colonia_u == oficial or colonia_u in oficial or oficial in colonia_u):
            colonia_match = r["colonia"]
            break
    result["coincidencias"]["colonia"] = bool(colonia_match)
    if colonia_match:
        result["colonia_oficial"] = colonia_match

    score = sum(1 for v in result["coincidencias"].values() if v)
    if result["coincidencias"]["estado"] and result["coincidencias"]["municipio"] and result["coincidencias"]["colonia"]:
        score = 100
        result["verdict"] = "✓ Coincide totalmente con SEPOMEX"
    elif result["coincidencias"]["estado"] and result["coincidencias"]["municipio"]:
        score = 70
        result["verdict"] = "⚠ Estado y municipio coinciden, colonia difiere o ausente"
    elif result["coincidencias"]["estado"]:
        score = 40
        result["verdict"] = "⚠ Estado coincide, municipio y/o colonia difieren"
    else:
        score = 0
        result["verdict"] = "✗ No coincide con SEPOMEX"
    result["score"] = score

    return result


def sepomex_for_padron(padron: Dict[str, Any]) -> Dict[str, Any]:
    """Valida CP del padrón contra SEPOMEX."""
    if not padron:
        return {"error": "sin padron"}
    return _sepomex_validate(
        cp=padron.get("cp", ""),
        colonia=padron.get("colonia", ""),
        estado="",  # el padrón no tiene estado_nombre directo
        municipio="",
    )


def sepomex_for_fiscal(checkid: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Valida CP fiscal (CheckID) contra SEPOMEX. None si no hay CP fiscal."""
    if not checkid or not checkid.get("codigo_postal"):
        return None
    dom = checkid.get("domicilio_fiscal") or {}
    return _sepomex_validate(
        cp=checkid.get("codigo_postal", ""),
        colonia=dom.get("colonia", ""),
        estado=dom.get("estado", ""),
        municipio=dom.get("municipio", ""),
    )


# ── 4. CFE ───────────────────────────────────────────────────────────────────


def _cfe_query_by_domicilio(calle: str, numero: str, colonia: str, cp: str,
                            limit: int = 200) -> List[Dict[str, Any]]:
    """Búsqueda cruda en CFE por tokens de calle/colonia/CP.

    Devuelve filas crudas + score crudo por campo.
    """
    if not os.path.exists(CFE_DB):
        return []

    calle_n = _normalizar_calle(calle or "")
    col_n   = _normalizar_colonia(colonia or "")
    cp_n    = _norm_cp(cp)
    num_n   = re.sub(r"[^\w]", "", (numero or "").upper())

    if not any([calle_n, col_n, cp_n, num_n]):
        return []

    calle_tokens = _tokenizar(calle_n)
    col_tokens   = _tokenizar(col_n)

    where_clauses = []
    where_params  = []
    if cp_n and len(cp_n) == 5:
        where_clauses.append("cp = ?")
        where_params.append(cp_n)
    elif calle_tokens:
        first = calle_tokens[0]
        where_clauses.append(
            "(UPPER(direccion) LIKE ? OR UPPER(calle_adicional_1) LIKE ?)"
        )
        where_params.extend([f"%{first}%", f"%{first}%"])

    where_sql = " OR ".join(where_clauses) if len(where_clauses) > 1 else where_clauses[0]

    con = duckdb.connect(CFE_DB, read_only=True)
    try:
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
            "division", "zona", "agencia", "calle_adicional_1",
            "calle_adicional_2"]

    out = []
    for r in rows:
        rec = dict(zip(cols, r))

        if any(_is_us_address(rec.get(k) or "") for k in
               ("direccion", "calle_adicional_1", "calle_adicional_2", "colonia")):
            continue

        dir_tokens = _tokenizar(_normalizar_calle(
            f"{rec.get('direccion','')} {rec.get('calle_adicional_1','')} "
            f"{rec.get('calle_adicional_2','')}"
        ))
        col_record_tokens = _tokenizar(_normalizar_colonia(rec.get("colonia", "")))
        cp_record = _norm_cp(rec.get("cp", ""))

        score_calle = _bow_score(calle_tokens, dir_tokens) if calle_tokens else 0
        score_col   = _bow_score(col_tokens, col_record_tokens) if col_tokens else 0
        score_cp    = 1.0 if (cp_n and cp_n == cp_record) else 0

        if cp_n and cp_n == cp_record:
            score_total = min(1.0, 0.6 + 0.3 * (score_calle + score_col) / 2)
        elif cp_n:
            score_total = (score_calle * 0.5 + score_col * 0.3) * 0.3
        else:
            score_total = score_calle * 0.5 + score_col * 0.3

        if num_n:
            dir_completo = _normalizar_basico(
                f"{rec.get('direccion','')} {rec.get('calle_adicional_1','')}"
            )
            if num_n not in dir_completo:
                score_total *= 0.5

        if score_total == 0 and not (cp_n and cp_n == cp_record):
            continue

        rec["_score_total"]      = round(score_total, 4)
        rec["_score_calle"]      = round(score_calle, 4)
        rec["_score_colonia"]    = round(score_col, 4)
        rec["_score_cp"]         = round(score_cp, 4)
        out.append(rec)

    out.sort(key=lambda x: x["_score_total"], reverse=True)
    return out[:limit]


def _classify_cfe_candidates(sujeto: Dict[str, Any],
                             candidatos: List[Dict[str, Any]]) -> Dict[str, List]:
    """Clasifica candidatos CFE en 3 grupos:
        - exactos: dirección = padrón (calle + ext + cp)
        - familiares: titular con apellidos coincidentes (bow_score >= 0.5)
        - nombre_exacto: titular con bow_score nombre completo >= 0.85
    """
    exactos, familiares, nombre_exactos = [], [], []

    sujeto_calle_n = _normalizar_calle(sujeto.get("calle", ""))
    sujeto_num_n   = re.sub(r"[^\w]", "", (sujeto.get("numero", "")).upper())
    sujeto_cp_n    = _norm_cp(sujeto.get("cp", ""))
    sujeto_pat_n   = _normalizar_basico(sujeto.get("paterno", ""))
    sujeto_mat_n   = _normalizar_basico(sujeto.get("materno", ""))
    sujeto_nom_n   = _normalizar_basico(sujeto.get("nombre", ""))
    sujeto_full_n  = _normalizar_basico(sujeto.get("nombre_completo", ""))

    pat_tokens = _tokenizar(sujeto_pat_n)
    mat_tokens = _tokenizar(sujeto_mat_n)
    nom_tokens = _tokenizar(sujeto_nom_n)
    full_tokens = _tokenizar(sujeto_full_n)

    for c in candidatos:
        titular_n = _normalizar_basico(c.get("titular", ""))
        titular_tokens = _tokenizar(titular_n)

        rec_calle_n = _normalizar_calle(c.get("direccion", ""))
        rec_calle_full = _normalizar_calle(
            f"{c.get('direccion','')} {c.get('calle_adicional_1','')}"
        )
        rec_num_n = re.sub(r"[^\w]", "",
                           f"{c.get('direccion','')} {c.get('calle_adicional_1','')}".upper())
        rec_cp_n = _norm_cp(c.get("cp", ""))

        # ── (a) dirección EXACTA ──
        calle_match = (
            sujeto_calle_n and rec_calle_n and (
                sujeto_calle_n == rec_calle_n
                or sujeto_calle_n in rec_calle_n
                or rec_calle_n in sujeto_calle_n
            )
        )
        cp_match = sujeto_cp_n and sujeto_cp_n == rec_cp_n
        num_match = (
            not sujeto_num_n
            or sujeto_num_n in rec_num_n
        )
        if calle_match and cp_match and num_match:
            c["_clasificacion"] = "exacto_domicilio"
            exactos.append(c)
            continue

        # ── (b) match por APELLIDOS (familiar) ──
        score_pat = _bow_score(pat_tokens, titular_tokens)
        score_mat = _bow_score(mat_tokens, titular_tokens)
        score_apellidos = (score_pat + score_mat) / 2
        if score_apellidos >= BOW_APELLIDO_UMBRAL and (
            score_pat >= BOW_APELLIDO_UMBRAL or score_mat >= BOW_APELLIDO_UMBRAL
        ):
            c["_clasificacion"] = "familiar_apellidos"
            c["_score_apellidos"] = round(score_apellidos, 4)
            familiares.append(c)
            continue

        # ── (c) match por NOMBRE exacto (bow_score >= 0.85) ──
        score_nombre_full = _bow_score(full_tokens, titular_tokens)
        if score_nombre_full >= BOW_NOMBRE_UMBRAL:
            c["_clasificacion"] = "nombre_exacto"
            c["_score_nombre"] = round(score_nombre_full, 4)
            nombre_exactos.append(c)

    return {
        "exactos":        exactos,
        "familiares":     familiares,
        "nombre_exactos": nombre_exactos,
    }


def buscar_cfe(padron: Dict[str, Any], limit: int = 200) -> Dict[str, Any]:
    """Ejecuta la búsqueda CFE y clasifica los resultados."""
    if not padron:
        return {
            "candidatos":  [],
            "exactos":     [],
            "familiares":  [],
            "nombre_exactos": [],
            "total_raw":   0,
        }

    candidatos = _cfe_query_by_domicilio(
        calle=padron.get("calle", ""),
        numero=padron.get("numero", ""),
        colonia=padron.get("colonia", ""),
        cp=padron.get("cp", ""),
        limit=limit,
    )
    clasificacion = _classify_cfe_candidates(padron, candidatos)

    return {
        "candidatos":      candidatos,
        "exactos":         clasificacion["exactos"],
        "familiares":      clasificacion["familiares"],
        "nombre_exactos":  clasificacion["nombre_exactos"],
        "total_raw":       len(candidatos),
    }


# ── 5. Geocoding (para los mapas Leaflet) ───────────────────────────────────


def _geocode_cp(cp: str) -> Optional[Dict[str, float]]:
    """Devuelve centroide (lat, lon, boundingbox) del CP vía Nominatim.

    geo.db sólo tiene datos SEPOMEX (sin coordenadas), así que la geocodificación
    se hace siempre contra Nominatim. Se cachea en memoria por proceso.

    Returns dict con 'lat', 'lon', 'bbox' (4 floats) o None si no se pudo.
    """
    cp = _norm_cp(cp)
    if len(cp) != 5:
        return None

    # Cache simple en memoria
    if not hasattr(_geocode_cp, "_cache"):
        _geocode_cp._cache = {}
    if cp in _geocode_cp._cache:
        return _geocode_cp._cache[cp]

    try:
        import urllib.request, urllib.parse, json
        qs = urllib.parse.urlencode({
            "postalcode": cp, "country": "Mexico",
            "format": "json", "limit": "1",
        })
        url = f"https://nominatim.openstreetmap.org/search?{qs}"
        req = urllib.request.Request(
            url, headers={"User-Agent": "kyc-perfil-inicial/1.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        if data:
            bb = [float(x) for x in data[0]["boundingbox"]]
            out = {
                "lat":  float(data[0]["lat"]),
                "lon":  float(data[0]["lon"]),
                "bbox": [bb[2], bb[1], bb[0], bb[3]],  # [s, w, n, e]
            }
            _geocode_cp._cache[cp] = out
            return out
    except Exception as e:
        logger.debug("Nominatim geocode falló para cp=%s: %s", cp, e)
    _geocode_cp._cache[cp] = None
    return None


def build_mapas(padron: Optional[Dict[str, Any]],
                checkid: Optional[Dict[str, Any]],
                cfe: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Genera la lista de mapas a renderizar en Leaflet.

    Reglas:
      - 1 mapa para el domicilio del padrón (siempre que tenga CP)
      - 1 mapa adicional para el CP fiscal (si difiere del padrón)
      - 1 mapa por cada candidato CFE clasificado como exacto o familiar
    """
    mapas: List[Dict[str, Any]] = []
    cp_padron = _norm_cp((padron or {}).get("cp"))
    cp_fiscal = _norm_cp((checkid or {}).get("codigo_postal"))

    # Padrón
    if cp_padron:
        geo = _geocode_cp(cp_padron)
        if geo:
            mapas.append({
                "tipo":       "padron",
                "lat":        geo["lat"],
                "lon":        geo["lon"],
                "bbox":       geo["bbox"],
                "direccion":  (padron or {}).get("calle", "") + " " + (padron or {}).get("numero", ""),
                "colonia":    (padron or {}).get("colonia", ""),
                "cp":         cp_padron,
                "label":      "Domicilio Padrón Electoral",
            })

    # Fiscal (solo si difiere)
    if cp_fiscal and cp_fiscal != cp_padron:
        geo = _geocode_cp(cp_fiscal)
        if geo:
            dom = (checkid or {}).get("domicilio_fiscal", {}) or {}
            mapas.append({
                "tipo":       "fiscal",
                "lat":        geo["lat"],
                "lon":        geo["lon"],
                "bbox":       geo["bbox"],
                "direccion":  f"{dom.get('calle','')} {dom.get('numero','')}".strip(),
                "colonia":    dom.get("colonia", ""),
                "cp":         cp_fiscal,
                "label":      "Domicilio Fiscal (SAT)",
            })

    # CFE exactos
    for c in cfe.get("exactos", []):
        cp = _norm_cp(c.get("cp"))
        if not cp:
            continue
        geo = _geocode_cp(cp)
        if not geo:
            continue
        mapas.append({
            "tipo":       "exacto_cfe",
            "lat":        geo["lat"],
            "lon":        geo["lon"],
            "bbox":       geo["bbox"],
            "direccion":  c.get("direccion", ""),
            "colonia":    c.get("colonia", ""),
            "cp":         cp,
            "numero_servicio": c.get("numero_servicio"),
            "titular":    c.get("titular"),
            "label":      "CFE — match exacto domicilio padrón",
            "_score":     c.get("_score_total"),
        })

    # CFE familiares
    for c in cfe.get("familiares", []):
        cp = _norm_cp(c.get("cp"))
        if not cp:
            continue
        geo = _geocode_cp(cp)
        if not geo:
            continue
        mapas.append({
            "tipo":       "familiar_cfe",
            "lat":        geo["lat"],
            "lon":        geo["lon"],
            "bbox":       geo["bbox"],
            "direccion":  c.get("direccion", ""),
            "colonia":    c.get("colonia", ""),
            "cp":         cp,
            "numero_servicio": c.get("numero_servicio"),
            "titular":    c.get("titular"),
            "label":      "CFE — domicilio familiar (apellidos coincidentes)",
            "_score":     c.get("_score_total"),
        })

    return mapas


# ── 6. Orquestador principal ───────────────────────────────────────────────


def ejecutar_perfil_inicial(curp: str, rfc_hint: str = "") -> Dict[str, Any]:
    """Orquesta el flujo estándar: Padrón → CheckID → SEPOMEX → CFE → Mapas.

    Args:
        curp:     CURP del sujeto (18 chars). Requerido.
        rfc_hint: RFC opcional para ayudar a CheckID.

    Returns:
        dict con todas las secciones descritas en el docstring del módulo.
    """
    t0 = time.time()
    curp = (curp or "").upper().strip()

    if len(curp) != 18:
        return {
            "error": "curp inválido",
            "metadata": {"version": "1.0", "elapsed_ms": 0},
        }

    # 1. Padrón
    padron = fetch_padron_by_curp(curp)
    if not padron:
        return {
            "curp":  curp,
            "padron": None,
            "checkid": None,
            "direccion": None,
            "sepomex":  {"padron": None, "fiscal": None},
            "cfe":     {"candidatos": [], "exactos": [], "familiares": [],
                        "nombre_exactos": [], "total_raw": 0},
            "mapas":   [],
            "metadata": {
                "version": "1.0",
                "elapsed_ms": int((time.time() - t0) * 1000),
                "fuente": "sin_padron",
                "nota": "CURP no encontrada en padrón electoral",
            },
        }

    # 2. CheckID (directo, sin re-validar CURP)
    checkid = fetch_checkid_by_curp(curp, rfc_hint)

    # 3. SEPOMEX: padrón + fiscal (si difieren)
    sep_padron = sepomex_for_padron(padron)
    sep_fiscal = sepomex_for_fiscal(checkid) if checkid else None
    cp_padron  = _norm_cp(padron.get("cp"))
    cp_fiscal  = _norm_cp((checkid or {}).get("codigo_postal"))
    cp_coincide = bool(cp_padron) and cp_padron == cp_fiscal

    # 4. CFE: búsqueda + clasificación
    cfe = buscar_cfe(padron)

    # 5. Mapas
    mapas = build_mapas(padron, checkid, cfe)

    fuente = "ambos" if (padron and checkid and checkid.get("exitoso")) else \
             "padron" if padron else "sin_padron"

    return {
        "curp":  curp,
        "padron": padron,
        "checkid": checkid,
        "direccion": {
            "padron":      padron,
            "fiscal":      (checkid or {}).get("domicilio_fiscal"),
            "cp_coincide": cp_coincide,
            "cp_padron":   cp_padron,
            "cp_fiscal":   cp_fiscal,
        },
        "sepomex": {
            "padron":  sep_padron,
            "fiscal":  sep_fiscal,
        },
        "cfe": {
            "candidatos":     cfe["candidatos"][:50],  # cap output
            "exactos":        cfe["exactos"][:20],
            "familiares":     cfe["familiares"][:20],
            "nombre_exactos": cfe["nombre_exactos"][:20],
            "total_raw":      cfe["total_raw"],
        },
        "mapas": mapas,
        "metadata": {
            "version":    "1.0",
            "elapsed_ms": int((time.time() - t0) * 1000),
            "fuente":     fuente,
            "counts": {
                "cfe_candidatos":    cfe["total_raw"],
                "cfe_exactos":       len(cfe["exactos"]),
                "cfe_familiares":    len(cfe["familiares"]),
                "cfe_nombre_exactos": len(cfe["nombre_exactos"]),
                "mapas":             len(mapas),
            },
        },
    }
