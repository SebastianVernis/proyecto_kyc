"""Mapeo automático del sujeto en las bases bancarias.

Se ejecuta al abrir el expediente: el operador no elige banco ni escribe nada.
Cruza, en este orden:

  1. RFC — con los 10 primeros caracteres. Las bases bancarias guardan el RFC
     de la persona física sin homoclave (10) o completo (13), así que un
     `rfc = '<13>'` exacto devuelve cero; el prefijo cubre ambos.
  2. CURP exacta — las vistas la traen casi siempre vacía, pero es gratis.
  3. Nombre — los dos apellidos presentes en la MISMA fila, sobre la
     concatenación de `titular_nombre1` + `titular_nombre2`. Es obligatorio
     concatenar: el orden de esas columnas cambia entre bancos (Santander pone
     nombres + apellidos, Clavijero los apellidos primero, Amex los pega).

Los aciertos por nombre son CANDIDATOS: se devuelven con su score para que la
ficha los muestre como tales, nunca como identidad confirmada.
"""
from __future__ import annotations

import re
import time
import unicodedata

# Bancos con base local. `vistas` son las vistas api.* del extended DB
# (santander y hsbc están partidos en varios archivos).
BANCOS = {
    "santander": {
        "etiqueta": "Santander",
        "vistas": [
            "santander_1_cuentas_full", "santander_2_cuentas_full",
            "santander_3_cuentas_full", "santander_5_cuentas_full",
            "santander_6_cuentas_full", "santander_7_cuentas_full",
        ],
    },
    "hsbc": {
        "etiqueta": "HSBC",
        "vistas": ["hsbc_1_cuentas_full", "hsbc_2_cuentas_full"],
    },
    "banorte": {"etiqueta": "Banorte", "vistas": ["banorte_cuentas_full"]},
    "bancomer": {"etiqueta": "Bancomer", "vistas": ["bancomer_cuentas_full"]},
    "citibanamex": {"etiqueta": "Citibanamex", "vistas": ["citibanamex_cuentas_full"]},
    "bancoppel": {"etiqueta": "Bancoppel", "vistas": ["bancoppel_cuentas_full"]},
    "amex": {"etiqueta": "American Express", "vistas": ["amex_cuentas_full"]},
    "clavijero": {"etiqueta": "Clavijero", "vistas": ["clavijero_personas_full"]},
}

# Columnas que se devuelven a la ficha (todas las vistas bancarias comparten
# el mismo contrato; se omiten imei/iccid/marca/modelo/asesor porque son de
# telefonía y aquí sólo generan ruido).
CAMPOS = [
    "rfc", "curp", "cuenta", "telefono",
    "titular_nombre1", "titular_nombre2",
    "titular_domicilio", "titular_numero", "titular_interior",
    "titular_colonia", "titular_ciudad", "titular_estado", "titular_cp",
    "archivo_origen",
]

LIMITE_POR_BANCO = 25


def _norm(texto) -> str:
    """Mayúsculas, sin acentos y con espacios colapsados."""
    if texto is None:
        return ""
    s = unicodedata.normalize("NFKD", str(texto))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().upper()


def _tokens(texto, minimo: int = 3) -> list:
    return [t for t in re.split(r"[^A-Z0-9]+", _norm(texto)) if len(t) >= minimo]


def _clave_cache(rfc10: str, nombre: str, paterno: str, materno: str) -> str:
    return "|".join(_norm(x) for x in (rfc10, paterno, materno, nombre))


def _union_sql(vistas: list) -> str:
    cols = ", ".join(CAMPOS)
    return " UNION ALL ".join(f"SELECT {cols} FROM api.{v}" for v in vistas)


def _consultar(con, vistas: list, cond: str, params: list, limite: int) -> tuple:
    """Devuelve (registros, total, truncado) para un predicado."""
    union = _union_sql(vistas)
    sql = f"SELECT * FROM ({union}) t WHERE {cond} LIMIT {int(limite)}"
    rows = con.execute(sql, params).fetchall()
    cols = [d[0] for d in con.description]
    registros = [dict(zip(cols, r)) for r in rows]
    total = len(registros)
    truncado = total >= limite
    if truncado:
        try:
            total = con.execute(
                f"SELECT COUNT(*) FROM ({union}) t WHERE {cond}", params
            ).fetchone()[0]
        except Exception:
            pass
    return registros, int(total), truncado


def _evalua_nombre(reg: dict, nombre: str, paterno: str, materno: str) -> tuple:
    """Devuelve (score, fuerte, etiqueta) comparando el nombre del banco.

    `score` = fracción de tokens del sujeto presentes en el nombre del banco.
    `fuerte` = están los dos apellidos y además coincide al menos un nombre de
    pila: es un candidato con nombre completo, no un simple homónimo.
    """
    texto = _norm(f"{reg.get('titular_nombre1') or ''} {reg.get('titular_nombre2') or ''}")
    presentes = set(t for t in re.split(r"[^A-Z0-9]+", texto) if t)
    tok_apellidos = _tokens(f"{paterno} {materno}")
    tok_nombre = _tokens(nombre)
    deseados = [t for t in tok_apellidos + tok_nombre if len(t) > 2]
    if not deseados:
        return 0.0, False, "sin nombre"
    score = round(len(presentes & set(deseados)) / len(deseados), 3)
    apellidos_ok = all(t in presentes for t in tok_apellidos) if tok_apellidos else False
    nombre_ok = any(t in presentes for t in tok_nombre) if tok_nombre else False
    if apellidos_ok and nombre_ok:
        return score, True, "apellidos + nombre"
    if apellidos_ok:
        return score, False, "apellidos (homónimo probable)"
    return score, False, "coincidencia parcial"


def mapear_bancos(con, *, curp: str = "", rfc: str = "", nombre: str = "",
                  paterno: str = "", materno: str = "",
                  limite_por_banco: int = LIMITE_POR_BANCO) -> dict:
    """Cruza al sujeto contra TODAS las bases bancarias de una sola pasada.

    Args:
        con: conexión con las vistas api.* (la del servidor: `_init_extended_con()`).
        curp, rfc, nombre, paterno, materno: identidad del sujeto. Con RFC basta;
               sin RFC se exige el par de apellidos (y opcionalmente el nombre).

    Returns:
        dict con `bancos` (una entrada por banco, con criterio y registros),
        `total_coincidencias`, `bancos_con_datos` y `consultas` (con qué se buscó).
    """
    t0 = time.time()
    curp = (curp or "").strip().upper()
    rfc_clean = re.sub(r"[^A-Z0-9]", "", (rfc or "").upper())
    rfc10 = rfc_clean[:10] if len(rfc_clean) >= 10 else ""
    nombre_n = _norm(nombre)
    paterno_n = _norm(paterno)
    materno_n = _norm(materno)

    resultado = {
        "consultas": {
            "rfc": rfc_clean, "rfc10": rfc10, "curp": curp,
            "nombre": nombre_n, "paterno": paterno_n, "materno": materno_n,
        },
        "bancos": {},
        "total_coincidencias": 0,
        "bancos_con_datos": [],
        "criterio_global": "sin_datos",
        "elapsed_s": 0,
    }

    if not rfc10 and not (paterno_n and materno_n):
        resultado["error"] = (
            "sin RFC ni par de apellidos: no hay clave para cruzar las bases bancarias"
        )
        resultado["elapsed_s"] = round(time.time() - t0, 2)
        return resultado

    # Predicado 1: RFC (prefijo = cubre 10 y 13) o CURP exacta.
    cond_rfc, params_rfc = [], []
    if rfc10:
        cond_rfc.append("rfc LIKE ?")
        params_rfc.append(f"{rfc10}%")
    if len(curp) == 18:
        cond_rfc.append("curp = ?")
        params_rfc.append(curp)
    cond_rfc_sql = " OR ".join(f"({c})" for c in cond_rfc) if cond_rfc else "1=0"

    # Predicado 2: ambos apellidos en la misma fila (concat de las dos columnas).
    expr = "UPPER(COALESCE(titular_nombre1,'') || ' ' || COALESCE(titular_nombre2,''))"
    cond_nombre, params_nombre = [], []
    if paterno_n and materno_n:
        cond_nombre += [f"{expr} LIKE ?", f"{expr} LIKE ?"]
        params_nombre += [f"%{paterno_n}%", f"%{materno_n}%"]
    elif paterno_n or materno_n:
        # Un solo apellido es demasiado amplio: se exige además el nombre de pila.
        primer_nombre = next(iter(_tokens(nombre_n)), "")
        if not primer_nombre:
            cond_nombre = []
        else:
            cond_nombre += [f"{expr} LIKE ?", f"{expr} LIKE ?"]
            params_nombre += [f"%{paterno_n or materno_n}%", f"%{primer_nombre}%"]
    cond_nombre_sql = " AND ".join(f"({c})" for c in cond_nombre) if cond_nombre else "1=0"

    for entidad, cfg in BANCOS.items():
        entrada = {
            "etiqueta": cfg["etiqueta"],
            "criterio": "sin_datos",
            "total": 0,
            "truncado": False,
            "registros": [],
        }
        registros, criterio = [], "sin_datos"
        total, truncado = 0, False

        if cond_rfc_sql != "1=0":
            try:
                registros, total, truncado = _consultar(
                    con, cfg["vistas"], cond_rfc_sql, params_rfc, limite_por_banco
                )
                if registros:
                    criterio = "rfc"
            except Exception as e:  # una vista caída no tumba el mapeo
                entrada["error"] = str(e)[:160]
                registros = []

        if not registros and cond_nombre_sql != "1=0":
            try:
                registros, total, truncado = _consultar(
                    con, cfg["vistas"], cond_nombre_sql, params_nombre, limite_por_banco
                )
                if registros:
                    criterio = "nombre"
            except Exception as e:
                entrada["error"] = str(e)[:160]
                registros = []
        if not registros:
            total, truncado = 0, False

        if registros:
            for reg in registros:
                if criterio == "rfc":
                    reg["_coincidencia"] = "RFC"
                    reg["_fuerte"] = True
                else:
                    sc, fuerte, etiqueta = _evalua_nombre(reg, nombre_n, paterno_n, materno_n)
                    reg["_score"] = sc
                    reg["_fuerte"] = fuerte
                    reg["_coincidencia"] = etiqueta
            if criterio == "nombre":
                registros.sort(key=lambda r: (r.get("_fuerte", False), r.get("_score", 0)), reverse=True)
            entrada.update({
                "criterio": criterio, "total": total,
                "truncado": truncado, "registros": registros,
                "fuertes": sum(1 for r in registros if r.get("_fuerte")),
            })
            resultado["bancos_con_datos"].append(entidad)
            resultado["total_coincidencias"] += total

        resultado["bancos"][entidad] = entrada

    if resultado["bancos_con_datos"]:
        por_rfc = [
            e for e in resultado["bancos_con_datos"]
            if resultado["bancos"][e]["criterio"] == "rfc"
        ]
        resultado["criterio_global"] = "rfc" if por_rfc else "nombre"

    resultado["total_fuertes"] = sum(
        int(e.get("fuertes", 0) or 0) for e in resultado["bancos"].values()
    )
    resultado["elapsed_s"] = round(time.time() - t0, 2)
    return resultado
