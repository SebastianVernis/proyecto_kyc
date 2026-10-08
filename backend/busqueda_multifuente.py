"""busqueda_multifuente.py — búsquedas en las fuentes que el runtime no cubría.

Endpoints nuevos (se rutean desde servir.py):

    GET /api/v1/ine2018/buscar?curp=&rfc=&nombre=&paterno=&materno=&cp=&estado=&limit=
    GET /api/v1/covid/buscar?curp=&nombre=&paterno=&materno=&cp=&entidad=&limit=
    GET /api/v1/telefonia/buscar?telefono=&rfc=&curp=&nombre=&incluir_46m=1&limit=

Por qué existen (sesiones 2026-10-08, casos 5540553398 y 5613772366):

1. ``ine_2018.duckdb`` — padrón electoral 2018 en 34 tablas, una por
   estado-segmento, con ESQUEMA por estado (``DF2."Df2"``, ``EDM1."Edm1"``,
   ``AGS."Ags"``, ...). NO tiene una tabla única, por eso ``show tables``
   devuelve vacío y nadie la consultaba. Sus 23 columnas son las mismas de
   ``padron.duckdb`` y además trae el detalle electoral
   (sección/manzana/consecutivo/folio/credencial). Sirve para el caso
   "no empadronado" del padrón fusionado que sí aparece en 2018.

2. ``covid23_master.duckdb`` — expone DOS tablas de 19 609 795 filas:
   ``covid_clinico`` (130 columnas: CURP, domicilio, teléfono, DIAGPROB,
   FECINGRE, unidad) y ``personas`` (48 columnas que NO son COVID: es un
   volcado TELEFÓNICO con RFC/CURP/teléfono). El runtime attachea el archivo
   con tabla principal ``main.personas``, así que **todo lo clínico quedaba
   inaccesible**. Aquí se consulta la tabla correcta según lo que se pida.

3. Telefonía adicional — cruce por teléfono en una sola pasada, normalizando
   a dígitos (las columnas traen padding de espacios y a veces LADA).
   ``telcel_master_v2`` (44.6M) es el mismo corpus que ``telcel_v3``: se
   incluye solo con ``incluir_46m=1`` porque su primer escaneo en frío tarda
   ~20 s y no conviene por defecto en un endpoint HTTP.

Todas las conexiones son ``read_only=True`` y llevan ``statement_timeout``
para que una consulta lenta no bloquee el hilo del servidor. Nada de esto
escribe en las bases.
"""
from __future__ import annotations

import re
import time
import urllib.parse as _up
from pathlib import Path
from typing import Optional

import duckdb

BASES_DIR = Path(__file__).resolve().parent.parent / "bases"

INE2018_DB = BASES_DIR / "ine_2018.duckdb"
COVID_DB = BASES_DIR / "covid23_master.duckdb"

# Columnas de identidad electoral, iguales en las 34 tablas de ine_2018.
INE2018_COLS = ("cve", "edad", "nombre", "paterno", "materno", "fecnac", "sexo",
                 "calle", "int", "ext", "colonia", "cp", "e", "d", "m", "s", "l",
                 "mza", "consec", "cred", "folio", "nac", "curp")

# Telefonía con CURP/RFC. Se evita el twin *_v1 y el 46M por defecto.
TELEFONIA = [
    ("telcel.duckdb", "telcel"),
    ("telcel_mexico_master.duckdb", "personas"),
    ("telcel1_master.duckdb", "personas"),
    ("covid23_master.duckdb", "personas"),
]
TELEFONIA_46M = [("telcel_master_v2.duckdb", "telcel_46m_proc")]

STMT_TIMEOUT = "90s"
_ine_tablas: Optional[list] = None


def _con(path: Path):
    con = duckdb.connect(str(path), read_only=True)
    try:
        con.execute(f"set statement_timeout='{STMT_TIMEOUT}'")
    except Exception:
        pass
    return con


def _digitos(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def _variantes_tel(tel: str):
    d = _digitos(tel)
    out = {d}
    for n in (10, 12, 13):
        if len(d) >= n:
            out.add(d[-n:])
    return sorted(x for x in out if len(x) >= 7)


def _tablas_ine2018() -> list:
    """[(esquema, tabla)] de las tablas de padrón (sin las de Municipios).

    duckdb_tables() es obligatorio aquí: ``show tables`` devuelve vacío porque
    cada estado vive en su propio esquema."""
    global _ine_tablas
    if _ine_tablas is None:
        con = _con(INE2018_DB)
        _ine_tablas = [(s, t) for s, t in con.execute(
            "select schema_name, table_name from duckdb_tables() "
            "order by schema_name, table_name").fetchall()
            if t.lower() != "municipios"]
        con.close()
    return _ine_tablas


def _fila(cols, fila) -> dict:
    return {c: (None if v is None else str(v)) for c, v in zip(cols, fila)}


# ══════════════════════════════════════════════════════════════════════════
# 1) INE 2018 — padrón electoral por estado
# ══════════════════════════════════════════════════════════════════════════

def buscar_ine2018(curp: str = "", rfc: str = "", nombre: str = "",
                   paterno: str = "", materno: str = "", cp: str = "",
                   estado: str = "", limit: int = 50) -> dict:
    """Busca en las 34 tablas de ine_2018 y reporta en qué estado(s) aparece.

    Exige al menos uno de curp / (paterno o materno) / nombre, para no recorrer
    120M de filas por accidente. ``estado`` es el nombre del esquema
    (DF2, EDM1, AGS, ...) y acota el barrido a esa tabla.
    """
    t0 = time.time()
    curp = (curp or "").upper().strip()
    paterno = (paterno or "").upper().strip()
    materno = (materno or "").upper().strip()
    nombre = (nombre or "").upper().strip()
    rfc = (rfc or "").upper().strip()
    cp = _digitos(cp)
    estado = (estado or "").upper().strip()

    if not curp and not paterno and not materno and not nombre:
        return {"error": "se requiere curp, o paterno/materno, o nombre",
                "candidates": [], "count": 0}

    conds, params = [], []
    if curp:
        conds.append("upper(trim(curp)) = ?")
        params.append(curp)
    if paterno:
        conds.append("regexp_matches(upper(coalesce(paterno,'')), ?)")
        params.append(rf"(^|[^A-Z]){re.escape(paterno)}")
    if materno:
        conds.append("regexp_matches(upper(coalesce(materno,'')), ?)")
        params.append(rf"(^|[^A-Z]){re.escape(materno)}")
    if nombre:
        for tok in re.findall(r"[A-ZÑ]{3,}", nombre):
            conds.append("regexp_matches(upper(coalesce(nombre,'')), ?)")
            params.append(rf"(^|[^A-Z]){re.escape(tok)}")
    if cp:
        conds.append("lpad(regexp_replace(coalesce(cp,''),'[^0-9]','','g'),5,'0') = ?")
        params.append(cp.zfill(5))
    where = " AND ".join(conds)

    tablas = _tablas_ine2018()
    if estado:
        tablas = [(s, t) for s, t in tablas if s.upper() == estado]
        if not tablas:
            return {"error": f"estado sin tabla: {estado}",
                    "estados_validos": sorted({s for s, _ in _tablas_ine2018()}),
                    "candidates": [], "count": 0}

    con = _con(INE2018_DB)
    cands, errores = [], []
    try:
        for esq, tab in tablas:
            try:
                cur = con.execute(
                    f'select {", ".join(INE2018_COLS)} from "{esq}"."{tab}" '
                    f'where {where} limit ?', params + [limit])
                filas = cur.fetchall()
            except Exception as e:
                errores.append(f"{esq}.{tab}: {type(e).__name__} {str(e)[:80]}")
                continue
            for f in filas:
                d = _fila(INE2018_COLS, f)
                d["_estado_tabla"] = esq
                d["_fuente"] = "ine_2018"
                cands.append(d)
    finally:
        con.close()

    # El RFC no está en las tablas de 2018: se deriva del CURP cuando se pide.
    if rfc:
        rfc10 = rfc[:10]
        cands = [c for c in cands if (c.get("curp") or "").startswith(rfc10)]
        if not cands:
            return {"query": {"rfc": rfc}, "candidates": [], "count": 0,
                    "nota": "ine_2018 no tiene columna RFC; se filtró por los "
                            "10 primeros caracteres del CURP, sin coincidencia.",
                    "segundos": round(time.time() - t0, 2)}

    por_estado = {}
    for c in cands:
        por_estado.setdefault(c["_estado_tabla"], 0)
        por_estado[c["_estado_tabla"]] += 1

    return {
        "query": {"curp": curp, "paterno": paterno, "materno": materno,
                  "nombre": nombre, "cp": cp, "estado": estado},
        "candidates": cands,
        "count": len(cands),
        "por_estado": por_estado,
        "tablas_barridas": len(tablas),
        "errores": errores[:10],
        "segundos": round(time.time() - t0, 2),
        "nota": "Padrón electoral 2018 por estado-segmento. Trae ubicación "
                "electoral (e/d/m/s/l, mza, consec, cred, folio) que el padrón "
                "fusionado no expone.",
    }


# ══════════════════════════════════════════════════════════════════════════
# 2) COVID / clínico — covid_clinico (la tabla que el runtime nunca alcanzaba)
# ══════════════════════════════════════════════════════════════════════════

COVID_COLS = ("APEPATER", "APEMATER", "NOMBRE", "CURP", "SEXO", "FECNACI", "EDAD",
              "ENTIDAD", "DOMICILIO", "CP", "TELEFONO", "DIAGPROB", "FECINGRE",
              "ID_REGISTRO")


def buscar_covid(curp: str = "", nombre: str = "", paterno: str = "",
                 materno: str = "", cp: str = "", entidad: str = "",
                 diagnostico: str = "", limit: int = 50) -> dict:
    """Busca en covid_clinico (19.6M, 130 cols). Al menos un criterio real."""
    t0 = time.time()
    curp = (curp or "").upper().strip()
    paterno = (paterno or "").upper().strip()
    materno = (materno or "").upper().strip()
    nombre = (nombre or "").upper().strip()
    cp = _digitos(cp)
    entidad = (entidad or "").upper().strip()
    diagnostico = (diagnostico or "").upper().strip()

    if not any((curp, paterno, materno, nombre, cp, entidad, diagnostico)):
        return {"error": "se requiere al menos: curp, paterno, materno, nombre, "
                         "cp, entidad o diagnostico", "candidates": [], "count": 0}

    conds, params = [], []
    if curp:
        conds.append("upper(trim(CURP)) = ?")
        params.append(curp)
    if paterno:
        conds.append("upper(trim(APEPATER)) = ?")
        params.append(paterno)
    if materno:
        conds.append("upper(trim(APEMATER)) = ?")
        params.append(materno)
    if nombre:
        for tok in re.findall(r"[A-ZÑ]{3,}", nombre):
            conds.append("upper(coalesce(NOMBRE,'')) LIKE ?")
            params.append(f"%{tok}%")
    if cp:
        conds.append("lpad(regexp_replace(coalesce(CP,''),'[^0-9]','','g'),5,'0') = ?")
        params.append(cp.zfill(5))
    if entidad:
        conds.append("upper(coalesce(ENTIDAD,'')) LIKE ?")
        params.append(f"%{entidad}%")
    if diagnostico:
        conds.append("upper(coalesce(DIAGPROB,'')) LIKE ?")
        params.append(f"%{diagnostico}%")

    sql = (f'select {", ".join(COVID_COLS)} from covid_clinico where '
           + " AND ".join(conds) + " limit ?")
    try:
        con = _con(COVID_DB)
        cur = con.execute(sql, params + [limit])
        filas = cur.fetchall()
        con.close()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:160]}",
                "candidates": [], "count": 0}

    cands = [{**_fila(COVID_COLS, f), "_fuente": "covid_clinico"} for f in filas]
    return {"query": {"curp": curp, "paterno": paterno, "materno": materno,
                      "nombre": nombre, "cp": cp, "entidad": entidad,
                      "diagnostico": diagnostico},
            "candidates": cands, "count": len(cands),
            "segundos": round(time.time() - t0, 2),
            "nota": "covid23_master.duckdb::covid_clinico — 19.6M registros con "
                    "CURP, domicilio, teléfono y diagnóstico. La tabla personas "
                    "del mismo archivo es un volcado telefónico, no clínico."}


# ══════════════════════════════════════════════════════════════════════════
# 3) Telefonía — cruce por teléfono / RFC / CURP en una pasada
# ══════════════════════════════════════════════════════════════════════════

TEL_COLS = ("telefono", "rfc_clean", "curp", "nombre1", "nombre2", "domicilio",
            "colonia", "ciudad", "edo", "cp", "plan_actual", "st_tel",
            "tel_contacto", "contacto1", "contacto2")


def buscar_telefonia(telefono: str = "", rfc: str = "", curp: str = "",
                      nombre: str = "", incluir_46m: bool = False,
                      limit: int = 50) -> dict:
    """Cruza teléfono/RFC/CURP contra las bases telefónicas con CURP.

    Normaliza teléfonos a dígitos (padding de espacios y LADA incluidos).
    Busca también en ``tel_contacto``/``contacto1``/``contacto2``: la línea de
    contacto de la cuenta suele ser de otra persona y es el hilo que enlaza
    hogares.
    """
    t0 = time.time()
    curp = (curp or "").upper().strip()
    rfc = (rfc or "").upper().strip()
    nombre = (nombre or "").upper().strip()
    tels = _variantes_tel(telefono) if telefono else []

    if not (tels or rfc or curp or nombre):
        return {"error": "se requiere telefono, rfc, curp o nombre",
                "candidates": [], "count": 0}

    fuentes = TELEFONIA + (TELEFONIA_46M if incluir_46m else [])
    cands, errores, por_base = [], [], {}
    for archivo, tabla in fuentes:
        p = BASES_DIR / archivo
        if not p.exists():
            continue
        try:
            con = _con(p)
            cols = {r[0] for r in con.execute(f'describe "{tabla}"').fetchall()}
            conds, params = [], []
            if tels and "telefono" in cols:
                conds.append("regexp_replace(coalesce(cast(telefono as varchar),''),"
                             "'[^0-9]','','g') in (" + ",".join("?" * len(tels)) + ")")
                params.extend(tels)
            for c in ("tel_contacto", "contacto1", "contacto2"):
                if tels and c in cols:
                    conds.append(f"regexp_replace(coalesce(cast(\"{c}\" as varchar),''),"
                                 "'[^0-9]','','g') in (" + ",".join("?" * len(tels)) + ")")
                    params.extend(tels)
            if rfc and "rfc" in cols:
                conds.append("upper(trim(cast(rfc as varchar))) LIKE ?")
                params.append(rfc[:10] + "%")
            if rfc and "rfc_clean" in cols:
                conds.append("upper(trim(cast(rfc_clean as varchar))) LIKE ?")
                params.append(rfc[:10] + "%")
            if curp and "curp" in cols:
                conds.append("upper(trim(cast(curp as varchar))) = ?")
                params.append(curp)
            if nombre:
                for c in ("nombre1", "nombre2"):
                    if c in cols:
                        conds.append(f"upper(coalesce(cast(\"{c}\" as varchar),'')) LIKE ?")
                        params.append(f"%{nombre}%")
            if not conds:
                con.close()
                continue
            sel = [c for c in TEL_COLS if c in cols] or ["*"]
            sql = (f'select {", ".join(sel)} from "{tabla}" where '
                   + " or ".join(conds) + " limit ?")
            cur = con.execute(sql, params + [limit])
            filas = cur.fetchall()
            nombres = [d[0] for d in cur.description]
            con.close()
        except Exception as e:
            errores.append(f"{archivo}: {type(e).__name__} {str(e)[:90]}")
            continue
        if filas:
            por_base[archivo] = len(filas)
        for f in filas:
            d = {k: (None if v is None else str(v).strip()) for k, v in zip(nombres, f)}
            d["_fuente"] = archivo
            cands.append(d)

    return {"query": {"telefono": telefono, "variantes": tels, "rfc": rfc,
                      "curp": curp, "nombre": nombre, "incluir_46m": incluir_46m},
            "candidates": cands, "count": len(cands), "por_base": por_base,
            "bases": [a for a, _ in fuentes],
            "errores": errores[:10],
            "segundos": round(time.time() - t0, 2),
            "nota": "telcel_master_v2 (44.6M) solo con incluir_46m=1: mismo "
                    "corpus que telcel_v3 y ~20s en el primer escaneo."}


# ══════════════════════════════════════════════════════════════════════════
# Handlers HTTP (mismo contrato que busqueda_manual: reciben el handler)
# ══════════════════════════════════════════════════════════════════════════

def _q(handler) -> dict:
    return {k: v[0] for k, v in
            _up.parse_qs(_up.urlparse(handler.path).query).items()}


def _entero(d: dict, k: str, default: int, tope: int = 200) -> int:
    try:
        return max(1, min(int(d.get(k, "") or default), tope))
    except (TypeError, ValueError):
        return default


def _verdadero(d: dict, k: str) -> bool:
    return str(d.get(k, "")).strip().lower() in ("1", "true", "yes", "on")


def handle_ine2018_buscar(handler):
    """GET /api/v1/ine2018/buscar?curp=&rfc=&nombre=&paterno=&materno=&cp=&estado=&limit="""
    d = _q(handler)
    r = buscar_ine2018(curp=d.get("curp", ""), rfc=d.get("rfc", ""),
                       nombre=d.get("nombre", ""), paterno=d.get("paterno", ""),
                       materno=d.get("materno", ""), cp=d.get("cp", ""),
                       estado=d.get("estado", ""),
                       limit=_entero(d, "limit", 50))
    handler._json(200 if "error" not in r else 400, r)


def handle_covid_buscar(handler):
    """GET /api/v1/covid/buscar?curp=&nombre=&paterno=&materno=&cp=&entidad=&diagnostico=&limit="""
    d = _q(handler)
    r = buscar_covid(curp=d.get("curp", ""), nombre=d.get("nombre", ""),
                     paterno=d.get("paterno", ""), materno=d.get("materno", ""),
                     cp=d.get("cp", ""), entidad=d.get("entidad", ""),
                     diagnostico=d.get("diagnostico", ""),
                     limit=_entero(d, "limit", 50))
    handler._json(200 if "error" not in r else 400, r)


def handle_telefonia_buscar(handler):
    """GET /api/v1/telefonia/buscar?telefono=&rfc=&curp=&nombre=&incluir_46m=1&limit="""
    d = _q(handler)
    r = buscar_telefonia(telefono=d.get("telefono", ""), rfc=d.get("rfc", ""),
                         curp=d.get("curp", ""), nombre=d.get("nombre", ""),
                         incluir_46m=_verdadero(d, "incluir_46m"),
                         limit=_entero(d, "limit", 50))
    handler._json(200 if "error" not in r else 400, r)
