#!/usr/bin/env python3
"""kiban.py — Cliente Kiban (listas negras PEP, OFAC, sanciones).

Documentación: https://docs.kiban.com
Costo: $2-5/consulta.

Servicios:
  - Validar CURP/RFC contra listas de sanciones
  - Conector INE (lista nominal)
"""

from __future__ import annotations

from .base import BaseProvider, ProviderError, normalize_curp, normalize_rfc


class KibanClient(BaseProvider):
    """Cliente Kiban para listas negras y KYC."""

    def __init__(self, api_key: str = None, base_url: str = "https://docs.kiban.com/api/v1",
                 timeout: int = 30):
        super().__init__(name="kiban", api_key=api_key, base_url=base_url, timeout=timeout)

    def validate_curp(self, curp: str) -> dict:
        return self._post("/curp/validate", json={"curp": normalize_curp(curp)})

    def check_pep_ofac(self, nombre: str, apellido_paterno: str = "",
                       apellido_materno: str = "", fecha_nac: str = None) -> dict:
        """Verifica si la persona está en listas PEP, OFAC, ONU, etc."""
        data = {
            "nombre": nombre,
            "apellidoPaterno": apellido_paterno,
            "apellidoMaterno": apellido_materno,
        }
        if fecha_nac:
            data["fechaNacimiento"] = fecha_nac
        return self._post("/screening/pep-ofac", json=data)

    def check_lista_nominal(self, curp: str, clave_elector: str = None,
                             numero_emision: str = None, ocr: str = None,
                             cic: str = None, id_ciudadano: str = None) -> dict:
        """Valida credencial INE en lista nominal."""
        data = {"curp": normalize_curp(curp)}
        for k, v in (("claveElector", clave_elector), ("numeroEmision", numero_emision),
                     ("ocr", ocr), ("cic", cic), ("idCiudadano", id_ciudadano)):
            if v:
                data[k] = v
        return self._post("/ine/lista-nominal", json=data)

    def health(self) -> dict:
        try:
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def make_client_from_config() -> "KibanClient | None":
    try:
        from config import config
        if not config.kiban_api_key:
            return None
        return KibanClient(config.kiban_api_key)
    except Exception:
        return None
