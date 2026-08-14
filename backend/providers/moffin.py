#!/usr/bin/env python3
"""moffin.py — Cliente Moffin (APIs individuales de verificación).

Documentación: https://app.moffin.mx/api/v1/docs
Sandbox: https://sandbox.moffin.mx/api/v1
Producción: https://app.moffin.mx/api/v1

Auth: Authorization: Token <TOKEN>
Rate limit: 10 req/seg, 429 si se excede.

Endpoints principales:
  POST /query/renapo_curp   RENAPO con CURP
  POST /query/sat           SAT con RFC
  POST /query/ofac          OFAC/blacklist
  POST /report              Reporte Buró PF/PM
  POST /prospector          Prospecto KYC combinado
"""

from __future__ import annotations

from .base import BaseProvider, ProviderError, normalize_curp, normalize_rfc


class MoffinClient(BaseProvider):
    """Cliente Moffin (APIs individuales KYC/buró)."""

    def __init__(self, api_key: str = None,
                 base_url: str = "https://sandbox.moffin.mx/api/v1",
                 timeout: int = 60):
        # Por default vamos a sandbox. La key de producción debería
        # forzar app.moffin.mx explícitamente.
        super().__init__(name="moffin", api_key=api_key, base_url=base_url, timeout=timeout)
        # Sobrescribir el header por defecto del BaseProvider
        if api_key:
            self.session.headers["Authorization"] = f"Token {api_key}"

    def health(self) -> dict:
        """Verifica conectividad — Moffin no tiene /health, intenta un /account o /profile."""
        try:
            # intentar GET /account (perfil del token)
            r = self._get("/account")
            return {"ok": True, "account": r}
        except ProviderError as e:
            # si no existe /account, intentar cualquier endpoint barato
            if e.status in (404, 405):
                return {"ok": True, "note": f"conectado (auth ok, /account no existe: HTTP {e.status})"}
            return {"ok": False, "error": str(e)}

    def query_renapo_curp(self, curp: str, external_id: str = None,
                           account_type: str = "PF") -> dict:
        """POST /query/renapo_curp — consulta RENAPO.

        Payload: {curp, accountType (PF|PM), externalId?}
        """
        curp_clean = normalize_curp(curp)
        if len(curp_clean) != 18:
            raise ProviderError("moffin", f"CURP inválida (longitud {len(curp_clean)})")

        payload = {"curp": curp_clean, "accountType": account_type}
        if external_id:
            payload["externalId"] = external_id
        try:
            r = self._post("/query/renapo_curp", json=payload)
            response = r.get("response", r) if isinstance(r, dict) else r
            return {
                "valid": response.get("estatus") == "OK" if isinstance(response, dict) else False,
                "status": r.get("status") if isinstance(r, dict) else None,
                "curp": response.get("curp", curp_clean) if isinstance(response, dict) else curp_clean,
                "nombre": response.get("nombre") if isinstance(response, dict) else None,
                "apellido_paterno": response.get("apellidoPaterno") if isinstance(response, dict) else None,
                "apellido_materno": response.get("apellidoMaterno") if isinstance(response, dict) else None,
                "sexo": response.get("sexo") if isinstance(response, dict) else None,
                "fecha_nacimiento": response.get("fechaNacimiento") if isinstance(response, dict) else None,
                "estado_nacimiento": response.get("estadoNacimiento") if isinstance(response, dict) else None,
                "pais_nacimiento": response.get("paisNacimiento") if isinstance(response, dict) else None,
                "estatus_curp": response.get("estatusCurp") if isinstance(response, dict) else None,
                "doc_probatorio": response.get("datosDocProbatorio") if isinstance(response, dict) else None,
                "raw": r,
            }
        except ProviderError as e:
            return {"valid": False, "curp": curp_clean, "error": str(e), "status": e.status}

    def query_sat_rfc(self, rfc: str) -> dict:
        """POST /query/sat — consulta SAT con RFC."""
        rfc_clean = normalize_rfc(rfc)
        try:
            r = self._post("/query/sat", json={"rfc": rfc_clean})
            return r
        except ProviderError as e:
            return {"error": str(e), "rfc": rfc_clean, "status": e.status}

    def query_ofac(self, nombre: str, paterno: str, materno: str = "") -> dict:
        """POST /query/ofac — búsqueda en listas OFAC/PEP."""
        payload = {"nombre": nombre, "apellidoPaterno": paterno}
        if materno:
            payload["apellidoMaterno"] = materno
        try:
            return self._post("/query/ofac", json=payload)
        except ProviderError as e:
            return {"error": str(e), "status": e.status}

    def consult_buro_score(self, external_id: str, account_type: str = "PF",
                           curp: str = None, rfc: str = None,
                           nombre: str = None, paterno: str = None,
                           materno: str = None, fecnac: str = None,
                           email: str = None, phone: str = None,
                           address: dict = None) -> dict:
        """POST /prospector — prospecto KYC con score Buró.

        Para report completo de Buró se requiere consentimiento del titular.
        """
        payload = {
            "externalId": external_id,
            "accountType": account_type,  # PF o PM
            "serviceQueries": {
                "bureauPF": True,
                "satRFC": True,
                "renapoCurp": True,
                "bl-busqueda_judicial": False,
            },
        }
        # campos de identificación
        if nombre: payload["firstName"] = nombre
        if paterno: payload["lastName"] = paterno
        if materno: payload["secondLastName"] = materno
        if curp: payload["curp"] = normalize_curp(curp)
        if rfc: payload["rfc"] = normalize_rfc(rfc)
        if fecnac: payload["birthdate"] = fecnac  # YYYY-MM-DD
        if email: payload["email"] = email
        if phone: payload["phone"] = phone
        if address:
            payload["address"] = address
            payload["neighborhood"] = address.get("colonia")
            payload["zipCode"] = address.get("cp")
        try:
            return self._post("/prospector", json=payload)
        except ProviderError as e:
            return {"error": str(e), "status": e.status}


def make_client_from_config() -> "MoffinClient | None":
    try:
        from config import config
        if not config.moffin_api_key:
            return None
        return MoffinClient(config.moffin_api_key)
    except Exception:
        return None
