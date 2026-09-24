#!/usr/bin/env python3
"""tlaloc.py — Cliente Tlaloc (validación CURP ante RENAPO).

Documentación: https://www.tlaloc.sh/es/services/curp.html
Tier gratis: sandbox sin API key
Tier pago: $0.50 MXN / consulta

Endpoint principal:
  GET https://api.tlaloc.sh/mx/v1/curp?curp={curp}
  Headers: Authorization: Bearer tlmx_xxxx
"""

from __future__ import annotations

from typing import Any

from .base import BaseProvider, ProviderError, normalize_curp


class TlalocClient(BaseProvider):
    """Cliente para validación de CURP contra RENAPO vía Tlaloc."""

    def __init__(self, api_key: str = None, timeout: int = 60):
        super().__init__(
            name="tlaloc",
            api_key=api_key,
            base_url="https://api.tlaloc.sh/mx/v1",
            timeout=timeout,
            retries=0,  # Tlaloc es lento pero no queremos reintentar
        )

    def validate_curp(self, curp: str) -> dict:
        """Valida una CURP contra RENAPO.

        Returns:
            dict con datos de RENAPO o dict con error.
            {
                "valid": bool,
                "curp": str,
                "nombres": str,
                "primerApellido": str,
                "segundoApellido": str,
                "sexo": str,
                "fechaNacimiento": str,
                "nacionalidad": str,
                "entidad": str,
                "docProbatorio": dict,
                "raw": dict,
            }
        """
        curp_clean = normalize_curp(curp)
        if len(curp_clean) != 18:
            raise ProviderError("tlaloc", f"CURP inválida (longitud {len(curp_clean)})")

        try:
            r = self._get("/curp", params={"curp": curp_clean})
        except ProviderError as e:
            if e.status == 404:
                return {"valid": False, "curp": curp_clean, "error": "no encontrada en RENAPO"}
            raise

        if not isinstance(r, dict):
            raise ProviderError("tlaloc", f"respuesta no es JSON: {type(r)}")

        return {
            "valid": r.get("statusCurp") in ("RCN", "RBC", "ACT"),
            "status": r.get("statusCurp"),
            "curp": r.get("curp", curp_clean),
            "nombres": r.get("nombres", ""),
            "primerApellido": r.get("primerApellido", ""),
            "segundoApellido": r.get("segundoApellido", ""),
            "sexo": r.get("sexo", ""),
            "fechaNacimiento": r.get("fechaNacimiento", ""),
            "nacionalidad": r.get("nacionalidad", ""),
            "entidad": r.get("entidad", ""),
            "claveEntidad": r.get("claveEntidad", ""),
            "docProbatorio": r.get("datosDocProbatorio", {}),
            "raw": r,
        }

    def validate_rfc(self, rfc: str, *, nombre: str = None,
                     codigo_postal: str = None) -> dict:
        """Valida un RFC contra el SAT (no es cálculo local: consulta en vivo).

        Documentación: https://www.tlaloc.sh/es/services/rfc.html

        Args:
            rfc: 12 o 13 caracteres
            nombre: opcional, valida nombre_razon_social contra el SAT
            codigo_postal: opcional, valida CP contra el SAT (3 validaciones
                que aplica el SAT al timbrar CFDI 4.0)

        Returns:
            {
                "valid": bool,            # RFC existe en padrón del SAT
                "accept_cfdi": bool,      # RFC es susceptible de recibir facturas
                "reason": str,            # mensaje del SAT
                "rfc": str,               # RFC normalizado
                "nombre_match": bool,     # si se proporcionó nombre
                "cp_match": bool,         # si se proporcionó CP
                "raw": dict,
            }
        """
        rfc_clean = rfc.strip().upper()
        if len(rfc_clean) not in (12, 13):
            raise ProviderError("tlaloc", f"RFC inválido (longitud {len(rfc_clean)})")

        params = {"rfc": rfc_clean}
        if nombre:
            params["nombre_razon_social"] = nombre.strip().upper()
        if codigo_postal:
            params["codigo_postal"] = codigo_postal.strip()

        try:
            r = self._get("/rfc", params=params)
        except ProviderError as e:
            if e.status == 404:
                return {"valid": False, "rfc": rfc_clean,
                        "accept_cfdi": False,
                        "reason": "RFC no encontrado en el SAT",
                        "error": "no existe en padrón"}
            raise

        if not isinstance(r, dict):
            raise ProviderError("tlaloc", f"respuesta no es JSON: {type(r)}")

        result = {
            "valid": bool(r.get("valid", False)),
            "accept_cfdi": bool(r.get("accept_cfdi", False)),
            "reason": r.get("reason", ""),
            "rfc": rfc_clean,
            "raw": r,
        }
        # verificar match de nombre y CP
        if nombre and "nombre_razon_social" in r:
            result["nombre_input"] = nombre.strip().upper()
            result["nombre_match"] = r["nombre_razon_social"] == nombre.strip().upper()
        if codigo_postal and "codigo_postal" in r:
            result["cp_input"] = codigo_postal.strip()
            result["cp_match"] = r["codigo_postal"] == codigo_postal.strip()
        return result

    def health(self) -> dict:
        """Verifica que la API key funcione."""
        try:
            r = self._get("/health")
            return {"ok": True, "respuesta": r}
        except ProviderError as e:
            return {"ok": False, "error": str(e)}


# === helper de integración con config ===

def make_client_from_config() -> "TlalocClient | None":
    """Crea un cliente Tlaloc desde config.py. None si no hay API key."""
    try:
        from config import config
        if not config.tlaloc_api_key:
            return None
        return TlalocClient(config.tlaloc_api_key)
    except Exception:
        return None
