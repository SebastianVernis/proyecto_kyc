#!/usr/bin/env python3
"""Cliente Gemini para reportes IA y análisis estructurados.

Usa la API REST oficial de Google AI Studio para evitar acoplar el backend a
una versión concreta del SDK. El cliente expone una interfaz pequeña y
compatible con el resto de providers de la Plataforma Encuentra.
"""
from __future__ import annotations

import json
import os
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover - se reporta al invocar
    requests = None

from .base import BaseProvider, ProviderError

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-2.5-flash"


class GeminiClient(BaseProvider):
    """Cliente REST de Gemini con salida de texto y JSON opcional."""

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 timeout: int = 120):
        super().__init__(name="gemini", api_key=api_key or os.getenv("GEMINI_API_KEY"),
                         base_url=GEMINI_API_URL, timeout=timeout)
        self.model = model or os.getenv("GEMINI_MODEL") or DEFAULT_MODEL

    def _generate(self, contents: list[dict[str, Any]], *, system: str = "",
                  temperature: float = 0.3, max_tokens: int = 4096,
                  json_mode: bool = False) -> dict[str, Any]:
        if not self.api_key:
            raise ProviderError("gemini", "GEMINI_API_KEY no configurada")
        if requests is None:
            raise ProviderError("gemini", "módulo requests no disponible")

        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"

        url = f"{self.base_url}/{self.model}:generateContent"
        try:
            response = requests.post(
                url, params={"key": self.api_key}, json=body,
                timeout=self.timeout,
                headers={"Content-Type": "application/json"},
            )
            if response.status_code >= 400:
                raise ProviderError("gemini", f"HTTP {response.status_code}: {response.text[:300]}", response.status_code)
            data = response.json()
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("gemini", str(exc)) from exc

        candidates = data.get("candidates") or []
        parts = ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        if not text:
            raise ProviderError("gemini", "respuesta sin texto")
        usage = data.get("usageMetadata") or {}
        return {
            "model": self.model,
            "message": text,
            "tokens_in": usage.get("promptTokenCount", 0),
            "tokens_out": usage.get("candidatesTokenCount", 0),
            "raw": data,
        }

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.3,
             max_tokens: int = 4096, json_mode: bool = False) -> dict[str, Any]:
        system_parts = [m.get("content", "") for m in messages if m.get("role") == "system"]
        contents = []
        for message in messages:
            if message.get("role") == "system":
                continue
            contents.append({
                "role": "model" if message.get("role") == "assistant" else "user",
                "parts": [{"text": message.get("content", "")}],
            })
        return self._generate(contents, system="\n\n".join(system_parts),
                              temperature=temperature, max_tokens=max_tokens,
                              json_mode=json_mode)

    def generate_report_narrative(self, subject_data: dict, style: str = "formal",
                                  extra_context: str = "") -> str:
        prompts = {
            "formal": "Genera un resumen ejecutivo formal, objetivo e institucional en español. Solo describe hechos presentes en los datos y señala inconsistencias sin especular. Responde en 2 o 3 párrafos.",
            "investigative": "Genera un análisis preliminar profesional en español, con observaciones clave y banderas de verificación. No inventes hechos ni afirmes lo que no esté en los datos.",
            "concise": "Genera un resumen factual breve en español, de máximo un párrafo.",
        }
        data = str(subject_data)[:12000]
        user = f"Datos del sujeto y enriquecimientos:\n{data}"
        if extra_context:
            user += f"\nContexto adicional:\n{extra_context}"
        result = self.chat([
            {"role": "system", "content": prompts.get(style, prompts["formal"])},
            {"role": "user", "content": user},
        ], temperature=0.2, max_tokens=2048)
        return result["message"]


def validate_match_relationship(sujeto: dict, titular_cfe: dict,
                                match_local: dict, contexto: str = "") -> dict:
    """Valida un match de parentesco entre sujeto y titular CFE.

    Recibe:
      - sujeto: dict con datos del sujeto central (nombre, paterno, materno)
      - titular_cfe: dict con datos del titular CFE (numero_servicio, titular,
        direccion, colonia, cp)
      - match_local: dict con el resultado del matching local (score, tipo,
        confianza, detalle, titular_nombre_relacionado)
      - contexto: contexto adicional (ej. "vecino de calle", "mismo CP")

    Retorna dict con:
      - confirmado: bool (Gemini considera la relación plausible)
      - confianza_ia: float 0..1 (Gemini)
      - score_local: float (eco del score local)
      - tipo_local: str (eco del tipo local)
      - narrativa: str (1-2 frases explicando la relación, o por qué no)

    Si no hay GEMINI_API_KEY configurada, devuelve un eco del match local
    sin Gemini (para que el reporte siga funcionando sin IA).
    """
    try:
        client = make_client_from_config()
    except Exception:
        client = None
    if client is None:
        return {
            "confirmado": match_local.get("score_match_titular", 0) >= 0.5,
            "confianza_ia": None,
            "score_local": match_local.get("score_match_titular", 0),
            "tipo_local": match_local.get("tipo_relacion_titular", "sin_match"),
            "narrativa": (
                f"Validación IA no disponible (GEMINI_API_KEY no configurada). "
                f"Score local: {match_local.get('score_match_titular', 0):.2f}. "
                f"Mecanismo: {match_local.get('detalle_match_titular', '')}"
            ),
            "ia_disponible": False,
        }

    sys_prompt = (
        "Eres un analista KYC (Know Your Customer) en una plataforma mexicana de "
        "inteligencia. Tu trabajo es validar si un titular de servicio CFE "
        "probablemente es familiar (o la misma persona) que el sujeto bajo "
        "investigación, basándote ESTRICTAMENTE en los datos entregados. "
        "Responde en JSON con esta forma:\n"
        '{"confirmado": bool, "confianza": 0.0-1.0, "tipo_relacion": str, "narrativa": str}\n'
        "Donde:\n"
        "- confirmado: true si consideras la relación PLAUSIBLE (no requiere certeza).\n"
        "- confianza: 0.0 (nada plausible) a 1.0 (casi seguro).\n"
        "- tipo_relacion: 'mismo_sujeto', 'familiar_directo', 'familiar_lejano', 'sin_relacion'.\n"
        "- narrativa: UNA frase breve y factual que resuma por qué.\n"
        "IMPORTANTE: no inventes hechos. Si los datos no son concluyentes, "
        "marca confirmado=false con confianza baja y narrativa explicando el motivo."
    )
    user_prompt = (
        f"Sujeto bajo investigación:\n"
        f"  - Nombre: {sujeto.get('nombre', '')}\n"
        f"  - Paterno: {sujeto.get('paterno', '')}\n"
        f"  - Materno: {sujeto.get('materno', '')}\n"
        f"  - Domicilio declarado: {sujeto.get('calle', '')} #{sujeto.get('ext', '')}, "
        f"{sujeto.get('colonia', '')}, CP {sujeto.get('cp', '')}\n\n"
        f"Titular del servicio CFE:\n"
        f"  - Servicio No.: {titular_cfe.get('numero_servicio', '')}\n"
        f"  - Nombre completo: {titular_cfe.get('titular', '')}\n"
        f"  - Domicilio del recibo: {titular_cfe.get('direccion', '')}, "
        f"{titular_cfe.get('colonia', '')}, CP {titular_cfe.get('cp', '')}\n\n"
        f"Match local (heurístico):\n"
        f"  - Score: {match_local.get('score_match_titular', 0):.2f}\n"
        f"  - Tipo: {match_local.get('tipo_relacion_titular', '')}\n"
        f"  - Match contra: {match_local.get('titular_nombre_relacionado', 'N/A')}\n"
        f"  - Mecanismo: {match_local.get('detalle_match_titular', '')}\n"
        f"  - Coincidencia domicilio: {match_local.get('coincidencia_domicilio', False)}\n\n"
    )
    if contexto:
        user_prompt += f"Contexto adicional: {contexto}\n"
    try:
        result = client.chat([
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ], temperature=0.1, max_tokens=512, json_mode=True)
        parsed = json.loads(result["message"])
        return {
            "confirmado": bool(parsed.get("confirmado", False)),
            "confianza_ia": float(parsed.get("confianza", 0)),
            "score_local": match_local.get("score_match_titular", 0),
            "tipo_local": match_local.get("tipo_relacion_titular", "sin_match"),
            "tipo_ia": parsed.get("tipo_relacion", ""),
            "narrativa": parsed.get("narrativa", "").strip(),
            "ia_disponible": True,
        }
    except Exception as e:
        return {
            "confirmado": match_local.get("score_match_titular", 0) >= 0.5,
            "confianza_ia": None,
            "score_local": match_local.get("score_match_titular", 0),
            "tipo_local": match_local.get("tipo_relacion_titular", "sin_match"),
            "narrativa": f"Error IA: {str(e)[:200]}. Score local: {match_local.get('score_match_titular', 0):.2f}.",
            "ia_disponible": False,
            "ia_error": str(e)[:200],
        }


def make_client_from_config() -> GeminiClient | None:
    try:
        from config import config
        if not config.gemini_api_key:
            return None
        return GeminiClient(config.gemini_api_key, model=config.gemini_model)
    except Exception:
        return None
