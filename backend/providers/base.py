#!/usr/bin/env python3
"""base.py — clase base para todos los proveedores + helpers."""

from __future__ import annotations

import re
import time
from typing import Any, Optional
from urllib.parse import urlencode

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


class ProviderError(Exception):
    """Error de un proveedor de API."""
    def __init__(self, provider: str, message: str, status: int = None):
        self.provider = provider
        self.message = message
        self.status = status
        super().__init__(f"[{provider}] {message}")


class BaseProvider:
    """Clase base con retry, timeout y auth."""

    def __init__(self, name: str, api_key: str = None, base_url: str = "",
                 timeout: int = 30, retries: int = 2):
        self.name = name
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        if not HAS_REQUESTS:
            raise ImportError("instala requests: pip install requests")
        self.session = requests.Session()
        if api_key:
            self.session.headers["Authorization"] = f"Bearer {api_key}"

    def _get(self, path: str, params: dict = None, **kwargs) -> Any:
        return self._request("GET", path, params=params, **kwargs)

    def _post(self, path: str, data: dict = None, json: dict = None) -> Any:
        return self._request("POST", path, data=data, json=json)

    def _patch(self, path: str, data: dict = None, json: dict = None) -> Any:
        return self._request("PATCH", path, data=data, json=json)

    def _request(self, method: str, path: str, params: dict = None,
                 data: dict = None, json: dict = None) -> Any:
        url = f"{self.base_url}{path}"
        last_err = None
        for attempt in range(self.retries + 1):
            try:
                kwargs = {"timeout": self.timeout, "params": params,
                          "data": data, "json": json}
                r = self.session.request(method, url, **kwargs)
                if r.status_code == 429:  # rate limit
                    time.sleep(2 ** attempt)
                    continue
                if r.status_code >= 500:  # server error, retry
                    last_err = ProviderError(self.name, f"HTTP {r.status_code}: {r.text[:200]}", r.status_code)
                    time.sleep(0.5 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    raise ProviderError(
                        self.name,
                        f"HTTP {r.status_code}: {r.text[:200]}",
                        r.status_code
                    )
                # éxito
                if r.status_code == 204:
                    return None
                if r.headers.get("content-type", "").startswith("application/json"):
                    return r.json()
                return r.text
            except requests.exceptions.Timeout as e:
                last_err = ProviderError(self.name, f"timeout: {e}")
            except requests.exceptions.RequestException as e:
                last_err = ProviderError(self.name, f"request error: {e}")
            time.sleep(0.5 * (attempt + 1))
        raise last_err or ProviderError(self.name, "max retries excedidos")


# === normalizadores ===

def normalize_curp(curp: str) -> str:
    """Limpia y valida formato de CURP."""
    if not curp:
        return ""
    c = re.sub(r"[^A-Z0-9]", "", curp.upper())
    return c if len(c) == 18 else curp.upper()


def normalize_rfc(rfc: str) -> str:
    """Limpia y valida formato de RFC (12 o 13 caracteres)."""
    if not rfc:
        return ""
    r = re.sub(r"[^A-Z0-9&Ñ]", "", rfc.upper())
    return r if 12 <= len(r) <= 13 else rfc.upper()


def normalize_nombre(s: str) -> str:
    """Limpia y normaliza un nombre (mayúsculas sin acentos problemáticos)."""
    if not s:
        return ""
    return re.sub(r"\s+", " ", s.strip().upper())
