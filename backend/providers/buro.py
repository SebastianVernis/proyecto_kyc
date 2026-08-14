#!/usr/bin/env python3
"""buro.py — Cliente Buró de Crédito (requiere alta comercial).

Documentación: https://apif.burodecredito.com.mx
Costo: $5-15/consulta + setup comercial.

Requiere:
  - API key
  - Usuario
  - Contraseña
  - Cuenta comercial aprobada
"""

from __future__ import annotations

from .base import BaseProvider, ProviderError, normalize_curp, normalize_rfc


class BuroClient(BaseProvider):
    """Cliente Buró de Crédito API."""

    def __init__(self, api_key: str = None, username: str = None,
                 password: str = None,
                 base_url: str = "https://apif.burodecredito.com.mx/api/v1",
                 timeout: int = 30):
        super().__init__(name="buro", api_key=api_key, base_url=base_url, timeout=timeout)
        self.username = username
        self.password = password

    def _auth_headers(self) -> dict:
        return {
            "Authorization": f"Basic {self.api_key}",
            "Usuario": self.username or "",
            "Contrasena": self.password or "",
        }

    def consulta_reporte(self, nombre: str, paterno: str, materno: str,
                         fecnac: str, rfc: str = None, curp: str = None) -> dict:
        """Consulta reporte de crédito completo (requiere consentimiento)."""
        data = {
            "PrimerNombre": nombre,
            "ApellidoPaterno": paterno,
            "ApellidoMaterno": materno,
            "FechaNacimiento": fecnac,
        }
        if rfc:
            data["Rfc"] = normalize_rfc(rfc)
        if curp:
            data["Curp"] = normalize_curp(curp)
        try:
            r = self.session.post(
                f"{self.base_url}/reporte", json=data,
                headers=self._auth_headers(), timeout=self.timeout,
            )
            if r.status_code >= 400:
                raise ProviderError("buro", f"HTTP {r.status_code}: {r.text[:200]}", r.status_code)
            return r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text}
        except Exception as e:
            raise ProviderError("buro", str(e))

    def health(self) -> dict:
        try:
            r = self.session.get(
                f"{self.base_url}/health", headers=self._auth_headers(), timeout=10,
            )
            return {"ok": r.status_code == 200, "status": r.status_code}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def make_client_from_config() -> "BuroClient | None":
    try:
        from config import config
        if not config.buro_api_key:
            return None
        return BuroClient(
            api_key=config.buro_api_key,
            username=config.buro_username,
            password=config.buro_password,
        )
    except Exception:
        return None
