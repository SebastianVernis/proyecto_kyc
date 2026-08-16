"""Tests para /api/v1/sujeto/resolver_desde_hint.

Cubre la cascada de matching que el operador espera del front universal:
1. CURP directa (caso trivial)
2. RFC solo → xwalk imss_s → curp (caso Telcel/ATT/REPUVE)
3. RFC + nombre → desambiguar RFC10 ambiguo
4. NSS solo → xwalk imss_a → curp (caso IMSS Asegurado)
5. Padrón por nombre+paterno+materno+fecnac (2 datos secundarios)
6. Padrón por nombre+paterno+materno+cp (nombre + domicilio)
7. Datos insuficientes (sólo nombre+paterno, sin secundarios)
8. Sin matches en padrón
9. Sin parámetros mínimos
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

PROY = Path("/root/proyecto_kyc")
sys.path.insert(0, str(PROY / "backend"))


def _fake_handler(method="GET", path="/api/v1/sujeto/resolver_desde_hint",
                  session_user="test_user"):
    """Fabrica un Handler con stubs suficientes para invocar el handler
    sin levantar el servidor HTTP real."""
    from servir import Handler
    h = Handler.__new__(Handler)
    h.command = method
    h.path = path
    h.headers = {"Host": "localhost"}
    h._json_calls = []
    h._audit_calls = []
    h._session_user = session_user
    h.rfile = MagicMock()
    def _read_json_body():
        return {}
    h._read_json_body = _read_json_body
    def _require_session():
        return {"user_id": 1, "username": session_user, "is_admin": False}
    h._require_session = _require_session
    def _json(status, payload):
        h._json_calls.append((status, payload))
    h._json = _json
    return h


def _stub_con(ext_curps=None, padron_rows=None):
    """Crea los dos mocks de conexión: padron (DuckDB local) + extendido.
    `ext_curps` es la lista de CURPs que devuelve el xwalk imss_s por RFC."""
    padron = MagicMock()
    padron.execute.return_value.fetchall.return_value = padron_rows or []
    ext = MagicMock()
    if ext_curps is not None:
        ext.execute.return_value.fetchall.return_value = [(c,) for c in ext_curps]
    else:
        ext.execute.return_value.fetchall.return_value = []
    return padron, ext


class TestResolverDesdeHint(unittest.TestCase):

    # 1) CURP directa — caso trivial, no toca la BD.
    def test_curp_directa_devuelve_ok(self):
        h = _fake_handler(path="/api/v1/sujeto/resolver_desde_hint?curp=ABCD850101HDFABC01")
        h._handle_sujeto_resolver_desde_hint()
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["curp"], "ABCD850101HDFABC01")
        self.assertEqual(payload["estrategia"], "curp_directa")
        self.assertEqual(payload["score"], 1.0)

    # 2) RFC 13 chars → xwalk imss_s → CURP única.
    def test_rfc_unico_resuelve_curp(self):
        h = _fake_handler(path=(
            "/api/v1/sujeto/resolver_desde_hint?rfc=AISR750901AAA"
            "&nombre=ROSALIA&paterno=AVILA&materno=SALDAÑA"
        ))
        padron, ext = _stub_con(ext_curps=["AISR750901MDFVLS01"])
        with patch("duckdb.connect", return_value=padron), \
             patch("servir._init_extended_con", return_value=ext):
            h._handle_sujeto_resolver_desde_hint()
        status, payload = h._json_calls[0]
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["curp"], "AISR750901MDFVLS01")
        self.assertEqual(payload["estrategia"], "rfc_xwalk_imss_s")
        self.assertGreaterEqual(payload["score"], 0.9)

    # 3) RFC 10 chars + nombre completo → desambigua varios homónimos.
    def test_rfc_ambiguo_con_nombre_desambigua(self):
        # RFC 10 chars devuelve 3 CURPs candidatas; con nombre+apellidos
        # el padrón filtra a 1.
        h = _fake_handler(path=(
            "/api/v1/sujeto/resolver_desde_hint?rfc=AISR750901"
            "&nombre=ROSALIA&paterno=AVILA&materno=SALDAÑA"
        ))
        padron_rows = [
            ("AISR750901MDFVLS01", "ROSALIA", "AVILA", "SALDAÑA",
             "1975-09-01", "10200", None, None),
        ]
        padron, ext = _stub_con(
            ext_curps=["AISR750901MDFVLS01", "AISR750901MDFVLS02",
                       "AISR750901MDFVLS03"],
            padron_rows=padron_rows,
        )
        with patch("duckdb.connect", return_value=padron), \
             patch("servir._init_extended_con", return_value=ext):
            h._handle_sujeto_resolver_desde_hint()
        status, payload = h._json_calls[0]
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["curp"], "AISR750901MDFVLS01")
        self.assertEqual(payload["estrategia"], "rfc_xwalk_imms_s+nombre".replace(
            "imms_s", "imss_s"))

    # 4) NSS solo → xwalk imss_a → CURP.
    def test_nss_unico_resuelve_curp(self):
        h = _fake_handler(path=(
            "/api/v1/sujeto/resolver_desde_hint?nss=12345678901"
        ))
        padron, ext = _stub_con(ext_curps=["AISR750901MDFVLS01"])
        with patch("duckdb.connect", return_value=padron), \
             patch("servir._init_extended_con", return_value=ext):
            h._handle_sujeto_resolver_desde_hint()
        status, payload = h._json_calls[0]
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["curp"], "AISR750901MDFVLS01")
        self.assertEqual(payload["estrategia"], "nss_xwalk_imss_a")

    # 5) Padrón directo por nombre+fecnac (2 datos secundarios).
    def test_nombre_paterno_materno_fecnac_resuelve(self):
        h = _fake_handler(path=(
            "/api/v1/sujeto/resolver_desde_hint"
            "?paterno=AVILA&materno=SALDAÑA&nombre=ROSALIA&fecnac=1975-09-01"
        ))
        padron_rows = [
            ("AISR750901MDFVLS01", "ROSALIA", "AVILA", "SALDAÑA",
             "1975-09-01", "10200", "C VISTA DEL ATARDECER 159",
             "FRACC LOMAS DE VISTABELLA"),
        ]
        padron, ext = _stub_con(padron_rows=padron_rows)
        with patch("duckdb.connect", return_value=padron), \
             patch("servir._init_extended_con", return_value=ext):
            h._handle_sujeto_resolver_desde_hint()
        status, payload = h._json_calls[0]
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["curp"], "AISR750901MDFVLS01")
        self.assertIn("padron_nombre", payload["estrategia"])

    # 6) Padrón por nombre + domicilio (CP).
    def test_nombre_paterno_materno_cp_resuelve(self):
        h = _fake_handler(path=(
            "/api/v1/sujeto/resolver_desde_hint"
            "?paterno=AVILA&materno=SALDAÑA&nombre=ROSALIA&cp=10200"
        ))
        padron_rows = [
            ("AISR750901MDFVLS01", "ROSALIA", "AVILA", "SALDAÑA",
             "1975-04-27", "10200", None, None),
        ]
        padron, ext = _stub_con(padron_rows=padron_rows)
        with patch("duckdb.connect", return_value=padron), \
             patch("servir._init_extended_con", return_value=ext):
            h._handle_sujeto_resolver_desde_hint()
        status, payload = h._json_calls[0]
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["curp"], "AISR750901MDFVLS01")

    # 7) Sin datos secundarios → devuelve error informativo.
    def test_solo_nombre_paterno_datos_insuficientes(self):
        h = _fake_handler(path=(
            "/api/v1/sujeto/resolver_desde_hint?paterno=GARCIA&nombre=JUAN"
        ))
        padron, ext = _stub_con()
        with patch("duckdb.connect", return_value=padron), \
             patch("servir._init_extended_con", return_value=ext):
            h._handle_sujeto_resolver_desde_hint()
        status, payload = h._json_calls[0]
        self.assertFalse(payload["ok"])
        self.assertIn("datos insuficientes", payload["error"])

    # 8) Sin matches en padrón.
    def test_sin_match_devuelve_estrategia_sin_match(self):
        h = _fake_handler(path=(
            "/api/v1/sujeto/resolver_desde_hint"
            "?paterno=ZZZZZZ&materno=YYYYYY&nombre=XXXXXX&fecnac=1900-01-01"
        ))
        padron, ext = _stub_con(padron_rows=[])
        with patch("duckdb.connect", return_value=padron), \
             patch("servir._init_extended_con", return_value=ext):
            h._handle_sujeto_resolver_desde_hint()
        status, payload = h._json_calls[0]
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["estrategia"], "sin_match")
        self.assertIn("sin coincidencias", payload["error"])

    # 9) Sin ningún parámetro → 400.
    def test_sin_parametros_retorna_400(self):
        h = _fake_handler(path="/api/v1/sujeto/resolver_desde_hint")
        h._handle_sujeto_resolver_desde_hint()
        status, payload = h._json_calls[0]
        self.assertEqual(status, 400)
        self.assertIn("se requiere", payload["error"])

    # 10) Match único con materno + fecnac → estrategia "padron_nombre+extras".
    def test_estrategia_devuelve_extras_count(self):
        h = _fake_handler(path=(
            "/api/v1/sujeto/resolver_desde_hint"
            "?paterno=AVILA&materno=SALDAÑA&nombre=ROSALIA"
            "&fecnac=1975-09-01&cp=10200"
        ))
        padron_rows = [
            ("AISR750901MDFVLS01", "ROSALIA", "AVILA", "SALDAÑA",
             "1975-09-01", "10200", None, None),
        ]
        padron, ext = _stub_con(padron_rows=padron_rows)
        with patch("duckdb.connect", return_value=padron), \
             patch("servir._init_extended_con", return_value=ext):
            h._handle_sujeto_resolver_desde_hint()
        status, payload = h._json_calls[0]
        self.assertTrue(payload["ok"])
        # 2 extras (fecnac + cp) → score >= 0.85
        self.assertGreaterEqual(payload["score"], 0.85)
        # hints_used debe reflejar exactamente lo que mandó el front
        self.assertEqual(payload["hints_used"]["paterno"], "AVILA")
        self.assertEqual(payload["hints_used"]["fecnac"], "1975-09-01")
        self.assertEqual(payload["hints_used"]["cp"], "10200")
