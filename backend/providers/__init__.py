#!/usr/bin/env python3
"""providers/ — clientes para cada API externa.

Cada proveedor expone una clase con métodos consistentes.
Los métodos devuelven dicts JSON normalizados.
"""

from .tlaloc import TlalocClient
from .singula import SingulaClient
from .moffin import MoffinClient
from .kiban import KibanClient
from .apify_osint import ApifyOSINTClient
from .consultaunica import ConsultaUnicaClient
from .ollama_cloud import OllamaCloudClient
from .gemini import GeminiClient
from .base import BaseProvider, ProviderError, normalize_curp, normalize_rfc

__all__ = [
    "TlalocClient",
    "SingulaClient",
    "MoffinClient",
    "KibanClient",
    "ApifyOSINTClient",
    "ConsultaUnicaClient",
    "OllamaCloudClient",
    "GeminiClient",
    "BaseProvider",
    "ProviderError",
    "normalize_curp",
    "normalize_rfc",
]
