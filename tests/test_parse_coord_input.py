"""Tests de parse_coord_input: entrada libre de coordenadas → lat/lon.

Cubre los formatos que el operador puede pegar en CFE Coords:
  - par decimal "de corrido" (coma, espacio, ';', '/')
  - DMS
  - links de Google Maps completos (@, q=, ll=, !3d!4d, /place/)
  - inversión lon,lat (swap automático)
  - expansión de links cortos (mockeada, sin red real)
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROY / "backend"))

import servir

P = lambda t, **k: servir.parse_coord_input(t, **k)


class TestDecimalCorrido(unittest.TestCase):
    def test_coma(self):
        r = P("16.8114722, -99.8446944", allow_network=False)
        self.assertEqual((r["lat"], r["lon"]), (16.8114722, -99.8446944))
        self.assertEqual(r["source"], "decimal")

    def test_espacio(self):
        r = P("16.8114722 -99.8446944", allow_network=False)
        self.assertEqual((r["lat"], r["lon"]), (16.8114722, -99.8446944))

    def test_pegado_sin_espacio(self):
        r = P("16.8114722,-99.8446944", allow_network=False)
        self.assertEqual(r["lat"], 16.8114722)

    def test_slash(self):
        r = P("19.4326/-99.1332", allow_network=False)
        self.assertEqual((r["lat"], r["lon"]), (19.4326, -99.1332))

    def test_swap_lon_lat(self):
        # Pegado invertido (lon, lat): lat fuera de rango → swap.
        r = P("-99.1332, 19.4326", allow_network=False)
        self.assertEqual((r["lat"], r["lon"]), (19.4326, -99.1332))
        self.assertIn("swap", r["source"])


class TestDMS(unittest.TestCase):
    def test_dms(self):
        r = P('16°48\'41.3"N 99°50\'40.9"W', allow_network=False)
        self.assertAlmostEqual(r["lat"], 16.8114722, places=5)
        self.assertAlmostEqual(r["lon"], -99.8446944, places=5)
        self.assertEqual(r["source"], "dms")


class TestGoogleMapsUrl(unittest.TestCase):
    def test_arroba(self):
        r = P("https://www.google.com/maps/place/X/@19.4326,-99.1332,17z/data=!3m1",
              allow_network=False)
        self.assertEqual((r["lat"], r["lon"]), (19.4326, -99.1332))
        self.assertTrue(r["source"].startswith("url:@"))

    def test_query_q(self):
        r = P("https://www.google.com/maps?q=20.6597,-103.3496", allow_network=False)
        self.assertEqual((r["lat"], r["lon"]), (20.6597, -103.3496))

    def test_ll(self):
        r = P("https://maps.google.com/?ll=21.1619,-86.8515&z=15", allow_network=False)
        self.assertEqual((r["lat"], r["lon"]), (21.1619, -86.8515))

    def test_3d4d(self):
        r = P("https://www.google.com/maps/place/data=!3d19.4326!4d-99.1332",
              allow_network=False)
        self.assertEqual((r["lat"], r["lon"]), (19.4326, -99.1332))

    def test_url_sin_coords(self):
        r = P("https://www.google.com/maps/search/farmacia", allow_network=False)
        self.assertIn("error", r)


class TestInvalidos(unittest.TestCase):
    def test_vacio(self):
        self.assertIn("error", P("", allow_network=False))

    def test_basura(self):
        self.assertIn("error", P("no soy coordenadas", allow_network=False))

    def test_cero_cero(self):
        self.assertIn("error", P("0, 0", allow_network=False))

    def test_fuera_de_rango(self):
        self.assertIn("error", P("200, 500", allow_network=False))


class TestLinkCorto(unittest.TestCase):
    def test_expansion_mockeada(self):
        # Simula que maps.app.goo.gl redirige a una URL de maps con @lat,lon.
        final = "https://www.google.com/maps/place/Zocalo/@19.4326,-99.1332,17z"

        class _Resp:
            def geturl(self_inner):
                return final
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, *a):
                return False

        with patch.object(servir.urllib.request, "urlopen", return_value=_Resp()):
            r = P("https://maps.app.goo.gl/abc123", allow_network=True)
        self.assertEqual((r["lat"], r["lon"]), (19.4326, -99.1332))
        self.assertIn("expand", r["source"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
