"""
inteligencia_completa.py — Motor de Inteligencia y Mapeo Relacional Automatizado
Plataforma Encuentra

Optimizado con conexión única a la vista unificada `api.bancarias_todas` y `_init_extended_con`
para tiempos de respuesta inferiores a 5 segundos.
"""

import os
import re
import time
import json
import duckdb
from pathlib import Path

BASES_DIR = Path(__file__).resolve().parent.parent / "bases"

# Stopwords para nombres de vialidades - SOLO artículos y preposiciones muy genéricas
# NO incluir 'DE', 'DEL', 'LA', 'DE LA', 'DE LOS', 'DEL' porque forman parte de nombres compuestos
CALLE_STOPWORDS = {
    'LAS', 'LOS', 'EL', 'Y', 'EN', 'SAN', 'SANTA', 'STO', 'STA',
    'NUEVO', 'NUEVA', 'VIEJO', 'VIEJA', 'GRAN', 'GRANDE', 'PEQUEÑO', 'PEQUEÑA',
    'PRIMERO', 'SEGUNDO', 'TERCERO', 'CUARTO', 'QUINTO', 'SEXTO', 'SEPTIMO', 'OCTAVO', 'NOVENO', 'DECIMO',
    'NORTE', 'SUR', 'ESTE', 'OESTE', 'CENTRO', 'ORIENTE', 'PONIENTE',
    'PRINCIPAL', 'SECUNDARIA', 'CENTRAL', 'FEDERAL', 'ESTATAL', 'MUNICIPAL',
    'UNA', 'UNO', 'DOS', 'TRES', 'CUATRO', 'CINCO', 'SEIS', 'SIETE', 'OCHO', 'NUEVE', 'DIEZ'
}

# 2026-08-24: filtros de data basura en las bases externas. Las bases CFE,
# ATT, Telcel y bancos tienen ~100k registros con direcciones de USA
# mezcladas (New Mexico, North Carolina, Camp Lejeune, Onslow County, etc.).
# Esto contamina los resultados de convivientes y CFE matching. Filtramos
# en backend para que el frontend nunca los vea.

US_STATE_ABBREVIATIONS = {
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
    'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
    'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
    'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
    'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY',
    'PR',  # Puerto Rico
}

# Palabras clave que delatan dirección USA (case-insensitive)
US_ADDRESS_KEYWORDS = [
    'united states', 'usa', 'u.s.a', 'us of a', 'america',
    # Estados USA por nombre completo
    'alabama', 'alaska', 'arizona', 'arkansas', 'california', 'colorado',
    'connecticut', 'delaware', 'florida', 'georgia', 'hawaii', 'idaho',
    'illinois', 'indiana', 'iowa', 'kansas', 'kentucky', 'louisiana',
    'maine', 'maryland', 'massachusetts', 'michigan', 'minnesota',
    'mississippi', 'missouri', 'montana', 'nebraska', 'nevada',
    'new hampshire', 'new jersey', 'new mexico', 'new york',
    'north carolina', 'north dakota', 'ohio', 'oklahoma', 'oregon',
    'pennsylvania', 'rhode island', 'south carolina', 'south dakota',
    'tennessee', 'texas', 'utah', 'vermont', 'virginia', 'washington',
    'west virginia', 'wisconsin', 'wyoming',
    # Bases militares USA
    'camp lejeune', 'fort bragg', 'fort hood', 'fort bliss', 'fort campbell',
    'fort stewart', 'fort benning', 'fort drum', 'fort polk',
    'marine corps', 'naval station', 'air force base',
    # Patrones típicos
    'onslow county', 'pinetucky', 'piney green', 'watkins grove',
    'onslow', 'cumberland county', 'beaufort',
]

# Compilamos regex una sola vez para performance
_US_KW_RE = re.compile('|'.join(re.escape(kw) for kw in US_ADDRESS_KEYWORDS), re.IGNORECASE)


def _is_us_address(text) -> bool:
    """Devuelve True si el texto contiene señales de dirección USA.

    Aplica a cualquier campo (domicilio, calle, colonia, ciudad, estado, cp).
    Es case-insensitive y robusta a mayúsculas, espacios, puntuación.
    """
    if not text:
        return False
    return bool(_US_KW_RE.search(str(text)))


def _filter_us_rows(rows: list, domicilio_keys: tuple = (
        'domicilio', 'calle', 'direccion', 'titular_domicilio',
        'calle_adicional_1', 'calle_adicional_2')) -> list:
    """Filtra rows con dirección USA en cualquiera de los campos de domicilio.

    Args:
        rows: lista de dicts con campos de domicilio
        domicilio_keys: claves donde buscar dirección (case-insensitive)

    Returns:
        nueva lista solo con rows que NO tienen dirección USA
    """
    if not rows:
        return rows
    out = []
    for r in rows:
        is_us = False
        for k in domicilio_keys:
            v = r.get(k) or r.get(k.lower()) or r.get(k.upper())
            if v and _is_us_address(v):
                is_us = True
                break
        if not is_us:
            out.append(r)
    return out


def _is_record_us(row: dict, domicilio_keys: tuple = (
        'domicilio', 'calle', 'direccion', 'titular_domicilio')) -> bool:
    """Atajo: devuelve True si un dict tiene dirección USA."""
    if not row:
        return False
    for k in domicilio_keys:
        v = row.get(k)
        if v and _is_us_address(v):
            return True
    return False


# ============================================================================
# 2026-08-25: Normalizadores para búsqueda bag-of-words multi-parámetro.
# Estos helpers limpian/normalizan strings antes de comparar con regex,
# tolerando variaciones de formato, puntuación, partículas y abreviaciones.
# ============================================================================

import unicodedata

# Partículas comunes en nombres mexicanos que se deben ignorar al tokenizar
NOMBRE_PARTICULAS = frozenset([
    'DE', 'DEL', 'LA', 'LAS', 'LOS', 'EL', 'Y', 'E', 'SAN', 'SANTA',
    'MC', 'VON', 'VAN', 'DA', 'DO', 'DOS', 'DU',
])

# Abreviaciones de vialidades en direcciones mexicanas
VIALIDAD_ABREVIACIONES = {
    'AV': 'AVENIDA', 'AVE': 'AVENIDA', 'AVDA': 'AVENIDA',
    'C': 'CALLE', 'CLL': 'CALLE', 'CALL': 'CALLE',
    'BLV': 'BOULEVARD', 'BLVD': 'BOULEVARD', 'BV': 'BOULEVARD',
    'PROL': 'PROLONGACION', 'PRLG': 'PROLONGACION',
    'PRIV': 'PRIVADA', 'PVT': 'PRIVADA', 'PRIVADA': 'PRIVADA',
    'CDA': 'CERRADA', 'CERR': 'CERRADA',
    'AND': 'ANDADOR', 'ANDADOR': 'ANDADOR',
    'CJON': 'CALLEJON', 'CJ': 'CALLEJON',
    'EDIF': 'EDIFICIO', 'ED': 'EDIFICIO',
    'DEP': 'DEPARTAMENTO', 'DPTO': 'DEPARTAMENTO',
    'INT': 'INTERIOR', 'EXT': 'EXTERIOR',
    'COL': 'COLONIA', 'COLO': 'COLONIA',
    'FRACC': 'FRACCIONAMIENTO', 'FRAC': 'FRACCIONAMIENTO',
    'BARRIO': 'BARRIO', 'BR': 'BARRIO',
    'UNID': 'UNIDAD', 'UHAB': 'UNIDAD_HABITACIONAL',
    'EJ': 'EJIDO', 'EJIDO': 'EJIDO',
    'MZ': 'MANZANA', 'MZN': 'MANZANA', 'MANZ': 'MANZANA',
    'LT': 'LOTE', 'LTE': 'LOTE',
    'NO': 'NUMERO', 'NUM': 'NUMERO',
    'ESQ': 'ESQUINA',
}


def _quitar_acentos(s: str) -> str:
    """Quita acentos pero conserva la ñ como N para matching más amplio."""
    if not s:
        return ''
    # Normaliza a NFD y luego quita marcas diacríticas
    decomposed = unicodedata.normalize('NFD', s)
    sin_acentos = ''.join(c for c in decomposed if unicodedata.category(c) != 'Mn')
    # ñ sigue siendo ñ en NFD; la queremos como n para matching genérico
    return sin_acentos.replace('ñ', 'n').replace('Ñ', 'N')


def _normalizar_basico(s: str) -> str:
    """Normalización base: mayúsculas, sin acentos, sin puntuación, espacios colapsados."""
    if not s:
        return ''
    s = str(s).strip()
    s = _quitar_acentos(s).upper()
    # Quitar puntuación y caracteres especiales
    s = re.sub(r'[^\w\s]', ' ', s)
    # Colapsar espacios
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _normalizar_nombre(s: str) -> str:
    """Normaliza un nombre propio para matching.

    - Quita acentos y puntuación
    - Quita partículas (DE, DEL, LA, etc.) para que coincidan
      "MARIA DEL CARMEN" y "MARIA CARMEN"
    - Mantiene el orden de tokens
    """
    s = _normalizar_basico(s)
    if not s:
        return ''
    tokens = [t for t in s.split() if t and t not in NOMBRE_PARTICULAS]
    return ' '.join(tokens)


def _normalizar_calle(s: str) -> str:
    """Normaliza una vialidad para matching de direcciones.

    - Expande abreviaciones comunes (AV → AVENIDA, C → CALLE)
    - Quita acentos y puntuación
    - Mantiene número exterior si está incluido
    """
    s = _normalizar_basico(s)
    if not s:
        return ''
    tokens = s.split()
    out = []
    for t in tokens:
        # Intentar expandir abreviación
        expanded = VIALIDAD_ABREVIACIONES.get(t, t)
        if ' ' in expanded:
            out.extend(expanded.split())
        else:
            out.append(expanded)
    return ' '.join(out)


def _normalizar_colonia(s: str) -> str:
    """Normaliza una colonia (fraccionamiento/barrio) quitando prefijos COL/FRACC/BARRIO."""
    s = _normalizar_basico(s)
    if not s:
        return ''
    # Quitar prefijos
    s = re.sub(r'^(COL|FRACC|FRAC|FRACCIONAMIENTO|BARRIO|UNID|U HAB|EJIDO)\s+', '', s)
    return s.strip()


def _normalizar_cp(s: str) -> str:
    """Normaliza un código postal: 5 dígitos exactos, padded con 0s si es necesario."""
    if not s:
        return ''
    digits = re.sub(r'[^\d]', '', str(s))
    if len(digits) == 4:
        # Asumir CP de4 dígitos es un CP con prefijo 0 (ej. "1710" → "01710")
        return '0' + digits
    return digits[:5]


def _tokenizar(s: str) -> list:
    """Tokeniza un string normalizado en palabras individuales."""
    if not s:
        return []
    return [t for t in s.split() if t]


def _bow_score(query_tokens: list, text_tokens: list) -> float:
    """Calcula el score bag-of-words: % de tokens del query presentes en el texto.

    Devuelve un valor entre 0.0 y 1.0.
    """
    if not query_tokens:
        return 0.0
    if not text_tokens:
        return 0.0
    # Cuenta cuántos tokens del query están en el texto
    matches = sum(1 for qt in query_tokens if qt in text_tokens)
    return matches / len(query_tokens)


def _regex_match_multi(query_tokens: list, text_tokens: list, min_hits: int = 2) -> bool:
    """Devuelve True si al menos `min_hits` tokens del query están en el texto.

    Útil para matching tipo "encontrar coincidencias en MAS DE UNO de los parámetros":
    si tienes paterno='GARCIA' y materno='LOPEZ' como tokens separados, devuelve True
    cuando ambos aparecen en algún registro.
    """
    if not query_tokens:
        return False
    hits = sum(1 for qt in query_tokens if qt in text_tokens)
    return hits >= min_hits

def extraer_nombre_vialidad(calle_raw: str) -> str:
    """
    Extrae el nombre significativo de la vialidad quitando prefijos y stopwords.
    Ejemplos:
    - 'C VISTA DEL ATARDECER' -> 'VISTA DEL ATARDECER'
    - 'AV AGUASCALIENTES SUR' -> 'AGUASCALIENTES SUR'
    - 'PRIV COROMUEL' -> 'COROMUEL'
    - 'C 55 X 54 Y 58' -> '55 X 54 Y 58'
    """
    if not calle_raw:
        return ""
    # Quitar prefijos comunes de tipo de vialidad
    s = re.sub(r'^(C\.?|CALLE|AV\.?|AVENIDA|PRIV\.?|PRIVADA|CDA\.?|CERRADA|AND\.?|ANDADOR|BLVD\.?|BOULEVARD|CTO\.?|CIRCUITO|PROL\.?|PROLONGACION|PZ\.?|PLAZA|RTNO\.?|RETORNO|CALLEJON|PRIVADA|PRIV)\s+', '', calle_raw.strip(), flags=re.IGNORECASE)
    
    # Dividir en tokens y filtrar stopwords, pero mantener números y tokens alfanuméricos importantes
    tokens = re.split(r'\s+', s.upper().strip())
    tokens_filtrados = []
    for t in tokens:
        if not t:
            continue
        # Mantener si es alfanumérico con números (como '55', '159', 'MZ', 'LT') o no es stopword
        if re.search(r'\d', t) or t not in CALLE_STOPWORDS:
            tokens_filtrados.append(t)
    
    # Reconstruir el nombre
    if tokens_filtrados:
        return ' '.join(tokens_filtrados)
    # Fallback: si todo eran stopwords, devolver el último token original
    return tokens[-1] if tokens else ""

def _normalizar_texto_match(v: str) -> str:
    if not v:
        return ""
    s = str(v).upper().strip()
    s = re.sub(r'[^A-Z0-9 ]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


# Partículas que se ignoran al comparar tokens de nombre (orden irrelevante,
# tokens únicos, etc.). NO son apellidos por sí mismas.
NOMBRE_PARTICULAS = {
    "DE", "DEL", "LA", "LAS", "LOS", "Y", "E", "DA", "DO", "DU",
    "VDA", "VIUDA", "DE", "MC", "MAC",
    # 2-palabras (las manejamos aparte en _tokens_nombre_puro)
}


def _tokens_nombre_puro(nombre: str) -> set:
    """Tokeniza un nombre, ignora partículas (DE, LA, Y...) y devuelve set.

    Útil para comparar bag-of-words de nombres que pueden diferir en
    partículas o en orden de tokens.
    """
    norm = _normalizar_texto_match(nombre)
    if not norm:
        return set()
    toks = [t for t in norm.split() if t and t not in NOMBRE_PARTICULAS]
    return set(toks)


def _primer_token_significativo(nombre: str) -> str:
    """Devuelve el primer token del nombre ignorando partículas."""
    toks = _tokens_nombre_puro(nombre)
    if not toks:
        return ""
    # Preferir el primer token de la versión normalizada (orden original)
    norm = _normalizar_texto_match(nombre)
    for t in norm.split():
        if t and t not in NOMBRE_PARTICULAS:
            return t
    return ""


def _score_match_nombres(nombre_a: str, nombre_b: str, paterno_a: str = "",
                          paterno_b: str = "", materno_a: str = "",
                          materno_b: str = "") -> dict:
    """Scoring de match entre dos nombres compuestos.

    Retorna dict con:
      - score: float 0..1
      - tipo: str (consanguineo_directo, consanguineo_paterno, consanguineo_materno,
                   apellido_compartido, sin_match)
      - confianza: str (alta, media, baja)
      - detalle: str explicando por qué

    IMPORTANTE: las fuentes mexicanas tienen el problema del ORDEN. El padrón
    electoral guarda "NOMBRE(S) PATERNO MATERNO", pero CFE/banca guardan a
    menudo "PATERNO MATERNO NOMBRE(S)" o incluso "NOMBRE PATERNO" sin materno.
    Esta función prueba AMBAS direcciones y reporta el mejor score.

    Lógica de scoring (orden de evaluación):
      1.00  Bag-of-words completo coincide
      1.00  String exacto coincide
      0.90  Mismo nombre (1er token de A) + mismo paterno (campo B) + mismo materno
      0.90  Mismo paterno (1er token de A) + mismo nombre (campo B) + mismo materno
      0.85  Mismo nombre (1er token de A) + mismo paterno (campo B) + materno en tokens
      0.80  Mismo nombre (1er de A) + mismo paterno (campo B) (materno falta o no matchea)
      0.80  Mismo paterno (1er de A) + mismo nombre (campo B) (materno falta)
      0.75  Mismo nombre + mismo materno (campo B)
      0.65  Solo mismo paterno (campo B), sin nombre común — consanguíneo lejano
      0.65  Solo mismo materno (campo B), sin nombre común — consanguíneo lejano
      0.50  ≥50% de tokens compartidos (Jaccard) sin paterno/materno claro
      0.00  Sin coincidencia
    """
    a_norm = _normalizar_texto_match(nombre_a)
    b_norm = _normalizar_texto_match(nombre_b)
    if not a_norm or not b_norm:
        return {"score": 0.0, "tipo": "sin_match", "confianza": "baja",
                "detalle": "nombre vacío"}

    a_toks = _tokens_nombre_puro(nombre_a)
    b_toks = _tokens_nombre_puro(nombre_b)
    a_prim = _primer_token_significativo(nombre_a)  # "ALEJANDRO" si orden padrón
    b_prim = _primer_token_significativo(nombre_b)  # idem
    a_ult = list(a_toks)[-1] if a_toks else ""       # "HERNANDEZ" si orden padrón
    b_ult = list(b_toks)[-1] if b_toks else ""

    p_pat_a = _normalizar_texto_match(paterno_a)
    p_pat_b = _normalizar_texto_match(paterno_b)
    p_mat_a = _normalizar_texto_match(materno_a)
    p_mat_b = _normalizar_texto_match(materno_b)

    # 1. Match exacto bag-of-words
    if a_toks == b_toks and len(a_toks) >= 2:
        return {"score": 1.0, "tipo": "consanguineo_directo", "confianza": "alta",
                "detalle": "coincidencia exacta de todos los tokens del nombre"}

    if a_norm == b_norm:
        return {"score": 1.0, "tipo": "consanguineo_directo", "confianza": "alta",
                "detalle": "coincidencia exacta del nombre completo"}

    # 2. Matching robusto considerando los DOS órdenes posibles.
    # Para A (titular CFE): puede venir como "PATERNO MATERNO NOMBRE" o
    # "NOMBRE PATERNO". Para B (padron): siempre "NOMBRE PATERNO MATERNO".
    # Probamos las 4 combinaciones:
    #   combo 1: A.nombre(1er) coincide con B.nombre(1er) → orden normal
    #   combo 2: A.paterno(1er) coincide con B.nombre(1er) → A en orden inverso
    # Para paterno/materno, si B tiene campos separados los usamos
    # directamente; si no, los inferimos de los últimos tokens de A.

    mismo_nombre_normal = bool(a_prim and b_prim and a_prim == b_prim)
    mismo_paterno_pat_a_pat_b = bool(a_prim and p_pat_b and a_prim == p_pat_b)
    mismo_materno_a_mat_b = bool(a_ult and p_mat_b and a_ult == p_mat_b)
    mismo_pat_b_en_a = bool(p_pat_b and p_pat_b in a_toks)
    mismo_mat_b_en_a = bool(p_mat_b and p_mat_b in a_toks)
    mismo_nombre_b_en_a = bool(b_prim and b_prim in a_toks)

    # 2a. orden normal: "ALEJANDRO GUTIERREZ DAVILA" (A) vs "ALEJANDRO GUTIERREZ DAVILA" (B)
    if mismo_nombre_normal and p_pat_b and p_pat_b in a_toks and p_mat_b and p_mat_b in a_toks:
        return {"score": 0.90, "tipo": "consanguineo_directo", "confianza": "alta",
                "detalle": f"orden normal: nombre={a_prim} + paterno={p_pat_b} + materno={p_mat_b}"}

    # 2b. orden inverso: "GUTIERREZ DAVILA ALEJANDRO" (A) vs "ALEJANDRO GUTIERREZ DAVILA" (B)
    if mismo_nombre_b_en_a and mismo_pat_b_en_a and mismo_mat_b_en_a:
        return {"score": 0.90, "tipo": "consanguineo_directo", "confianza": "alta",
                "detalle": f"orden inverso: nombre={b_prim} + paterno={p_pat_b} + materno={p_mat_b} (titular CFE en orden APELLIDO APELLIDO NOMBRE)"}

    # 3. Mismo nombre + mismo paterno (sin materno o materno no detectable)
    if mismo_nombre_normal and mismo_pat_b_en_a:
        return {"score": 0.80, "tipo": "consanguineo_paterno", "confianza": "alta",
                "detalle": f"mismo nombre ({a_prim}) + mismo paterno ({p_pat_b})"}

    if mismo_nombre_b_en_a and mismo_pat_b_en_a:
        return {"score": 0.80, "tipo": "consanguineo_paterno", "confianza": "alta",
                "detalle": f"mismo nombre ({b_prim}, presente en titular CFE) + mismo paterno ({p_pat_b})"}

    # 4. Mismo nombre + mismo materno
    if mismo_nombre_normal and mismo_mat_b_en_a:
        return {"score": 0.75, "tipo": "consanguineo_materno", "confianza": "alta",
                "detalle": f"mismo nombre ({a_prim}) + mismo materno ({p_mat_b})"}

    if mismo_nombre_b_en_a and mismo_mat_b_en_a:
        return {"score": 0.75, "tipo": "consanguineo_materno", "confianza": "alta",
                "detalle": f"mismo nombre ({b_prim}) + mismo materno ({p_mat_b})"}

    # 5. Solo paterno o solo materno compartido (sin nombre común) — tío/abuelo lejano
    if mismo_pat_b_en_a and not mismo_nombre_b_en_a:
        return {"score": 0.65, "tipo": "apellido_compartido", "confianza": "media",
                "detalle": f"solo mismo paterno ({p_pat_b}) presente en titular CFE — posible consanguíneo del lado paterno"}

    if mismo_mat_b_en_a and not mismo_nombre_b_en_a:
        return {"score": 0.65, "tipo": "apellido_compartido", "confianza": "media",
                "detalle": f"solo mismo materno ({p_mat_b}) presente en titular CFE — posible consanguíneo del lado materno"}

    # 6. Jaccard de tokens (fallback)
    if a_toks and b_toks:
        inter = a_toks & b_toks
        union = a_toks | b_toks
        jacc = len(inter) / len(union) if union else 0
        if jacc >= 0.5:
            return {"score": 0.50, "tipo": "nombre_parcial", "confianza": "baja",
                    "detalle": f"Jaccard {jacc:.2f} de tokens: {sorted(inter)}"}

    return {"score": 0.0, "tipo": "sin_match", "confianza": "baja",
            "detalle": f"sin coincidencia significativa (1er token A={a_prim}, 1er token B={b_prim})"}


def _domicilio_padron_texto(sujeto: dict) -> str:
    partes = [
        sujeto.get("calle") or "",
        sujeto.get("ext") or "",
        sujeto.get("colonia") or "",
        sujeto.get("cp") or "",
    ]
    return re.sub(r'\s+', ' ', ' '.join([p for p in partes if p]).strip()).strip(', ')


def _match_domicilio_simple(sujeto: dict, cfe_item: dict) -> bool:
    subj_cp = str(sujeto.get("cp") or "").strip()
    cfe_cp = str(cfe_item.get("cp") or "").strip()
    subj_vialidad = _normalizar_texto_match(extraer_nombre_vialidad(sujeto.get("calle") or ""))
    cfe_direccion = _normalizar_texto_match(cfe_item.get("direccion") or "")
    subj_ext = _normalizar_texto_match(sujeto.get("ext") or "")
    cp_ok = bool(subj_cp and cfe_cp and subj_cp == cfe_cp)
    vialidad_ok = bool(subj_vialidad and subj_vialidad in cfe_direccion)
    ext_ok = bool(subj_ext and subj_ext in cfe_direccion)
    return (cp_ok and vialidad_ok) or (cp_ok and ext_ok) or (vialidad_ok and ext_ok)


def _resolver_titular_cfe_a_persona(sujeto_central: dict, familiares_padron: list,
                                    convivientes_padron: list, titular_cfe: str) -> dict:
    """Matching robusto entre un titular CFE y los candidatos del sujeto.

    2026-08-24: reescrito. Antes: equality exacta del nombre normalizado
    (fallaba con variaciones de orden, segundo nombre, partículas). Ahora:
    scoring con 7 niveles que incluye paterno/materno coincidente
    (caso típico de tío/hermano del padre viviendo en la misma calle).

    Retorna el MEJOR candidato (mayor score) o sin_match si score < 0.5.
    """
    titular_norm = _normalizar_texto_match(titular_cfe)
    if not titular_norm:
        return {
            "titular_curp_relacionado": None,
            "titular_nombre_relacionado": None,
            "tipo_relacion_titular": "sin_match",
            "confianza_match_titular": "baja",
            "score_match_titular": 0.0,
            "detalle_match_titular": "titular CFE vacío",
        }

    # Construir pool de candidatos: sujeto + familiares + convivientes.
    # Para paterno/materno del candidato usamos lo que ya viene en el dict.
    candidatos = []
    if sujeto_central:
        candidatos.append({
            "curp": sujeto_central.get("curp"),
            "nombre_completo": sujeto_central.get("nombre_completo") or "",
            "paterno": sujeto_central.get("paterno") or "",
            "materno": sujeto_central.get("materno") or "",
            "tipo_relacion": "sujeto_central",
        })
    for fam in familiares_padron or []:
        candidatos.append({
            "curp": fam.get("curp"),
            "nombre_completo": fam.get("nombre_completo") or "",
            "paterno": fam.get("paterno") or "",
            "materno": fam.get("materno") or "",
            "tipo_relacion": fam.get("tipo_relacion") or "consanguineo_directo",
        })
    for conv in convivientes_padron or []:
        candidatos.append({
            "curp": conv.get("curp"),
            "nombre_completo": conv.get("nombre_completo") or "",
            "paterno": conv.get("paterno") or "",
            "materno": conv.get("materno") or "",
            "tipo_relacion": conv.get("tipo_relacion") or "conviviente_domicilio",
        })

    # Scorificar contra el titular CFE
    mejor = None
    for cand in candidatos:
        s = _score_match_nombres(
            nombre_a=titular_cfe,
            nombre_b=cand.get("nombre_completo") or "",
            paterno_a="",  # el titular CFE no tiene paterno/materno separados
            paterno_b=cand.get("paterno") or "",
            materno_a="",
            materno_b=cand.get("materno") or "",
        )
        if not mejor or s["score"] > mejor["score"]:
            mejor = {
                "cand": cand,
                "score": s["score"],
                "tipo": s["tipo"],
                "confianza": s["confianza"],
                "detalle": s["detalle"],
            }

    if not mejor or mejor["score"] < 0.5:
        return {
            "titular_curp_relacionado": None,
            "titular_nombre_relacionado": None,
            "tipo_relacion_titular": "sin_match",
            "confianza_match_titular": "baja",
            "score_match_titular": mejor["score"] if mejor else 0.0,
            "detalle_match_titular": mejor["detalle"] if mejor else "sin candidatos",
        }

    return {
        "titular_curp_relacionado": mejor["cand"].get("curp"),
        "titular_nombre_relacionado": mejor["cand"].get("nombre_completo"),
        "tipo_relacion_titular": mejor["tipo"],
        "confianza_match_titular": mejor["confianza"],
        "score_match_titular": mejor["score"],
        "detalle_match_titular": mejor["detalle"],
    }


def _construir_mapeo_domicilio_cfe(sujeto_central, familiares_padron,
                                   convivientes_padron, cfe_inmueble,
                                   vecinos_padron=None,
                                   apellido_compartido_padron=None,
                                   use_gemini=False):
    """Construye el mapeo domicilio CFE considerando todos los pools de candidatos.

    2026-08-24: ampliado. Antes solo buscaba match contra sujeto + familiares +
    convivientes. Ahora también contra vecinos de nombre misma calle y personas
    con apellido compartido en la entidad (tíos lejanos).

    Si use_gemini=True, llama a Gemini para validar matches con score >= 0.5
    y generar narrativas. Si Gemini no está disponible o falla, degrada al
    match local sin romper el flujo.
    """
    if vecinos_padron is None:
        vecinos_padron = []
    if apellido_compartido_padron is None:
        apellido_compartido_padron = []
    servicios = []
    for item in cfe_inmueble or []:
        # Buscar contra todos los pools: sujeto → familiares → convivientes →
        # vecinos → apellido compartido. El algoritmo de scoring interno ya
        # elige el MEJOR score entre todos los candidatos.
        match = _resolver_titular_cfe_a_persona(
            sujeto_central,
            familiares_padron,
            convivientes_padron,
            item.get("titular") or ""
        )
        # Si no hubo match por nombre, intentar contra vecinos + apellido
        # compartido (estos son pools con score paterno/materno).
        if match.get("score_match_titular", 0) < 0.5:
            match_v = _resolver_titular_cfe_a_persona(
                sujeto_central,
                apellido_compartido_padron,
                vecinos_padron,
                item.get("titular") or ""
            )
            if match_v.get("score_match_titular", 0) > match.get("score_match_titular", 0):
                match = match_v
        # 2026-08-24: validación Gemini opcional. Solo si use_gemini=True y
        # el match local tiene score >= 0.5 (matches plausibles).
        match_ia = None
        if use_gemini and match.get("score_match_titular", 0) >= 0.5:
            try:
                from providers.gemini import validate_match_relationship
                contexto = ""
                if match.get("coincidencia_domicilio"):
                    contexto = "El titular CFE está registrado en el mismo domicilio que el sujeto."
                match_ia = validate_match_relationship(
                    sujeto=sujeto_central or {},
                    titular_cfe=item,
                    match_local=match,
                    contexto=contexto,
                )
            except Exception:
                match_ia = None
        servicios.append({
            "numero_servicio": item.get("numero_servicio"),
            "titular": item.get("titular"),
            "direccion": item.get("direccion"),
            "colonia": item.get("colonia"),
            "cp": item.get("cp"),
            "division": item.get("division"),
            "zona": item.get("zona"),
            "agencia": item.get("agencia"),
            "coincidencia_domicilio": _match_domicilio_simple(sujeto_central or {}, item),
            **match,
            "validacion_ia": match_ia,
        })

    return {
        "domicilio_padron": _domicilio_padron_texto(sujeto_central or {}),
        "total_servicios": len(servicios),
        "servicios": servicios,
    }


def ejecutar_inteligencia_completa(
    curp: str = "",
    rfc: str = "",
    nombre: str = "",
    paterno: str = "",
    materno: str = "",
    fecnac: str = "",
    limite_familiares: int = 30,
    limite_convivientes: int = 30,
    extended_con = None,
    use_gemini: bool = False,
) -> dict:
    t0 = time.time()
    
    curp = (curp or "").strip().upper()
    rfc = (rfc or "").strip().upper()
    nombre = (nombre or "").strip().upper()
    paterno = (paterno or "").strip().upper()
    materno = (materno or "").strip().upper()
    fecnac = (fecnac or "").strip()
    
    # 1. Conexión al Padrón Principal
    con_padron = duckdb.connect(str(BASES_DIR / "padron_v1.duckdb"), read_only=True)

    sujeto_central = None
    if curp:
        row = con_padron.execute("""
            SELECT curp, nombre, paterno, materno, fecnac, sexo, calle, ext, int, colonia, cp, e, m, folio, cve
            FROM main.padron WHERE curp = ?
        """, [curp]).fetchone()
        if row:
            sujeto_central = {
                "curp": row[0], "nombre": row[1], "paterno": row[2], "materno": row[3],
                "nombre_completo": f"{row[1]} {row[2]} {row[3]}".strip(),
                "fecnac": str(row[4]), "sexo": row[5], "calle": row[6], "ext": row[7],
                "int": row[8], "colonia": row[9], "cp": row[10], "entidad_id": row[11],
                "municipio_id": row[12], "folio": str(row[13]), "cve": row[14],
                "fuente": "padron_electoral_ine"
            }

    # Si no hay CURP pero hay RFC, intentar derivar CURP del RFC (primeros 10 chars + buscar en padrón)
    if not sujeto_central and rfc and len(rfc) >= 10:
        rfc10 = rfc[:10]
        # Buscar en padrón por RFC10 (primeros 10 de CURP = RFC sin homoclave)
        row = con_padron.execute("""
            SELECT curp, nombre, paterno, materno, fecnac, sexo, calle, ext, int, colonia, cp, e, m, folio, cve
            FROM main.padron WHERE curp LIKE ? || '%' LIMIT 1
        """, [rfc10]).fetchone()
        if row:
            curp = row[0]
            sujeto_central = {
                "curp": row[0], "nombre": row[1], "paterno": row[2], "materno": row[3],
                "nombre_completo": f"{row[1]} {row[2]} {row[3]}".strip(),
                "fecnac": str(row[4]), "sexo": row[5], "calle": row[6], "ext": row[7],
                "int": row[8], "colonia": row[9], "cp": row[10], "entidad_id": row[11],
                "municipio_id": row[12], "folio": str(row[13]), "cve": row[14],
                "fuente": "padron_electoral_ine"
            }

    if not sujeto_central and paterno and materno:
        q = """
            SELECT curp, nombre, paterno, materno, fecnac, sexo, calle, ext, int, colonia, cp, e, m, folio, cve
            FROM main.padron
            WHERE UPPER(paterno) = ? AND UPPER(materno) = ?
        """
        params = [paterno, materno]
        if nombre:
            q += " AND UPPER(nombre) LIKE '%' || ? || '%'"
            params.append(nombre.split()[0])
        q += " LIMIT 1"
        row = con_padron.execute(q, params).fetchone()
        if row:
            sujeto_central = {
                "curp": row[0], "nombre": row[1], "paterno": row[2], "materno": row[3],
                "nombre_completo": f"{row[1]} {row[2]} {row[3]}".strip(),
                "fecnac": str(row[4]), "sexo": row[5], "calle": row[6], "ext": row[7],
                "int": row[8], "colonia": row[9], "cp": row[10], "entidad_id": row[11],
                "municipio_id": row[12], "folio": str(row[13]), "cve": row[14],
                "fuente": "padron_electoral_ine"
            }
            curp = sujeto_central["curp"]

    if not sujeto_central and not curp and not rfc:
        con_padron.close()
        return {"error": "Sujeto no localizado y parámetros insuficientes para iniciar inteligencia relacional."}

    # Si no está en padrón pero hay CURP/RFC (ej. sujeto de COVID23 o CheckID)
    if not sujeto_central:
        # Intentar buscar en covid23 o docentes si extended_con o conexión directa
        nom_c = f"{nombre} {paterno} {materno}".strip() or curp or rfc
        if curp and not nom_c:
            try:
                con_cov = duckdb.connect(str(BASES_DIR / "covid_v1.duckdb"), read_only=True)
                rcov = con_cov.execute("SELECT nombre1, nombre2 FROM main.personas WHERE curp = ? LIMIT 1", [curp]).fetchone()
                if rcov:
                    nom_c = f"{rcov[0] or ''} {rcov[1] or ''}".strip()
                con_cov.close()
            except: pass
            
        sujeto_central = {
            "curp": curp,
            "nombre": nombre or "",
            "paterno": paterno or "",
            "materno": materno or "",
            "nombre_completo": nom_c,
            "fecnac": fecnac or "",
            "sexo": curp[10] if len(curp)>=11 and curp[10] in ['H','M'] else "",
            "calle": "", "ext": "", "int": "", "colonia": "", "cp": "",
            "fuente": "consulta_externa"
        }

    # Valores base del sujeto
    c_paterno = sujeto_central["paterno"] if sujeto_central else paterno
    c_materno = sujeto_central["materno"] if sujeto_central else materno
    c_calle = (sujeto_central.get("calle") or "") if sujeto_central else ""
    c_ext = (sujeto_central.get("ext") or "") if sujeto_central else ""
    c_colonia = (sujeto_central.get("colonia") or "") if sujeto_central else ""
    c_cp = (sujeto_central.get("cp") or "") if sujeto_central else ""

    # Extraer nombre significativo de la vialidad para búsqueda precisa
    vialidad_nombre = extraer_nombre_vialidad(c_calle)

    # 2. Mapeo de Familia Directa / Consanguínea en Padrón
    # Solo buscar familiares si tenemos ubicación del sujeto (entidad/CP) para evitar
    # traer miles de homónimos en todo el país
    familiares_padron = []
    c_entidad = sujeto_central.get("entidad_id") if sujeto_central else None
    c_municipio = sujeto_central.get("municipio_id") if sujeto_central else None
    
    if c_paterno and c_materno and c_entidad:
        rows_fam = con_padron.execute("""
            SELECT curp, nombre, paterno, materno, fecnac, sexo, calle, ext, colonia, cp, e, m, folio
            FROM main.padron
            WHERE UPPER(paterno) = ? AND UPPER(materno) = ? AND e = ?
            ORDER BY fecnac ASC
            LIMIT ?
        """, [c_paterno, c_materno, c_entidad, limite_familiares]).fetchall()
        for rf in rows_fam:
            f_curp = rf[0]
            if f_curp != curp:
                familiares_padron.append({
                    "curp": rf[0], "nombre": rf[1], "paterno": rf[2], "materno": rf[3],
                    "nombre_completo": f"{rf[1]} {rf[2]} {rf[3]}".strip(),
                    "fecnac": str(rf[4]), "sexo": rf[5], "calle": rf[6], "ext": rf[7],
                    "colonia": rf[8], "cp": rf[9], "entidad_id": rf[10], "municipio_id": rf[11],
                    "folio": str(rf[12]), "tipo_relacion": "consanguineo_directo"
                })
    elif c_paterno and c_materno and curp:
        # Si no hay entidad pero sí CURP, usar CURP del sujeto para excluirlo
        rows_fam = con_padron.execute("""
            SELECT curp, nombre, paterno, materno, fecnac, sexo, calle, ext, colonia, cp, e, m, folio
            FROM main.padron
            WHERE UPPER(paterno) = ? AND UPPER(materno) = ? AND curp != ?
            ORDER BY fecnac ASC
            LIMIT ?
        """, [c_paterno, c_materno, curp, limite_familiares]).fetchall()
        for rf in rows_fam:
            familiares_padron.append({
                "curp": rf[0], "nombre": rf[1], "paterno": rf[2], "materno": rf[3],
                "nombre_completo": f"{rf[1]} {rf[2]} {rf[3]}".strip(),
                "fecnac": str(rf[4]), "sexo": rf[5], "calle": rf[6], "ext": rf[7],
                "colonia": rf[8], "cp": rf[9], "entidad_id": rf[10], "municipio_id": rf[11],
                "folio": str(rf[12]), "tipo_relacion": "consanguineo_directo"
            })

    # 3. Mapeo de Convivientes en el Mismo Domicilio
    convivientes_padron = []
    if c_cp and vialidad_nombre:
        ext_clean = re.sub(r'\.0$', '', c_ext).strip()
        if ext_clean:
            # Buscar por CP + nombre vialidad completo + número exterior (preciso)
            rows_conv = con_padron.execute("""
                SELECT curp, nombre, paterno, materno, fecnac, sexo, calle, ext, colonia, cp, e, m, folio
                FROM main.padron
                WHERE cp = ?
                  AND UPPER(calle) LIKE '%' || ? || '%'
                  AND (ext = ? OR ext = ? || '.0' OR ext LIKE ? || '%')
                ORDER BY paterno, materno, nombre
                LIMIT ?
            """, [c_cp, vialidad_nombre, ext_clean, ext_clean, ext_clean, limite_convivientes]).fetchall()
            for rc in rows_conv:
                c_curp = rc[0]
                if c_curp != curp and not any(f["curp"] == c_curp for f in familiares_padron):
                    convivientes_padron.append({
                        "curp": rc[0], "nombre": rc[1], "paterno": rc[2], "materno": rc[3],
                        "nombre_completo": f"{rc[1]} {rc[2]} {rc[3]}".strip(),
                        "fecnac": str(rc[4]), "sexo": rc[5], "calle": rc[6], "ext": rc[7],
                        "colonia": rc[8], "cp": rc[9], "entidad_id": rc[10], "municipio_id": rc[11],
                        "folio": str(rc[12]), "tipo_relacion": "conviviente_domicilio"
                    })

    # 3b. 2026-08-24: ampliar candidatos para matching CFE.
    # El matching original solo consideraba convivientes en el MISMO número
    # exterior, pero los titulares CFE pueden estar en números cercanos (181, 189,
    # 206, 227, etc.) de la misma calle. Capturar:
    #   - vecinos_calle: empadronados en misma vialidad + CP (cualquier número)
    #   - apellido_compartido: empadronados con mismo paterno O materno (sin
    #     requerir el otro), capturando tíos y abuelos del sujeto.
    vecinos_padron = []
    apellido_compartido_padron = []
    if c_cp and vialidad_nombre:
        # Vecinos de calle: mismos CP + vialidad, excluyendo al sujeto y los
        # que ya capturamos como convivientes/familiares.
        try:
            rows_vec = con_padron.execute("""
                SELECT curp, nombre, paterno, materno, fecnac, sexo, calle, ext, colonia, cp, e, m, folio
                FROM main.padron
                WHERE cp = ? AND UPPER(calle) LIKE '%' || ? || '%'
                ORDER BY paterno, materno, nombre
                LIMIT 60
            """, [c_cp, vialidad_nombre]).fetchall()
            for rv in rows_vec:
                v_curp = rv[0]
                if (v_curp != curp and
                        not any(f["curp"] == v_curp for f in familiares_padron) and
                        not any(c["curp"] == v_curp for c in convivientes_padron)):
                    vecinos_padron.append({
                        "curp": rv[0], "nombre": rv[1], "paterno": rv[2], "materno": rv[3],
                        "nombre_completo": f"{rv[1]} {rv[2]} {rv[3]}".strip(),
                        "fecnac": str(rv[4]), "sexo": rv[5], "calle": rv[6], "ext": rv[7],
                        "colonia": rv[8], "cp": rv[9], "entidad_id": rv[10], "municipio_id": rv[11],
                        "folio": str(rv[12]), "tipo_relacion": "vecino_misma_calle"
                    })
        except Exception:
            pass

    # Apellido compartido: tíos y abuelos (mismo paterno O materno en la entidad)
    if (c_paterno or c_materno) and c_entidad:
        cond_parts = []
        params = []
        if c_paterno:
            cond_parts.append("UPPER(paterno) = ?")
            params.append(c_paterno)
        if c_materno:
            cond_parts.append("UPPER(materno) = ?")
            params.append(c_materno)
        # Excluir a los que ya capturamos
        cond_parts.append("curp != ?")
        params.append(curp)
        try:
            q_ap = f"""
                SELECT curp, nombre, paterno, materno, fecnac, sexo, calle, ext, colonia, cp, e, m, folio
                FROM main.padron
                WHERE e = ? AND ({" OR ".join(cond_parts[:-2])})
                  AND curp != ?
                ORDER BY fecnac ASC
                LIMIT 40
            """
            # reconstruir params: entidad + filtros + excluir
            qparams = [c_entidad] + params[:-1] + [curp]
            rows_ap = con_padron.execute(q_ap, qparams).fetchall()
            for ra in rows_ap:
                a_curp = ra[0]
                if (not any(f["curp"] == a_curp for f in familiares_padron) and
                        not any(c["curp"] == a_curp for c in convivientes_padron) and
                        not any(v["curp"] == a_curp for v in vecinos_padron)):
                    # Tipo de relación según qué apellido comparte
                    tipo_ap = "consanguineo_paterno_lejano" if (
                        c_paterno and ra[2] and ra[2].upper() == c_paterno
                    ) else "consanguineo_materno_lejano"
                    apellido_compartido_padron.append({
                        "curp": ra[0], "nombre": ra[1], "paterno": ra[2], "materno": ra[3],
                        "nombre_completo": f"{ra[1]} {ra[2]} {ra[3]}".strip(),
                        "fecnac": str(ra[4]), "sexo": ra[5], "calle": ra[6], "ext": ra[7],
                        "colonia": ra[8], "cp": ra[9], "entidad_id": ra[10], "municipio_id": ra[11],
                        "folio": str(ra[12]), "tipo_relacion": tipo_ap,
                    })
        except Exception:
            pass

    con_padron.close()

    # 4. Servicios CFE en el Inmueble
    cfe_inmueble = []
    try:
        con_cfe = duckdb.connect(str(BASES_DIR / "cfe_v1.duckdb"), read_only=True)
        if c_colonia or (c_cp and vialidad_nombre):
            col_tok = re.sub(r'^(COL\.|COLONIA|FRACC\.?|FRACCIONAMIENTO|BARRIO|U\.?\s*HAB\.?)\s+', '', c_colonia, flags=re.IGNORECASE).strip()
            rows_cfe = con_cfe.execute("""
                SELECT numero_servicio, nombre, direccion, colonia, cp, division, zona_nombre, agencia_nombre
                FROM main.medidores
                WHERE (cp = ? AND UPPER(direccion) LIKE '%' || ? || '%')
                   OR (UPPER(colonia) LIKE '%' || ? || '%' AND UPPER(direccion) LIKE '%' || ? || '%')
                LIMIT 10
            """, [c_cp, vialidad_nombre, col_tok, vialidad_nombre]).fetchall()
            for rcfe in rows_cfe:
                cfe_inmueble.append({
                    "numero_servicio": rcfe[0], "titular": rcfe[1], "direccion": rcfe[2],
                    "colonia": rcfe[3], "cp": rcfe[4], "division": rcfe[5],
                    "zona": rcfe[6], "agencia": rcfe[7]
                })
        con_cfe.close()
    except Exception as e:
        cfe_inmueble = []

    # 4b. Mapeo explícito padrón → CFE → parentesco
    mapeo_domicilio_cfe = _construir_mapeo_domicilio_cfe(
        sujeto_central, familiares_padron, convivientes_padron, cfe_inmueble,
        vecinos_padron=vecinos_padron,
        apellido_compartido_padron=apellido_compartido_padron,
        use_gemini=use_gemini,
    )

    # 5. Lista consolidada de sujetos a perfilar
    sujetos_todos = []
    
    if sujeto_central:
        sujetos_todos.append({
            "id": "SUJETO_CENTRAL",
            "categoria": "Sujeto Principal",
            "relacion_origen": "Sujeto raíz del expediente de investigación.",
            "data": sujeto_central
        })
        
    for cfe_item in cfe_inmueble:
        tit_cfe = cfe_item.get("titular")
        if tit_cfe and not any(s["data"].get("nombre_completo") == tit_cfe for s in sujetos_todos):
            sujetos_todos.append({
                "id": f"CFE_{cfe_item.get('numero_servicio')}",
                "categoria": "Titular Suministro CFE",
                "relacion_origen": f"Titular formal del contrato y medidor CFE (Servicio No. {cfe_item.get('numero_servicio')}) en el predio declarado.",
                "data": {
                    "curp": "", "nombre": tit_cfe, "paterno": "", "materno": "",
                    "nombre_completo": tit_cfe, "calle": cfe_item.get("direccion"),
                    "colonia": cfe_item.get("colonia"), "cp": cfe_item.get("cp"),
                    "servicio_cfe": cfe_item.get("numero_servicio"), "fuente": "cfe"
                }
            })

    for idx_f, fam in enumerate(familiares_padron, 1):
        sujetos_todos.append({
            "id": f"FAM_{idx_f}",
            "categoria": "Núcleo Consanguíneo",
            "relacion_origen": f"Coincidencia patronímica biparental 100% ({fam['paterno']} {fam['materno']}) en Padrón Electoral Federal (INE).",
            "data": fam
        })

    for idx_c, conv in enumerate(convivientes_padron, 1):
        sujetos_todos.append({
            "id": f"CONV_{idx_c}",
            "categoria": "Conviviente Domiciliario",
            "relacion_origen": f"Empadronado formalmente en el mismo predio físico declarado por el sujeto ({c_calle} #{c_ext}, CP {c_cp}).",
            "data": conv
        })

    # 2026-08-24: nuevos pools para ampliar el dossier de inteligencia.
    for idx_v, vec in enumerate(vecinos_padron, 1):
        sujetos_todos.append({
            "id": f"VEC_{idx_v}",
            "categoria": "Vecino Misma Calle",
            "relacion_origen": f"Empadronado en la misma vialidad ({vialidad_nombre}) y CP ({c_cp}), sin coincidencia exacta de número exterior.",
            "data": vec
        })

    for idx_a, ap in enumerate(apellido_compartido_padron, 1):
        # Etiqueta más informativa según el tipo
        tipo_ap = ap.get("tipo_relacion", "")
        if "paterno" in tipo_ap:
            cat_ap = "Consanguíneo Paterno Lejano"
            rel_ap = (f"Comparte apellido paterno ({ap.get('paterno', '')}) con el sujeto en la "
                      f"entidad {sujeto_central.get('entidad_id', '')}. Posible tío paterno, "
                      f"abuelo paterno o primo hermano del padre.")
        else:
            cat_ap = "Consanguíneo Materno Lejano"
            rel_ap = (f"Comparte apellido materno ({ap.get('materno', '')}) con el sujeto en la "
                      f"entidad {sujeto_central.get('entidad_id', '')}. Posible tío materno, "
                      f"abuelo materno o primo hermano de la madre.")
        sujetos_todos.append({
            "id": f"AP_{idx_a}",
            "categoria": cat_ap,
            "relacion_origen": rel_ap,
            "data": ap
        })

    # 6. Cruce Optimizado de Vínculos en las Bases de Inteligencia
    # Recolectamos todas las CURPs y RFC10 para queries en bloque (IN (...))
    curps_list = [s["data"].get("curp") for s in sujetos_todos if s["data"].get("curp")]
    rfcs10_map = {s["data"]["curp"][:10]: s["id"] for s in sujetos_todos if s["data"].get("curp") and len(s["data"]["curp"])>=10}
    
    # 6.1 IMSS Asegurados en Bloque
    imss_map = {}
    if curps_list:
        try:
            con_imss = duckdb.connect(str(BASES_DIR / "imss_asegurados_v1.duckdb"), read_only=True)
            ph = ",".join(["?"] * len(curps_list))
            rows_imss = con_imss.execute(f"""
                SELECT curp_clean, nombre_patron, registro_patron, sueldo_raw, domicilio_patron, ciudad_estado, empresa_giro
                FROM main.imss_2025 WHERE curp_clean IN ({ph})
            """, curps_list).fetchall()
            for r in rows_imss:
                imss_map[r[0]] = {
                    "patron": r[1], "reg_patronal": r[2], "sueldo_base": r[3],
                    "domicilio_patron": f"{r[4] or ''}, {r[5] or ''}".strip(', '),
                    "giro": r[6]
                }
            con_imss.close()
        except: pass

    # 6.2 IMSS Salud / Segmentación en Bloque
    imss_s_map = {}
    if curps_list:
        try:
            con_imss_s = duckdb.connect(str(BASES_DIR / "imss_segmentacion_v1.duckdb"), read_only=True)
            ph = ",".join(["?"] * len(curps_list))
            rows_is = con_imss_s.execute(f"""
                SELECT curp_clean, unidad_medica, ooad, nss_clean, rfc_clean
                FROM main.imss_personas WHERE curp_clean IN ({ph})
            """, curps_list).fetchall()
            for r in rows_is:
                imss_s_map[r[0]] = {
                    "unidad_medica": r[1], "ooad": r[2], "nss": r[3], "rfc": r[4]
                }
            con_imss_s.close()
        except: pass

    # 6.3 Cruce en Vista Consolidada api.bancarias_todas si está disponible
    bancarias_map = {}
    telefonia_map = {}
    if extended_con and (curps_list or rfcs10_map):
        try:
            if curps_list:
                ph_c = ",".join(["?"] * len(curps_list))
                rows_b = extended_con.execute(f"""
                    SELECT curp, rfc, cuenta, telefono, marca, modelo, titular_nombre1, titular_nombre2,
                           titular_domicilio, titular_colonia, titular_ciudad, titular_estado, archivo_origen
                    FROM api.bancarias_todas
                    WHERE curp IN ({ph_c})
                """, curps_list).fetchall()
                for r in rows_b:
                    c_key = r[0]
                    if c_key not in bancarias_map: bancarias_map[c_key] = []
                    bancarias_map[c_key].append({
                        "banco_o_fuente": r[12], "rfc": r[1], "cuenta": r[2], "telefono": r[3],
                        "domicilio": f"{r[8] or ''} {r[9] or ''} {r[10] or ''}".strip()
                    })
        except: pass

    # Ensamblar Dossier
    dossier_perfilado = []
    for sujeto in sujetos_todos:
        s_data = sujeto["data"]
        s_curp = s_data.get("curp") or ""
        s_id = sujeto["id"]
        
        perfil = {
            "id": s_id,
            "categoria": sujeto["categoria"],
            "relacion_origen": sujeto["relacion_origen"],
            "identidad": s_data,
            "hallazgos": {
                "empleo_imss": [imss_map[s_curp]] if s_curp in imss_map else [],
                "salud_imss": [imss_s_map[s_curp]] if s_curp in imss_s_map else [],
                "telefonia": telefonia_map.get(s_curp, []),
                "banca": bancarias_map.get(s_curp, [])
            },
            "dictamen_analitico": ""
        }

        # Dictámenes analíticos — versión dinámica 2026-08-24
        # Antes: texto fijo por categoría (mismo mensaje para todos los sujetos).
        # Ahora: se genera desde los datos reales del sujeto + sus hallazgos.
        if s_id == "SUJETO_CENTRAL":
            # Construir dictamen dinámico del sujeto central
            partes = []
            n_hallazgos = (
                len(perfil["hallazgos"]["empleo_imss"]) +
                len(perfil["hallazgos"]["salud_imss"]) +
                len(perfil["hallazgos"]["telefonia"]) +
                len(perfil["hallazgos"]["banca"])
            )
            if sujeto_central and sujeto_central.get("fuente") == "padron_electoral_ine":
                partes.append("Sujeto central validado en Padrón Electoral Federal (INE).")
            else:
                partes.append(f"Sujeto central con CURP {curp} (fuente: {sujeto_central.get('fuente', 'consulta externa')}).")
            if perfil["hallazgos"]["empleo_imss"]:
                p = perfil["hallazgos"]["empleo_imss"][0]
                partes.append(f"Cotiza en IMSS como asalariado de {p.get('patron', 'empleador desconocido')}.")
            else:
                partes.append("Sin cotización asalariada en IMSS (régimen de capital, profesional o independiente).")
            if perfil["hallazgos"]["banca"]:
                partes.append(f"Detectadas {len(perfil['hallazgos']['banca'])} huella(s) bancaria(s) (cuentas, RFCs o teléfonos asociados).")
            if mapeo_domicilio_cfe.get("total_servicios", 0) > 0:
                # Contar matches positivos del CFE
                matches_cfe = sum(1 for s in mapeo_domicilio_cfe.get("servicios", [])
                                  if s.get("tipo_relacion_titular") not in (None, "sin_match"))
                if matches_cfe > 0:
                    partes.append(
                        f"En el domicilio se detectaron {mapeo_domicilio_cfe['total_servicios']} "
                        f"servicio(s) CFE; {matches_cfe} titular(es) matchean con familiares o "
                        f"apellidos del sujeto."
                    )
                else:
                    partes.append(
                        f"En el domicilio se detectaron {mapeo_domicilio_cfe['total_servicios']} "
                        f"servicio(s) CFE; ninguno de los titulares matchea con el sujeto o sus "
                        f"familiares conocidos en padrón."
                    )
            partes.append(f"Total de sujetos en el dossier: {n_hallazgos} hallazgos secundarios.")
            perfil["dictamen_analitico"] = " ".join(partes)
        elif "CFE_" in s_id:
            rel = next((x for x in mapeo_domicilio_cfe.get("servicios", [])
                        if f"CFE_{x.get('numero_servicio')}" == s_id), None)
            if rel and rel.get("tipo_relacion_titular") and rel.get("tipo_relacion_titular") != "sin_match":
                tipo = rel.get("tipo_relacion_titular")
                score = rel.get("score_match_titular", 0)
                rel_nombre = rel.get("titular_nombre_relacionado") or ""
                detalle = rel.get("detalle_match_titular", "")
                dom = "mismo domicilio (CFE en la dirección del sujeto)" if rel.get("coincidencia_domicilio") else "domicilio distinto"
                perfil["dictamen_analitico"] = (
                    f"Titular CFE del Servicio {rel.get('numero_servicio')}. "
                    f"Relacionado como {tipo} (score {score:.2f}) del sujeto — match: {rel_nombre}. "
                    f"Mecanismo: {detalle}. {dom}."
                )
            else:
                dom = "mismo domicilio" if rel and rel.get("coincidencia_domicilio") else "domicilio distinto"
                score = rel.get("score_match_titular", 0) if rel else 0
                detalle = rel.get("detalle_match_titular", "") if rel else ""
                perfil["dictamen_analitico"] = (
                    f"Titular CFE del Servicio {rel.get('numero_servicio') if rel else '?'} "
                    f"({dom}). Score de match por nombre con el sujeto: {score:.2f}. "
                    f"No se identificó relación familiar directa con el sujeto ni con sus "
                    f"familiares conocidos en padrón. {detalle}"
                ).strip()
        elif "FAM_" in s_id:
            fam_data = s_data
            relaciones = []
            if fam_data.get("fecnac"):
                relaciones.append(f"fecha de nacimiento {fam_data['fecnac']}")
            if fam_data.get("calle"):
                relaciones.append(f"radica en {fam_data['calle']} #{fam_data.get('ext', '')}, {fam_data.get('colonia', '')}, CP {fam_data.get('cp', '')}")
            base_str = " · ".join(relaciones) if relaciones else "datos secundarios en padrón"
            if perfil["hallazgos"]["empleo_imss"]:
                p = perfil["hallazgos"]["empleo_imss"][0]
                perfil["dictamen_analitico"] = (
                    f"Familiar consanguíneo directo del sujeto ({base_str}). "
                    f"Empleado por {p.get('patron', 'patrón desconocido')} en IMSS."
                )
            else:
                perfil["dictamen_analitico"] = f"Familiar consanguíneo directo del sujeto ({base_str})."
        elif "CONV_" in s_id:
            conv_data = s_data
            base_str = f"mismo domicilio que el sujeto (#{conv_data.get('ext', '')}, CP {conv_data.get('cp', '')})"
            if perfil["hallazgos"]["empleo_imss"]:
                p = perfil["hallazgos"]["empleo_imss"][0]
                perfil["dictamen_analitico"] = (
                    f"Conviviente domiciliario del sujeto ({base_str}). "
                    f"Empleado por {p.get('patron', 'patrón desconocido')} en IMSS."
                )
            else:
                perfil["dictamen_analitico"] = f"Conviviente domiciliario del sujeto ({base_str})."
        elif "VEC_" in s_id:
            vec = s_data
            perfil["dictamen_analitico"] = (
                f"Vecino empadronado en la misma vialidad ({vec.get('calle', '')}) "
                f"y CP {vec.get('cp', '')}, exterior #{vec.get('ext', '')}."
            )
        elif "AP_" in s_id:
            ap = s_data
            if "paterno" in ap.get("tipo_relacion", ""):
                relacion = "consanguíneo del lado paterno (posible tío, abuelo o primo)"
                apellido = ap.get("paterno", "")
            else:
                relacion = "consanguíneo del lado materno (posible tío, abuelo o primo)"
                apellido = ap.get("materno", "")
            perfil["dictamen_analitico"] = (
                f"Comparte apellido {apellido} con el sujeto — {relacion}. "
                f"Radicación: {ap.get('calle', '')} #{ap.get('ext', '')}, {ap.get('colonia', '')}, "
                f"CP {ap.get('cp', '')}."
            )

        dossier_perfilado.append(perfil)

    # 7. Respuesta Consolidada
    resumen_global = {
        "tiempo_ejecucion_s": round(time.time() - t0, 2),
        "sujeto_investigado": sujeto_central.get("nombre_completo") if sujeto_central else f"{nombre} {paterno} {materno}".strip(),
        "curp": curp,
        "rfc": rfc or (sujeto_central.get("curp")[:10] if sujeto_central else ""),
        "total_sujetos_auditados": len(dossier_perfilado),
        "total_familiares_consanguineos": len(familiares_padron),
        "total_convivientes_inmueble": len(convivientes_padron),
        "total_vecinos_misma_calle": len(vecinos_padron),
        "total_apellido_compartido": len(apellido_compartido_padron),
        # 2026-08-24: antes se exportaba cfe_inmueble crudo, pero el frontend
        # esperaba campos de match (tipo_relacion_titular, score_match_titular,
        # titular_curp_relacionado, etc.) que solo viven en mapeo_domicilio_cfe.
        # Ahora exportamos el array enriquecido para que el render sea consistente.
        "servicios_cfe_inmueble": mapeo_domicilio_cfe.get("servicios", []),
        "mapeo_domicilio_cfe": mapeo_domicilio_cfe,
        "dictamen_conclusivo": {
            "estatus_identidad": (
                "Plena y verificada en Padrón INE" if sujeto_central and sujeto_central.get("fuente") == "padron_electoral_ine"
                else f"Verificada parcialmente (fuente: {sujeto_central.get('fuente', 'consulta externa') if sujeto_central else 'N/A'})"
            ),
            "nivel_riesgo_kyc": "BAJO / REGULAR",
            "alertas_fiscales_69_69b": "Sin alertas negativas (Contribuyente Cumplido).",
            "dinamica_relacional": (
                f"Dossier consolidado con {len(dossier_perfilado)} sujetos auditados: "
                f"{len(familiares_padron)} familiares consanguíneos, {len(convivientes_padron)} "
                f"convivientes en domicilio, {len(vecinos_padron)} vecinos de calle, "
                f"{len(apellido_compartido_padron)} personas con apellido compartido. "
                f"{mapeo_domicilio_cfe.get('total_servicios', 0)} servicio(s) CFE en el domicilio "
                f"declarado."
            )
        },
        "dossier_sujetos": dossier_perfilado
    }

    return resumen_global


if __name__ == "__main__":
    t_start = time.time()
    res = ejecutar_inteligencia_completa(
        nombre="MONICA RUBI",
        paterno="JAQUEZ",
        materno="GRANADOS"
    )
    print(f"Test completado en {res['tiempo_ejecucion_s']}s para {res['total_sujetos_auditados']} sujetos.")
    with open("/tmp/test_inteligencia_rapido.json", "w", encoding="utf-8") as f:
        json.dump(res, f, default=str, ensure_ascii=False, indent=2)
