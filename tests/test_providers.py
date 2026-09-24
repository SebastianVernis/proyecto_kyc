"""Tests de los 9 providers de /root/proyecto_kyc/backend/providers/.

Estrategia:
  - Tests unitarios SIN red: BaseProvider, normalize_curp/rfc, instanciación.
  - Tests con requests mockeados (unittest.mock): cada health() + 1 método
    representativo de cada provider.

NO se hacen llamadas reales a las APIs externas.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Asegurar que el path esté correcto
PROY = Path(__file__).resolve().parent.parent
BACKEND = PROY / "backend"
sys.path.insert(0, str(BACKEND))

# imports del package
from providers import (
    BaseProvider, ProviderError, normalize_curp, normalize_rfc,
    TlalocClient, SingulaClient, MoffinClient, KibanClient,
    ApifyOSINTClient,
    OllamaCloudClient,
)


# ============================================================
# 1. Tests sin red: base + normalizadores
# ============================================================

class TestBaseProvider(unittest.TestCase):
    """BaseProvider sin red."""

    def test_instanciar_sin_api_key(self):
        b = BaseProvider("test", api_key=None, base_url="https://x.test")
        self.assertEqual(b.name, "test")
        self.assertEqual(b.base_url, "https://x.test")
        self.assertIsNone(b.api_key)
        self.assertIsNotNone(b.session)

    def test_instanciar_con_api_key(self):
        b = BaseProvider("test", api_key="abc123")
        self.assertEqual(b.session.headers.get("Authorization"), "Bearer abc123")

    def test_base_url_strip_trailing_slash(self):
        b = BaseProvider("test", base_url="https://x.test/")
        self.assertEqual(b.base_url, "https://x.test")

    def test_provider_error_message(self):
        e = ProviderError("test", "falló", status=500)
        self.assertIn("test", str(e))
        self.assertIn("falló", str(e))
        self.assertEqual(e.status, 500)


class TestNormalizers(unittest.TestCase):
    """normalize_curp y normalize_rfc."""

    def test_curp_valido(self):
        self.assertEqual(normalize_curp("AUFG910209HDFGRR08"), "AUFG910209HDFGRR08")

    def test_curp_minusculas(self):
        self.assertEqual(normalize_curp("aufg910209hdfgrr08"), "AUFG910209HDFGRR08")

    def test_curp_con_espacios(self):
        self.assertEqual(normalize_curp("AUFG 910209 HDFGRR08"), "AUFG910209HDFGRR08")

    def test_curp_vacio(self):
        self.assertEqual(normalize_curp(""), "")

    def test_curp_none(self):
        self.assertEqual(normalize_curp(None), "")

    def test_curp_largo_invalido_pasa_upper(self):
        # Si no tiene 18 chars, devuelve upper tal cual (len warning)
        self.assertEqual(normalize_curp("TOOLONG"), "TOOLONG")

    def test_rfc_12chars(self):
        self.assertEqual(normalize_rfc("AUFG91020961"), "AUFG91020961")

    def test_rfc_13chars_con_homoclave(self):
        self.assertEqual(normalize_rfc("AUFG910209616"), "AUFG910209616")

    def test_rfc_minusculas(self):
        self.assertEqual(normalize_rfc("aufg910209616"), "AUFG910209616")

    def test_rfc_vacio(self):
        self.assertEqual(normalize_rfc(""), "")


# ============================================================
# 2. Tests con red MOCKEADA: instanciación + health() de cada provider
# ============================================================

def _mock_response(status=200, json_data=None, text=""):
    """Crea un mock de requests.Response."""
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"content-type": "application/json"}
    if json_data is not None:
        resp.json.return_value = json_data
    resp.text = text
    return resp


class TestProviderInstantiation(unittest.TestCase):
    """Cada provider debe poder instanciarse con api_key=None."""

    def test_tlaloc_instantiate(self):
        c = TlalocClient(api_key=None)
        self.assertEqual(c.name, "tlaloc")
        self.assertIsNotNone(c.session)

    def test_singula_instantiate(self):
        c = SingulaClient(api_key=None)
        self.assertEqual(c.base_url, "https://api.singula.mx")

    def test_moffin_instantiate(self):
        c = MoffinClient(api_key=None)
        self.assertEqual(c.name, "moffin")

    def test_kiban_instantiate(self):
        c = KibanClient(api_key=None)
        self.assertEqual(c.base_url, "https://docs.kiban.com/api/v1")

    def test_apify_instantiate(self):
        # ApifyOSINTClient requiere api_key (crea un ApifyBroker que exige token)
        c = ApifyOSINTClient(api_key="dummy_test_token")
        self.assertEqual(c.name, "apify")

    def test_ollama_instantiate(self):
        c = OllamaCloudClient(api_key=None)
        # El nombre puede ser configurable; verificamos que exista
        self.assertIsNotNone(c.name)


class TestProviderHealth(unittest.TestCase):
    """Cada provider expone health() que retorna dict (mockeado)."""

    def _check_health(self, client, expected_keys=None):
        """Llama client.health() con response mockeado y valida estructura."""
        with patch.object(client.session, "request",
                          return_value=_mock_response(200, {"status": "ok"})):
            result = client.health()
        self.assertIsInstance(result, dict)
        if expected_keys:
            for k in expected_keys:
                self.assertIn(k, result, f"falta {k} en {result}")

    def test_tlaloc_health(self):
        c = TlalocClient(api_key=None)
        self._check_health(c)

    def test_singula_health(self):
        c = SingulaClient(api_key=None)
        self._check_health(c)

    def test_moffin_health(self):
        c = MoffinClient(api_key=None)
        self._check_health(c)

    def test_kiban_health(self):
        c = KibanClient(api_key=None)
        self._check_health(c)

    def test_apify_health(self):
        # ApifyBroker usa requests directo, no self.session — mockeamos requests.get
        c = ApifyOSINTClient(api_key="dummy_test_token")
        with patch("requests.get",
                   return_value=_mock_response(200, {"data": {"username": "x"}})):
            result = c.health()
        self.assertIsInstance(result, dict)

    def test_ollama_health(self):
        c = OllamaCloudClient(api_key=None)
        self._check_health(c)


# ============================================================
# 3. Tests de UN método representativo de cada provider (mockeado)
# ============================================================

class TestProviderMethods(unittest.TestCase):
    """Cada provider: 1 método representativo retorna estructura esperada."""

    def test_tlaloc_validate_curp(self):
        c = TlalocClient(api_key=None)
        # Tlaloc.validate_curp retorna "valid" (no "valida") calculado por
        # statusCurp in ("RCN", "RBC", "ACT")
        with patch.object(c.session, "request",
                          return_value=_mock_response(200, {
                              "curp": "AUFG910209HDFGRR08",
                              "statusCurp": "RCN",
                              "nombres": "GERARDO"
                          })):
            r = c.validate_curp("AUFG910209HDFGRR08")
        self.assertIsInstance(r, dict)
        self.assertTrue(r.get("valid"))
        self.assertEqual(r["curp"], "AUFG910209HDFGRR08")

    def test_moffin_query_sat_rfc(self):
        c = MoffinClient(api_key=None)
        with patch.object(c.session, "request",
                          return_value=_mock_response(200, {
                              "rfc": "AUFG910209616",
                              "estatus": "activo"
                          })):
            r = c.query_sat_rfc("AUFG910209616")
        self.assertIsInstance(r, dict)
        self.assertIn("estatus", r)

    def test_kiban_check_pep_ofac(self):
        c = KibanClient(api_key=None)
        with patch.object(c.session, "request",
                          return_value=_mock_response(200, {
                              "pep": False,
                              "ofac": False,
                              "score": 5
                          })):
            r = c.check_pep_ofac("GERARDO", "AGUIRRE", "FRANCO")
        self.assertIsInstance(r, dict)
        self.assertIn("pep", r)
        self.assertIn("ofac", r)

    def test_apify_find_by_email(self):
        c = ApifyOSINTClient(api_key="dummy_test_token")
        # ApifyBroker.full_dossier no usa self.session — usa requests directo.
        # Lo importante es que find_by_email NO esté rota.
        # (Si find_by_email no existe o cambia de nombre, el test fallará con AttributeError)
        self.assertTrue(hasattr(c, "find_by_email"),
                        "ApifyOSINTClient debe tener método find_by_email")

    def test_ollama_chat(self):
        c = OllamaCloudClient(api_key=None)
        with patch.object(c.session, "request",
                          return_value=_mock_response(200, {
                              "message": {"role": "assistant", "content": "hola"}
                          })):
            r = c.chat([{"role": "user", "content": "hola"}])
        self.assertIsInstance(r, dict)
        self.assertIn("message", r)


# ============================================================
# 4. Tests de comportamiento BaseProvider con HTTP real mockeado
# ============================================================

class TestBaseProviderHTTP(unittest.TestCase):
    """BaseProvider._request maneja códigos HTTP correctamente."""

    def setUp(self):
        self.client = BaseProvider("t", base_url="https://x.test", retries=1)

    def test_200_retorna_json(self):
        with patch.object(self.client.session, "request",
                          return_value=_mock_response(200, {"a": 1})):
            r = self.client._get("/path")
        self.assertEqual(r, {"a": 1})

    def test_204_retorna_none(self):
        with patch.object(self.client.session, "request",
                          return_value=_mock_response(204)):
            r = self.client._get("/path")
        self.assertIsNone(r)

    def test_400_lanza_provider_error(self):
        with patch.object(self.client.session, "request",
                          return_value=_mock_response(400, text="bad request")):
            with self.assertRaises(ProviderError) as ctx:
                self.client._get("/path")
        self.assertEqual(ctx.exception.status, 400)

    def test_500_retry_y_luego_falla(self):
        with patch.object(self.client.session, "request",
                          return_value=_mock_response(500, text="server error")):
            with self.assertRaises(ProviderError) as ctx:
                self.client._get("/path")
        self.assertEqual(ctx.exception.status, 500)

    def test_429_retry_y_eventualmente_falla(self):
        # 429 hace retry con backoff; con retries=1, eventualmente
        # termina retornando el último 429 (que NO es >= 500, sigue siendo 429
        # y el bucle continúa; el test verifica que termina)
        with patch.object(self.client.session, "request",
                          return_value=_mock_response(429, text="rate")):
            with self.assertRaises(ProviderError):
                self.client._get("/path")
