#!/usr/bin/env python3
"""rfc_utils.py — utilidades para cálculo de RFC según algoritmo SAT (IFAI folio 0610100135506).

El RFC se calcula determinísticamente desde la CURP:
  RFC = (letra_nombre + 2_letras_paterno + 1_letra_materno + AA + MM + DD + 3_homoclave) + DV

Homoclave: 3 caracteres alfanuméricos asignados por SAT al momento de inscripción.
Para calcular el RFC sin homoclave, se usan las primeras 2 consonantes internas
del nombre completo (no solo de los apellidos) + 1 letra de paterno + 1 de materno.

Para evitar errores, preferimos:
  1. Si el padrón nos da el RFC directamente, lo usamos
  2. Si no, llamamos a Singula /app/rfc/validate (que tiene la homoclave real)
  3. Si no, calculamos con las reglas del SAT
"""

from __future__ import annotations

import re

# Tabla del SAT (Anexo III, IFAI folio 0610100135506)
# BLANCO=37, Ñ=38, & =24 (NO se usa en RFC, solo en homonimia)
TABLA_SAT = {
    "0": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
    "A": 10, "B": 11, "C": 12, "D": 13, "E": 14, "F": 15, "G": 16, "H": 17,
    "I": 18, "J": 19, "K": 20, "L": 21, "M": 22, "N": 23, "&": 24, "O": 25,
    "P": 26, "Q": 27, "R": 28, "S": 29, "T": 30, "U": 31, "V": 32, "W": 33,
    "X": 34, "Y": 35, "Z": 36, " ": 37, "Ñ": 38,
}

# Caracteres inválidos en RFC (se omiten)
CARACTERES_INVALIDOS = re.compile(r"[^A-Z0-9Ñ&]")


def _normalizar_nombre(nombre: str) -> str:
    """Mayúsculas, sin acentos, sin espacios extra."""
    if not nombre:
        return ""
    n = nombre.upper().strip()
    # quitar acentos
    n = (n.replace("Á", "A").replace("É", "E").replace("Í", "I")
         .replace("Ó", "O").replace("Ú", "U").replace("Ü", "U"))
    n = CARACTERES_INVALIDOS.sub(" ", n)
    return " ".join(n.split())


def _primera_vocal_interna(s: str) -> str:
    """Primera vocal dentro de la palabra (después de la primera letra)."""
    if len(s) < 2:
        return ""
    for c in s[1:]:
        if c in "AEIOU":
            return c
    return "X"


def _primera_consonante_interna(s: str) -> str:
    """Primera consonante interna (después de la primera letra)."""
    if len(s) < 2:
        return "X"
    for c in s[1:]:
        if c in "BCDFGHJKLMNPQRSTVWXYZ":
            return c
    return "X"


def _calcular_dv(rfc_sin_dv: str) -> str:
    """Calcula el dígito verificador del RFC según SAT (suma ponderada módulo 11)."""
    if len(rfc_sin_dv) != 12:
        return ""
    # mapa de multiplicadores: 13, 12, 11, ..., 2
    multiplicadores = list(range(13, 1, -1))
    total = 0
    for i, c in enumerate(rfc_sin_dv):
        val = TABLA_SAT.get(c, 0)
        total += val * multiplicadores[i]
    residuo = total % 11
    dv_num = 11 - residuo
    if dv_num == 11:
        return "0"
    if dv_num == 10:
        return "A"
    return str(dv_num)


def calcular_rfc_desde_curp(curp: str, *, nombre: str = "", paterno: str = "",
                            materno: str = "", fecnac: str = "") -> str:
    """Calcula el RFC a 12 chars (sin DV) desde CURP o datos demográficos.

    Prioridad de datos:
      1. Si tenemos CURP válida (18 chars), extraemos de ahí:
         - posiciones 0-3: nombre + 2 letras paterno + 1 materna
         - posiciones 4-9: fecnac AAMMDD
         - posiciones 10-12: homoclave parcial (en realidad no, sólo 3 chars)
         PERO la CURP trae 3 chars (10-12) que NO son la homoclave completa
      2. Si no, usamos nombre + paterno + materno + fecnac.

    La homoclave real (3 chars) NO se puede calcular; requiere consulta al SAT.
    Esta función devuelve el RFC a 12 chars (sin DV), útil para que Singula
    lo complete con la homoclave real vía /app/rfc/validate.
    """
    curp = (curp or "").upper().strip()

    if len(curp) == 18 and curp[4:6].isdigit():
        # Extraer del CURP
        # RFC esperado: 4 letras (NOM+AP+AM) + 6 dígitos (AAMMDD) + 3 homoclave
        # En la CURP: 0=N, 1=vocal_interna_paterno, 2=consonante_interna_paterno? no, 2=inicial_materna, 3=consonante_interna_materna
        # En realidad la CURP trae más info:
        # 0: inicial nombre
        # 1: primera vocal interna paterno
        # 2: inicial materno
        # 3: consonante interna materno
        # 4-9: AAMMDD
        # 10-11: entidad federativa
        # 12: primera consonante interna del nombre completo
        # 13: segunda consonante interna
        # 14-15: año registro
        # 16-17: folio
        # => para RFC solo necesitamos posiciones 0-9
        rfc_10 = curp[:10]  # 4 letras + 6 dígitos
        return rfc_10

    # calcular desde datos
    nombre = _normalizar_nombre(nombre)
    paterno = _normalizar_nombre(paterno)
    materno = _normalizar_nombre(materno)

    if not paterno or not nombre:
        return ""

    # evitar MARIA / JOSE en nombres compuestos para RFC
    partes_nombre = nombre.split()
    if partes_nombre and partes_nombre[0] in ("MARIA", "JOSE", "MA", "J"):
        partes_nombre = partes_nombre[1:] if len(partes_nombre) > 1 else partes_nombre
    nombre_para_rfc = partes_nombre[0] if partes_nombre else ""

    # primeras letras
    letra_nombre = nombre_para_rfc[0] if nombre_para_rfc else "X"
    letra_pat_1 = paterno[0] if paterno else "X"
    letra_pat_2 = _primera_vocal_interna(paterno) if paterno else "X"
    letra_mat = (materno[0] if materno else "X")

    # fecha
    if not fecnac:
        return ""
    f = fecnac.replace("-", "").replace("/", "")
    if len(f) != 8:
        return ""
    aa = f[2:4]
    mm = f[4:6]
    dd = f[6:8]

    # 10 primeros chars
    rfc_10 = f"{letra_nombre}{letra_pat_1}{letra_pat_2}{letra_mat}{aa}{mm}{dd}"
    return rfc_10


def rfc_completo(rfc_10: str, *, homoclave: str = "") -> str:
    """Toma los 10 primeros chars del RFC y agrega la homoclave + DV.

    Si homoclave está vacío, devuelve rfc_10 + "XXX" + DV (sólo útil como placeholder).
    Si homoclave tiene 3 chars, devuelve rfc_10 + homoclave + DV.
    """
    if not rfc_10 or len(rfc_10) != 10:
        return ""
    h = (homoclave or "XXX").upper()[:3].ljust(3, "X")
    rfc_13 = f"{rfc_10}{h}"
    dv = _calcular_dv(rfc_13)
    return f"{rfc_13}{dv}"


def validar_rfc(rfc: str) -> dict:
    """Valida el DV de un RFC dado.

    Returns:
        {"valid": bool, "rfc": str, "dv_match": str, "expected_dv": str, "homoclave": str}
    """
    if not rfc:
        return {"valid": False, "error": "RFC vacío"}
    rfc = rfc.upper().strip()
    if len(rfc) != 13:
        return {"valid": False, "error": f"RFC debe tener 13 chars (tiene {len(rfc)})"}
    rfc_12 = rfc[:12]
    dv = rfc[12]
    expected = _calcular_dv(rfc_12)
    return {
        "valid": dv == expected,
        "rfc": rfc,
        "dv_received": dv,
        "dv_expected": expected,
        "homoclave": rfc_12[10:13],
        "matches_dv": dv == expected,
    }
