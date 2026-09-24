"""
normalizar_direccion.py — Normalización canónica de domicilios.

Objetivo: llevar CUALQUIER base (padrón INE, CFE, Telcel, ATT, empleadores,
IMSS…) a un mismo layout de dirección limpio y profesional.

La base de referencia es el PADRÓN INE 2018 (88.4M filas), el domicilio más
descompuesto y limpio del sistema. Su modelo:
    calle | int | ext | colonia | cp | e d m s l mza (geo electoral)

Hallazgos que motivan cada transformación (medidos sobre las 88.4M filas):
  - `calle`   lleva prefijo de TIPO DE VIALIDAD (C=69%, AV=9%, PRIV, CDA…).  99%+ catálogo INE.
  - `ext`     61% trae artefacto float ".0"; 37% trae sufijo alfa (146A, 124 B).
  - `colonia` lleva prefijo de TIPO DE ASENTAMIENTO (COL=47%, LOC=20%, FRACC=12%…).
  - mojibake  doble-encoding UTF-8 (Ã‘→Ñ) en ~0.09% de filas.
  - `mza`=9999 es sentinela de "desconocido" (9.3%).
  - `cp`      2.6% no son 5 dígitos.

El split de tipo usa WHITELIST (no "primer token a ciegas"): SAN, SANTA, EL,
LA, 2DA… son NOMBRES, no tipos, y deben preservarse intactos.

API principal:
    normalizar_direccion(**campos) -> dict canónico
    from_padron(row_dict)          -> dict canónico (adapter del padrón)
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Catálogos INE / SEPOMEX (abreviatura -> descripción canónica)
# ─────────────────────────────────────────────────────────────────────────────
VIALIDAD = {
    "C": "CALLE", "CALLE": "CALLE", "CALL": "CALLE",
    "AV": "AVENIDA", "AVE": "AVENIDA", "AVENIDA": "AVENIDA",
    "PRIV": "PRIVADA", "PRIVADA": "PRIVADA",
    "CDA": "CERRADA", "CERRADA": "CERRADA",
    "AND": "ANDADOR",
    "CARR": "CARRETERA",
    "PROL": "PROLONGACION",
    "CJON": "CALLEJON", "CALLEJON": "CALLEJON",
    "CTO": "CIRCUITO", "CIRCUITO": "CIRCUITO",
    "BLVD": "BOULEVARD", "BLV": "BOULEVARD", "BOULEVARD": "BOULEVARD",
    "CALZ": "CALZADA", "CALZADA": "CALZADA",
    "CAM": "CAMINO", "CAMINO": "CAMINO",
    "RTNO": "RETORNO", "RETORNO": "RETORNO",
    "DIAG": "DIAGONAL",
    "PSO": "PASEO", "PASEO": "PASEO",
    "VIAD": "VIADUCTO",
    "EJE": "EJE VIAL",
    "PERIF": "PERIFERICO",
    "CALZADA": "CALZADA",
}

# Marcadores rurales que aparecen como prefijo de CALLE en el padrón rural
# (HGO/OAX/CHIS/VER: "LOC EL SAUCITO", "EJIDO LA PALMA"). Subconjunto de
# ASENTAMIENTO inequívoco como marcador — VILLA/COL/CD quedan fuera porque
# encabezan nombres reales de calle.
VIA_RURAL = {
    "LOC": "LOCALIDAD",
    "EJ": "EJIDO", "EJIDO": "EJIDO",
    "RIA": "RANCHERIA", "RANCHERIA": "RANCHERIA",
    "RCHO": "RANCHO", "RANCHO": "RANCHO",
    "BARR": "BARRIO", "BARRIO": "BARRIO",
    "PJE": "PARAJE", "PARAJE": "PARAJE",
    "CTON": "CANTON", "CANTON": "CANTON",
    "PBLO": "PUEBLO", "POB": "PUEBLO", "POBLADO": "PUEBLO",
}

ASENTAMIENTO = {
    "COL": "COLONIA", "COLONIA": "COLONIA",
    "FRACC": "FRACCIONAMIENTO", "FRAC": "FRACCIONAMIENTO",
    "BARR": "BARRIO", "BARRIO": "BARRIO",
    "U": "UNIDAD HABITACIONAL", "UNID": "UNIDAD HABITACIONAL",
    "PBLO": "PUEBLO", "POB": "PUEBLO", "POBLADO": "PUEBLO",
    "EJ": "EJIDO", "EJIDO": "EJIDO",
    "AMPL": "AMPLIACION",
    "RIA": "RANCHERIA", "RCHO": "RANCHO", "RANCHO": "RANCHO",
    "ZONA": "ZONA",
    "SUPMZA": "SUPER MANZANA",
    "RDCIAL": "RESIDENCIAL", "RESIDENCIAL": "RESIDENCIAL",
    "CONJ": "CONJUNTO HABITACIONAL",
    "SECC": "SECCION", "SEC": "SECCION",
    "REG": "REGION",
    "INFONAVIT": "UNIDAD INFONAVIT", "INF": "UNIDAD INFONAVIT",
    "COND": "CONDOMINIO",
    "VILLA": "VILLA",
    "CTON": "CANTON",
    "CD": "CIUDAD",
    "LOC": "LOCALIDAD",
    "PJE": "PARAJE",
}

# INE: clave de entidad 1..32 -> nombre
ENTIDAD = {
    1: "AGUASCALIENTES", 2: "BAJA CALIFORNIA", 3: "BAJA CALIFORNIA SUR",
    4: "CAMPECHE", 5: "COAHUILA", 6: "COLIMA", 7: "CHIAPAS", 8: "CHIHUAHUA",
    9: "CIUDAD DE MEXICO", 10: "DURANGO", 11: "GUANAJUATO", 12: "GUERRERO",
    13: "HIDALGO", 14: "JALISCO", 15: "MEXICO", 16: "MICHOACAN", 17: "MORELOS",
    18: "NAYARIT", 19: "NUEVO LEON", 20: "OAXACA", 21: "PUEBLA",
    22: "QUERETARO", 23: "QUINTANA ROO", 24: "SAN LUIS POTOSI", 25: "SINALOA",
    26: "SONORA", 27: "TABASCO", 28: "TAMAULIPAS", 29: "TLAXCALA",
    30: "VERACRUZ", 31: "YUCATAN", 32: "ZACATECAS",
}

# Abreviaturas de estado (varios esquemas: 2-letra tipo ATT, 3-letra tipo
# Telcel, y nombres) -> clave INE 1..32. Best-effort; el ancla de match real
# es el CP, no la entidad.
ESTADO_ABREV = {
    "AGU": 1, "AGS": 1, "AG": 1,
    "BC": 2, "BCN": 2, "BN": 2,
    "BCS": 3, "BS": 3,
    "CAM": 4, "CAMP": 4, "CM": 4, "CP": 4,
    "COA": 5, "COAH": 5, "CH": 5,
    "COL": 6, "CL": 6,
    "CHP": 7, "CHIS": 7, "CS": 7,
    "CHH": 8, "CHIH": 8,
    "CDMX": 9, "DF": 9, "DIF": 9, "MX": 9,
    "DUR": 10, "DGO": 10, "DG": 10,
    "GUA": 11, "GTO": 11, "GT": 11,
    "GRO": 12, "GR": 12,
    "HID": 13, "HGO": 13, "HG": 13,
    "JAL": 14, "JA": 14, "JC": 14,
    "MEX": 15, "EM": 15, "MC": 15,
    "MIC": 16, "MICH": 16, "MN": 16,
    "MOR": 17, "MS": 17,
    "NAY": 18, "NA": 18, "NT": 18,
    "NL": 19, "NLE": 19,
    "OAX": 20, "OA": 20, "OC": 20,
    "PUE": 21, "PB": 21,
    "QUE": 22, "QRO": 22, "QT": 22, "QO": 22,
    "ROO": 23, "QR": 23, "QROO": 23,
    "SLP": 24, "SP": 24,
    "SIN": 25, "SI": 25, "SL": 25,
    "SON": 26, "SO": 26, "SR": 26,
    "TAB": 27, "TB": 27,
    "TAM": 28, "TAMS": 28, "TS": 28, "TM": 28,
    "TLA": 29, "TLAX": 29, "TL": 29,
    "VER": 30, "VE": 30, "VZ": 30,
    "YUC": 31, "YU": 31, "YN": 31,
    "ZAC": 32, "ZS": 32, "ZT": 32,
}
# nombre completo (sin acentos) -> clave
_ENTIDAD_REV = {v: k for k, v in ENTIDAD.items()}

# catálogo (e, m) INE -> nombre de municipio, derivado del padrón + SEPOMEX
# (generar_catalogo_municipios.py). Carga perezosa: {} si el json no existe.
_MUNICIPIOS: Optional[dict] = None


def _municipio_ine(ent_clave, cve_mun) -> Optional[str]:
    global _MUNICIPIOS
    if _MUNICIPIOS is None:
        ruta = Path(__file__).resolve().parent.parent / "bases" / "catalogo_municipios_ine.json"
        try:
            _MUNICIPIOS = json.loads(ruta.read_text(encoding="utf-8"))
        except OSError:
            _MUNICIPIOS = {}
    if ent_clave and cve_mun:
        return _MUNICIPIOS.get(f"{ent_clave}|{cve_mun}")
    return None


def _resolver_entidad(valor):
    """(clave|None, nombre|None) desde nombre completo o abreviatura."""
    if valor is None:
        return None, None
    s = re.sub(r"\s+", " ", str(valor).strip()).upper()
    if not s:
        return None, None
    s2 = s.translate(str.maketrans("ÁÉÍÓÚÜ", "AEIOUU"))
    if s2 in _ENTIDAD_REV:
        c = _ENTIDAD_REV[s2]; return c, ENTIDAD[c]
    key = s2.replace(".", "").replace(" ", "")
    if key in ESTADO_ABREV:
        c = ESTADO_ABREV[key]; return c, ENTIDAD[c]
    return None, s  # desconocido: devolver el crudo como nombre

# Placeholders que significan "vacío"
_VACIOS = {"", "-", "--", "S/N", "SN", "S/D", "N/A", "NULL", "NONE", ".", "0"}

# Reparaciones de mojibake (doble-encoding UTF-8 leído como latin-1)
_MOJIBAKE = [
    ("Ã‘", "Ñ"), ("Ã±", "ñ"), ("Ã¡", "á"), ("Ã©", "é"), ("Ã­", "í"),
    ("Ã³", "ó"), ("Ãº", "ú"), ("Ã", "Á"), ("Ã‰", "É"), ("Ã", "Í"),
    ("Ã“", "Ó"), ("Ãš", "Ú"), ("Ã¼", "ü"), ("Âº", "º"), ("Âª", "ª"),
    ("¥", "Ñ"),  # Telcel: CA¥ADAS -> CAÑADAS
]


def _fix_mojibake(s: str) -> str:
    for bad, good in _MOJIBAKE:
        if bad in s:
            s = s.replace(bad, good)
    return s


def _clean_str(v) -> Optional[str]:
    """Trim, colapsa espacios, repara mojibake; '' y placeholders -> None."""
    if v is None:
        return None
    s = re.sub(r"\s+", " ", str(v).strip())
    if s.upper() in _VACIOS:
        return None
    return _fix_mojibake(s)


def _clean_ext(v) -> tuple[Optional[str], Optional[str]]:
    """Separa el número exterior en (numérico, sufijo alfa) y quita '.0'.

    '137.0'  -> ('137', None)
    '124 B'  -> ('124', 'B')
    '146A'   -> ('146', 'A')
    'S/N'    -> (None, None)
    """
    s = _clean_str(v)
    if s is None:
        return None, None
    # artefacto float: 137.0 -> 137  (pero conservar 137.5 raro)
    m = re.match(r"^(\d+)\.0+$", s)
    if m:
        s = m.group(1)
    # numérico + sufijo alfa opcional
    m = re.match(r"^(\d+)\s*([A-Za-z]{0,3})$", s)
    if m:
        num = m.group(1)
        alfa = (m.group(2) or "").upper() or None
        return num, alfa
    # no encaja patrón limpio: devolver crudo como alfa (dirección atípica)
    return None, s.upper()


def _split_tipo(texto: Optional[str], catalogo: dict) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Separa (tipo_abbr, tipo_desc, nombre) usando whitelist de catálogo.

    Si el primer token NO está en el catálogo, tipo=None y todo el texto es
    nombre (así SAN, EL, 2DA… se preservan intactos).
    """
    s = _clean_str(texto)
    if s is None:
        return None, None, None
    parts = s.split(" ", 1)
    tok = parts[0].upper()
    if tok in catalogo and len(parts) == 2 and parts[1].strip():
        return tok, catalogo[tok], parts[1].strip()
    return None, None, s


def _valid_cp(v) -> Optional[str]:
    s = _clean_str(v)
    if s and re.fullmatch(r"\d{5}", s):
        return s
    # a veces viene como 20110.0 o 2011 (4 díg) -> intentar rescatar
    if s:
        s2 = re.sub(r"\.0+$", "", s)
        if re.fullmatch(r"\d{5}", s2):
            return s2
    return None


def _geo_int(v) -> Optional[int]:
    """Normaliza códigos geo-electorales; 9999 y 0 son sentinelas -> None."""
    if v is None:
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    if n in (0, 9999):
        return None
    return n


# ─────────────────────────────────────────────────────────────────────────────
# LAYOUT CANÓNICO — el contrato universal
# ─────────────────────────────────────────────────────────────────────────────
CAMPOS_CANONICOS = [
    "tipo_vialidad", "tipo_vialidad_desc", "nombre_vialidad",
    "num_ext", "num_ext_alfa", "num_int", "lote", "entrecalles",
    "tipo_asentamiento", "tipo_asentamiento_desc", "nombre_asentamiento",
    "cp", "municipio", "entidad", "entidad_clave",
    "cve_distrito", "cve_municipio", "seccion", "localidad", "manzana",
    "direccion_completa", "_flags",
]


def normalizar_direccion(
    *,
    calle=None, ext=None, int_=None, colonia=None, cp=None,
    municipio=None, entidad=None,
    lote=None, entrecalles=None,
    e=None, d=None, m=None, s=None, l=None, mza=None, nac=None,
    fuente=None,
) -> dict:
    """Devuelve el domicilio en el layout canónico universal.

    Acepta el modelo del padrón (calle/ext/int/colonia/cp + geo e/d/m/s/l/mza),
    pero cualquier base puede llamar con el subconjunto que tenga.
    """
    flags: list[str] = []

    tv, tvd, nv = _split_tipo(calle, VIALIDAD)
    if tv is None:
        tv, tvd, nv = _split_tipo(calle, VIA_RURAL)
        if tv:
            flags.append("via_rural")
    ta, tad, na = _split_tipo(colonia, ASENTAMIENTO)
    num_ext, num_ext_alfa = _clean_ext(ext)
    num_int = _clean_str(int_)
    lote_ok = _clean_str(lote)
    entrecalles_ok = _clean_str(entrecalles)
    cp_ok = _valid_cp(cp)
    if cp is not None and cp_ok is None:
        flags.append("cp_invalido")

    ent_clave = _geo_int(e) or _geo_int(nac)
    ent_c2, ent_n2 = _resolver_entidad(entidad)
    if ent_clave is None:
        ent_clave = ent_c2
    entidad_nom = ent_n2 or (ENTIDAD.get(ent_clave) if ent_clave else None)
    mza_ok = _geo_int(mza)
    if mza is not None and mza_ok is None and str(mza).strip() in ("9999", "0"):
        flags.append("mza_sentinela")

    # dirección legible reconstruida
    piezas = []
    if tvd or nv:
        via = " ".join(p for p in (tvd, nv) if p)
        if num_ext:
            via += f" {num_ext}"
            if num_ext_alfa:
                via += num_ext_alfa
        if num_int:
            via += f" INT {num_int}"
        if lote_ok:
            via += f" LOTE {lote_ok}"
        piezas.append(via)
    if entrecalles_ok:
        piezas.append(f"ENTRE {entrecalles_ok}")
    if tad or na:
        piezas.append(" ".join(p for p in (tad, na) if p))
    if cp_ok:
        piezas.append(f"CP {cp_ok}")
    loc = _clean_str(municipio) or _municipio_ine(ent_clave, _geo_int(m))
    if loc:
        piezas.append(loc)
    if entidad_nom:
        piezas.append(entidad_nom)
    direccion_completa = ", ".join(piezas) or None

    return {
        "tipo_vialidad": tv, "tipo_vialidad_desc": tvd, "nombre_vialidad": nv,
        "num_ext": num_ext, "num_ext_alfa": num_ext_alfa, "num_int": num_int,
        "lote": lote_ok, "entrecalles": entrecalles_ok,
        "tipo_asentamiento": ta, "tipo_asentamiento_desc": tad, "nombre_asentamiento": na,
        "cp": cp_ok, "municipio": loc, "entidad": entidad_nom, "entidad_clave": ent_clave,
        "cve_distrito": _geo_int(d), "cve_municipio": _geo_int(m),
        "seccion": _geo_int(s), "localidad": _geo_int(l), "manzana": mza_ok,
        "direccion_completa": direccion_completa,
        "_flags": flags,
    }


def from_padron(row: dict) -> dict:
    """Adapter: fila del padrón (dict) -> layout canónico."""
    return normalizar_direccion(
        calle=row.get("calle"), ext=row.get("ext"), int_=row.get("int"),
        colonia=row.get("colonia"), cp=row.get("cp"),
        e=row.get("e"), d=row.get("d"), m=row.get("m"),
        s=row.get("s"), l=row.get("l"), mza=row.get("mza"), nac=row.get("nac"),
        fuente=row.get("fuente"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# CFE — el domicilio viene como TEXTO LIBRE en `direccion`
#   "AV LUIS CABRERA 516 INTC36"  |  "CDA CERRO NEGRO MZ 8 LT 6"
# Estrategia: extraer marcadores (CP, INT, MZ, LT) primero, luego el prefijo de
# vialidad, y de lo que queda: nombre + número exterior.
# `calle_adicional_1/2` = ENTRECALLES (referencias), NO la vialidad principal.
# `colonia` viene TRUNCADA (~18 chars) -> flag colonia_truncada.
# ─────────────────────────────────────────────────────────────────────────────
_RE_CP_EMB   = re.compile(r"\bCP\s*(\d{5})\b")
_RE_INT      = re.compile(r"\b(?:INTC|INT|EDFC|EDF|DEPTO|DPTO|DEPT)\.?\s*([A-Z0-9\-]+)")
_RE_MZ       = re.compile(r"\bMZ[AZ]?\.?\s*([A-Z0-9]+)")
_RE_LT       = re.compile(r"\bL(?:OTE|TE|T)\.?\s*([A-Z0-9]+)")
# notación rural compacta CFE: "M6 L50" (manzana/lote sin Z/T)
_RE_ML_COMPACT = re.compile(r"\bM(\d+)\s+L(\d+)\b")
_RE_EXT_TAIL = re.compile(r"\b(\d{1,6})\s*([A-Z])?\s*$")
_RE_EXT_MID  = re.compile(r"^(.*?\D)\s(\d{1,6})\b")
_FECHA_BASURA = re.compile(r"\d{4}-\d{2}-\d{2}")


def _parse_cfe_direccion(texto) -> dict:
    """Parsea el texto libre de CFE en componentes canónicos parciales."""
    s = _clean_str(texto)
    out = {"tipo": None, "nombre": None, "ext": None, "ext_alfa": None,
           "int": None, "mza": None, "lote": None, "cp": None, "flags": []}
    if not s:
        return out
    s = s.upper()

    m = _RE_CP_EMB.search(s)
    if m:
        out["cp"] = m.group(1); s = _RE_CP_EMB.sub(" ", s)
    m = _RE_INT.search(s)
    if m:
        out["int"] = m.group(1); s = _RE_INT.sub(" ", s)
    # notación compacta "M6 L50" primero (evita que M6 se lea como nombre)
    m = _RE_ML_COMPACT.search(s)
    if m:
        out["mza"] = m.group(1); out["lote"] = m.group(2)
        s = _RE_ML_COMPACT.sub(" ", s)
    m = _RE_MZ.search(s)
    if m:
        out["mza"] = m.group(1); s = _RE_MZ.sub(" ", s)
    m = _RE_LT.search(s)
    if m:
        out["lote"] = m.group(1); s = _RE_LT.sub(" ", s)

    s = re.sub(r"\s+", " ", s).strip()

    # prefijo de vialidad (whitelist)
    parts = s.split(" ", 1)
    if parts and parts[0] in VIALIDAD and len(parts) == 2:
        out["tipo"] = parts[0]; s = parts[1].strip()

    # número exterior: primero al final; si no, primer número tras texto
    m = _RE_EXT_TAIL.search(s)
    if m:
        out["ext"] = m.group(1)
        out["ext_alfa"] = (m.group(2) or None)
        s = s[:m.start()].strip()
    else:
        m = _RE_EXT_MID.search(s)
        if m:
            out["ext"] = m.group(2)
            s = m.group(1).strip()
            out["flags"].append("ext_en_medio")

    out["nombre"] = s or None
    return out


def from_cfe(row: dict) -> dict:
    """Adapter: fila de CFE (dict) -> layout canónico.

    Espera columnas: direccion, calle_adicional_1, calle_adicional_2,
    colonia, cp.
    """
    p = _parse_cfe_direccion(row.get("direccion"))

    # entrecalles: unir ca1 + ca2, descartando basura (fechas por desalineación)
    ec = []
    for k in ("calle_adicional_1", "calle_adicional_2"):
        v = _clean_str(row.get(k))
        # descartar basura: fechas (desalineación) y valores puramente numéricos
        # (códigos, no nombres de calle)
        if v and not _FECHA_BASURA.search(v) and not v.isdigit():
            ec.append(v)
    entrecalles = " Y ".join(ec) or None

    # cp: columna directa o el embebido en direccion
    cp = _clean_str(row.get("cp")) or p["cp"]

    # reconstruir "calle" para reusar el split de vialidad del núcleo
    calle = " ".join(x for x in (p["tipo"], p["nombre"]) if x) or None
    ext = p["ext"]
    if p["ext_alfa"] and ext:
        ext = f"{ext}{p['ext_alfa']}"

    d = normalizar_direccion(
        calle=calle, ext=ext, int_=p["int"], colonia=row.get("colonia"), cp=cp,
        lote=p["lote"], entrecalles=entrecalles,
        mza=p["mza"], fuente="cfe",
    )

    # colonia truncada (CFE la corta ~18 chars)
    col = _clean_str(row.get("colonia"))
    if col and len(col) >= 18:
        d["_flags"].append("colonia_truncada")
    d["_flags"].extend(p["flags"])
    return d


# ─────────────────────────────────────────────────────────────────────────────
# CLAVE DE MATCH CANÓNICA — el contrato que permite cruzar bases.
#
# Se generan claves EN CASCADA (de más estricta a más laxa). Dos filas de bases
# distintas "matchean" si coinciden en la clave más estricta que ambas puedan
# producir. Cada clave declara su nivel de confianza.
#
#   k_via_ext   cp | vialidad_norm | ext          (fuerte: misma puerta)
#   k_cp_ext    cp | ext                           (media: mismo número en el CP)
#   k_via_cp    cp | vialidad_norm                 (media: misma calle en el CP)
#   k_geo       entidad | seccion | manzana        (padrón: micro-geo electoral)
#
# `vialidad_norm`: nombre de vialidad SIN tipo, SIN acentos, colapsado. Es el
# eje que cruza padrón (tipificado) con CFE (texto libre) porque ambos terminan
# con el mismo `nombre_vialidad` una vez normalizados.
# ─────────────────────────────────────────────────────────────────────────────
_ACENTOS = str.maketrans("ÁÉÍÓÚÜÑáéíóúüñ", "AEIOUUNAEIOUUN")
_STOP_VIA = {"DE", "DEL", "LA", "LAS", "LOS", "EL", "Y", "A"}
# abreviaturas frecuentes en vialidad MX -> forma canónica (para match)
_ABREV_VIA = {
    "NTE": "NORTE", "OTE": "ORIENTE", "PTE": "PONIENTE", "PONIENTE": "PONIENTE",
    "NO": "NORTE", "SO": "SUR", "SUR": "SUR",
    "1RA": "1", "2DA": "2", "3RA": "3", "1A": "1", "2A": "2", "3A": "3",
    "1O": "1", "2O": "2", "3O": "3",
    "MZ": "MANZANA", "MZA": "MANZANA", "LT": "LOTE", "LTE": "LOTE",
    "AV": "AVENIDA", "AVE": "AVENIDA", "CJON": "CALLEJON", "PRIV": "PRIVADA",
    "PROL": "PROLONGACION", "CDA": "CERRADA", "BLVD": "BOULEVARD",
}


def _norm_texto(s: Optional[str]) -> Optional[str]:
    """Mayúsculas, sin acentos, sin puntuación, espacios colapsados."""
    if not s:
        return None
    s = s.upper().translate(_ACENTOS)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def _norm_vialidad(nombre: Optional[str]) -> Optional[str]:
    """Nombre de vialidad canónico para match: sin acentos y sin stopwords.

    'AV. José Ma. Morelos y Pavón' y 'JOSE MA MORELOS PAVON' colapsan igual.
    """
    n = _norm_texto(nombre)
    if not n:
        return None
    toks = [_ABREV_VIA.get(t, t) for t in n.split(" ") if t not in _STOP_VIA]
    # ordenar alfabéticamente los tokens para absorber variación de orden
    # ("2 OTE NORTE" == "2 NORTE ORIENTE"): match por conjunto de palabras
    toks = sorted(toks)
    return " ".join(toks) or n


def clave_match(canon: dict) -> dict:
    """Dado un dict canónico (salida de normalizar_direccion/from_*), devuelve
    las claves de match en cascada. Cada valor es None si no hay dato suficiente.
    """
    cp = canon.get("cp")
    via = _norm_vialidad(canon.get("nombre_vialidad"))
    ext = canon.get("num_ext")
    ent = canon.get("entidad_clave")
    secc = canon.get("seccion")
    mza = canon.get("manzana")
    # colonia normalizada, recortada a 15 chars: CFE trunca la colonia ~18, así
    # que el prefijo común es lo único comparable entre padrón (completa) y CFE.
    col_full = _norm_texto(canon.get("nombre_asentamiento"))
    col = col_full[:15] if col_full else None

    def j(*parts):
        return "|".join(str(p) for p in parts) if all(p not in (None, "") for p in parts) else None

    return {
        # claves con cp (fuertes, pero CFE casi no trae cp)
        "k_via_ext":     j(cp, via, ext),
        "k_cp_ext":      j(cp, ext),
        "k_via_cp":      j(cp, via),
        # claves cp-INDEPENDIENTES (el puente real con CFE)
        "k_col_via_ext": j(col, via, ext),
        "k_col_via":     j(col, via),
        "k_col_ext":     j(col, ext),
        # micro-geo electoral (solo padrón)
        "k_geo":         j(ent, secc, mza),
    }


# orden de estrictez: cp-fuertes > cp-independientes ricas > laxas > geo
_NIVELES = ("k_via_ext", "k_col_via_ext", "k_cp_ext", "k_col_via",
            "k_col_ext", "k_via_cp", "k_geo")


def mejor_clave(canon: dict) -> tuple[Optional[str], Optional[str]]:
    """Devuelve (nivel, clave) de la clave más estricta disponible."""
    ks = clave_match(canon)
    for nivel in _NIVELES:
        if ks[nivel]:
            return nivel, ks[nivel]
    return None, None


# ─────────────────────────────────────────────────────────────────────────────
# ADAPTERS de las bases restantes -> layout canónico
# ─────────────────────────────────────────────────────────────────────────────
def from_telcel(row: dict) -> dict:
    """Telcel: domicilio='CALLE/ENTRECALLES', numero=ext, edo=abrev 3-letra."""
    dom = _clean_str(row.get("domicilio"))
    calle, entre = dom, None
    if dom and "/" in dom:
        izq, der = dom.split("/", 1)
        calle = izq.strip() or None
        entre = der.strip().strip("/").strip() or None
        # descartar entrecalles basura ("Y", "ENTR", "ESQ", fragmentos <=4)
        if entre and (len(entre) <= 4 or entre.upper() in ("Y", "ENTR", "ESQ", "ENTRE")):
            entre = None
    return normalizar_direccion(
        calle=calle, ext=row.get("numero"), int_=row.get("interior"),
        colonia=row.get("colonia"), cp=row.get("cp"),
        municipio=row.get("ciudad"), entidad=row.get("edo"),
        entrecalles=entre, fuente="telcel",
    )


def from_att(row: dict) -> dict:
    """ATT: direccion=calle, exterior/interior separados, estado=abrev 2-letra.

    Quirk: `exterior` viene casi siempre NULL y el número real está en
    `interior`. Si exterior está vacío e interior es numérico, se promueve
    interior -> ext para habilitar el match puerta-exacta.
    """
    ext = _clean_str(row.get("exterior"))
    interior = _clean_str(row.get("interior"))
    if not ext and interior:
        # "84 B" / "211" -> tomar el número como ext
        m = re.match(r"^(\d+)", interior)
        if m:
            ext = interior
            interior = None
    return normalizar_direccion(
        calle=row.get("direccion"), ext=ext,
        int_=interior, colonia=row.get("colonia"),
        municipio=row.get("municipio"), entidad=row.get("estado"),
        fuente="att",
    )


def from_empleadores(row: dict, otra: bool = False) -> dict:
    """Empleadores: ubicacion.* (o otraUbicacion.* si otra=True). Muy limpio."""
    pfx = "otraUbicacion." if otra else "ubicacion."
    g = lambda k: row.get(pfx + k)
    return normalizar_direccion(
        calle=g("calle"), ext=g("numero_exterior"), int_=g("numero_interior"),
        colonia=g("colonia"), cp=g("codigopostal"),
        municipio=g("municipio"), entidad=g("entidad"),
        fuente="empleadores",
    )


def from_imss_patron(row: dict) -> dict:
    """IMSS asegurados: domicilio_patron es texto libre (dirección del PATRÓN,
    no de la persona). ciudad_estado combina municipio+estado; cp5 limpio.
    """
    p = _parse_cfe_direccion(row.get("domicilio_patron"))
    calle = " ".join(x for x in (p["tipo"], p["nombre"]) if x) or None
    ext = p["ext"]
    if p["ext_alfa"] and ext:
        ext = f"{ext}{p['ext_alfa']}"
    # ciudad_estado: intentar separar el estado del final
    ce = _clean_str(row.get("ciudad_estado"))
    municipio, entidad = ce, None
    if ce:
        toks = ce.split()
        for n in (2, 1):  # probar 2 y 1 token finales como estado
            if len(toks) > n:
                cand = " ".join(toks[-n:])
                c, _ = _resolver_entidad(cand)
                if c:
                    entidad = cand
                    municipio = " ".join(toks[:-n]) or None
                    break
    d = normalizar_direccion(
        calle=calle, ext=ext, int_=p["int"], cp=row.get("cp5") or p["cp"],
        lote=p["lote"], mza=p["mza"],
        municipio=municipio, entidad=entidad, fuente="imss_patron",
    )
    d["_flags"].append("direccion_del_patron")
    return d


def from_repuve(row: dict) -> dict:
    """REPUVE: dir_prop_fix es texto libre SIN CP, formato:
        'VIALIDAD NOMBRE NUM <ext>. <int>. <COLONIA> <MUNICIPIO>'
    Segmentos separados por '.'. Mayormente Veracruz. Sin CP -> el match de
    dirección solo llega a nivel colonia+vialidad; el valor real de REPUVE es
    el cruce por RFC/propietario.
    """
    s = _clean_str(row.get("dir_prop_fix") or row.get("DIR_PROP"))
    if not s:
        d = normalizar_direccion(fuente="repuve")
        d["_flags"].append("sin_direccion")
        return d
    segs = [seg.strip() for seg in s.split(".")]
    calle_seg = segs[0]
    # cola: último segmento no vacío = COLONIA + MUNICIPIO pegados
    tail = next((seg for seg in reversed(segs[1:]) if seg), "")

    # preprocesar seg de calle: quitar etiqueta NUM, normalizar palabras largas
    cs = re.sub(r"\bNUM\b", " ", calle_seg)
    cs = re.sub(r"\bMANZANA\b", "MZ", cs)
    cs = re.sub(r"\bLOTE\b", "LT", cs)
    cs = re.sub(r"\bEDIF(ICIO)?\b", "EDF", cs)
    cs = re.sub(r"\s+0+\b", " ", cs)  # ceros placeholder (int/ext nulos)
    cs = re.sub(r"\s+", " ", cs).strip()
    p = _parse_cfe_direccion(cs)
    calle = " ".join(x for x in (p["tipo"], p["nombre"]) if x) or None
    ext = p["ext"]
    if ext == "0":
        ext = None
    if p["ext_alfa"] and ext:
        ext = f"{ext}{p['ext_alfa']}"

    # cola: separar municipio (último token) de la colonia. Best-effort.
    colonia, municipio = tail or None, None
    if tail:
        toks = tail.split()
        if len(toks) >= 2:
            municipio = toks[-1]
            colonia = " ".join(toks[:-1])

    d = normalizar_direccion(
        calle=calle, ext=ext, int_=p["int"], lote=p["lote"], mza=p["mza"],
        colonia=colonia, municipio=municipio, fuente="repuve",
    )
    d["_flags"].append("sin_cp")
    return d
