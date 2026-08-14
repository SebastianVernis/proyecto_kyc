#!/usr/bin/env python3
"""apify_osint.py — wrapper del apify_broker como proveedor de OSINT.

Usa la lógica ya implementada en apify_broker.py, con la misma interfaz
de los otros proveedores (health, full_osint).
"""

from __future__ import annotations

import sys
from pathlib import Path

# importamos el broker ya creado
sys.path.insert(0, str(Path(__file__).parent.parent))
from apify_broker import ApifyBroker  # noqa: E402

from .base import BaseProvider, ProviderError  # noqa: E402


class ApifyOSINTClient(BaseProvider):
    """Wrapper de ApifyBroker con interfaz uniforme de proveedor.

    Hereda de BaseProvider solo por convención; en realidad delega todo
    al ApifyBroker ya existente (que usa la API REST de Apify).
    """

    def __init__(self, api_key: str = None, timeout: int = 180):
        # No llamamos super().__init__() porque ApifyBroker tiene su propia lógica
        self.name = "apify"
        self.api_key = api_key
        self.timeout = timeout
        self.broker = ApifyBroker(api_key)

    def health(self) -> dict:
        """Verifica token llamando a /v2/users/me."""
        try:
            import requests
            r = requests.get(
                f"https://api.apify.com/v2/users/me?token={self.api_key}",
                timeout=10,
            )
            if r.status_code == 200:
                u = r.json()
                return {"ok": True, "user": u.get("username"), "email": u.get("email")}
            return {"ok": False, "status": r.status_code}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def full_osint(self, nombre: str = "", paterno: str = "",
                   materno: str = "", email: str = "",
                   telefono: str = "", max_results: int = 15) -> dict:
        """Búsqueda comprehensiva de redes sociales + validación IA. DEPRECATED: usar full_dossier."""
        return self.full_dossier(
            email=email, phone=telefono,
            nombre=nombre, paterno=paterno, materno=materno,
            max_results=max_results,
        )

    def full_dossier(self, *, email: str = "", phone: str = "",
                     nombre: str = "", paterno: str = "", materno: str = "",
                     company: str = "", website: str = "",
                     max_results: int = 10) -> dict:
        """Pipeline OSINT completo con validación IA (Ollama Cloud)."""
        return self.broker.full_dossier(
            email=email, phone=phone,
            nombre=nombre, paterno=paterno, materno=materno,
            company=company, website=website,
            max_results=max_results,
        )

    def find_by_name(self, nombre: str, max_results: int = 20) -> dict:
        return self.broker.find_by_name(nombre, max_results=max_results)

    def find_by_email(self, email: str) -> dict:
        return self.broker.find_by_email(email)

    def find_by_username(self, username: str) -> dict:
        return self.broker.find_by_username(username)

    def find_by_phone(self, phone: str) -> dict:
        return self.broker.find_by_phone(phone)

    def scrape_website(self, url: str) -> dict:
        return self.broker.scrape_website(url)


def make_client_from_config() -> "ApifyOSINTClient | None":
    try:
        from config import config
        if not config.apify_token:
            return None
        return ApifyOSINTClient(config.apify_token)
    except Exception:
        return None
