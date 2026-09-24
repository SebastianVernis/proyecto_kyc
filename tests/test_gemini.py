import sys
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from providers.gemini import GeminiClient  # noqa: E402


class TestGeminiClient(unittest.TestCase):
    def test_generate_report_narrative_maps_response(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Resumen factual"}]}}],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2},
        }
        with patch("providers.gemini.requests.post", return_value=response) as post:
            result = GeminiClient("test-key", model="gemini-test").generate_report_narrative(
                {"nombre": "JUAN"}, style="concise"
            )
        self.assertEqual(result, "Resumen factual")
        self.assertIn(":generateContent", post.call_args.args[0])
        self.assertEqual(post.call_args.kwargs["params"], {"key": "test-key"})

    def test_http_error_is_provider_error(self):
        response = Mock(status_code=429, text="quota")
        with patch("providers.gemini.requests.post", return_value=response):
            with self.assertRaises(Exception) as ctx:
                GeminiClient("test-key").chat([{"role": "user", "content": "hola"}])
        self.assertIn("HTTP 429", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
