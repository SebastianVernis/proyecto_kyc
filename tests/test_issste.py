"""Tests de issste.duckdb + endpoint HTTP /api/v1/issste/buscar."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROY = Path(__file__).resolve().parent.parent
BACKEND = PROY / "backend"

sys.path.insert(0, str(BACKEND))


class TestISSSTEView(unittest.TestCase):
    """api.issste_empleado debe existir y tener 2.7M filas."""

    @classmethod
    def setUpClass(cls):
        import servir
        cls.con = servir._init_extended_con()
        if cls.con is None:
            raise unittest.SkipTest("_init_extended_con() devolvió None")

    def test_vista_issste_empleado_existe(self):
        rows = self.con.execute("""
            SELECT count(*) FROM duckdb_views()
            WHERE schema_name='api' AND view_name='issste_empleado'
        """).fetchone()
        self.assertEqual(rows[0], 1)

    def test_vista_tiene_2_7m_filas(self):
        n = self.con.execute("SELECT count(*) FROM api.issste_empleado").fetchone()[0]
        self.assertGreaterEqual(n, 2_700_000)
        self.assertLessEqual(n, 2_800_000)

    def test_vista_todas_las_columnas(self):
        cols = [d[0] for d in self.con.execute(
            "SELECT * FROM api.issste_empleado LIMIT 1").description]
        expected = ["id","paterno","materno","nombres",
                    "cargo","sexo","sueldo",
                    "ramo","entidad","modalidad","sector","estado"]
        self.assertEqual(cols, expected)

    def test_vista_join_con_catalogos_100pct(self):
        # Todos los empleados deben tener ramo+estado (los JOINs deben funcionar)
        con_ramo = self.con.execute(
            "SELECT count(*) FROM api.issste_empleado WHERE ramo IS NOT NULL").fetchone()[0]
        con_estado = self.con.execute(
            "SELECT count(*) FROM api.issste_empleado WHERE estado IS NOT NULL").fetchone()[0]
        total = self.con.execute(
            "SELECT count(*) FROM api.issste_empleado").fetchone()[0]
        self.assertEqual(con_ramo, total, "JOIN con cat_ramos debe ser 100%")
        self.assertEqual(con_estado, total, "JOIN con cat_estados debe ser 100%")


class TestISSSTEBuscaHandler(unittest.TestCase):
    """_handle_issste_buscar debe funcionar end-to-end."""

    @classmethod
    def setUpClass(cls):
        import servir
        cls.servir = servir
        cls.handler_class = servir.Handler

    def _make_handler(self, query):
        """Simula un handler con query string."""
        h = self.handler_class.__new__(self.handler_class)
        h.path = f"/api/v1/issste/buscar?{query}"
        h._json_calls = []
        h._audit_calls = []

        def _json(status, payload):
            h._json_calls.append((status, payload))
        def _audit(*args, **kwargs):
            h._audit_calls.append((args, kwargs))
        h._json = _json
        h._audit = _audit
        return h

    def test_paterno_LOPEZ_retorna_resultados(self):
        h = self._make_handler("paterno=LOPEZ&limit=10")
        h._handle_issste_buscar()
        self.assertEqual(len(h._json_calls), 1)
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)
        self.assertGreater(payload["count"], 0)
        self.assertLessEqual(payload["count"], 10)
        self.assertIn("rows", payload)
        self.assertIn("query", payload)
        # Verificar estructura de rows
        if payload["rows"]:
            r0 = payload["rows"][0]
            self.assertIn("paterno", r0)
            self.assertIn("sueldo", r0)
            self.assertIn("ramo", r0)

    def test_paterno_materno_nombre_filtra_correctamente(self):
        h = self._make_handler("paterno=GARCIA&nombre=JUAN&limit=5")
        h._handle_issste_buscar()
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)
        # Todos los rows deben tener paterno LIKE %GARCIA% y nombre LIKE %JUAN%
        for r in payload["rows"]:
            self.assertIn("GARCIA", r["paterno"].upper())
            self.assertIn("JUAN", r["nombres"].upper())

    def test_sin_filtros_retorna_400(self):
        h = self._make_handler("")
        h._handle_issste_buscar()
        status, payload = h._json_calls[0]
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_sexo_M_filtra_solo_mujeres(self):
        h = self._make_handler("paterno=LOPEZ&sexo=M&limit=5")
        h._handle_issste_buscar()
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)
        for r in payload["rows"]:
            self.assertEqual(r["sexo"], "M")

    def test_orden_por_sueldo_descendente(self):
        h = self._make_handler("paterno=GARCIA&limit=10")
        h._handle_issste_buscar()
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)
        sueldos = [r["sueldo"] for r in payload["rows"]]
        # Verificar orden descendente (None al final)
        filtered = [s for s in sueldos if s is not None]
        self.assertEqual(filtered, sorted(filtered, reverse=True),
                         "rows deben estar ordenados por sueldo DESC")

    def test_paterno_inexistente_retorna_count_0(self):
        h = self._make_handler("paterno=ZZZZZ_NO_EXISTE&limit=10")
        h._handle_issste_buscar()
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["found"], False)
        self.assertEqual(payload["rows"], [])

    def test_limit_capped_a_100(self):
        h = self._make_handler("paterno=LOPEZ&limit=999")
        h._handle_issste_buscar()
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)
        # Si count >= 100, fue capado
        # Si count < 100, no se alcanzó el cap (pero el query.limit debe ser 100)
        self.assertLessEqual(payload["count"], 100)

    def test_auditoria_no_falla(self):
        """_audit debe ejecutarse sin lanzar excepciones (puede ser no-op)."""
        h = self._make_handler("paterno=LOPEZ&limit=5")
        # Si _audit requiere session, el handler lo pasa en kwargs (puede ser None en test)
        try:
            h._handle_issste_buscar()
            # Handler debe haber enviado respuesta 200 sin error
            self.assertEqual(h._json_calls[-1][0], 200)
        except Exception as e:
            self.fail(f"_handle_issste_buscar() lanzó excepción: {e}")


class TestReportIncluyeISSSTE(unittest.TestCase):
    """El reporte IA debe incluir sección 06b — Padrón ISSSTE."""

    @classmethod
    def setUpClass(cls):
        from report_generator import generate_subject_html
        cls.gen = staticmethod(generate_subject_html)

    def test_seccion_issste_sin_datos(self):
        """Sin enrichment['issste'] → muestra alert-info."""
        sujeto = {
            "curp": "TEST000000HDFXXX00",
            "nombre": "TEST",
            "paterno": "TEST",
            "materno": "TEST",
            "fecnac": "2000-01-01",
        }
        html = self.gen(subject_data=sujeto, narrative="test", enrichment={})
        self.assertIn("06b", html)
        self.assertIn("ISSSTE", html)
        self.assertIn("alert-info", html)

    def test_seccion_issste_con_matches(self):
        """Con enrichment['issste']['matches'] → muestra tabla."""
        sujeto = {
            "curp": "TEST000000HDFXXX00",
            "nombre": "TEST",
            "paterno": "TEST",
            "materno": "TEST",
            "fecnac": "2000-01-01",
        }
        enrichment = {
            "issste": {
                "matches": [
                    {"paterno": "TEST", "materno": "USER", "nombres": "FOO",
                     "cargo": "BASE", "sexo": "H",
                     "sueldo": 15000.00, "ramo": "SEP",
                     "entidad": "MEXICO", "sector": "GOBIERNO"},
                ],
                "count": 1,
                "query": {"paterno": "TEST"},
            }
        }
        html = self.gen(subject_data=sujeto, narrative="test", enrichment=enrichment)
        self.assertIn("06b", html)
        self.assertIn("15,000", html)  # Sueldo formateado
        self.assertIn("TEST USER", html)
        self.assertIn("alert-ok", html)

    def test_seccion_issste_con_error(self):
        """Con enrichment['issste']['error'] → muestra alert-warn."""
        sujeto = {"curp": "X", "nombre": "X", "paterno": "X", "materno": "X"}
        enrichment = {"issste": {"error": "DB no inicializada"}}
        html = self.gen(subject_data=sujeto, narrative="test", enrichment=enrichment)
        self.assertIn("06b", html)
        self.assertIn("DB no inicializada", html)
        self.assertIn("alert-warn", html)

    def test_sources_list_incluye_issste(self):
        sujeto = {"curp": "X", "nombre": "X", "paterno": "X", "materno": "X"}
        html = self.gen(subject_data=sujeto, narrative="test", enrichment={})
        self.assertIn("ISSSTE", html)
        self.assertIn("2.7M", html)


class TestCFECoordenadasDisplayName(unittest.TestCase):
    """El endpoint /api/v1/cfe/coordenadas debe retornar display_name."""

    @classmethod
    def setUpClass(cls):
        import servir
        cls.servir = servir
        cls.handler_class = servir.Handler

    def _make_handler(self, query):
        h = self.handler_class.__new__(self.handler_class)
        h.path = f"/api/v1/cfe/coordenadas?{query}"
        h._json_calls = []
        h._audit_calls = []
        def _json(status, payload):
            h._json_calls.append((status, payload))
        def _audit(*args, **kwargs):
            h._audit_calls.append((args, kwargs))
        h._json = _json
        h._audit = _audit
        return h

    @patch("urllib.request.urlopen")
    def test_response_incluye_display_name(self, mock_urlopen):
        """Mockea Nominatim local para retornar display_name completo."""
        import json as _json
        body = _json.dumps([{
            "lat": "19.4326",
            "lon": "-99.1332",
            "display_name": "Ciudad de México, México",
            "address": {"road": "Av. 5 de Mayo", "city": "Ciudad de México",
                       "state": "CDMX", "postcode": "06000"},
        }]).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = lambda s, *a: None
        mock_resp.read = lambda: body
        mock_urlopen.return_value = mock_resp

        h = self._make_handler("lat=19.4326&lon=-99.1332&limit=5")
        h._handle_cfe_coordenadas()
        self.assertEqual(len(h._json_calls), 1)
        status, payload = h._json_calls[0]
        self.assertEqual(status, 200)
        # display_name debe estar presente
        self.assertIn("display_name", payload)
        self.assertEqual(payload["display_name"], "Ciudad de México, México")
        # También debe seguir retornando geocode_source
        self.assertIn("geocode_source", payload)


class TestISSSTEHTML(unittest.TestCase):
    """El frontend issste.html debe existir y tener los elementos clave."""

    @classmethod
    def setUpClass(cls):
        cls.html_path = PROY / "frontend" / "issste.html"
        if not cls.html_path.exists():
            raise unittest.SkipTest("issste.html no existe")
        cls.html = cls.html_path.read_text()

    def test_tiene_formulario(self):
        self.assertIn("paterno", self.html)
        self.assertIn("materno", self.html)
        self.assertIn("nombre", self.html)
        self.assertIn("Buscar en padrón ISSSTE", self.html)

    def test_llama_endpoint_correcto(self):
        self.assertIn("/api/v1/issste/buscar", self.html)

    def test_tiene_navegacion_a_otras_paginas(self):
        self.assertIn("/dashboard.html", self.html)

    def test_tiene_estilos_dark_theme(self):
        self.assertIn("global.css", self.html)
        self.assertIn("var(--panel)", self.html)

    def test_tiene_auto_search(self):
        self.assertIn("auto", self.html)
        self.assertIn("params.get", self.html)


if __name__ == "__main__":
    unittest.main()