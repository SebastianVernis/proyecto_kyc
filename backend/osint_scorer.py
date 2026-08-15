#!/usr/bin/env python3
"""osint_scorer.py — Scoring determinista para resultados OSINT.

Cruza resultados crudos de Apify contra:
- Nombre completo del padrón (nombre, paterno, materno)
- Email fiscal de CheckID
- Edad estimada por fecha de nacimiento
- Estado/Municipio/Localidad del padrón

Devuelve solo perfiles con coincidencia fuerte (score >= 60) o marca
"sin_coincidencias_determinantes" cuando ninguno alcanza umbral.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any


def normalizar(txt: str) -> str:
    if not txt:
        return ""
    txt = str(txt).lower().strip()
    # quitar tildes
    for a, b in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"),
                 ("ñ", "n"), ("ü", "u")]:
        txt = txt.replace(a, b)
    return re.sub(r"[^a-z0-9\s]", " ", txt)


def tokens(txt: str) -> set[str]:
    return {t for t in normalizar(txt).split() if len(t) >= 2}


def calcular_edad(fecha_nacimiento: str | None) -> int | None:
    """Calcula edad a partir de YYYY-MM-DD o YYYYMMDD."""
    if not fecha_nacimiento:
        return None
    f = str(fecha_nacimiento).replace("-", "").replace("/", "")
    if len(f) != 8 or not f.isdigit():
        return None
    try:
        nac = datetime.strptime(f, "%Y%m%d")
        hoy = datetime.now()
        return hoy.year - nac.year - ((hoy.month, hoy.day) < (nac.month, nac.day))
    except Exception:
        return None


def score_nombre(perfil: dict, sujeto: dict) -> int:
    """Score 0-100 basado en coincidencia de nombre."""
    n = normalizar(sujeto.get("nombre", ""))
    p = normalizar(sujeto.get("paterno", ""))
    m = normalizar(sujeto.get("materno", ""))
    full_sujeto = f"{n} {p} {m}".strip()

    candidatos = []
    for key in ["fullName", "full_name", "name", "username", "biography", "headline"]:
        val = perfil.get(key)
        if val:
            candidatos.append(normalizar(val))

    if not candidatos or not (n or p):
        return 0

    mejor = 0
    for c in candidatos:
        c_tokens = tokens(c)
        if not c_tokens:
            continue
        puntos = 0
        matches = 0
        # nombre exacto en el texto completo
        if n and len(n) >= 3 and n in c:
            puntos += 40
            matches += 1
        elif n and n in c_tokens:
            puntos += 25
            matches += 1

        # paterno exacto
        if p and len(p) >= 3 and p in c:
            puntos += 35
            matches += 1
        elif p and p in c_tokens:
            puntos += 20
            matches += 1

        # materno exacto
        if m and len(m) >= 3 and m in c:
            puntos += 25
            matches += 1
        elif m and m in c_tokens:
            puntos += 15
            matches += 1

        # bonus por nombre completo concatenado (username = nombre + numeros)
        # o si el username contiene nombre y paterno juntos
        username = normalizar(str(perfil.get("username", "")))
        if n and p and (n + p in username or p + n in username):
            puntos += 10

        # penalización fuerte si solo coincide un nombre común sin paterno
        if matches == 1 and n in c and not p and not m:
            puntos = max(0, puntos - 40)

        # bonus si el nombre completo del sujeto aparece junto
        if full_sujeto and full_sujeto in c:
            puntos += 15

        mejor = max(mejor, min(100, puntos))

    return mejor


def score_email(perfil: dict, email_fiscal: str | None) -> int:
    if not email_fiscal:
        return 0
    email_norm = email_fiscal.lower().strip()
    user_fiscal = email_norm.split("@")[0]

    # buscar email exacto en cualquier campo
    def buscar(obj: Any) -> bool:
        if isinstance(obj, str):
            return obj.lower().strip() == email_norm
        if isinstance(obj, list):
            return any(buscar(v) for v in obj)
        if isinstance(obj, dict):
            return any(buscar(v) for v in obj.values())
        return False

    if buscar(perfil):
        return 100

    # username del perfil
    username = str(perfil.get("username") or "").lower().strip()
    if username:
        if username == user_fiscal:
            return 90
        if user_fiscal in username or username in user_fiscal:
            return 50

    # public_email del perfil
    pub = str(perfil.get("public_email") or perfil.get("publicEmail") or "").lower().strip()
    if pub == email_norm:
        return 100

    return 0


def score_demografia(perfil: dict, sujeto: dict) -> int:
    puntos = 0
    edad = calcular_edad(sujeto.get("fecnac"))
    if edad is None:
        return 0

    bio = normalizar(str(perfil.get("biography", "")) + " " + str(perfil.get("fullName", "")))

    # palabras clave de etapa de vida adulta/profesional
    profesiones = ["abogado", "ingeniero", "medico", "doctor", "licenciado", "empresario",
                   "docente", "maestro", "contador", "arquitecto", "profesor", "administrador",
                   "director", "gerente", "consultor", "analista", "ejecutivo"]
    if any(x in bio for x in profesiones):
        puntos += 10

    # ubicación
    estados = {
        "aguascalientes": "01", "baja california": "02", "baja california sur": "03",
        "campeche": "04", "coahuila": "05", "colima": "06", "chiapas": "07",
        "chihuahua": "08", "ciudad de mexico": "09", "cdmx": "09", "durango": "10",
        "guanajuato": "11", "guerrero": "12", "hidalgo": "13", "jalisco": "14",
        "mexico": "15", "estado de mexico": "15", "edomex": "15", "michoacan": "16",
        "morelos": "17", "nayarit": "18", "nuevo leon": "19", "oaxaca": "20",
        "puebla": "21", "queretaro": "22", "quintana roo": "23", "san luis potosi": "24",
        "sinaloa": "25", "sonora": "26", "tabasco": "27", "tamaulipas": "28",
        "tlaxcala": "29", "veracruz": "30", "yucatan": "31", "zacatecas": "32",
    }
    bio_location = normalizar(str(perfil.get("biography", "")) + " " +
                              str(perfil.get("city", "")) + " " +
                              str(perfil.get("country", "")))
    edo_sujeto = normalizar(str(sujeto.get("estado", "")))
    for nombre_edo, codigo in estados.items():
        if nombre_edo in bio_location:
            if codigo in str(sujeto.get("estado", "")) or nombre_edo in edo_sujeto:
                puntos += 20
            else:
                puntos += 5
            break

    return min(40, puntos)


def score_perfil(perfil: dict, sujeto: dict, email_fiscal: str | None = None) -> dict:
    s_nombre = score_nombre(perfil, sujeto)
    s_email = score_email(perfil, email_fiscal)
    s_demo = score_demografia(perfil, sujeto)

    # Email exacto es muy determinante
    if s_email >= 80:
        score_final = min(100, 80 + s_nombre * 0.2)
    else:
        score_final = min(100, s_nombre * 0.7 + s_email * 0.2 + s_demo * 0.1)

    return {
        "score": round(score_final, 1),
        "score_nombre": s_nombre,
        "score_email": s_email,
        "score_demografia": s_demo,
        "determinante": score_final >= 60 or s_email >= 80,
    }


def filtrar_resultados(resultados: list[dict], sujeto: dict,
                       email_fiscal: str | None = None,
                       umbral: float = 60.0,
                       max_perfiles: int = 5) -> dict:
    """Filtra resultados crudos y devuelve solo los determinantes."""
    scored = []
    for item in resultados:
        if not isinstance(item, dict):
            continue
        if item.get("error"):
            continue
        sc = score_perfil(item, sujeto, email_fiscal)
        item["_score"] = sc
        scored.append(item)

    scored.sort(key=lambda x: x["_score"]["score"], reverse=True)

    determinantes = [p for p in scored if p["_score"]["determinante"]]
    otros = [p for p in scored if not p["_score"]["determinante"]]

    if not determinantes:
        top = scored[:max_perfiles]
        return {
            "sin_coincidencias_determinantes": True,
            "umbral": umbral,
            "perfiles": top,
            "total_evaluados": len(scored),
            "mensaje": "Ningún perfil alcanzó coincidencia fuerte con el nombre/email fiscal del sujeto. Los mostrados son los más cercanos con baja confianza.",
        }

    return {
        "sin_coincidencias_determinantes": False,
        "umbral": umbral,
        "perfiles": determinantes[:max_perfiles],
        "total_evaluados": len(scored),
        "total_descartados": len(otros),
        "mensaje": f"{len(determinantes)} perfiles con coincidencia fuerte (score >= {umbral}).",
    }
