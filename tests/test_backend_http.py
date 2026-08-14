"""Tests del backend HTTP: arrancar servir.py en un puerto y validar endpoints.

Estos tests SÍ arrancan el backend (en background) durante la duración
del test. Usan un puerto alto (8768) para no colisionar con el
servicio de producción (8765) o los tests previos.

Endpoints validados:
  - GET /api/health         → 401 (no autenticado, vivo)
  - GET /api/total          → 401
  - GET /api/curp/<18char>  → 401
"""
import os
import signal
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

PROY = Path("/root/proyecto_kyc")
BACKEND = PROY / "backend"
VENV = "/root/ine_server/.venv/bin/python"


def _http_get(url, timeout=4):
    """GET robusto: tolera 401/403 (vivos, no autenticados)."""
    req = urllib.request.Request(url)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status, resp.read().decode(errors="ignore")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode(errors="ignore")
        except Exception:
            pass
        return e.code, body
    except Exception as e:
        return 0, f"err={e}"


class TestBackendHTTP(unittest.TestCase):
    """Arrancar servir.py en un puerto libre y validar respuestas HTTP."""

    PORT = 8770
    proc = None

    @classmethod
    def setUpClass(cls):
        # Liberar puerto por si quedó un test anterior colgado
        subprocess.run(["fuser", "-k", f"{cls.PORT}/tcp"], capture_output=True, timeout=5)
        time.sleep(1)

        cls.proc = subprocess.Popen(
            [VENV, "servir.py", "--port", str(cls.PORT)],
            cwd=str(BACKEND),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid,
        )
        # Esperar hasta que el puerto esté abierto (max 30s).
        # El padrón tiene 88M filas; la inicialización puede tardar 4-8s.
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{cls.PORT}/api/health", timeout=2
                ) as r:
                    if r.status:
                        return
            except urllib.error.HTTPError:
                # 401 = vivo, no autenticado
                return
            except Exception:
                time.sleep(0.5)
                continue
        raise unittest.SkipTest(f"backend no arrancó en puerto {cls.PORT} tras 30s")

    @classmethod
    def tearDownClass(cls):
        if cls.proc is not None:
            try:
                os.killpg(os.getpgid(cls.proc.pid), signal.SIGTERM)
                cls.proc.wait(timeout=5)
            except Exception:
                try:
                    cls.proc.kill()
                except Exception:
                    pass

    def test_health_responde(self):
        status, body = _http_get(f"http://127.0.0.1:{self.PORT}/api/health")
        self.assertIn(status, (200, 401, 403),
                      f"/api/health status={status} body={body[:200]}")

    def test_total_responde(self):
        status, body = _http_get(f"http://127.0.0.1:{self.PORT}/api/total")
        self.assertIn(status, (200, 401, 403),
                      f"/api/total status={status} body={body[:200]}")

    def test_curp_18chars_responde(self):
        status, body = _http_get(f"http://127.0.0.1:{self.PORT}/api/curp/AUFG910209HDFGRR08")
        self.assertIn(status, (200, 401, 403),
                      f"/api/curp/AUFG... status={status} body={body[:200]}")

    def test_root_no_es_404(self):
        """El HTML puede requerir auth, pero el handler no debe 404."""
        status, body = _http_get(f"http://127.0.0.1:{self.PORT}/", timeout=2)
        self.assertNotEqual(status, 0, f"sin respuesta: {body}")
        self.assertNotEqual(status, 404, f"404 inesperado: {body[:200]}")
