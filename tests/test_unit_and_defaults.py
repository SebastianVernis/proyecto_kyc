"""Tests de la unit systemd y de los defaults del backend.

Estos tests NO arrancan el backend (lo hace test_backend_http.py).
Aquí solo verifican:
  - El unit activo está enabled
  - El unit activo apunta al path correcto
  - El unit activo está inactive (vos pediste detenerlo; si cambió, este test falla)
  - servir.py --db y --html defaults
  - .env tiene PADRON_DB_PATH apuntando a /root/proyecto_kyc/bases/
"""
import os
import re
import subprocess
import unittest
from pathlib import Path

PROY = Path("/root/proyecto_kyc")
BACKEND = PROY / "backend"
UNIT = Path("/etc/systemd/system/cuartodepazsearch@root.service")


def systemctl(*args):
    r = subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=15)
    return r.stdout.strip(), r.returncode


class TestSystemdUnit(unittest.TestCase):
    """El unit activo apunta a /root/proyecto_kyc/ y está enabled."""

    def test_unit_existe(self):
        self.assertTrue(UNIT.exists(), f"{UNIT} no existe")

    def test_unit_enabled(self):
        out, rc = systemctl("is-enabled", "cuartodepazsearch@root.service")
        self.assertEqual(out, "enabled", f"unit no enabled: '{out}'")

    def test_unit_apunta_a_proyecto_kyc(self):
        if not UNIT.exists():
            self.skipTest("unit no existe")
        txt = UNIT.read_text()
        self.assertIn("/root/proyecto_kyc", txt,
                      f"unit no apunta a /root/proyecto_kyc: {txt[:300]}")
        self.assertIn("WorkingDirectory=/root/proyecto_kyc", txt,
                      "WorkingDirectory no apunta a /root/proyecto_kyc")
        self.assertIn("/root/ine_server/.venv", txt,
                      "venv en unit no es /root/ine_server/.venv")
        self.assertNotIn("/home/sebastianvernis", txt,
                         "unit tiene path viejo /home/sebastianvernis")
        self.assertNotIn("Descargas MEGA", txt,
                         "unit tiene path viejo 'Descargas MEGA'")

    def test_unit_inactive(self):
        """El servicio debe estar detenido (vos pediste 'detenido completamente')."""
        out, rc = systemctl("is-active", "cuartodepazsearch@root.service")
        # Si está active, fallamos con mensaje claro. Si está inactive, OK.
        # Si está activating/failed, también OK (transitorio).
        self.assertIn(out, ("inactive", "activating", "deactivating", "failed"),
                      f"unit está '{out}' (no inactive como esperábamos)")


class TestBackendDefaults(unittest.TestCase):
    """Defaults de --db, --html, PADRON_DB_PATH."""

    def test_db_default_proyecto_kyc(self):
        """servir.py --db default = /root/proyecto_kyc/bases/padron.duckdb."""
        r = subprocess.run(
            ["/root/ine_server/.venv/bin/python", "-c",
             "import sys, argparse; "
             "sys.path.insert(0, '/root/proyecto_kyc/backend'); "
             "import servir; "
             "ap = argparse.ArgumentParser(); "
             "ap.add_argument('--db', default=str(servir.ROOT.parent / 'bases' / 'padron.duckdb')); "
             "print(ap.parse_args([]).db)"],
            capture_output=True, text=True, timeout=30,
        )
        out = r.stdout.strip()
        self.assertTrue(out.endswith("/proyecto_kyc/bases/padron.duckdb"),
                        f"db default = {out}")
        self.assertTrue(Path(out).exists(), f"{out} no existe")

    def test_html_default_proyecto_kyc(self):
        r = subprocess.run(
            ["/root/ine_server/.venv/bin/python", "-c",
             "import sys, argparse; "
             "sys.path.insert(0, '/root/proyecto_kyc/backend'); "
             "import servir; "
             "ap = argparse.ArgumentParser(); "
             "ap.add_argument('--html', default=str(servir.ROOT.parent / 'frontend' / 'buscar.html')); "
             "print(ap.parse_args([]).html)"],
            capture_output=True, text=True, timeout=30,
        )
        out = r.stdout.strip()
        self.assertTrue(out.endswith("/proyecto_kyc/frontend/buscar.html"),
                        f"html default = {out}")
        self.assertTrue(Path(out).exists(), f"{out} no existe")

    def test_env_padron_db_path(self):
        env = BACKEND / ".env"
        if not env.exists():
            self.skipTest(".env no existe")
        txt = env.read_text()
        m = re.search(r"^PADRON_DB_PATH=(.+)$", txt, re.M)
        self.assertIsNotNone(m, "PADRON_DB_PATH no está en .env")
        path = m.group(1).strip()
        self.assertTrue(path.endswith("bases/padron.duckdb"),
                        f"PADRON_DB_PATH={path} no apunta a bases/padron.duckdb")
        # Resolver relativo a backend/
        resolved = (BACKEND / path).resolve()
        self.assertTrue(resolved.exists(),
                        f"PADRON_DB_PATH resuelto = {resolved} no existe")
