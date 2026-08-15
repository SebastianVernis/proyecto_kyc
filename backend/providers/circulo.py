#!/usr/bin/env python3
"""circulo.py — Cliente Círculo de Crédito (requiere alta comercial + certificado).

Documentación: https://developer.circulodecredito.com.mx
Costo: $5-15/consulta + setup comercial con certificado .p12.

Requiere:
  - Usuario
  - Contraseña
  - API key
  - Certificado .p12 + password
"""

from __future__ import annotations

from .base import BaseProvider, ProviderError, normalize_curp, normalize_rfc


class CirculoCreditoClient(BaseProvider):
    """Cliente Círculo de Crédito API.

    Implementa los productos principales:
      - Reporte de Crédito Consolidado + FICO Score
      - Reporte de Crédito Personas Físicas
      - PLD Check
    """

    PRODUCTOS = {
        "rcc_fico": "/rcc-fico-score",
        "rcc_fico_pld": "/rcc-fico-pld",
        "rc_pf": "/reporte-credito-pf",
        "extended_score": "/fico-extended-score",
    }

    def __init__(self, api_key: str = None, username: str = None,
                 password: str = None, cert_path: str = None,
                 cert_password: str = None,
                 base_url: str = "https://api.circulodecredito.com.mx/v2",
                 timeout: int = 60):
        super().__init__(name="cdc", api_key=api_key, base_url=base_url, timeout=timeout)
        self.username = username
        self.password = password
        self.cert_path = cert_path
        self.cert_password = cert_password

        # configurar certificado si existe
        if cert_path and Path(cert_path).exists():
            self.session.cert = (cert_path, cert_password)
        elif cert_path:
            raise FileNotFoundError(f"cert CDC no encontrado: {cert_path}")

    def _auth(self) -> tuple:
        """Genera auth básico + headers requeridos por CDC."""
        import base64
        token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def consulta_reporte(self, producto: str, persona: dict) -> dict:
        """Consulta un reporte de Círculo de Crédito.

        persona debe incluir:
          - PrimerNombre, ApellidoPaterno, ApellidoMaterno
          - FechaNacimiento (DD/MM/YYYY)
          - Rfc o Curp
          - Domicilio.Direccion, Domicilio.Colonia, etc.
        """
        if producto not in self.PRODUCTOS:
            raise ProviderError("cdc", f"producto '{producto}' no soportado. "
                                 f"Opciones: {list(self.PRODUCTOS)}")
        # completar campos requeridos
        persona.setdefault("ApellidoAdicional", None)
        persona.setdefault("Nacionalidad", "MX")
        persona.setdefault("Residencia", None)
        persona.setdefault("EstadoCivil", None)
        persona.setdefault("Sexo", None)
        # domicilio
        dom = persona.get("Domicilio", {})
        dom.setdefault("ColoniaPoblacion", None)
        dom.setdefault("Ciudad", None)
        dom.setdefault("FechaResidencia", None)
        dom.setdefault("NumeroTelefono", None)
        dom.setdefault("TipoDomicilio", None)
        dom.setdefault("TipoAsentamiento", None)

        path = self.PRODUCTOS[producto]
        try:
            r = self.session.post(
                f"{self.base_url}{path}",
                json=persona,
                headers={**self._auth(), "x-api-key": self.api_key or ""},
                timeout=self.timeout,
            )
            if r.status_code >= 400:
                raise ProviderError("cdc", f"HTTP {r.status_code}: {r.text[:300]}", r.status_code)
            return r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text}
        except Exception as e:
            raise ProviderError("cdc", str(e))

    def health(self) -> dict:
        return {"ok": bool(self.cert_path or self.api_key)}


def make_client_from_config() -> "CirculoCreditoClient | None":
    try:
        from config import config
        if not config.cdc_api_key or not config.cdc_username:
            return None
        return CirculoCreditoClient(
            api_key=config.cdc_api_key,
            username=config.cdc_username,
            password=config.cdc_password,
            cert_path=config.cdc_cert_path,
            cert_password=config.cdc_cert_password,
        )
    except Exception:
        return None
