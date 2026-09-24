#!/usr/bin/env python3
"""checkid.py — proveedor CheckID (Canicalabs) para consulta fiscal/méxicana.

Documentación:
    URL base: https://www.checkid.mx/api/
    Método: POST
    Content-Type: application/json
    Auth: API Key en el body (ApiKey)

Endpoints:
    /Busqueda          -> RFC, CURP, NSS, CP, régimen fiscal, 69/69B
    /SolicitudesRestantes -> créditos disponibles
"""

from __future__ import annotations

from typing import Any, Optional

from providers.base import BaseProvider, ProviderError


class CheckIdClient(BaseProvider):
    """Cliente para la API de CheckID."""

    def __init__(self, api_key: str = None, base_url: str = "https://www.checkid.mx/api",
                 timeout: int = 30, retries: int = 1):
        super().__init__("checkid", api_key=api_key, base_url=base_url,
                         timeout=timeout, retries=retries)
        # CheckID no usa Authorization Bearer; la key va en el body JSON.
        if "Authorization" in self.session.headers:
            del self.session.headers["Authorization"]
        self.session.headers["Content-Type"] = "application/json"

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _call(self, path: str, payload: dict) -> dict:
        """Envía POST a la API CheckID y devuelve el JSON parseado."""
        if not self.api_key:
            raise ProviderError(self.name, "API key no configurada")
        body = {"ApiKey": self.api_key, **payload}
        resp = self._post(path, json=body)
        if not isinstance(resp, dict):
            raise ProviderError(self.name, f"respuesta inesperada: {type(resp)}")
        return resp

    @staticmethod
    def _ok_node(resp: dict, node: str) -> Optional[dict]:
        """Extrae un nodo hijo si la petición general y el nodo son exitosos."""
        if not resp or not resp.get("exitoso"):
            return None
        resultado = resp.get("resultado") or {}
        data = resultado.get(node)
        if not isinstance(data, dict) or not data.get("exitoso"):
            return None
        return data

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def busqueda(self, termino: str, *, obtener_rfc: bool = True,
                 obtener_curp: bool = True, obtener_69_69b: bool = True,
                 obtener_nss: bool = True, obtener_regimen: bool = True,
                 obtener_cp: bool = True) -> dict:
        """Consulta general por RFC o CURP.

        Devuelve el diccionario crudo de CheckID con la estructura completa.
        """
        payload = {
            "TerminoBusqueda": termino,
            "ObtenerRFC": obtener_rfc,
            "ObtenerCURP": obtener_curp,
            "Obtener69o69B": obtener_69_69b,
            "ObtenerNSS": obtener_nss,
            "ObtenerRegimenFiscal": obtener_regimen,
            "ObtenerCP": obtener_cp,
        }
        return self._call("/Busqueda", payload)

    def busqueda_por_curp(self, curp: str) -> dict:
        """Wrapper para buscar por CURP con todos los nodos activados.

        Si el plan no permite CURP, devuelve el dict con error E101; el caller
        puede reintentar por RFC.
        """
        return self.busqueda(curp)

    def busqueda_por_rfc(self, rfc: str) -> dict:
        """Wrapper para buscar por RFC con todos los nodos activados."""
        return self.busqueda(rfc)

    def busqueda_curp_o_rfc(self, curp: str, rfc: str = "") -> dict:
        """Intenta por CURP; si el plan no lo permite, reintenta por RFC."""
        resp = self.busqueda_por_curp(curp)
        codigo = resp.get("codigoError")
        # E101 = CURP no permitido / no es RFC o CURP
        if codigo == "E101" and rfc:
            resp = self.busqueda_por_rfc(rfc)
        return resp

    def solicitudes_restantes(self) -> dict:
        """Devuelve créditos disponibles en la cuenta."""
        return self._call("/SolicitudesRestantes", {})

    # ------------------------------------------------------------------
    # Extractores de datos comunes (normalizados)
    # ------------------------------------------------------------------
    def get_rfc(self, curp: str, rfc_hint: str = "") -> Optional[str]:
        """Obtiene RFC por CURP (13 chars con homoclave real).

        Si el plan no admite CURP, consulta por RFC hint y devuelve el RFC real.
        """
        resp = self.busqueda_curp_o_rfc(curp, rfc_hint)
        node = self._ok_node(resp, "rfc")
        return node.get("rfc") if node else None

    def get_nss(self, curp: str, rfc_hint: str = "") -> Optional[str]:
        resp = self.busqueda_curp_o_rfc(curp, rfc_hint)
        node = self._ok_node(resp, "nss")
        return node.get("nss") if node else None

    def get_cp(self, curp: str, rfc_hint: str = "") -> Optional[str]:
        resp = self.busqueda_curp_o_rfc(curp, rfc_hint)
        node = self._ok_node(resp, "codigoPostal")
        return node.get("codigoPostal") if node else None

    def get_regimen_fiscal(self, curp: str, rfc_hint: str = "") -> Optional[str]:
        resp = self.busqueda_curp_o_rfc(curp, rfc_hint)
        node = self._ok_node(resp, "regimenFiscal")
        return node.get("regimenesFiscales") if node else None

    def get_69_69b(self, curp: str, rfc_hint: str = "") -> Optional[dict]:
        resp = self.busqueda_curp_o_rfc(curp, rfc_hint)
        node = self._ok_node(resp, "estado69o69B")
        return {
            "con_problema": node.get("conProblema"),
            "detalles": node.get("detalles"),
        } if node else None

    def get_full(self, curp: str, rfc_hint: str = "") -> dict:
        """Devuelve respuesta cruda completa de CheckID con metadata."""
        resp = self.busqueda_curp_o_rfc(curp, rfc_hint)
        rfc_real = self._ok_node(resp, "rfc")
        e69_raw = self._ok_node(resp, "estado69o69B") or {}
        # normalizar conProblema → con_problema y preservar detalles completos
        e69_norm = {}
        if e69_raw:
            e69_norm = {
                "con_problema": e69_raw.get("conProblema"),
                "detalles": e69_raw.get("detalles"),
            }
        return {
            "raw": resp,
            "exitoso": resp.get("exitoso", False),
            "codigo_error": resp.get("codigoError"),
            "error": resp.get("error"),
            "rfc": rfc_real.get("rfc") if rfc_real else None,
            "razon_social": rfc_real.get("razonSocial") if rfc_real else None,
            "rfc_valido": rfc_real.get("valido") if rfc_real else None,
            "curp": self._ok_node(resp, "curp"),
            "nss": self._ok_node(resp, "nss"),
            "codigo_postal": self._ok_node(resp, "codigoPostal"),
            "regimen_fiscal": self._ok_node(resp, "regimenFiscal"),
            "estado_69_69b": e69_norm,
            "busqueda_por": "rfc" if rfc_hint and resp.get("codigoError") != "E101" else "curp",
        }

    def health(self) -> dict:
        """Verifica saldo disponible como proxy de salud."""
        try:
            resp = self.solicitudes_restantes()
            if resp.get("exitoso"):
                return {"ok": True, "solicitudes_restantes": resp.get("resultado")}
            return {"ok": False, "error": resp.get("error"), "codigo": resp.get("codigoError")}
        except ProviderError as e:
            return {"ok": False, "error": str(e), "status": e.status}


# alias para compatibilidad con factory
CheckIDClient = CheckIdClient
