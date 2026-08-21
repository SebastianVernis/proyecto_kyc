#!/usr/bin/env python3
"""rastreo_relacion.py — Endpoint POST /api/v1/rastreo/relacion

Recibe un dossier estructurado con:
- sujetos: lista de CURPs/RFCs/nombres de los involucrados
- hechos: lista de eventos cronológicos con fecha, lugar, descripción, fuentes
- contexto: texto libre con contexto adicional

Hace:
1. Enriquece cada sujeto cruzándolo contra padrón, RENAPO, IMSS, CFE, ATT, Telcel,
   REPUVE, Empleadores, ISSSTE (vía los endpoints internos ya existentes).
2. Construye un grafo de relaciones: nodos (sujetos), aristas (hechos que los vinculan).
3. Llama a Ollama Cloud (deepseek-v4-pro / gpt-oss:20b) para generar:
   - Análisis situacional (narrativo)
   - Mapa de riesgos
   - Líneas de investigación sugeridas
   - Conclusión factual
4. Devuelve JSON con:
   - subjects_enriched: lista de sujetos con datos verificados
   - events: hechos estructurados
   - graph: {nodes, edges} para renderizar en frontend
   - ai_analysis: {narrative, riesgos, lineas_investigacion, conclusion}
   - metadata: timestamps, modelo usado, fuentes consultadas

Uso:
  POST /api/v1/rastreo/relacion
  Authorization: Bearer <token>
  Content-Type: application/json
  {
    "sujetos": [
      {"id": "A", "nombre": "ALEJANDRO MURAT HINOJOSA", "curp": "MUHA750804HMCRNL01",
       "rol": "director_politico"},
      {"id": "B", "nombre": "ANABEL ALCOCER CRUZ", "curp": "AOCA760620MNLLRN02",
       "rol": "operadora_juridica"},
      {"id": "C", "nombre": "ERIK BUCIO URBINA", "curp": "BUUE030113HMNCRRA8",
       "rol": "intermediario"},
      {"id": "V1", "nombre": "MARIANA RODRIGUEZ CANTU", "curp": "ROCM950810MNLDNR06",
       "rol": "victima"},
      {"id": "V2", "nombre": "MIGUEL ALFONSO MEZA AGUILAR", "curp": "MEAM860929HSLZGG09",
       "rol": "victima"}
    ],
    "hechos": [
      {"fecha": "2026-05-13", "tipo": "conferencia_prensa",
       "descripcion": "Murat y Alcocer comparecen públicamente...",
       "sujetos_involucrados": ["A","B"],
       "fuentes": ["Reforma", "Proceso"]},
      ...
    ],
    "contexto": "Operación de desprestigio contra el colectivo NarcoPolíticos..."
  }
"""

from __future__ import annotations

import json
import os
import re
import time
import datetime
from typing import Any
from urllib.parse import urlparse

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    requests = None


# =========================================================================
# FUNCIONES DE ENRIQUECIMIENTO (cruzan contra las bases locales)
# =========================================================================

def _enriquecer_sujeto_padron(handlers_self, curp: str) -> dict:
    """Busca al sujeto en el padrón y devuelve los campos clave."""
    out = {"curp": curp, "padron": None, "imss": [], "cfe": [], "apify": [],
           "telcel": [], "att": [], "repuve": [], "issste": [], "empleadores": []}
    if not curp or len(curp) != 18:
        return out
    try:
        # Padrón
        rows = handlers_self.db.by_curp(curp)
        if rows:
            r = rows[0]
            out["padron"] = {
                "id": r.get("id"),
                "curp": r.get("curp"),
                "nombre": r.get("nombre"),
                "paterno": r.get("paterno"),
                "materno": r.get("materno"),
                "fecnac": str(r.get("fecnac")) if r.get("fecnac") else None,
                "sexo": r.get("sexo"),
                "calle": r.get("calle"),
                "ext": r.get("ext"),
                "colonia": r.get("colonia"),
                "cp": r.get("cp"),
                "estado": r.get("e"),
                "municipio": r.get("m"),
                "seccion": r.get("s"),
                "folio": r.get("folio"),
            }
    except Exception as e:
        out["error_padron"] = str(e)
    return out


def _enriquecer_sujeto_apis(handlers_self, sujeto: dict) -> dict:
    """Llama a los endpoints /api/v1/persona/curp/<curp>/todo para traer todas
    las bases externas (IMSS, CFE, ATT, REPUVE, Telcel, ISSSTE, Empleadores)."""
    curp = sujeto.get("curp")
    if not curp or len(curp) != 18:
        return {}
    base_url = f"http://127.0.0.1:{getattr(handlers_self, 'port', 8765)}"
    try:
        # Llamada interna al endpoint /todo
        # Reutilizamos la lógica del handler pero vía HTTP interno
        from urllib.request import urlopen, Request
        url = f"http://127.0.0.1:8765/api/v1/persona/curp/{curp}/todo"
        req = Request(url, headers={"Authorization": f"Bearer {getattr(handlers_self, '_token_for_rastreo', '')}"})
        with urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        return data
    except Exception as e:
        return {"error": str(e)}


def _enriquecer_sujeto_osint(handlers_self, sujeto: dict) -> dict:
    """Llama al endpoint /api/osint para Tlaloc, GitHub, LinkedIn, Google, etc."""
    base_url = f"http://127.0.0.1:{getattr(handlers_self, 'port', 8765)}"
    payload = {
        "curp": sujeto.get("curp"),
        "nombre": sujeto.get("nombre", "").split()[0] if sujeto.get("nombre") else "",
        "paterno": sujeto.get("paterno") or sujeto.get("nombre", "").split()[-2] if sujeto.get("nombre") else "",
        "materno": sujeto.get("materno") or sujeto.get("nombre", "").split()[-1] if sujeto.get("nombre") else "",
        "fecnac": sujeto.get("fecnac"),
        "rfc": "",
        "email": "",
        "telefono": "",
    }
    # Si tenemos el nombre completo, intentamos dividirlo mejor
    nombre = sujeto.get("nombre", "").strip()
    if nombre:
        parts = nombre.split()
        if len(parts) >= 3:
            payload["nombre"] = parts[0]
            payload["paterno"] = parts[-2]
            payload["materno"] = parts[-1]
        elif len(parts) == 2:
            payload["nombre"] = parts[0]
            payload["paterno"] = parts[1]

    try:
        from urllib.request import urlopen, Request
        url = "http://127.0.0.1:8765/api/osint"
        body = json.dumps(payload).encode()
        req = Request(url, data=body, headers={
            "Authorization": f"Bearer {getattr(handlers_self, '_token_for_rastreo', '')}",
            "Content-Type": "application/json",
        })
        with urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        return data
    except Exception as e:
        return {"error": str(e)}


def _enriquecer_sujeto_kyc(handlers_self, sujeto: dict) -> dict:
    """Llama al endpoint /api/kyc (broker completo: Tlaloc + Singula + Apify + Moffin + Kiban)."""
    payload = {
        "curp": sujeto.get("curp"),
        "nombre": "",
        "paterno": "",
        "materno": "",
        "fecnac": sujeto.get("fecnac"),
        "rfc": "",
        "email": "",
        "telefono": "",
    }
    nombre = sujeto.get("nombre", "").strip()
    if nombre:
        parts = nombre.split()
        if len(parts) >= 3:
            payload["nombre"] = parts[0]
            payload["paterno"] = parts[-2]
            payload["materno"] = parts[-1]
        elif len(parts) == 2:
            payload["nombre"] = parts[0]
            payload["paterno"] = parts[1]
    try:
        from urllib.request import urlopen, Request
        url = "http://127.0.0.1:8765/api/kyc"
        body = json.dumps(payload).encode()
        req = Request(url, data=body, headers={
            "Authorization": f"Bearer {getattr(handlers_self, '_token_for_rastreo', '')}",
            "Content-Type": "application/json",
        })
        with urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode())
        return data
    except Exception as e:
        return {"error": str(e)}


# =========================================================================
# CONSTRUCCIÓN DEL GRAFO DE RELACIONES
# =========================================================================

def _build_graph(sujetos: list, hechos: list) -> dict:
    """Construye un grafo dirigido: nodos = sujetos, aristas = hechos."""
    nodes = []
    edges = []

    # Mapa id → sujeto
    by_id = {s.get("id"): s for s in sujetos}

    for s in sujetos:
        sid = s.get("id")
        if not sid:
            continue
        nodes.append({
            "id": sid,
            "label": s.get("nombre") or s.get("curp") or sid,
            "rol": s.get("rol", ""),
            "curp": s.get("curp"),
            "tipo": "sujeto",
        })

    # Las aristas son los hechos que vinculan sujetos
    for i, h in enumerate(hechos):
        involucrados = h.get("sujetos_involucrados", [])
        if len(involucrados) < 2:
            continue
        # Aristas dirigidas entre todos los pares (grafo completo de los involucrados)
        for j in range(len(involucrados)):
            for k in range(j + 1, len(involucrados)):
                src = involucrados[j]
                dst = involucrados[k]
                edges.append({
                    "id": f"e{i}_{j}_{k}",
                    "source": src,
                    "target": dst,
                    "fecha": h.get("fecha"),
                    "tipo": h.get("tipo", ""),
                    "descripcion": h.get("descripcion", "")[:200],
                    "fuentes": h.get("fuentes", []),
                    "weight": 1.0,
                })
    return {"nodes": nodes, "edges": edges}


# =========================================================================
# LLAMADA A OLLAMA CLOUD PARA ANÁLISIS IA
# =========================================================================

SYSTEM_PROMPT_RASTREO = """Eres un analista de inteligencia especializado en investigación
estructurada y rastreo situacional. Tu trabajo es analizar un dossier con sujetos y
hechos cronológicos y generar un ANÁLISIS SITUACIONAL objetivo y factual.

Estructura tu respuesta en 4 secciones claramente delimitadas con estos encabezados
exactos:

=== ANÁLISIS SITUACIONAL ===
[2-3 párrafos describiendo el estado actual de la operación, los actores principales,
la dinámica de poder observable y el momento situacional]

=== MAPA DE RIESGOS ===
[Bullets con los riesgos identificados en formato: "• [TIPO_RIESGO]: descripción breve"]
Donde TIPO_RIESGO puede ser: REPUTACIONAL, LEGAL, OPERATIVO, POLÍTICO, FISCAL,
CIBERNÉTICO, FINANCIERO, INTEGRIDAD_FÍSICA]

=== LÍNEAS DE INVESTIGACIÓN SUGERIDAS ===
[Bullets con acciones concretas para profundizar: "• [PRIORIDAD_alta/media/baja]:
descripción de la acción"]

=== CONCLUSIÓN FACTUAL ===
[1 párrafo con la conclusión factual del caso, sin especulación, basada solo en los
datos del dossier]

REGLAS ESTRICTAS:
1. NO especules. NO inventes hechos. SOLO describe lo que los datos muestran.
2. Usa lenguaje factual, tercera persona, tono institucional.
3. Si los datos son insuficientes, indícalo explícitamente.
4. Identifica contradicciones o inconsistencias si las hay.
5. Responde SIEMPRE en español.
6. Tono asertivo: usa "es", "está", "se observa"; evita "podría", "aparenta",
   "sugiere", "es probable", "tal vez", "quizá"."""

USER_PROMPT_TEMPLATE = """Analiza el siguiente dossier de rastreo situacional.

=== SUJETOS ===
{sujetos_json}

=== HECHOS CRONOLÓGICOS ===
{hechos_json}

=== ENRIQUECIMIENTO DE CADA SUJETO ===
{enrichment_json}

=== CONTEXTO ADICIONAL ===
{contexto}

Genera el análisis situacional completo (4 secciones: ANÁLISIS SITUACIONAL, MAPA
DE RIESGOS, LÍNEAS DE INVESTIGACIÓN SUGERIDAS, CONCLUSIÓN FACTUAL)."""


def _llamar_ollama_analisis(dossier: dict) -> dict:
    """Llama a Ollama Cloud para generar el análisis IA."""
    api_key = os.getenv("OLLAMA_API_KEY") or ""
    model = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")

    if not api_key:
        # Buscar en config del proyecto
        try:
            from config import config
            api_key = getattr(config, "ollama_api_key", "") or ""
            model = getattr(config, "ollama_model", model) or model
        except Exception:
            pass

    if not api_key:
        return {
            "error": "OLLAMA_API_KEY no configurada",
            "narrative": "",
            "riesgos": [],
            "lineas_investigacion": [],
            "conclusion": "",
            "status": "no_api_key",
        }

    if requests is None:
        return {
            "error": "módulo requests no disponible",
            "status": "no_requests_lib",
            "narrative_full": "",
        }

    # Serializar el dossier para el prompt
    subjects_json = json.dumps(
        [{"id": s.get("id"), "nombre": s.get("nombre"), "rol": s.get("rol"),
          "curp": s.get("curp"), "datos_verificados": s.get("datos_verificados", {})}
         for s in dossier.get("sujetos", [])],
        indent=2, ensure_ascii=False, default=str
    )[:6000]

    hechos_json = json.dumps(
        dossier.get("hechos", []), indent=2, ensure_ascii=False, default=str
    )[:4000]

    enrichment_json = json.dumps(
        {s.get("id"): {
            "padron_encontrado": bool(s.get("datos_verificados", {}).get("padron")),
            "imss_count": len(s.get("datos_verificados", {}).get("imss_asegurado", {}).get("rows", [])),
            "apify_perfiles": len(s.get("datos_verificados", {}).get("apify", {}).get("resultados", {}).get("name", {}).get("plataformas", [])),
            "tlaloc_status": s.get("datos_verificados", {}).get("tlaloc", {}).get("status", "?"),
            "github_count": len(s.get("datos_verificados", {}).get("osint", {}).get("github", [])),
            "google_count": len(s.get("datos_verificados", {}).get("osint", {}).get("google_results", [])),
        } for s in dossier.get("sujetos", [])},
        indent=2, ensure_ascii=False, default=str
    )[:2000]

    contexto = dossier.get("contexto", "")

    user_msg = USER_PROMPT_TEMPLATE.format(
        sujetos_json=subjects_json,
        hechos_json=hechos_json,
        enrichment_json=enrichment_json,
        contexto=contexto,
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT_RASTREO},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_predict": 4096,
        },
    }

    try:
        r = requests.post(
            "https://ollama.com/api/chat",
            json=payload,
            timeout=120,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        r.raise_for_status()
        result = r.json()
        message = result.get("message", {}).get("content", "")
        thinking = result.get("message", {}).get("thinking", "")
        return {
            "status": "ok",
            "model": result.get("model", model),
            "narrative_full": message,
            "thinking": thinking,
            "tokens_in": result.get("prompt_eval_count", 0),
            "tokens_out": result.get("eval_count", 0),
            "duration_ms": (result.get("total_duration", 0) or 0) / 1_000_000,
            # Parseo básico por secciones
            "secciones": _parse_secciones(message),
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "narrative_full": ""}


def _parse_secciones(text: str) -> dict:
    """Divide el texto de la IA por secciones usando los encabezados ===."""
    secciones = {
        "analisis_situacional": "",
        "mapa_riesgos": "",
        "lineas_investigacion": "",
        "conclusion_factual": "",
    }
    if not text:
        return secciones

    # Regex para los delimitadores === TITULO ===
    parts = re.split(r"===\s*([A-ZÁÉÍÓÚÑ][^=]*?)\s*===", text)
    # parts: ['texto previo', 'TITULO1', 'contenido1', 'TITULO2', 'contenido2', ...]
    for i in range(1, len(parts), 2):
        titulo = parts[i].strip().upper()
        contenido = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if "ANÁLISIS SITUACIONAL" in titulo or "ANALISIS SITUACIONAL" in titulo:
            secciones["analisis_situacional"] = contenido
        elif "MAPA DE RIESGOS" in titulo:
            secciones["mapa_riesgos"] = contenido
        elif "LÍNEAS DE INVESTIGACIÓN" in titulo or "LINEAS DE INVESTIGACION" in titulo:
            secciones["lineas_investigacion"] = contenido
        elif "CONCLUSIÓN FACTUAL" in titulo or "CONCLUSION FACTUAL" in titulo:
            secciones["conclusion_factual"] = contenido

    # Fallback: si no se detectó ninguna sección, poner todo en analisis_situacional
    if not any(secciones.values()):
        secciones["analisis_situacional"] = text.strip()

    return secciones


# =========================================================================
# HANDLER PRINCIPAL DEL ENDPOINT
# =========================================================================

def _handle_rastreo_relacion(handlers_self):
    """POST /api/v1/rastreo/relacion

    Recibe un dossier con sujetos + hechos + contexto, enriquece cada sujeto
    contra las bases locales, construye el grafo de relaciones y genera un
    análisis situacional con Ollama Cloud.

    Body:
      {
        "sujetos": [{"id": "A", "nombre": "...", "curp": "...", "rol": "..."}, ...],
        "hechos": [{"fecha": "YYYY-MM-DD", "tipo": "...", "descripcion": "...",
                     "sujetos_involucrados": ["A","B"], "fuentes": ["..."]}, ...],
        "contexto": "texto libre",
        "use_ai": true/false  # default true
      }

    Returns:
      {
        "metadata": {...},
        "sujetos": [...con datos verificados...],
        "hechos": [...],
        "graph": {"nodes": [...], "edges": [...]},
        "ai_analysis": {...},
        "fuentes_consultadas": [...]
      }
    """
    t0 = time.time()

    # Auth
    session = handlers_self._require_session()
    if not session:
        return

    # Parsear body
    try:
        ln = int(handlers_self.headers.get("Content-Length", 0))
        raw = handlers_self.rfile.read(ln) if ln else b"{}"
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as e:
        handlers_self._json(400, {"error": f"JSON inválido: {e}"})
        return

    sujetos = payload.get("sujetos", [])
    hechos = payload.get("hechos", [])
    contexto = payload.get("contexto", "")
    use_ai = payload.get("use_ai", True)

    if not sujetos:
        handlers_self._json(400, {"error": "campo 'sujetos' requerido (lista no vacía)"})
        return

    # Token para llamadas internas (usar el mismo Bearer)
    token = handlers_self.headers.get("Authorization", "").replace("Bearer ", "")
    handlers_self._token_for_rastreo = token

    # 1) Enriquecer cada sujeto
    enriched = []
    fuentes_usadas = set()
    for s in sujetos:
        sid = s.get("id")
        curp = s.get("curp", "")
        datos_verificados = {}

        # 1a) Padrón
        padron_data = _enriquecer_sujeto_padron(handlers_self, curp)
        if padron_data.get("padron"):
            datos_verificados["padron"] = padron_data["padron"]
            fuentes_usadas.add("padron_ine")

        # 1b) APIs externas (IMSS, CFE, ATT, REPUVE, Telcel, ISSSTE, Empleadores)
        if curp and len(curp) == 18:
            apis_data = _enriquecer_sujeto_apis(handlers_self, s)
            if isinstance(apis_data, dict):
                for k in ["imss_asegurado", "imss_salud", "xwalk"]:
                    if apis_data.get(k, {}).get("count", 0) > 0:
                        datos_verificados[k] = apis_data[k]
                        fuentes_usadas.add(f"api_{k}")
                # Anotar KPIs
                if apis_data.get("kpi"):
                    datos_verificados["kpi"] = apis_data["kpi"]

        # 1c) OSINT (Tlaloc via RENAPO, GitHub, LinkedIn, Google)
        osint_data = _enriquecer_sujeto_osint(handlers_self, s)
        if isinstance(osint_data, dict):
            tlaloc = osint_data.get("tlaloc")
            if tlaloc and tlaloc.get("valid"):
                datos_verificados["tlaloc"] = tlaloc
                fuentes_usadas.add("tlaloc_renapo")
            datos_verificados["osint"] = {
                "github": osint_data.get("github", []),
                "linkedin": osint_data.get("linkedin", []),
                "google_results": osint_data.get("google_results", []),
                "sintesis": osint_data.get("sintesis", {}),
            }

        # 1d) KYC broker (Apify + Tlaloc via RENAPO)
        kyc_data = _enriquecer_sujeto_kyc(handlers_self, s)
        if isinstance(kyc_data, dict):
            apify = kyc_data.get("resultados", {}).get("apify")
            if apify:
                datos_verificados["apify"] = apify
                fuentes_usadas.add("apify_kyc")
            # Tlaloc via KYC broker (incluso si no aparece en padrón)
            tlaloc_via_kyc = kyc_data.get("resultados", {}).get("tlaloc")
            if tlaloc_via_kyc and tlaloc_via_kyc.get("valid"):
                # No sobrescribir si ya tenemos uno mejor
                if not datos_verificados.get("tlaloc"):
                    datos_verificados["tlaloc"] = tlaloc_via_kyc
                    fuentes_usadas.add("tlaloc_renapo")
            # Singula, Moffin, etc.
            for prov, val in (kyc_data.get("resultados") or {}).items():
                if prov not in ("apify", "tlaloc") and val:
                    datos_verificados[f"broker_{prov}"] = val
                    fuentes_usadas.add(f"broker_{prov}")

        enriched.append({
            **s,
            "datos_verificados": datos_verificados,
        })

    # 2) Construir grafo
    graph = _build_graph(enriched, hechos)

    # 3) Análisis IA con Ollama Cloud
    ai_analysis = {"status": "skipped"}
    if use_ai:
        dossier = {
            "sujetos": enriched,
            "hechos": hechos,
            "contexto": contexto,
        }
        ai_result = _llamar_ollama_analisis(dossier)
        ai_analysis = ai_result

    # 4) Respuesta final
    elapsed = int((time.time() - t0) * 1000)

    response = {
        "metadata": {
            "timestamp": datetime.datetime.now().isoformat(),
            "duration_ms": elapsed,
            "sujetos_count": len(enriched),
            "hechos_count": len(hechos),
            "fuentes_consultadas": sorted(list(fuentes_usadas)),
            "ai_model": os.getenv("OLLAMA_MODEL", "gpt-oss:20b"),
        },
        "sujetos": enriched,
        "hechos": hechos,
        "graph": graph,
        "ai_analysis": ai_analysis,
        "contexto": contexto,
    }

    handlers_self._json(200, response)
