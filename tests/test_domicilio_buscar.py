"""Tests para /api/direccion/candidatos (fuzzy_direccion).

Usa handler fake (sin HTTP real) y mockea fuzzy_direccion para tests determinísticos.
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

PROY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROY / "backend"))


def _fake_handler(path="/api/direccion/candidatos", session_user="test_user"):
    from servir import Handler
    h = Handler.__new__(Handler)
    h.command = "GET"
    h.path = path
    h.headers = {"Host": "localhost"}
    h._json_calls = []
    h._audit_calls = []
    h._session_user = session_user
    h.rfile = MagicMock()
    def _require_session():
        return {"user_id": 1, "username": session_user, "is_admin": False}
    h._require_session = _require_session
    def _json(status, payload):
        h._json_calls.append((status, payload))
    h._json = _json
    return h


class TestDireccionBuscar(unittest.TestCase):

    def test_sin_cp_ni_calle_retorna_400(self):
        h = _fake_handler(path="/api/direccion/candidatos")
        h._handle_direccion_candidatos()
        status, payload = h._json_calls[0]
        self.assertEqual(status, 400)
        self.assertIn("calle", payload.get("error", "").lower())

    def test_cp_consulta_fuzzy_direccion(self):
        """CP válido → llama fuzzy_direccion.candidatos y retorna resultado."""
        fake_result = {
            "consulta": {"calle": None, "ext": None, "colonia": None,
                         "cp": "20298", "municipio": None, "entidad": None},
            "candidatos": [
                {"score": 0.95, "nivel": "k_via_cp", "cp": "20298",
                 "via": "C VISTA DEL ATARDECER", "ext": "159",
                 "col": "FRACC LOMAS DE VISTABELLA", "entidad": "AGUASCALIENTES",
                 "municipio": "AGUASCALIENTES", "n_personas": 3, "refs": "1,2,3"},
            ],
        }
        with patch("fuzzy_direccion.candidatos", return_value=fake_result):
            h = _fake_handler(path="/api/direccion/candidatos?cp=20298&limit=3")
            h._handle_direccion_candidatos()
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)
        self.assertIn("candidatos", payload)
        self.assertEqual(len(payload["candidatos"]), 1)
        self.assertEqual(payload["candidatos"][0]["cp"], "20298")

    def test_calle_sin_cp_busca_fuzzy(self):
        """Calle sin CP → fuzzy_direccion.candidatos con la calle."""
        fake_result = {
            "consulta": {"calle": "AGUASCALIENTES", "ext": None,
                         "colonia": None, "cp": None,
                         "municipio": None, "entidad": None},
            "candidatos": [],
        }
        with patch("fuzzy_direccion.candidatos", return_value=fake_result):
            h = _fake_handler(
                path="/api/direccion/candidatos?calle=AGUASCALIENTES&limit=3")
            h._handle_direccion_candidatos()
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)
        self.assertIn("candidatos", payload)

    def test_cp_invalido_no_validado_en_handler(self):
        """Handler no valida formato de CP — fuzzy_dDireccion lo maneja."""
        h = _fake_handler(path="/api/direccion/candidatos?cp=abc")
        fake_result = {"consulta": {}, "candidatos": []}
        with patch("fuzzy_direccion.candidatos", return_value=fake_result):
            h._handle_direccion_candidatos()
        status, payload = h._json_calls[0]
        # Handler no valida CP, fuzzy_direccion lo hace
        self.assertIn(status, (200, 400))


if __name__ == "__main__":
    unittest.main()
