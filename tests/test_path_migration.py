"""Tests de los paths hardcodeados en el código del backend.

Verifica que después de la migración, el código activo (no comentarios)
NO contiene paths a:
  - /home/sebastianvernis  (viejo user)
  - /Descargas MEGA/.../src  (viejo operativo)
  - /root/ine_server/nuevas_bases  (viejo staging)
  - /root/ine_server/Descargas MEGA  (viejo staging, borrado)
"""
import os
import sys
import re
import unittest
from pathlib import Path

PROY = Path(__file__).resolve().parent.parent
BACKEND = PROY / "backend"

# Archivos a inspeccionar (excluir backups, snapshots, _backups, caches)
SCAN_PATTERNS = [
    BACKEND / "servir.py",
    PROY / "init_extended_sources.sql" if (PROY / "init_extended_sources.sql").exists() else None,
]
# Scan recursivo
for f in BACKEND.rglob("*.py"):
    SCAN_PATTERNS.append(f)
for f in (BACKEND / "providers").rglob("*.py") if (BACKEND / "providers").exists() else []:
    SCAN_PATTERNS.append(f)
for f in (PROY / "scripts").rglob("*.py") if (PROY / "scripts").exists() else []:
    SCAN_PATHS = [p for p in {f for f in SCAN_PATTERNS} if p is not None]

FORBIDDEN_PATTERNS = [
    "/home/sebastianvernis",
    "/Descargas MEGA",
    "/root/ine_server/nuevas_bases",
    "/root/ine_server/Descargas MEGA",
]


def strip_comments_and_strings(text, suffix):
    """Quita comentarios y (parcialmente) strings para reducir falsos positivos.

    Estrategia simple: reemplazar comentarios de línea (# ...) por vacío,
    y para strings dejamos el contenido (los paths DENTRO de strings son
    los que importan).
    """
    if suffix == ".py":
        # Quita comentarios
        text = re.sub(r"#[^\n]*", "", text)
        # Quita docstrings triple-comilla
        text = re.sub(r'"""[\s\S]*?"""', "", text)
        text = re.sub(r"'''[\s\S]*?'''", "", text)
    elif suffix in (".sh", ".template"):
        text = re.sub(r"#[^\n]*", "", text)
    # SQL: -- comentarios
    elif suffix == ".sql":
        text = re.sub(r"--[^\n]*", "", text)
    return text


class TestPathMigration(unittest.TestCase):
    """No quedan paths viejos en código activo (sin contar strings de error/docs)."""

    def test_no_home_sebastianvernis_en_codigo_activo(self):
        offenders = []
        for p in SCAN_PATHS:
            try:
                text = p.read_text(errors="ignore")
            except Exception:
                continue
            cleaned = strip_comments_and_strings(text, p.suffix)
            for pat in FORBIDDEN_PATTERNS:
                if pat in cleaned:
                    offenders.append((p, pat))
        if offenders:
            msg = "\n".join(f"  {p}: contiene '{pat}'" for p, pat in offenders)
            self.fail(f"Paths viejos en código activo:\n{msg}")

    def test_no_hay_nuevas_bases_staging_en_codigo(self):
        """Específicamente: no debe quedar /root/ine_server/nuevas_bases o
        /root/ine_server/Descargas MEGA en ningún archivo activo."""
        offenders = []
        for p in SCAN_PATHS:
            try:
                text = p.read_text(errors="ignore")
            except Exception:
                continue
            cleaned = strip_comments_and_strings(text, p.suffix)
            for pat in ("/root/ine_server/nuevas_bases",
                        "/root/ine_server/Descargas MEGA"):
                if pat in cleaned:
                    offenders.append((p, pat))
        if offenders:
            msg = "\n".join(f"  {p}: contiene '{pat}'" for p, pat in offenders)
            self.fail(f"Paths staging viejos en código:\n{msg}")

    def test_servir_default_db_apunta_a_proyecto_kyc(self):
        """El --db default de servir.py debe ser /root/proyecto_kyc/bases/padron_v1.duckdb."""
        import subprocess
        r = subprocess.run(
            [sys.executable, "-c",
             f"import sys; sys.path.insert(0, {str(BACKEND)!r}); "
             "import servir; print(servir.ROOT.parent / 'bases' / 'padron_v1.duckdb')"],
            capture_output=True, text=True, timeout=30,
        )
        out = r.stdout.strip()
        self.assertTrue(out.endswith("/proyecto_kyc/bases/padron_v1.duckdb"),
                        f"servir ROOT.parent/bases/padron_v1.duckdb = {out}")
        self.assertTrue(Path(out).exists(), f"{out} no existe")


class TestServiceTemplate(unittest.TestCase):
    """service.template apunta a /root/proyecto_kyc/ y venv correcto."""

    def test_template_existe(self):
        tpl = BACKEND / "cuartodepazsearch.service.template"
        self.assertTrue(tpl.exists(), f"{tpl} no existe")

    def test_template_paths_correctos(self):
        tpl = (BACKEND / "cuartodepazsearch.service.template").read_text()
        self.assertIn("/root/proyecto_kyc", tpl, "falta path /root/proyecto_kyc")
        self.assertIn("/root/ine_server/.venv", tpl, "falta path venv")
        self.assertNotIn("/home/sebastianvernis", tpl, "queda /home/sebastianvernis")
        self.assertNotIn("Descargas MEGA", tpl, "queda 'Descargas MEGA'")
        self.assertIn("WorkingDirectory=/root/proyecto_kyc", tpl,
                      "WorkingDirectory no apunta a /root/proyecto_kyc")

    def test_install_service_parametrizable(self):
        sh = (BACKEND / "install-service.sh").read_text()
        # USER default root
        self.assertIn('USER="${1:-root}"', sh,
                      "install-service.sh no tiene USER parametrizable")
        # No debe tener sebastianvernis hardcoded en código activo
        cleaned = strip_comments_and_strings(sh, ".sh")
        self.assertNotIn("sebastianvernis", cleaned,
                         "install-service.sh tiene 'sebastianvernis' en código activo")

    def test_install_service_ejecutable(self):
        sh = BACKEND / "install-service.sh"
        self.assertTrue(os.access(sh, os.X_OK),
                        f"{sh} no es ejecutable (chmod +x)")
