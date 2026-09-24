#!/usr/bin/env python3
"""consultaunica.py — Cliente Consulta Única (KYC México).

Documentación: https://docs.consultaunica.mx
Costo: 1-4 créditos/consulta dependiendo del servicio.
Autenticación: header `x-api-key` (48 chars hex).

Servicios cubiertos aquí:
  - Afore (`POST /v3/afore`): Afore, nombre, medios de contacto por CURP.
  - Ifetel (`POST /v3/phones`): tipo de teléfono, compañía, lada, ciudad.
  - Registro Civil Actas (`POST /v3/actas`, `GET /v3/actas/{uuid}`):
      acta de nacimiento/matrimonio/defunción/divorcio, asíncrono con
      webhook firmado HMAC-SHA256 o polling por uuid.

Soporta modo mock (`base_url` con prefijo `/v3/mock/...`) para integrar
sin comprar créditos.

Notas de la API:
  - Códigos clave:
      200 OK · 400/409/422 validación · 429 cuota o rate-limit ·
      500/502/503 fallo del servicio externo.
  - Actas: sólo 07:30–22:00 CDMX, máx. 5 en curso. Webhook firmado con
    HMAC-SHA256 usando la propia `x-api-key` como secreto.
  - Actas cobra 4 créditos y revierte el cobro si termina en `failed`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any, Optional

from .base import BaseProvider, ProviderError, normalize_curp

# === Catálogo de servicios (rutas relativas a /v3) ===
#
# mock=True antepone `/mock` antes del nombre: /v3/mock/{service}.
# Usado para validar integración sin gastar créditos.
_SERVICES = {
    "afore":        {"method": "POST", "path": "/afore"},
    "ifetel":       {"method": "POST", "path": "/phones"},
    "actas_submit": {"method": "POST", "path": "/actas"},
    # actas_status y actas_replay usan path construido con uuid.
}

ACTA_TYPES = ("nacimiento", "matrimonio", "defuncion", "divorcio")

# Tolerancia para la firma del webhook (±5 minutos, según docs)
WEBHOOK_TS_TOLERANCE_S = 300


class ConsultaUnicaClient(BaseProvider):
    """Cliente para la API REST de Consulta Única.

    Todos los métodos devuelven dicts ya normalizados; errores se elevan
    como ProviderError con el provider="cu".
    """

    def __init__(self, api_key: str = None,
                 base_url: str = "https://api.consultaunica.mx/v3",
                 timeout: int = 60,
                 mock: bool = False):
        super().__init__(name="cu", api_key=api_key or "",
                         base_url=base_url, timeout=timeout)
        # base_url puede terminar en "/" y BaseProvider ya lo limpia.
        # BaseProvider pone Authorization: Bearer pero la API usa x-api-key,
        # se sobreescribe en _headers() para no romper otros usos.
        self.session.headers.pop("Authorization", None)
        self.mock = mock

    # === internos ===

    def _headers(self, extra: Optional[dict] = None) -> dict:
        h = {
            "x-api-key": self.api_key or "",
            "Content-Type": "application/json",
        }
        if extra:
            h.update(extra)
        return h

    def _service_path(self, key: str) -> str:
        """Devuelve la ruta con prefijo /v3 (o /v3/mock si mock=True)."""
        svc = _SERVICES[key]
        base = self.base_url  # ya apunta a .../v3
        if self.mock:
            base = base.replace("/v3", "/v3/mock", 1)
        return f"{base}{svc['path']}"

    # === Afore ===

    def afore_details(self, curp: str) -> dict:
        """Consulta la Afore y medios de contacto por CURP.

        Returns:
            {
              "ok": bool,
              "curp": str,
              "afore": str,             # e.g. "AFORE SURA"
              "name": str,
              "paternalName": str,
              "maternalName": str,
              "sex": str,
              "birthDate": str,          # DD/MM/YYYY
              "phoneNumber": str,
              "email": str,
              "hasActiveApp": bool,
              "raw": dict,
            }
        Errores 4xx/5xx → ProviderError con status.
        """
        curp_clean = normalize_curp(curp)
        if len(curp_clean) != 18:
            raise ProviderError("cu", f"CURP inválida (longitud {len(curp_clean)})")

        payload = {"variant": "encrypt", "curp": curp_clean}
        r = self.session.post(
            self._service_path("afore"),
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise ProviderError("cu", f"afore HTTP {r.status_code}: {r.text[:300]}",
                                r.status_code)
        body = r.json()
        return {
            "ok": True,
            "curp": body.get("curp", curp_clean),
            "afore": body.get("afore", ""),
            "name": body.get("name", ""),
            "paternalName": body.get("paternalName", ""),
            "maternalName": body.get("maternalName", ""),
            "sex": body.get("sex", ""),
            "birthDate": body.get("birthDate", ""),
            "phoneNumber": body.get("phoneNumber", ""),
            "email": body.get("email", ""),
            "hasActiveApp": bool(body.get("hasActiveApp", False)),
            "raw": body,
        }

    # === Ifetel (teléfono) ===

    def ifetel_lookup(self, phone_number: str) -> dict:
        """Consulta el registro Ifetel de un número (10 dígitos).

        Returns:
            {
              "ok": bool,
              "phoneNumber": str,
              "phoneType": "Fijo"|"Móvil"|"",
              "phoneCompany": str,        # e.g. "TELCEL"
              "registrationCity": str,
              "lada": str,
              "raw": dict,
            }
        """
        digits = "".join(c for c in str(phone_number) if c.isdigit())
        if len(digits) != 10:
            raise ProviderError("cu", f"teléfono inválido (dígitos: {len(digits)})")

        payload = {
            "variant": "ifetel",
            "ifetel": {"phoneNumber": digits},
        }
        r = self.session.post(
            self._service_path("ifetel"),
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise ProviderError("cu", f"ifetel HTTP {r.status_code}: {r.text[:300]}",
                                r.status_code)
        body = r.json()
        ifx = body.get("ifetel", {}) if isinstance(body, dict) else {}
        return {
            "ok": True,
            "phoneNumber": digits,
            "phoneType": ifx.get("phoneType", ""),
            "phoneCompany": ifx.get("phoneCompany", ""),
            "registrationCity": ifx.get("registrationCity") or "",
            "lada": ifx.get("lada") or "",
            "raw": body,
        }

    # === Actas del Registro Civil (asíncrono) ===

    def acta_submit(self, curp: str, acta_type: str = "nacimiento",
                    con_folio: bool = False,
                    webhook_url: Optional[str] = None,
                    idempotency_key: Optional[str] = None,
                    user_id: Optional[int] = None) -> dict:
        """Envía una solicitud de acta. Devuelve uuid + estado.

        Costo: 4 créditos (se revierten si termina en `failed`).

        Si acta_type="divorcio" con con_folio=True, la API la trata como
        folio=False y así se reporta en la respuesta (echo).

        Returns:
            {
              "ok": bool,
              "uuid": str,
              "curp": str,
              "actaType": str,
              "conFolio": bool,
              "redelivered": bool,   # re-entrega del mismo día sin cargo
              "message": str,
              "raw": dict,
            }
        """
        curp_clean = normalize_curp(curp)
        if len(curp_clean) != 18:
            raise ProviderError("cu", f"CURP inválida (longitud {len(curp_clean)})")
        if acta_type not in ACTA_TYPES:
            raise ProviderError("cu", f"actaType inválido: {acta_type!r}. "
                                f"Opciones: {ACTA_TYPES}")

        payload: dict[str, Any] = {
            "curp": curp_clean,
            "actaType": acta_type,
            "conFolio": bool(con_folio),
        }
        headers: dict[str, str] = {}
        if webhook_url:
            payload["webhookUrl"] = webhook_url
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        r = self.session.post(
            self._service_path("actas_submit"),
            json=payload,
            headers=self._headers(headers),
            timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise ProviderError("cu", f"actas submit HTTP {r.status_code}: {r.text[:300]}",
                                r.status_code)
        body = r.json()
        uuid_ = body.get("uuid", "")
        # Persistir mapping uuid → curp para que el webhook pueda indexar
        # el PDF entrante por CURP en R2. Si la consulta fue una re-entrega
        # del mismo día (redelivered=true) igual guardamos el uuid nuevo, que
        # es el que recibiremos en el webhook.
        try:
            import auth
            with auth._db() as conn:
                conn.execute(
                    """INSERT INTO actas_submissions
                       (uuid, curp, acta_type, con_folio, user_id, status)
                       VALUES (?, ?, ?, ?, ?, 'pending')
                       ON CONFLICT(uuid) DO NOTHING""",
                    (uuid_, curp_clean, acta_type, 1 if con_folio else 0, user_id),
                )
        except Exception:
            pass  # best-effort: si la DB no está accesible seguimos sin mapping
        return {
            "ok": True,
            "uuid": uuid_,
            "curp": body.get("curp", curp_clean),
            "actaType": body.get("actaType", acta_type),
            "conFolio": bool(body.get("conFolio", con_folio)),
            "redelivered": bool(body.get("redelivered", False)),
            "message": body.get("message", ""),
            "raw": body,
        }

    def acta_status(self, uuid: str) -> dict:
        """Polling: consulta el estado de una solicitud de acta.

        Returns:
            {
              "status": "pending"|"completed"|"failed",
              "uuid": str,
              "pdfBase64": str|None,     # presente si completed
              "errorMessage": str|None,  # presente si failed
              "pdf_bytes": bytes|None,   # ya decodificado si completed
              "raw": dict,
            }
        No consume créditos.
        """
        if not uuid:
            raise ProviderError("cu", "uuid requerido para acta_status")

        base = self.base_url
        if self.mock:
            base = base.replace("/v3", "/v3/mock", 1)
        url = f"{base}/actas/{uuid}"
        r = self.session.get(url, headers=self._headers(), timeout=self.timeout)
        if r.status_code >= 400:
            raise ProviderError("cu", f"actas status HTTP {r.status_code}: {r.text[:300]}",
                                r.status_code)
        body = r.json()
        pdf_b64 = body.get("pdfBase64")
        pdf_bytes = base64.b64decode(pdf_b64) if pdf_b64 else None
        return {
            "status": body.get("status", ""),
            "uuid": body.get("uuid", uuid),
            "pdfBase64": pdf_b64,
            "pdf_bytes": pdf_bytes,
            "errorMessage": body.get("errorMessage"),
            "raw": body,
        }

    def acta_replay(self, uuid: str) -> dict:
        """Reintenta la entrega del webhook para una solicitud ya submitida.

        No consume créditos. Cooldown: 60s por uuid.
        """
        if not uuid:
            raise ProviderError("cu", "uuid requerido para acta_replay")
        base = self.base_url
        if self.mock:
            base = base.replace("/v3", "/v3/mock", 1)
        url = f"{base}/actas/{uuid}/replay"
        r = self.session.post(url, headers=self._headers(), timeout=self.timeout)
        if r.status_code >= 400:
            raise ProviderError("cu", f"actas replay HTTP {r.status_code}: {r.text[:300]}",
                                r.status_code)
        body = r.json() if r.content else {"uuid": uuid, "scheduled": r.status_code == 202}
        return {"ok": True, "raw": body}

    # === Validación de webhook (helper puro para el endpoint receptor) ===

    @staticmethod
    def verify_webhook(raw_body: bytes, headers: dict,
                       api_key: str) -> tuple[bool, Optional[dict]]:
        """Valida firma HMAC y timestamp de un callback X-CU-*.

        Devuelve (válido, payload_decodificado_si_ok_None_si_no).
        Usar SIEMPRE con los bytes crudos del body, no re-serializar.
        Headers case-insensitive (algunos servers normalizan a X-Cu-*).
        """
        # lookup case-insensitive
        lower_map = {k.lower(): v for k, v in headers.items()}
        ts = (lower_map.get("x-cu-timestamp") or "").strip()
        sig = (lower_map.get("x-cu-signature") or "").strip()
        if not ts.isdigit():
            return False, None
        now = int(time.time())
        if abs(now - int(ts)) > WEBHOOK_TS_TOLERANCE_S:
            return False, None

        signed = f"{ts}.".encode() + raw_body
        digest = hmac.new(api_key.encode(), signed, hashlib.sha256).hexdigest()
        expected = f"sha256={digest}"
        if not hmac.compare_digest(expected, sig):
            return False, None

        # firma OK — decodificar payload
        import json
        try:
            return True, json.loads(raw_body.decode("utf-8"))
        except Exception:
            return True, None

    # === Health ===

    def health(self) -> dict:
        """No hay endpoint de salud dedicado; solo verifica presencia de key."""
        return {
            "ok": bool(self.api_key) and len(self.api_key) == 48,
            "mock": self.mock,
            "provider": "consultaunica",
        }


# === helper de integración con config ===

def make_client_from_config(mock: bool = False) -> "ConsultaUnicaClient | None":
    """Crea un cliente desde config.py. None si no hay API key."""
    try:
        from config import config
        if not config.consultaunica_api_key:
            return None
        return ConsultaUnicaClient(
            api_key=config.consultaunica_api_key,
            mock=mock or getattr(config, "consultaunica_mock", False),
        )
    except Exception:
        return None
