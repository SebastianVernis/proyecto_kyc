"""Tests para _ai_filter_matches.

Mockea OllamaCloudClient (importado localmente) para que los tests sean
determinísticos sin red ni API key real.
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

PROY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROY / "backend"))

# Importar servir AQUÍ (antes que cualquier otro test lo mockee) y
# guardar la referencia al original.
import servir
_REAL_AI = servir._ai_filter_matches_real


def _fake_ollama_response(score_per_index, confianza="baja"):
    matches = [{"index": i, "score": s, "razon": f"test razon {i}"}
               for i, s in enumerate(score_per_index)]
    content = json.dumps({
        "matches": matches,
        "confianza_global": confianza,
        "resumen": "Test resumen IA"
    })
    return {
        "model": "glm-5.2",
        "message": content,
        "tokens_in": 100,
        "tokens_out": 50,
        "duration_ms": 200.0,
    }


class TestAIFilterHelper(unittest.TestCase):

    def test_matches_vacios_retorna_sin_datos(self):
        r = _REAL_AI({"paterno": "X"}, [], "cfe")
        self.assertTrue(r["skipped"])
        self.assertEqual(r["confianza_global"], "sin_datos")

    def test_sin_api_key_retorna_skipped(self):
        with patch("os.getenv", return_value=None):
            with patch("config.config", MagicMock(ollama_api_key=None,
                                                  ollama_model="x")):
                r = _REAL_AI(
                    {"paterno": "RUIZ", "materno": "VEGA", "nombres": "ZITA"},
                    [{"id": 1, "paterno": "RUIZ", "materno": "VEGA"}],
                    "issste",
                )
                self.assertTrue(r["skipped"])
                self.assertEqual(r["error"], "no_api_key")

    def test_con_matches_enriquece_con_score(self):
        fake = _fake_ollama_response([0.92, 0.05, 0.42], "alta")
        with patch("os.getenv", return_value="fake-key"), \
             patch("config.config", MagicMock(ollama_api_key="k",
                                              ollama_model="glm-5.2")), \
             patch("providers.ollama_cloud.OllamaCloudClient") as M:
            M.return_value.chat.return_value = fake
            subj = {"paterno": "RUIZ", "materno": "VEGA", "nombres": "ZITA",
                    "curp": "RUVZ750427MOCZGT00"}
            matches = [
                {"id": 1, "paterno": "RUIZ", "materno": "VEGA", "nombres": "ZITA"},
                {"id": 2, "paterno": "RUIZ", "materno": "LOPEZ", "nombres": "JUAN"},
                {"id": 3, "paterno": "RUIZ", "materno": "VEGA", "nombres": "MARIA"},
            ]
            r = _REAL_AI(subj, matches, "issste")
            self.assertFalse(r["skipped"])
            self.assertEqual(r["confianza_global"], "alta")
            self.assertEqual(len(r["matches_filtrados"]), 3)
            self.assertEqual(r["matches_filtrados"][0]["ai_score"], 0.92)
            self.assertTrue(r["matches_filtrados"][0]["ai_match"])
            self.assertFalse(r["matches_filtrados"][1]["ai_match"])

    def test_ia_devuelve_markdown_json_limpia(self):
        wrapped = ('```json\n'
                   '{"matches":[{"index":0,"score":0.8,"razon":"ok"}],'
                   '"confianza_global":"alta","resumen":"x"}\n```')
        fake = {
            "model": "glm-5.2", "message": wrapped,
            "tokens_in": 100, "tokens_out": 50, "duration_ms": 200.0,
        }
        with patch("os.getenv", return_value="fake-key"), \
             patch("config.config", MagicMock(ollama_api_key="k",
                                              ollama_model="glm-5.2")), \
             patch("providers.ollama_cloud.OllamaCloudClient") as M:
            M.return_value.chat.return_value = fake
            r = _REAL_AI({"paterno": "X"}, [{"x": 1}], "cfe")
            self.assertFalse(r["skipped"])
            self.assertEqual(r["confianza_global"], "alta")

    def test_ia_devuelve_json_invalido_retorna_skipped(self):
        fake = {"model": "x", "message": "no es json {{{",
                "tokens_in": 1, "tokens_out": 1, "duration_ms": 1}
        with patch("os.getenv", return_value="fake-key"), \
             patch("config.config", MagicMock(ollama_api_key="k",
                                              ollama_model="glm-5.2")), \
             patch("providers.ollama_cloud.OllamaCloudClient") as M:
            M.return_value.chat.return_value = fake
            r = _REAL_AI({"paterno": "X"}, [{"x": 1}], "cfe")
            self.assertTrue(r["skipped"])
            self.assertIn("raw_ai_response", r)


if __name__ == "__main__":
    unittest.main()