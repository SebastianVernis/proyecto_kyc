"""Tests para endpoints nuevos: CFE buscar_por_nombre y verificar_unificada."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

PROY = Path(__file__).resolve().parent.parent
BACKEND = PROY / "backend"

sys.path.insert(0, str(BACKEND))


# Mock global para _ai_filter_matches — sin red, sin API key.
def _ai_noop(subject, matches, fuente):
    return {
        "matches_filtrados": list(matches),
        "confianza_global": "sin_datos",
        "resumen": "IA mocked en tests",
        "skipped": True,
    }


import servir
servir._ai_filter_matches_original = servir._ai_filter_matches
servir._ai_filter_matches = _ai_noop


# Forzar re-mock en cada test (en caso que test_ia_filter lo haya restaurado)
def _ensure_mocked():
    import servir as _s
    if _s._ai_filter_matches is not _ai_noop:
        _s._ai_filter_matches = _ai_noop


def _make_fake_handler(handler_class, path, session_user="test_user"):
    """Crea un handler mock con headers y session mock."""
    h = handler_class.__new__(handler_class)
    h.path = path
    h.headers = {"Host": "localhost"}  # necesario para _set_auth_rp
    h._json_calls = []
    h._audit_calls = []
    h._session_user = session_user

    # Mock de _require_session para que devuelva session válida
    # sin tocar auth.validate_session
    def mock_require_session():
        session = {"user_id": 1, "username": session_user, "is_admin": False}
        return session
    h._require_session = mock_require_session

    def _json(status, payload):
        h._json_calls.append((status, payload))
    def _audit(*args, **kwargs):
        h._audit_calls.append((args, kwargs))
    h._json = _json
    h._audit = _audit
    return h


class TestCFEBuscarPorNombre(unittest.TestCase):
    """Tests para /api/v1/cfe/buscar_por_nombre."""

    @classmethod
    def setUpClass(cls):
        import servir
        cls.servir = servir
        cls.handler_class = servir.Handler

    def _make_handler(self, query):
        return _make_fake_handler(self.handler_class, f"/api/v1/cfe/buscar_por_nombre?{query}")

    def test_like_paterno_retorna_resultados(self):
        h = self._make_handler("paterno=LOPEZ&limit=5")
        h._handle_cfe_buscar_por_nombre()
        self.assertEqual(len(h._json_calls), 1)
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)
        self.assertGreater(payload["count"], 0)
        self.assertLessEqual(payload["count"], 5)
        self.assertIn("rows", payload)
        if payload["rows"]:
            r0 = payload["rows"][0]
            # El nombre del titular está en 'nombre', no en 'paterno'
            self.assertIn("nombre", r0)
            self.assertIn("num_servicio", r0)

    def test_like_nombre_paterno_retorna_filtrado(self):
        h = self._make_handler("nombre=JUAN&paterno=LOPEZ&limit=3")
        h._handle_cfe_buscar_por_nombre()
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)
        self.assertLessEqual(payload["count"], 3)
        for r in payload["rows"]:
            self.assertIn("LOPEZ", r["nombre"].upper())
            self.assertIn("JUAN", r["nombre"].upper())

    def test_regex_true_usa_regex_matches(self):
        h = self._make_handler("paterno=LOPEZ&regex=1&limit=2")
        h._handle_cfe_buscar_por_nombre()
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("query", {}).get("regex"))
        self.assertLessEqual(payload["count"], 2)

    def test_sin_filtros_retorna_400(self):
        h = self._make_handler("")
        h._handle_cfe_buscar_por_nombre()
        status, payload = h._json_calls[0]
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_limit_cap_a_100(self):
        h = self._make_handler("paterno=LOPEZ&limit=999")
        h._handle_cfe_buscar_por_nombre()
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)
        self.assertLessEqual(payload["count"], 100)

    def test_paterno_inexistente_count_0(self):
        h = self._make_handler("paterno=ZZZZ_NO_EXISTE&limit=10")
        h._handle_cfe_buscar_por_nombre()
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["found"], False)
        self.assertEqual(payload["rows"], [])


class TestValidarEntidad(unittest.TestCase):
    """Tests para /api/v1/sujeto/validar/<entidad>."""

    def setUp(self):
        _ensure_mocked()

    @classmethod
    def setUpClass(cls):
        import servir
        cls.servir = servir
        cls.handler_class = servir.Handler

    def _make_handler(self, entidad, query):
        return _make_fake_handler(
            self.handler_class,
            f"/api/v1/sujeto/validar/{entidad}?{query}")

    def test_curp_valida_cfe_retorna_resultados(self):
        h = self._make_handler("cfe", "curp=RUVZ750427MOCZGT00")
        h._handle_sujeto_validar_entidad("cfe")
        self.assertEqual(len(h._json_calls), 1)
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)
        self.assertIn("entidad", payload)
        self.assertEqual(payload["entidad"], "cfe")
        self.assertIn("bases_consultadas", payload)
        self.assertIn("total_encontrado", payload)
        self.assertIn("resultados", payload)

    def test_curp_vacia_retorna_error(self):
        h = self._make_handler("cfe", "curp=")
        h._handle_sujeto_validar_entidad("cfe")
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)
        self.assertIn("error", payload)

    def test_entidad_desconocida_retorna_error(self):
        h = _make_fake_handler(
            self.handler_class,
            "/api/v1/sujeto/validar/noexiste?curp=RUVZ750427MOCZGT00")
        h._handle_sujeto_validar_entidad("noexiste")
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)
        self.assertIn("error", payload)
        self.assertIn("entidades_validas", payload)

    def test_cfe_devuelve_estructura_correcta(self):
        h = self._make_handler("cfe", "curp=RUVZ750427MOCZGT00")
        h._handle_sujeto_validar_entidad("cfe")
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)
        self.assertIsInstance(payload["bases_consultadas"], list)
        self.assertIsInstance(payload["resultados"], list)
        self.assertIsInstance(payload["total_encontrado"], int)

    def test_limit_se_aplica(self):
        h = self._make_handler("cfe", "curp=RUVZ750427MOCZGT00&limit=1")
        h._handle_sujeto_validar_entidad("cfe")
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)

    def test_telefono_como_parametro(self):
        h = self._make_handler("telcel", "telefono=5512345678")
        h._handle_sujeto_validar_entidad("telcel")
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)
        self.assertIn("entidad", payload)
        self.assertEqual(payload["entidad"], "telcel")

    def test_rfc_como_parametro(self):
        h = self._make_handler("santander", "rfc=LOMA750427")
        h._handle_sujeto_validar_entidad("santander")
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)
        self.assertIn("entidad", payload)


class TestValidarEntidadPatrones(unittest.TestCase):
    """Tests de patrones de respuesta de validación por entidad."""

    def setUp(self):
        _ensure_mocked()

    @classmethod
    def setUpClass(cls):
        import servir
        cls.servir = servir
        cls.handler_class = servir.Handler

    def test_respuesta_siempre_tiene_campos_basicos(self):
        h = _make_fake_handler(
            self.handler_class,
            "/api/v1/sujeto/validar/cfe?curp=RUVZ750427MOCZGT00")
        h._handle_sujeto_validar_entidad("cfe")
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)
        for field in ["entidad", "bases_consultadas", "total_encontrado",
                       "total_por_base", "resultados"]:
            self.assertIn(field, payload)


if __name__ == "__main__":
    unittest.main()