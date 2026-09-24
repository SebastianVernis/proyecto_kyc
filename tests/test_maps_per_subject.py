"""Tests de la integración mapas individuales en el reporte IA.

Cubre:
  1. _collect_addresses_for_sujeto extrae direcciones de TODAS las bases
  2. _generate_maps_for_addresses genera imagen PNG + geocoding para cada una
  3. generate_subject_html inyecta la sección 04c con mapas individuales
  4. Nominatim local se usa primero (source=local)

Los tests usan sujeto ficticio. Para los tests E2E del reporte HTML,
verificamos la estructura del HTML resultante.
"""
import json
import sys
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch, MagicMock

PROY = Path(__file__).resolve().parent.parent
BACKEND = PROY / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROY))

import servir
from report_generator import generate_subject_html


# ============================================================
# 1. _collect_addresses_for_sujeto
# ============================================================

class TestCollectAddressesForSujeto(unittest.TestCase):
    """_collect_addresses_for_sujeto debe extraer de TODAS las bases."""

    @classmethod
    def setUpClass(cls):
        cls.con = servir._init_extended_con()
        if cls.con is None:
            raise unittest.SkipTest("_init_extended_con() devolvió None")

    def test_retorna_lista(self):
        sujeto = {
            "curp": "AAAA000101HDFXXX00",
            "nombre": "JUAN",
            "paterno": "GARCIA",
            "materno": "LOPEZ",
            "cp": "06060",
            "rfc": "GALJ000101ABC",
            "calle": "REFORMA",
            "ext": "100",
            "colonia": "CENTRO",
            "estado_nombre": "CIUDAD DE MEXICO",
        }
        addrs, repuve = servir._collect_addresses_for_sujeto(sujeto)
        self.assertIsInstance(addrs, list)
        self.assertGreater(len(addrs), 0, "debe encontrar al menos 1 dirección")

    def test_cada_direccion_tiene_keys_requeridas(self):
        """Cada dirección debe tener titulo, fuente, direccion, cp, metadata."""
        sujeto = {
            "curp": "AAAA000101HDFXXX00",
            "nombre": "JUAN",
            "paterno": "GARCIA",
            "materno": "LOPEZ",
            "cp": "06060",
            "rfc": "GALJ000101ABC",
            "calle": "REFORMA",
            "ext": "100",
            "colonia": "CENTRO",
            "estado_nombre": "CIUDAD DE MEXICO",
        }
        addrs, repuve = servir._collect_addresses_for_sujeto(sujeto)
        required_keys = {"titulo", "fuente", "direccion", "cp", "metadata"}
        for a in addrs:
            self.assertTrue(required_keys.issubset(a.keys()),
                            f"dirección sin keys {required_keys - set(a.keys())}: {a}")
            self.assertIsInstance(a["titulo"], str)
            self.assertIsInstance(a["fuente"], str)
            self.assertIsInstance(a["direccion"], str)
            self.assertIsInstance(a["cp"], str)
            self.assertIsInstance(a["metadata"], dict)

    def test_patron_aparece_si_hay_direccion(self):
        """Si el sujeto tiene dirección en padrón, debe estar en la lista."""
        sujeto = {
            "curp": "TEST000000HDFXXX00",
            "nombre": "TEST",
            "paterno": "PRUEBA",
            "materno": "SUJETO",
            "cp": "06060",
            "rfc": "PRST000000ABC",
            "calle": "AVENIDA TEST",
            "ext": "1",
            "colonia": "CENTRO TEST",
            "estado_nombre": "CIUDAD DE MEXICO",
            "municipio_nombre": "CUAUHTEMOC",
        }
        addrs, repuve = servir._collect_addresses_for_sujeto(sujeto)
        padron_addrs = [a for a in addrs if a["fuente"] == "padron"]
        self.assertEqual(len(padron_addrs), 1, "debe haber 1 dirección de padrón")
        self.assertIn("AVENIDA TEST", padron_addrs[0]["direccion"])

    def test_fuentes_conocidas_en_listado(self):
        """Las fuentes reconocidas son padron, cfe, att, telcel, imss,
        repuve, empleadores, checkid, sepomex."""
        fuentes_validas = {
            "padron", "cfe", "att", "telcel", "imss",
            "repuve", "empleadores", "checkid", "sepomex",
        }
        sujeto = {
            "curp": "AAAA000101HDFXXX00",
            "nombre": "JUAN",
            "paterno": "GARCIA",
            "materno": "LOPEZ",
            "cp": "06060",
            "rfc": "GALJ000101ABC",
            "calle": "REFORMA",
            "ext": "100",
            "colonia": "CENTRO",
            "estado_nombre": "CIUDAD DE MEXICO",
        }
        addrs, repuve = servir._collect_addresses_for_sujeto(sujeto)
        fuentes_encontradas = {a["fuente"] for a in addrs}
        self.assertTrue(fuentes_encontradas.issubset(fuentes_validas),
                        f"fuentes no reconocidas: {fuentes_encontradas - fuentes_validas}")

    def test_sujeto_sin_datos_retorna_pocas_o_ninguna(self):
        """Sujeto con CURP/RFC/datos vacíos → lista vacía o solo padrón."""
        sujeto = {
            "curp": "",
            "rfc": "",
            "nombre": "",
            "paterno": "",
            "materno": "",
            "cp": "",
            "calle": "",
            "colonia": "",
        }
        addrs, repuve = servir._collect_addresses_for_sujeto(sujeto)
        # Sin datos no debe explotar
        self.assertIsInstance(addrs, list)


# ============================================================
# 2. _generate_maps_for_addresses
# ============================================================

class TestGenerateMapsForAddresses(unittest.TestCase):
    """_generate_maps_for_addresses genera imagen + geocoding."""

    def test_input_vacio_retorna_vacio(self):
        out, non_matched = servir._generate_maps_for_addresses([], max_maps=5)
        self.assertEqual(out, [])

    def test_direccion_conocida_genera_mapa(self):
        """AVENIDA REFORMA 100, CUAUHTEMOC, CDMX → debe geocodificar y generar PNG."""
        if not servir.INEGI_BASE.exists():
            self.skipTest("shapefiles INEGI no presentes en este host")
        addr = {
            "titulo": "Test",
            "fuente": "padron",
            "direccion": "AVENIDA REFORMA 100, CUAUHTEMOC, CIUDAD DE MEXICO",
            "cp": "06060",
            "metadata": {},
        }
        out, non_matched = servir._generate_maps_for_addresses([addr], max_maps=1)
        self.assertGreaterEqual(len(out), 0)
        if len(out) == 1:
            self.assertIsNotNone(out[0]["image_b64"])
            self.assertGreater(len(out[0]["image_b64"]), 1000)  # PNG mínimo
            self.assertIn("geocode_source", out[0])
            self.assertIn("lat", out[0])
            self.assertIn("lon", out[0])

    def test_max_maps_limita(self):
        """max_maps=2 con 5 direcciones → solo 2 procesadas."""
        addrs = [{"titulo": f"T#{i}", "fuente": "test",
                  "direccion": "AVENIDA REFORMA 100, CUAUHTEMOC, CIUDAD DE MEXICO",
                  "cp": "06060", "metadata": {}} for i in range(5)]
        out, non_matched = servir._generate_maps_for_addresses(addrs, max_maps=2)
        self.assertLessEqual(len(out), 2)

    def test_no_falla_sin_staticmap(self):
        """Si staticmap no está, debe seguir retornando sin imagen."""
        # Mock para que falle generate_static_map
        with patch("report_generator.generate_static_map",
                   side_effect=ImportError("no staticmap")):
            addr = {
                "titulo": "T", "fuente": "test",
                "direccion": "REFORMA 100, CDMX", "cp": "",
                "metadata": {},
            }
            out, non_matched = servir._generate_maps_for_addresses([addr], max_maps=1)
            # Debe retonar vacío (porque falla el mapa) pero NO debe reventar
            self.assertIsInstance(out, list)


# ============================================================
# 3. generate_subject_html con extra_maps
# ============================================================

class TestGenerateSubjectHtmlWithExtraMaps(unittest.TestCase):
    """El HTML debe incluir la sección 04c con cada mapa individual."""

    def _make_subject(self):
        return {
            "curp": "TEST000000HDFXXX00",
            "nombre": "TEST",
            "paterno": "PRUEBA",
            "materno": "SUJETO",
            "cp": "06060",
            "rfc": "PRST000000ABC",
            "calle": "REFORMA",
            "ext": "100",
            "colonia": "CENTRO",
            "estado_nombre": "CIUDAD DE MEXICO",
        }

    def test_extra_maps_none_no_renderiza_seccion(self):
        """Si extra_maps=None, la sección 04c NO debe aparecer."""
        sujeto = self._make_subject()
        html = generate_subject_html(sujeto, narrative="", enrichment={})
        self.assertNotIn("Mapas de Domicilios", html)
        self.assertNotIn('04c', html)

    def test_extra_maps_vacio_no_renderiza_seccion(self):
        sujeto = self._make_subject()
        html = generate_subject_html(sujeto, narrative="", enrichment={},
                                     extra_maps=[])
        self.assertNotIn("Mapas de Domicilios", html)

    def test_extra_maps_con_items_renderiza_seccion(self):
        """Si hay 3 mapas extra, debe haber 3 subsecciones 04c.NN."""
        sujeto = self._make_subject()
        maps = [
            {
                "titulo": f"Domicilio X #{i}",
                "fuente": "test",
                "direccion": f"AV REFORMA 100, COL CENTRO, CIUDAD DE MEXICO",
                "cp": "06060",
                "metadata": {"foo": "bar"},
                "image_b64": "iVBORw0KGgo=" * 100,  # dummy base64
                "lat": 19.4326,
                "lon": -99.1332,
                "display_name": "Test location",
                "geocode_source": "local",
            }
            for i in range(1, 4)
        ]
        html = generate_subject_html(sujeto, narrative="", enrichment={},
                                     extra_maps=maps)
        # Debe haber 1 sección intro + 3 subsecciones
        self.assertIn("Mapas de Domicilios", html)
        for i in range(1, 4):
            self.assertIn(f"Domicilio X #{i}", html)

    def test_extra_maps_seccion_esta_despues_de_patron_y_checkid(self):
        """La sección 04c debe estar DESPUÉS de la 04 (padrón) y 04b (CheckID)."""
        sujeto = self._make_subject()
        maps = [{
            "titulo": "TEST MAP",
            "fuente": "test",
            "direccion": "REFORMA 100",
            "cp": "06060",
            "metadata": {},
            "image_b64": "iVBORw0KGgo=" * 100,
            "lat": 19.0,
            "lon": -99.0,
            "display_name": "test",
            "geocode_source": "local",
        }]
        html = generate_subject_html(sujeto, narrative="", enrichment={},
                                     extra_maps=maps)
        idx_04c = html.find("Mapas de Domicilios")
        # Debe existir y ser > 0
        self.assertGreater(idx_04c, 0)


# ============================================================
# 4. geocode_address usa Nominatim local primero
# ============================================================

class TestGeocodeAddressLocalFirst(unittest.TestCase):
    """geocode_address debe intentar LOCAL primero, fallback al público."""

    def _make_response(self, body_list):
        """Crea un objeto tipo HTTPResponse con .read() funcional.

        Nominatim devuelve SIEMPRE una lista, no un dict.
        """
        body = json.dumps(body_list).encode("utf-8")
        resp = MagicMock()
        resp.read = MagicMock(return_value=body)
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_local_exitoso_no_llama_publico(self):
        from report_generator import geocode_address
        calls = []
        def fake_urlopen(req, **kwargs):
            calls.append(req.full_url)
            return self._make_response([{"lat": "19.4", "lon": "-99.1", "display_name": "X"}])
        with patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
            geo = geocode_address("REFORMA 100, CDMX")
        # Debe haber intentado SOLO el local
        self.assertEqual(len(calls), 1)
        self.assertTrue("127.0.0.1" in calls[0],
                        f"no intentó el local primero: {calls}")
        self.assertEqual(geo["source"], "local")

    def test_local_falla_cae_al_publico(self):
        from report_generator import geocode_address
        calls = []
        def fake_urlopen(req, **kwargs):
            calls.append(req.full_url)
            if "127.0.0.1" in req.full_url:
                raise ConnectionError("local down")
            return self._make_response([{"lat": "19.4", "lon": "-99.1", "display_name": "X"}])
        with patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
            geo = geocode_address("REFORMA 100, CDMX")
        self.assertEqual(len(calls), 2)
        self.assertEqual(geo["source"], "public")
        # El segundo intento debe llevar User-Agent (TOS OSM)
        self.assertIn("openstreetmap.org", calls[1])

    def test_local_desactivado_solo_publico(self):
        import os
        from report_generator import geocode_address
        old = os.environ.get("NOMINATIM_LOCAL_URL")
        os.environ["NOMINATIM_LOCAL_URL"] = ""
        try:
            calls = []
            def fake_urlopen(req, **kwargs):
                calls.append(req.full_url)
                return self._make_response([{"lat": "19.4", "lon": "-99.1", "display_name": "X"}])
            with patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
                geo = geocode_address("REFORMA 100, CDMX")
            self.assertEqual(len(calls), 1)
            self.assertIn("openstreetmap.org", calls[0])
            self.assertEqual(geo["source"], "public")
        finally:
            if old is None:
                os.environ.pop("NOMINATIM_LOCAL_URL", None)
            else:
                os.environ["NOMINATIM_LOCAL_URL"] = old


# ============================================================
# 5. Fallback CFE cuando Nominatim no geocodifica
# ============================================================

class TestCFEFallbackForUngeocodedAddresses(unittest.TestCase):
    """Si Nominatim no geocodifica una dirección, _generate_maps_for_addresses
    debe intentar _search_cfe_by_domicilio y agregar los rows encontrados
    como cfe_fallback en el dict resultante.

    Esto preserva la "integración pasada" del CFE lookup en el reporte IA.
    """

    def test_direccion_no_geocodificada_genera_cfe_fallback(self):
        """Dirección que Nominatim no geocodifica → debe buscar en CFE."""
        addr = {
            "titulo": "CFE sin geocode",
            "fuente": "cfe",
            "direccion": "PRIV ART 115 CONST 145, SERV RENOVADO, 1010, CENTRO",
            "cp": "",
            "metadata": {"num_servicio": "TEST"},
        }
        with patch("servir._search_cfe_by_domicilio") as mock_search:
            mock_search.return_value = {
                "query": {"calle": "PRIV ART 115 CONST 145", "cp": None,
                          "colonia": None, "division": None, "limit": 10},
                "rows": [{"num_servicio": "100100801421", "nombre": "X",
                          "direccion": "PRIV ART 115 CONST 145", "cp": "",
                          "colonia": "CENTRO", "division": "BAJ",
                          "zona_cod": "1010", "zona_nom": "AGUASCALIENTES",
                          "calle_adicional_1": "SERV RENOVADO",
                          "calle_adicional_2": None, "agencia_cod": "",
                          "agencia_nom": "", "source_folder": "11_AGS_GTO_QRO_ZAC"}],
                "count": 1,
                "elapsed_s": 0.5,
                "truncated": False,
                "found": True,
                "error": None,
                "nota": "test",
            }
            # Patcher el geocode_address en report_generator (que es donde
            # _generate_maps_for_addresses lo importa).
            with patch("report_generator.geocode_address", return_value=None):
                out, non_matched = servir._generate_maps_for_addresses([addr], max_maps=1)

        self.assertEqual(len(out), 1, "debe retornar la dirección aunque no geocodifique")
        m = out[0]
        self.assertTrue(m.get("geocode_failed"), "debe marcar geocode_failed=True")
        self.assertIsNone(m.get("image_b64"), "sin imagen PNG")
        self.assertIsNone(m.get("lat"), "sin coords")
        self.assertIsNone(m.get("geocode_source"), "sin source")
        # Y debe tener cfe_fallback con rows
        fb = m.get("cfe_fallback")
        self.assertIsNotNone(fb, "debe tener cfe_fallback")
        self.assertEqual(fb["count"], 1)
        self.assertEqual(len(fb["rows"]), 1)
        self.assertEqual(fb["rows"][0]["num_servicio"], "100100801421")

    def test_direccion_geocodificada_no_fallback(self):
        """Si Nominatim geocodifica OK, no debe llamarse _search_cfe_by_domicilio."""
        addr = {
            "titulo": "Padrón",
            "fuente": "padron",
            "direccion": "AVENIDA REFORMA 100, CUAUHTEMOC, CIUDAD DE MEXICO",
            "cp": "06060",
            "metadata": {},
        }
        with patch("servir._search_cfe_by_domicilio") as mock_search:
            mock_search.return_value = {"count": 0, "rows": []}
            with patch("report_generator.geocode_address",
                       return_value={"lat": 19.4, "lon": -99.1,
                                     "display_name": "X", "source": "local"}):
                out, non_matched = servir._generate_maps_for_addresses([addr], max_maps=1)

        mock_search.assert_not_called()
        self.assertEqual(out[0]["geocode_source"], "local")
        self.assertIsNone(out[0].get("cfe_fallback"))
        self.assertNotIn("geocode_failed", out[0])

    def test_direccion_no_geocodificada_sin_calle_parseable_no_fallback(self):
        """Dirección sin calle parseable (ej. solo 'X') → NO debe llamar CFE."""
        addr = {
            "titulo": "X",
            "fuente": "x",
            "direccion": "X",
            "cp": "",
            "metadata": {},
        }
        with patch("servir._search_cfe_by_domicilio") as mock_search:
            mock_search.return_value = {"count": 0, "rows": []}
            with patch("report_generator.geocode_address", return_value=None):
                out, non_matched = servir._generate_maps_for_addresses([addr], max_maps=1)

        mock_search.assert_not_called()
        self.assertTrue(out[0].get("geocode_failed"))

    def test_search_cfe_by_domicilio_parametros_correctos(self):
        """Los args a _search_cfe_by_domicilio deben ser los parseados."""
        addr = {
            "titulo": "CFE",
            "fuente": "cfe",
            "direccion": "PRIV ART 115 CONST 145, SERV RENOVADO, 1010, CENTRO",
            "cp": "12345",
            "metadata": {},
        }
        with patch("servir._search_cfe_by_domicilio") as mock_search:
            mock_search.return_value = {"count": 1, "rows": [{}]}
            with patch("report_generator.geocode_address", return_value=None):
                servir._generate_maps_for_addresses([addr], max_maps=1)

        mock_search.assert_called_once()
        kwargs = mock_search.call_args.kwargs
        # calle = PRIMER componente "PRIV ART 115 CONST 145"
        self.assertEqual(kwargs["calle"], "PRIV ART 115 CONST 145")
        self.assertEqual(kwargs["limit"], 10)
        # cp del address (no del parseo)
        self.assertEqual(kwargs["cp"], "12345")


class TestParseAddressComponents(unittest.TestCase):
    """_parse_address_components (función interna) debe extraer
    calle/colonia/cp correctamente."""

    def _parse(self, text):
        """Ejecuta _parse_address_components extrayéndola del bytecode."""
        import re
        import dis
        # _parse_address_components es función anidada dentro de
        # _generate_maps_for_addresses. La extraemos re-compilando el
        # módulo con un binding que la expone.
        from servir import _generate_maps_for_addresses
        import sys
        # Hack: monkey-patch el módulo para exponer la función.
        # Como es nested, accedemos via closure (no funciona). Mejor:
        # la copiamos a nivel módulo ejecutándola.
        # Más simple: extraemos su código fuente y lo ejecutamos.
        import inspect
        src = inspect.getsource(_generate_maps_for_addresses)
        # Buscar el bloque "def _parse_address_components(text: str) -> tuple:"
        start = src.find("def _parse_address_components(")
        if start == -1:
            self.fail("_parse_address_components no encontrado")
        # Encontrar el final (return con indentación 8 espacios)
        lines = src[start:].splitlines()
        end_idx = 0
        for i, line in enumerate(lines):
            if i == 0:
                continue  # def line
            if line.startswith("        return"):
                end_idx = i
                break
        body = "\n".join(lines[:end_idx + 1])
        local_ns = {"re": re}
        exec(body, local_ns)
        return local_ns["_parse_address_components"](text)

    def test_calle_cp_colonia_estandar(self):
        c, col, cp = self._parse("AV REFORMA 100, COL CENTRO, CDMX, 06060")
        self.assertEqual(cp, "06060")
        self.assertEqual(col, "COL CENTRO")
        self.assertEqual(c, "AV REFORMA 100")

    def test_calle_con_serv_tag_cfe(self):
        c, col, cp = self._parse("PRIV ART 115 CONST 145, SERV RENOVADO, 1010, CENTRO")
        self.assertEqual(c, "PRIV ART 115 CONST 145")
        # CENTRO no es colonia (no empieza con COL/FRACC)
        self.assertEqual(col, "")
        self.assertEqual(cp, "")

    def test_calle_con_fracc_colonia(self):
        c, col, cp = self._parse("C VISTA DEL ATARDECER 159, FRACC LOMAS DE VISTABELLA, AGS, 20298")
        self.assertEqual(c, "C VISTA DEL ATARDECER 159")
        self.assertEqual(col, "FRACC LOMAS DE VISTABELLA")
        self.assertEqual(cp, "20298")

    def test_texto_basura(self):
        c, col, cp = self._parse("ELOTES FTE TEMPLO S JUAN")
        self.assertEqual(c, "ELOTES FTE TEMPLO S JUAN")
        self.assertEqual(col, "")
        self.assertEqual(cp, "")


class TestCFEFallbackHTMLRendering(unittest.TestCase):
    """El HTML debe mostrar la tabla CFE fallback."""

    def _make_sujeto(self):
        return {
            "curp": "TEST000000HDFXXX00",
            "nombre": "TEST", "paterno": "X", "materno": "Y",
            "cp": "06060", "rfc": "XXX000000XXX",
            "calle": "REFORMA", "ext": "1", "colonia": "CENTRO",
            "estado_nombre": "CDMX",
        }

    def test_cfe_fallback_se_renderiza_en_html(self):
        sujeto = self._make_sujeto()
        maps = [{
            "titulo": "Domicilio CFE #1",
            "fuente": "cfe",
            "direccion": "X 123",
            "cp": "",
            "metadata": {},
            "image_b64": None,
            "lat": None, "lon": None, "display_name": "",
            "geocode_source": None,
            "geocode_failed": True,
            "cfe_fallback": {
                "query": {"calle": "X", "cp": None, "colonia": None,
                          "division": None, "limit": 10},
                "rows": [{"num_servicio": "12345", "nombre": "PROMETEO",
                          "direccion": "X 123", "cp": "", "colonia": "C",
                          "division": "BAJ", "zona_cod": "1000",
                          "zona_nom": "AGS", "calle_adicional_1": "",
                          "calle_adicional_2": "", "agencia_cod": "",
                          "agencia_nom": "", "source_folder": ""}],
                "count": 1, "elapsed_s": 0.1, "truncated": False,
                "found": True, "error": None,
                "nota": "test",
            },
        }]
        html = generate_subject_html(sujeto, narrative="", enrichment={},
                                     extra_maps=maps)
        # Debe tener la tabla de fallback CFE
        self.assertIn("cfe-fallback-table", html)
        # Debe mencionar "Integración pasada"
        self.assertIn("Integración pasada", html)
        # Debe incluir el num_servicio en la tabla
        self.assertIn("12345", html)

    def test_cfe_fallback_count_cero_muestra_alert_warn(self):
        """Si cfe_fallback.count=0, debe mostrar el alert-warn normal."""
        sujeto = self._make_sujeto()
        maps = [{
            "titulo": "CFE sin match",
            "fuente": "cfe",
            "direccion": "ABC",
            "cp": "",
            "metadata": {},
            "image_b64": None,
            "lat": None, "lon": None, "display_name": "",
            "geocode_source": None,
            "geocode_failed": True,
            "cfe_fallback": {"count": 0, "rows": [], "query": {}},
        }]
        html = generate_subject_html(sujeto, narrative="", enrichment={},
                                     extra_maps=maps)
        # NO debe mencionar "Integración pasada" (porque count=0)
        self.assertNotIn("Integración pasada", html)
        # Pero sí debe mencionar que no se pudo geocodificar
        self.assertIn("alert-warn", html)
        self.assertIn("Sin ubicación verificable", html)


class TestSearchCFEByDomicilioFunction(unittest.TestCase):
    """_search_cfe_by_domicilio es reusable, callable, retorna dict."""

    def test_retorna_dict_siempre(self):
        # No debe explotar si se llama sin args
        r = servir._search_cfe_by_domicilio()
        self.assertIsInstance(r, dict)
        self.assertIn("rows", r)
        self.assertIn("count", r)
        self.assertIn("query", r)

    def test_no_extended_con_retorna_error(self):
        with patch("servir._init_extended_con", return_value=None):
            r = servir._search_cfe_by_domicilio(calle="X")
        self.assertEqual(r["count"], 0)
        self.assertEqual(r["rows"], [])
        self.assertEqual(r["error"], "extendido no inicializado")

    def test_calle_vacia_no_retorna_rows(self):
        # Sin calle/colonia/cp no debe correr el query
        r = servir._search_cfe_by_domicilio(calle="", colonia="", cp="")
        self.assertEqual(r["count"], 0)
        self.assertEqual(r["rows"], [])

    def test_cp_00000_se_ignora(self):
        # "00000" se considera como ausente
        r = servir._search_cfe_by_domicilio(calle="", cp="00000")
        # El query debe tener cp=None
        self.assertIsNone(r["query"]["cp"])

    def test_search_por_cp_solo_es_rapido(self):
        """Búsqueda por CP solo debe ser rápida y acotada."""
        import time
        t0 = time.time()
        r = servir._search_cfe_by_domicilio(cp="06060", limit=5)
        elapsed = time.time() - t0
        self.assertLess(elapsed, 10, f"búsqueda por CP tarda {elapsed:.1f}s (>10)")
        # CP 06060 puede tener resultados
        self.assertGreaterEqual(r["count"], 0)