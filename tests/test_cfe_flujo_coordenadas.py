"""Tests del flujo CFE + coordenadas GPS.

Cubre:
  1. _handle_cfe_buscar_domicilio (reparado, usa columnas normalizadas)
  2. _handle_persona_cfe_buscar (reparado)
  3. _handle_persona_cfe_servicio (reparado)
  4. _handle_cfe_coordenadas (nuevo)
  5. _init_extended_con expone api.cfe_medidor con todas las columnas
     esperadas para estos handlers.

Los tests NO hacen llamadas reales a Nominatim — mockean urllib.request.
Las queries a DuckDB son reales (66M filas, datos sintéticos).
"""
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

PROY = Path(__file__).resolve().parent.parent
BACKEND = PROY / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROY))

import servir

# ============================================================
# 1. Schema de api.cfe_medidor (lo que los handlers esperan)
# ============================================================

class TestCFEMedidorSchema(unittest.TestCase):
    """api.cfe_medidor debe tener las columnas que los handlers leen."""

    @classmethod
    def setUpClass(cls):
        cls.con = servir._init_extended_con()
        if cls.con is None:
            raise unittest.SkipTest("_init_extended_con() devolvió None")

    def test_columnas_normalizadas_requeridas(self):
        """Las columnas que los handlers _handle_cfe_* esperan deben existir."""
        cols = {r[0] for r in self.con.execute(
            "DESCRIBE api.cfe_medidor").fetchall()}
        required = {
            "numero_servicio", "division", "zona_cod", "zona_nom",
            "agencia_cod", "agencia_nom", "cp", "nombre",
            "direccion", "calle_adicional_1", "calle_adicional_2",
            "colonia", "source_folder", "source_file",
            "codigo_medidor", "numero_medidor", "campo_adicional_1", "hilos",
        }
        missing = required - cols
        self.assertEqual(missing, set(),
                         f"api.cfe_medidor sin columnas {missing}")

    def test_no_columnas_cXX_viejas(self):
        """Las columnas c01..c19 NO deben existir (eran del CFE crudo)."""
        cols = {r[0] for r in self.con.execute(
            "DESCRIBE api.cfe_medidor").fetchall()}
        legacy = {f"c{i:02d}" for i in range(1, 20)}
        leftover = cols & legacy
        self.assertEqual(leftover, set(),
                         f"api.cfe_medidor aún tiene columnas viejas {leftover}")

    def test_vista_tiene_datos(self):
        """La vista debe tener las 66M filas."""
        n = self.con.execute(
            "SELECT count(*) FROM api.cfe_medidor").fetchone()[0]
        self.assertGreater(n, 60_000_000, f"solo {n:,} filas")


# ============================================================
# 2. Handler HTTP para /api/v1/cfe/* (autenticado)
# ============================================================

def _make_handler(path, authed=True):
    """Crea un Handler mock con path fijo y sesión autenticada.

    _json se intercepta guardando (code, payload) en _json_calls.
    """
    h = servir.Handler.__new__(servir.Handler)
    h.path = path
    h._audit = MagicMock()
    h._json_calls = []

    def fake_json(code, payload):
        h._json_calls.append((code, payload))
    h._json = fake_json
    h._require_session = MagicMock(return_value="test_session" if authed else None)
    return h


class TestCFEHandlerBuscarDomicilio(unittest.TestCase):
    """/api/v1/cfe/buscar_domicilio — usa columnas normalizadas."""

    def test_calle_retorna_domicilios(self):
        """Una calle conocida debe devolver ≥1 fila."""
        # CFE tiene 'BAJA CATITA' según CFE_flujo_coordenadas.md
        h = _make_handler("/api/v1/cfe/buscar_domicilio?calle=BAJA%20CATITA&limit=10")
        h._handle_cfe_buscar_domicilio()
        self.assertGreater(len(h._json_calls), 0,
                           "handler no llamó _json")
        code, payload = h._json_calls[0]
        # Si encontró, debe tener rows + count
        if payload.get("found"):
            self.assertIn("rows", payload)
            self.assertGreater(payload["count"], 0)
            row = payload["rows"][0]
            # Debe usar columnas normalizadas, no c10..c14
            self.assertNotIn("c10", row)
            self.assertIn("direccion", row)
            self.assertIn("num_servicio", row)

    def test_sin_parametros_retorna_400(self):
        h = _make_handler("/api/v1/cfe/buscar_domicilio")
        h._handle_cfe_buscar_domicilio()
        code, payload = h._json_calls[0]
        self.assertEqual(code, 400)
        self.assertIn("se requiere", payload["error"])

    def test_cp_invalido_retorna_400(self):
        h = _make_handler("/api/v1/cfe/buscar_domicilio?cp=ABCDE")
        h._handle_cfe_buscar_domicilio()
        code, payload = h._json_calls[0]
        self.assertEqual(code, 400)


class TestCFEHandlerPersonaBuscar(unittest.TestCase):
    """/api/v1/persona/cfe/buscar — usa columna 'nombre' (no c10)."""

    def test_nombre_existente(self):
        """Un nombre que exista en CFE debe devolver filas."""
        # Buscar un nombre real en la base
        con = servir._init_extended_con()
        if con is None:
            self.skipTest("_init_extended_con() no inicializó")
        r = con.execute("""
            SELECT nombre FROM api.cfe_medidor
            WHERE nombre IS NOT NULL AND TRIM(nombre) != ''
            LIMIT 1
        """).fetchone()
        if r is None or not r[0]:
            self.skipTest("no hay nombres en api.cfe_medidor")
        nombre = r[0].split()[0]  # primer token = paterno

        h = _make_handler(f"/api/v1/persona/cfe/buscar?nombre={nombre}&limit=5")
        h._handle_persona_cfe_buscar()
        self.assertGreater(len(h._json_calls), 0)
        code, payload = h._json_calls[0]
        if payload.get("found"):
            row = payload["rows"][0]
            self.assertNotIn("c10", row)
            self.assertIn("nombre", row)

    def test_nombre_muy_corto_retorna_400(self):
        h = _make_handler("/api/v1/persona/cfe/buscar?nombre=AB")
        h._handle_persona_cfe_buscar()
        code, payload = h._json_calls[0]
        self.assertEqual(code, 400)


class TestCFEHandlerPersonaServicio(unittest.TestCase):
    """/api/v1/persona/cfe/<num_servicio> — usa columnas normalizadas."""

    def test_num_servicio_existente(self):
        """Un num_servicio válido debe devolver su fila."""
        con = servir._init_extended_con()
        if con is None:
            self.skipTest("_init_extended_con() no inicializó")
        r = con.execute("""
            SELECT numero_servicio FROM api.cfe_medidor
            WHERE numero_servicio IS NOT NULL
              AND LENGTH(TRIM(numero_servicio)) BETWEEN 10 AND 12
            LIMIT 1
        """).fetchone()
        if r is None:
            self.skipTest("no hay num_servicio válido en api.cfe_medidor")
        num = r[0].strip()

        h = _make_handler(f"/api/v1/persona/cfe/{num}")
        h._handle_persona_cfe_servicio(num)
        self.assertGreater(len(h._json_calls), 0)
        code, payload = h._json_calls[0]
        if payload.get("found"):
            row = payload["rows"][0]
            self.assertNotIn("c01", row)
            self.assertIn("num_servicio", row)
            self.assertIn("nombre", row)

    def test_num_servicio_invalido_retorna_400(self):
        h = _make_handler("/api/v1/persona/cfe/123")
        h._handle_persona_cfe_servicio("123")
        code, payload = h._json_calls[0]
        self.assertEqual(code, 400)


# ============================================================
# 3. Handler NUEVO: /api/v1/cfe/coordenadas (Nominatim + CFE)
# ============================================================

def _mock_nominatim_response(display_name, **addr_kwargs):
    """Crea una respuesta simulada de Nominatim reverse geocoding."""
    data = {
        "display_name": display_name,
        "address": addr_kwargs,
    }
    resp = MagicMock()
    resp.read = MagicMock(return_value=json.dumps(data).encode())
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestCFECoordenadasHandler(unittest.TestCase):
    """/api/v1/cfe/coordenadas — coord → dirección → CFE."""

    def test_sin_lat_lon_retorna_400(self):
        h = _make_handler("/api/v1/cfe/coordenadas")
        h._handle_cfe_coordenadas()
        code, payload = h._json_calls[0]
        self.assertEqual(code, 400)
        self.assertIn("lat/lon", payload["error"])

    def test_lat_fuera_de_rango(self):
        h = _make_handler("/api/v1/cfe/coordenadas?lat=99&lon=0")
        h._handle_cfe_coordenadas()
        code, payload = h._json_calls[0]
        self.assertEqual(code, 400)

    def test_coordenadas_cero(self):
        h = _make_handler("/api/v1/cfe/coordenadas?lat=0&lon=0")
        h._handle_cfe_coordenadas()
        code, payload = h._json_calls[0]
        self.assertEqual(code, 400)

    def test_coordenadas_no_numericas(self):
        h = _make_handler("/api/v1/cfe/coordenadas?lat=abc&lon=xyz")
        h._handle_cfe_coordenadas()
        code, payload = h._json_calls[0]
        self.assertEqual(code, 400)

    def test_nominatim_falla_retorna_503(self):
        """Si Nominatim no responde, retorna 503 con detalle."""
        h = _make_handler("/api/v1/cfe/coordenadas?lat=16.81&lon=-99.84")
        with patch("urllib.request.urlopen",
                   side_effect=Exception("connection timeout")):
            h._handle_cfe_coordenadas()
        code, payload = h._json_calls[0]
        self.assertEqual(code, 503)
        self.assertIn("no se pudo geocodificar", payload["error"])
        self.assertIn("fallback", payload)

    def test_nominatim_ok_sin_direccion_postal(self):
        """Si Nominatim devuelve un punto sin dirección postal, retorna 200 vacío."""
        h = _make_handler("/api/v1/cfe/coordenadas?lat=16.81&lon=-99.84")
        resp = _mock_nominatim_response("Punto en el mar",
                                         ocean="Pacífico")
        with patch("urllib.request.urlopen", return_value=resp):
            h._handle_cfe_coordenadas()
        code, payload = h._json_calls[0]
        self.assertEqual(code, 200)
        self.assertFalse(payload["found"])
        self.assertEqual(payload["count"], 0)

    def test_nominatim_ok_con_calle_buscada_en_cfe(self):
        """Flujo completo: coords → Nominatim → CFE."""
        con = servir._init_extended_con()
        if con is None:
            self.skipTest("_init_extended_con() no inicializó")
        # Sacar una calle real de CFE para el mock
        r = con.execute("""
            SELECT TRIM(direccion) FROM api.cfe_medidor
            WHERE direccion IS NOT NULL AND TRIM(direccion) != ''
            LIMIT 1
        """).fetchone()
        if r is None or not r[0]:
            self.skipTest("no hay direccion poblada en api.cfe_medidor")
        calle = r[0].split()[0]  # primer token
        if len(calle) < 3:
            self.skipTest(f"calle {calle!r} demasiado corta")

        resp = _mock_nominatim_response(
            f"Calle {calle} 123, Colonia X, Acapulco, Guerrero",
            road=calle,
            neighbourhood="Centro",
            city="Acapulco",
            state="Guerrero",
            postcode="39300",
        )
        h = _make_handler(f"/api/v1/cfe/coordenadas?lat=16.81&lon=-99.84&limit=5")
        with patch("urllib.request.urlopen", return_value=resp):
            h._handle_cfe_coordenadas()
        self.assertGreater(len(h._json_calls), 0,
                           "handler no llamó _json")
        code, payload = h._json_calls[0]
        self.assertEqual(code, 200)
        # Debe tener la dirección parseada
        self.assertIn("address", payload)
        self.assertEqual(payload["address"]["calle"], calle.upper())
        # Debe haber hecho query a CFE (rows puede estar vacío si no matchea)
        self.assertIn("rows", payload)
        self.assertIn("count", payload)
        self.assertIn("found", payload)

    def test_export_csv_con_resultados(self):
        """Si hay resultados y csv=1, devuelve el CSV en payload."""
        con = servir._init_extended_con()
        if con is None:
            self.skipTest("_init_extended_con() no inicializó")
        # Sacar calle real + cp
        r = con.execute("""
            SELECT TRIM(direccion), cp FROM api.cfe_medidor
            WHERE direccion IS NOT NULL AND TRIM(direccion) != ''
              AND cp IS NOT NULL AND TRIM(cp) != ''
            LIMIT 1
        """).fetchone()
        if r is None:
            self.skipTest("no hay calle+cp en api.cfe_medidor")
        calle, cp = r
        calle_token = calle.split()[0]

        resp = _mock_nominatim_response(
            f"Calle {calle_token}, X",
            road=calle_token, postcode=str(cp).strip(),
        )
        h = _make_handler(
            f"/api/v1/cfe/coordenadas?lat=16.81&lon=-99.84&csv=1&limit=10")
        with patch("urllib.request.urlopen", return_value=resp):
            h._handle_cfe_coordenadas()
        code, payload = h._json_calls[0]
        # Si hubo match (cp+calle), debe incluir csv
        if payload.get("found"):
            self.assertIn("csv", payload)
            self.assertIn("csv_size", payload)
            # CSV debe tener header
            first_line = payload["csv"].split("\n")[0]
            self.assertIn("num_servicio", first_line)
            self.assertIn("direccion", first_line)
        # Si no hubo match, found=False y no hay csv — eso también es válido
        self.assertIn("found", payload)


class TestCFECoordenadasLocalNominatim(unittest.TestCase):
    """/api/v1/cfe/coordenadas con Nominatim LOCAL activo."""

    def test_usa_local_primero(self):
        """Si Nominatim local responde, debe usarlo y NO ir al público."""
        sent_to = []
        call_count = [0]

        def fake_urlopen(req, **kwargs):
            call_count[0] += 1
            sent_to.append(req.full_url)
            return _mock_nominatim_response(
                "Calle X, Y", road="Calle", postcode="12345")

        h = _make_handler("/api/v1/cfe/coordenadas?lat=16.81&lon=-99.84")
        import os
        old_local = os.environ.get("NOMINATIM_LOCAL_URL")
        os.environ["NOMINATIM_LOCAL_URL"] = "http://127.0.0.1:9999"  # local ficticio
        try:
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                h._handle_cfe_coordenadas()
        finally:
            if old_local is None:
                os.environ.pop("NOMINATIM_LOCAL_URL", None)
            else:
                os.environ["NOMINATIM_LOCAL_URL"] = old_local

        # Debe haber intentado SOLO una vez (al local)
        self.assertEqual(call_count[0], 1,
                         f"esperaba 1 intento (local), obtuve {call_count[0]}: {sent_to}")
        self.assertTrue(sent_to[0].startswith("http://127.0.0.1:9999"),
                        f"no fue al local: {sent_to[0]}")
        # Debe reportar la fuente
        code, payload = h._json_calls[0]
        self.assertEqual(code, 200)
        self.assertEqual(payload.get("geocode_source"), "local")

    def test_fallback_al_publico_si_local_falla(self):
        """Si Nominatim local falla, debe caer al público."""
        call_count = [0]

        def fake_urlopen(req, **kwargs):
            call_count[0] += 1
            if "127.0.0.1:9999" in req.full_url:
                # Local: falla
                raise ConnectionError("local down")
            # Público: éxito
            return _mock_nominatim_response(
                "Calle Y, Z", road="Calle", postcode="54321")

        h = _make_handler("/api/v1/cfe/coordenadas?lat=16.81&lon=-99.84")
        import os
        old_local = os.environ.get("NOMINATIM_LOCAL_URL")
        os.environ["NOMINATIM_LOCAL_URL"] = "http://127.0.0.1:9999"
        try:
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                h._handle_cfe_coordenadas()
        finally:
            if old_local is None:
                os.environ.pop("NOMINATIM_LOCAL_URL", None)
            else:
                os.environ["NOMINATIM_LOCAL_URL"] = old_local

        self.assertEqual(call_count[0], 2,
                         "debe intentar local + público (2 intentos)")
        code, payload = h._json_calls[0]
        self.assertEqual(payload.get("geocode_source"), "public")

    def test_local_desactivado_solo_publico(self):
        """Si NOMINATIM_LOCAL_URL='', debe ir directo al público."""
        sent_to = []
        def fake_urlopen(req, **kwargs):
            sent_to.append(req.full_url)
            return _mock_nominatim_response(
                "Z", road="Calle", postcode="00000")

        h = _make_handler("/api/v1/cfe/coordenadas?lat=16.81&lon=-99.84")
        import os
        old_local = os.environ.get("NOMINATIM_LOCAL_URL")
        os.environ["NOMINATIM_LOCAL_URL"] = ""  # desactivado
        try:
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                h._handle_cfe_coordenadas()
        finally:
            if old_local is None:
                os.environ.pop("NOMINATIM_LOCAL_URL", None)
            else:
                os.environ["NOMINATIM_LOCAL_URL"] = old_local

        self.assertEqual(len(sent_to), 1, "debe ir solo al público")
        self.assertIn("nominatim.openstreetmap.org", sent_to[0])
        code, payload = h._json_calls[0]
        self.assertEqual(payload.get("geocode_source"), "public")


class TestCFECoordenadasUserAgent(unittest.TestCase):
    """El User-Agent enviado a Nominatim es identificable (TOS de OSM)."""

    def test_user_agent_identificable(self):
        """El User-Agent enviado al Nominatim PÚBLICO es identificable (TOS de OSM).

        Nominatim LOCAL no requiere UA. El test verifica que cuando el local
        falla y se cae al público, el público SÍ recibe un UA identificable.
        """
        sent_to_public_headers = {}
        call_count = [0]

        def fake_urlopen(req, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # Primer intento: Nominatim local (sin UA). Fallar.
                raise ConnectionError("local nominatim down")
            # Segundo intento: Nominatim público. Capturar UA.
            sent_to_public_headers.update(req.headers)
            return _mock_nominatim_response(
                "X, Y", road="Calle", neighbourhood="Colonia")

        h = _make_handler("/api/v1/cfe/coordenadas?lat=16.81&lon=-99.84")
        # Deshabilitar local para forzar fallback al público
        import os
        old_local = os.environ.get("NOMINATIM_LOCAL_URL")
        os.environ["NOMINATIM_LOCAL_URL"] = "http://127.0.0.1:1"  # puerto muerto
        try:
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                h._handle_cfe_coordenadas()
        finally:
            if old_local is None:
                os.environ.pop("NOMINATIM_LOCAL_URL", None)
            else:
                os.environ["NOMINATIM_LOCAL_URL"] = old_local

        ua = sent_to_public_headers.get("User-agent") or sent_to_public_headers.get("User-Agent")
        self.assertIsNotNone(ua, "no se envió User-Agent al público")
        # Debe tener algo identificable, NO el default de urllib
        self.assertNotIn("Python-urllib", ua,
                         f"User-Agent genérico detectado: {ua}")
        self.assertGreaterEqual(call_count[0], 2,
                                "debe intentar local + público, no solo público")
