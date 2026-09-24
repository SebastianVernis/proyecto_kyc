"""Capa 1: Normalización del query.

Parsea "Juan Pérez López" → {paterno, materno, nombre, partículas}.
También detecta fecnac, RFC, estado implícito en la query.
"""
from __future__ import annotations
import re, unicodedata
from datetime import datetime
from .. import ToolDef, ToolContext, register

PARTICULAS = {"DE", "DEL", "LA", "LAS", "LOS", "SAN", "SANTA", "STO", "STA", "VDA", "VIUDA"}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _norm_name(s: str) -> str:
    return _strip_accents(s).upper().strip()


def _normalize(args: dict, ctx: ToolContext) -> dict:
    raw = (args.get("query") or args.get("nombre_completo") or "").strip()
    if not raw:
        return {"error": "se requiere query o nombre_completo", "rows": []}

    # Detectar fecha de nacimiento tipo "1952-09-12" o "12/09/1952"
    fecnac = args.get("fecnac") or None
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", raw)
    if not fecnac and m:
        fecnac = m.group(0)
        raw = (raw[:m.start()] + raw[m.end():]).strip()

    # Detectar estado en la query ("en Oaxaca", "de CDMX")
    estado_texto = None
    m = re.search(r"\b(?:en|de|del)\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?)\s*$", raw, re.IGNORECASE)
    if m:
        estado_texto = m.group(1).upper()
        raw = (raw[:m.start()] + raw[m.end():]).strip().rstrip(",")

    # Tokenizar y separar partículas
    words = [w for w in re.split(r"[\s,]+", _norm_name(raw)) if w]
    # quitar partículas al final
    while words and words[-1] in PARTICULAS:
        words.pop()
    # partir: [nombres..., paterno, materno]  ← convención mexicana
    if len(words) >= 4:
        nombres = words[:-2]
        paterno = words[-2]
        materno = words[-1]
    elif len(words) == 3:
        # no claro si (PADRE, MAT, NOM) o (NOM, PADRE, MAT) — devolvemos variantes
        nombres, paterno, materno = ["?"], words[0], words[1]
        variantes = [
            {"paterno": words[0], "materno": words[1], "nombre": " ".join(words[2:])},
            {"paterno": words[1], "materno": words[0], "nombre": " ".join(words[2:])},
        ]
    elif len(words) == 2:
        paterno, materno = words[0], words[1]
        nombres = []
        variantes = [{"paterno": words[0], "materno": words[1], "nombre": ""}]
    else:
        return {"error": "no se pudo parsear el nombre", "rows": []}

    out = {
        "raw_query": raw,
        "fecnac_detectada": fecnac,
        "estado_texto_detectado": estado_texto,
        "nombres": nombres,
        "paterno": paterno,
        "materno": materno,
        "nombre_completo": " ".join(nombres) if nombres else "",
        "particulas_removidas": [w for w in re.split(r"[\s,]+", _norm_name(raw)) if w in PARTICULAS],
    }
    if len(words) == 3:
        out["variantes_orden_apellidos"] = variantes
    return out


register(ToolDef(
    name="recon_normalize",
    description=(
        "CAPA 1: Normaliza la query de búsqueda. Parsea un nombre completo en "
        "[paterno, materno, nombre], quita partículas (DE, LA, DEL, SAN, SANTA, VDA), "
        "detecta fecnac en formato YYYY-MM-DD o DD/MM/YYYY, y detecta estado en la query "
        "(\"en Oaxaca\", \"de CDMX\"). Devuelve la estructura parseada y, si hay ambigüedad, "
        "variantes del orden de apellidos. SIEMPRE llamar primero."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "texto libre, ej. 'Juan Pérez López de Oaxaca, nacido 1952-09-12'"},
            "fecnac": {"type": "string", "description": "opcional, YYYY-MM-DD"},
        },
        "required": ["query"],
    },
    fn=_normalize,
    cost_hint="low",
))
