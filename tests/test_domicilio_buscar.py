"""Tests para /api/direccion/buscar (CFE + INE).

Usa handler fake (sin HTTP real) y mockea DuckDB para tests determinísticos.
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

PROY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROY / "backend"))


def _fake_handler(method="POST", path="/api/direccion/buscar",
                  body=None, session_user="test_user"):
    from servir import Handler
    h = Handler.__new__(Handler)
    h.command = method
    h.path = path
    h.headers = {"Host": "localhost"}
    h._json_calls = []
    h._audit_calls = []
    h._session_user = session_user
    h._body_json = body or {}
    h.rfile = MagicMock()
    def _read_json_body():
        return h._body_json
    h._read_json_body = _read_json_body
    def _require_session():
        return {"user_id": 1, "username": session_user, "is_admin": False}
    h._require_session = _require_session
    def _json(status, payload):
        h._json_calls.append((status, payload))
    h._json = _json
    return h


class TestDireccionBuscar(unittest.TestCase):

    def test_sin_cp_ni_calle_retorna_400(self):
        h = _fake_handler(body={})
        h._handle_direccion_buscar()
        status, payload = h._json_calls[0]
        self.assertEqual(status, 400)
        self.assertIn("cp", payload.get("error", "").lower() + " calle")

    def test_cp_solo_consulta_ine_y_cfe(self):
        h = _fake_handler(body={"cp": "20298", "limit": 3})
        # Mockear duckdb.connect para padrón + _init_extended_con para CFE
        fake_ine_rows = [
            ("ZITA RUIZ VEGA", "RUIZ", "VEGA", "C VISTA 159", "159.0",
             "FRACC LOMAS", "20298", "M", "1975-04-27", 28)
        ]
        fake_cfe_rows = [
            ("123", "JUAN PEREZ", "DN", "20298", "AV AGUASCALIENTES 159",
             None, None, "FRACC LOMAS", "SAN ANGEL", "ZONA")
        ]
        with patch("duckdb.connect") as m_ine, \
             patch("servir._init_extended_con") as m_cfe:
            mock_ine = MagicMock()
            # fetchone() debe retornar tuple (count,), fetchall() la lista
            mock_ine.execute.return_value.fetchone.return_value = (5,)
            mock_ine.execute.return_value.fetchall.return_value = fake_ine_rows
            m_ine.return_value = mock_ine
            mock_cfe = MagicMock()
            mock_cfe.execute.return_value.fetchone.return_value = (2,)
            mock_cfe.execute.return_value.fetchall.return_value = fake_cfe_rows
            m_cfe.return_value = mock_cfe
            h._handle_direccion_buscar()
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)
        self.assertIn("personas", payload)
        self.assertIn("cfe", payload)
        self.assertEqual(payload["total_encontrado"], 5)
        self.assertEqual(payload["total_cfe"], 2)
        self.assertEqual(payload["personas"][0]["source"], "ine")
        self.assertEqual(payload["cfe"][0]["source"], "cfe")

    def test_solo_calle_no_busca_cfe_sin_cp(self):
        """Calle sin CP — el bloque CFE debe devolver 0 sin hacer queries."""
        h = _fake_handler(body={"calle": "AGUASCALIENTES", "limit": 3})
        with patch("duckdb.connect") as m_ine, \
             patch("servir._init_extended_con") as m_cfe:
            mock_ine = MagicMock()
            mock_ine.execute.return_value.fetchone.return_value = (10,)
            mock_ine.execute.return_value.fetchall.return_value = []
            m_ine.return_value = mock_ine
            m_cfe.return_value = None
            h._handle_direccion_buscar()
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)
        self.assertEqual(payload["total_cfe"], 0)
        self.assertEqual(payload["cfe"], [])
        # No debe haberse hecho ningún execute sobre el con CFE (no se usó)
        # El con es None, así que el `if cfe_con is not None` evita queries.

    def test_cp_invalido_retorna_400(self):
        h = _fake_handler(body={"cp": "abc"})
        h._handle_direccion_buscar()
        status, payload = h._json_calls[0]
        self.assertEqual(status, 400)
        self.assertIn("5 dígitos", payload.get("error", ""))


if __name__ == "__main__":
    unittest.main()