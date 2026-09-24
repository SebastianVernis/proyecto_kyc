"""Tests para ConsultaUnicaClient — Afore / Ifetel / Actas.

Mockea requests.Session para no tocar la red.
"""
import base64
import hashlib
import hmac
import json
import time
import unittest
from unittest.mock import MagicMock, patch

import sys
from pathlib import Path
PROY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROY / "backend"))

from providers.consultaunica import ConsultaUnicaClient


def _resp(status=200, body=None):
    r = MagicMock()
    r.status_code = status
    r.headers = {"content-type": "application/json"}
    r.text = json.dumps(body or {})
    r.json = lambda: body or {}
    r.content = r.text.encode()
    return r


class TestAfore(unittest.TestCase):
    def test_afore_details_ok(self):
        c = ConsultaUnicaClient(api_key="k" * 48)
        body = {
            "curp": "LOOA531113HTCPBN07",
            "afore": "AFORE SURA",
            "name": "ANDRES MANUEL",
            "paternalName": "LOPEZ",
            "maternalName": "OBRADOR",
            "sex": "H",
            "birthDate": "13/11/1953",
            "phoneNumber": "5512345678",
            "email": "amlo@gmail.com",
            "hasActiveApp": True,
        }
        with patch.object(c.session, "post", return_value=_resp(200, body)):
            r = c.afore_details("LOOA531113HTCPBN07")
        self.assertTrue(r["ok"])
        self.assertEqual(r["afore"], "AFORE SURA")
        self.assertEqual(r["name"], "ANDRES MANUEL")
        self.assertEqual(r["phoneNumber"], "5512345678")

    def test_afore_curp_invalida_rechaza_local(self):
        c = ConsultaUnicaClient(api_key="k" * 48)
        with self.assertRaises(Exception) as ctx:
            c.afore_details("CORTA")
        self.assertIn("CURP inválida", str(ctx.exception))

    def test_afore_422_no_encontrada(self):
        c = ConsultaUnicaClient(api_key="k" * 48)
        with patch.object(c.session, "post",
                          return_value=_resp(422, {"message": "No se encontró la CURP en RENAPO"})):
            with self.assertRaises(Exception) as ctx:
                c.afore_details("LOOA531113HTCPBN07")
        self.assertIn("422", str(ctx.exception))


class TestIfetel(unittest.TestCase):
    def test_ifetel_movil_ok(self):
        c = ConsultaUnicaClient(api_key="k" * 48)
        body = {
            "variant": "ifetel",
            "ifetel": {
                "phoneType": "Móvil",
                "phoneCompany": "TELCEL",
                "registrationCity": None,
                "lada": None,
            },
        }
        with patch.object(c.session, "post", return_value=_resp(200, body)):
            r = c.ifetel_lookup("5512345678")
        self.assertTrue(r["ok"])
        self.assertEqual(r["phoneType"], "Móvil")
        self.assertEqual(r["phoneCompany"], "TELCEL")

    def test_ifetel_telefono_invalido_rechaza_local(self):
        c = ConsultaUnicaClient(api_key="k" * 48)
        with self.assertRaises(Exception) as ctx:
            c.ifetel_lookup("123")
        self.assertIn("teléfono inválido", str(ctx.exception))


class TestActas(unittest.TestCase):
    def test_acta_submit_ok_sin_mapping_db(self):
        """submit ok pero sin DB disponible → igual devuelve uuid."""
        c = ConsultaUnicaClient(api_key="k" * 48)
        body = {
            "message": "Solicitud recibida, pendiente de resultado",
            "uuid": "9f2b7c1a-4d3e-4a5f-8b0c-2e1d0f9a8b77",
            "curp": "CASE020722HTSRNDA8",
            "actaType": "nacimiento",
            "conFolio": False,
            "redelivered": False,
        }
        with patch.object(c.session, "post", return_value=_resp(200, body)):
            r = c.acta_submit("CASE020722HTSRNDA8", "nacimiento")
        self.assertTrue(r["ok"])
        self.assertEqual(r["uuid"], "9f2b7c1a-4d3e-4a5f-8b0c-2e1d0f9a8b77")
        self.assertFalse(r["redelivered"])

    def test_acta_submit_acta_type_invalido(self):
        c = ConsultaUnicaClient(api_key="k" * 48)
        with self.assertRaises(Exception) as ctx:
            c.acta_submit("CASE020722HTSRNDA8", "divorcio-invalido")
        self.assertIn("actaType inválido", str(ctx.exception))

    def test_acta_submit_divorcio_con_folio_se_trata_como_false(self):
        c = ConsultaUnicaClient(api_key="k" * 48)
        body = {
            "uuid": "u1",
            "curp": "CASE020722HTSRNDA8",
            "actaType": "divorcio",
            "conFolio": False,
            "redelivered": False,
            "message": "ok",
        }
        with patch.object(c.session, "post", return_value=_resp(200, body)) as m:
            r = c.acta_submit("CASE020722HTSRNDA8", "divorcio", con_folio=True)
        # el servidor normaliza conFolio → False; eco es lo que cuenta
        self.assertFalse(r["conFolio"])
        # payload enviado lleva conFolio=True como lo pidió el caller
        sent = m.call_args.kwargs["json"]
        self.assertTrue(sent["conFolio"])

    def test_acta_status_completed(self):
        c = ConsultaUnicaClient(api_key="k" * 48)
        pdf_bytes = b"%PDF-sample"
        body = {
            "version": 1,
            "uuid": "u1",
            "status": "completed",
            "pdfBase64": base64.b64encode(pdf_bytes).decode(),
            "errorMessage": None,
        }
        with patch.object(c.session, "get", return_value=_resp(200, body)):
            r = c.acta_status("u1")
        self.assertEqual(r["status"], "completed")
        self.assertEqual(r["pdf_bytes"], pdf_bytes)

    def test_acta_status_pending(self):
        c = ConsultaUnicaClient(api_key="k" * 48)
        body = {"version": 1, "uuid": "u1", "status": "pending",
                "pdfBase64": None, "errorMessage": None}
        with patch.object(c.session, "get", return_value=_resp(200, body)):
            r = c.acta_status("u1")
        self.assertEqual(r["status"], "pending")
        self.assertIsNone(r["pdf_bytes"])


class TestWebhookVerification(unittest.TestCase):
    KEY = "a" * 48

    def _sign(self, raw: bytes, ts: int | str, key: str | None = None) -> str:
        k = (key or self.KEY).encode()
        t = str(ts).encode()
        d = hmac.new(k, t + b"." + raw, hashlib.sha256).hexdigest()
        return f"sha256={d}"

    def test_verify_webhook_ok(self):
        raw = b'{"version":1,"uuid":"u1","status":"completed","pdfBase64":"abc","errorMessage":null}'
        ts = int(time.time())
        sig = self._sign(raw, ts)
        ok, payload = ConsultaUnicaClient.verify_webhook(
            raw, {"X-CU-Timestamp": str(ts), "X-CU-Signature": sig}, self.KEY)
        self.assertTrue(ok)
        self.assertEqual(payload["uuid"], "u1")

    def test_verify_webhook_case_insensitive_headers(self):
        """Servidores Python normalizan X-CU-* a X-Cu-*."""
        raw = b'{"version":1,"uuid":"u1","status":"completed","pdfBase64":"aGVsbG8=",'
        raw += b'"errorMessage":null}'
        ts = int(time.time())
        sig = self._sign(raw, ts)
        ok, _ = ConsultaUnicaClient.verify_webhook(
            raw, {"X-Cu-Timestamp": str(ts), "X-Cu-Signature": sig}, self.KEY)
        self.assertTrue(ok)

    def test_verify_webhook_sig_invalida(self):
        raw = b'{"version":1,"uuid":"u1"}'
        ts = int(time.time())
        ok, _ = ConsultaUnicaClient.verify_webhook(
            raw, {"X-CU-Timestamp": str(ts), "X-CU-Signature": "sha256=bad"}, self.KEY)
        self.assertFalse(ok)

    def test_verify_webhook_ts_viejo(self):
        raw = b'{"version":1,"uuid":"u1"}'
        ts = int(time.time()) - 400  # fuera de la ventana de ±300 s
        sig = self._sign(raw, ts)
        ok, _ = ConsultaUnicaClient.verify_webhook(
            raw, {"X-CU-Timestamp": str(ts), "X-CU-Signature": sig}, self.KEY)
        self.assertFalse(ok)

    def test_verify_webhook_key_equivocada(self):
        raw = b'{"version":1,"uuid":"u1"}'
        ts = int(time.time())
        sig = self._sign(raw, ts, key="b" * 48)
        ok, _ = ConsultaUnicaClient.verify_webhook(
            raw, {"X-CU-Timestamp": str(ts), "X-CU-Signature": sig}, self.KEY)
        self.assertFalse(ok)


class TestMockPaths(unittest.TestCase):
    def test_mock_reescribe_rutas(self):
        c = ConsultaUnicaClient(api_key="k" * 48, mock=True)
        self.assertIn("/v3/mock/afore", c._service_path("afore"))
        self.assertIn("/v3/mock/phones", c._service_path("ifetel"))
        self.assertIn("/v3/mock/actas", c._service_path("actas_submit"))


class TestRenderAnexos(unittest.TestCase):
    def test_render_actas_anexos_solo_completed_con_url(self):
        from report_generator import _render_actas_anexos
        actas = [
            {"uuid": "u1", "acta_type": "nacimiento", "con_folio": False,
             "status": "completed", "created_at": "2026-09-11",
             "r2_key": "actas/C/n/u1.pdf", "r2_url": "https://x.r2.dev/actas/C/n/u1.pdf"},
            {"uuid": "u2", "acta_type": "defuncion", "con_folio": False,
             "status": "failed", "created_at": "2026-09-11", "r2_key": "", "r2_url": ""},
            {"uuid": "u3", "acta_type": "matrimonio", "con_folio": True,
             "status": "completed", "created_at": "2026-09-11",
             "r2_key": "actas/C/m.foliada/u3.pdf",
             "r2_url": "https://x.r2.dev/actas/C/m.foliada/u3.pdf"},
        ]
        html = _render_actas_anexos(actas)
        self.assertIn("Nacimiento", html)
        self.assertIn("Matrimonio", html)
        self.assertIn("(foliada)", html)
        self.assertNotIn("defuncion", html.lower())
        self.assertIn("u1", html)
        self.assertIn("u3", html)
        self.assertNotIn("u2", html)

    def test_render_actas_anexos_vacio(self):
        from report_generator import _render_actas_anexos
        self.assertEqual(_render_actas_anexos(None), "")
        self.assertEqual(_render_actas_anexos([]), "")
        self.assertEqual(_render_actas_anexos([
            {"uuid": "u1", "acta_type": "nacimiento", "con_folio": False,
             "status": "pending", "created_at": "2026-09-11", "r2_key": "", "r2_url": ""}
        ]), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
