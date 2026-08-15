#!/usr/bin/env python3
"""ollama_cloud.py — Cliente para Ollama Cloud (modelos flagship en la nube).

Documentación: https://docs.ollama.com/cloud
Base URL: https://ollama.com/api
Auth: Authorization: Bearer OLLAMA_API_KEY

Modelos flagship (deepseek-v4-pro default, gpt-oss:120b, mistral-large-3, etc.)
"""

from __future__ import annotations

import json
import os
from typing import Any

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

from .base import BaseProvider, ProviderError

OLLAMA_CLOUD_URL = "https://ollama.com/api"
DEFAULT_MODEL = "deepseek-v4-pro"


class OllamaCloudClient(BaseProvider):
    """Cliente para modelos flagship de Ollama Cloud.

    API compatible con OpenAI /api/chat y nativa Ollama /api/chat.
    """

    def __init__(self, api_key: str = None, model: str = None,
                 base_url: str = OLLAMA_CLOUD_URL, timeout: int = 120):
        super().__init__(name="ollama", api_key=api_key, base_url=base_url, timeout=timeout)
        self.model = model or os.getenv("OLLAMA_MODEL") or DEFAULT_MODEL
        # Ollama usa Authorization: Bearer, igual que ya configuramos en base

    def health(self) -> dict:
        try:
            r = self._get("/tags")
            models = r.get("models", []) if isinstance(r, dict) else []
            return {
                "ok": True,
                "model": self.model,
                "models_disponibles": [m.get("name", "?") for m in models[:20]],
            }
        except ProviderError as e:
            return {"ok": False, "error": str(e)}

    def chat(self, messages: list[dict], model: str = None,
             temperature: float = 0.3, max_tokens: int = 4096,
             stream: bool = False, format_json: bool = False) -> dict:
        """POST /api/chat — envía mensajes al modelo.

        Args:
            messages: lista de {role, content} (OpenAI format)
            model: override del modelo
            temperature: 0=determinístico, 1=creativo
            max_tokens: límite de output
            stream: si True devuelve generator
            format_json: si True pide JSON estructurado
        """
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": stream,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if format_json:
            payload["format"] = "json"

        if stream:
            return self._stream_chat(payload)

        try:
            r = self._post("/chat", json=payload)
            if not isinstance(r, dict):
                raise ProviderError("ollama", f"respuesta no es dict: {type(r)}")
            return {
                "model": r.get("model"),
                "message": r.get("message", {}).get("content", ""),
                "thinking": r.get("message", {}).get("thinking"),
                "tokens_in": r.get("prompt_eval_count", 0),
                "tokens_out": r.get("eval_count", 0),
                "duration_ms": (r.get("total_duration", 0) or 0) / 1_000_000,
                "raw": r,
            }
        except ProviderError as e:
            return {"error": str(e), "status": e.status}

    def _stream_chat(self, payload: dict):
        """Generator para streaming."""
        url = f"{self.base_url}/chat"
        with requests.post(url, json=payload, stream=True, timeout=self.timeout + 30,
                          headers={"Authorization": f"Bearer {self.token}"}) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if line:
                    try:
                        yield json.loads(line.decode("utf-8"))
                    except json.JSONDecodeError:
                        pass

    def generate_report_narrative(self, subject_data: dict, style: str = "formal",
                                 extra_context: str = "") -> str:
        """Genera una narrativa interpretativa de los datos del sujeto.

        Args:
            subject_data: dict con datos del padrón + enrichment
            style: 'formal' | 'investigative' | 'concise'
        """
        system_prompts = {
            "formal": (
                "Eres un analista de inteligencia especializado en verificación de identidad "
                "y due diligence. Tu trabajo es generar un RESUMEN EJECUTIVO formal, "
                "profesional y objetivo sobre una persona. Usa tono institucional, tercera persona, "
                "lenguaje claro. Identifica inconsistencias si las hay. NO especules, "
                "solo describe lo que los datos muestran. Responde en español, 2-3 párrafos."
            ),
            "investigative": (
                "Eres un investigador privado generando un informe preliminar sobre una persona. "
                "Tono profesional, observaciones clave, posibles banderas rojas. "
                "2-3 párrafos en español, tercera persona, lenguaje directo."
            ),
            "concise": (
                "Genera un resumen breve y directo del perfil de la persona, máximo 1 párrafo. "
                "Solo hechos confirmados. Español."
            ),
        }
        system = system_prompts.get(style, system_prompts["formal"])

        # serializar datos
        data_str = json.dumps(subject_data, indent=2, default=str, ensure_ascii=False)[:8000]

        user_msg = f"""Analiza los siguientes datos y genera el resumen ejecutivo solicitado:

{data_str}

{f'Contexto adicional: {extra_context}' if extra_context else ''}

Responde en español, sin formato markdown (solo párrafos de texto)."""

        r = self.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=2048,
        )
        return r.get("message", "")


def make_client_from_config() -> "OllamaCloudClient | None":
    try:
        from config import config
        if not config.ollama_api_key:
            return None
        return OllamaCloudClient(config.ollama_api_key, model=config.ollama_model)
    except Exception:
        return None
