"""Tests de cobertura de código.

Usa coverage.py para medir qué % del backend se ejecuta al correr
la suite completa. Genera reporte tabular y HTML en /tmp/.

Por qué NO asserteamos contra un umbral global:
  - servir.py es enorme (3923 statements, 8431 líneas) con muchos
    endpoints. Los tests actuales cubren ~6% (lógica core + endpoints
    principales que se llaman desde tests).
  - Assertear "cobertura > 50%" haría fallar este test cada vez que
    se agrega un endpoint nuevo sin tests específicos.

Por qué SÍ asserteamos por archivo:
  - providers/base.py: 80% (es el contrato base, debe estar cubierto)
  - providers/__init__.py: 100% (es trivial)

El reporte HTML se genera en /tmp/hermes-coverage-html/ para inspección
visual.

Para correr manualmente:
    /root/ine_server/.venv/bin/python /root/proyecto_kyc/tests/_coverage_runner.py
    cd /root/proyecto_kyc/backend && /root/ine_server/.venv/bin/coverage report --data-file=/tmp/.coverage-runner
"""
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

PROY = Path("/root/proyecto_kyc")
BACKEND = PROY / "backend"
VENV = "/root/ine_server/.venv/bin/python"
RUNNER = PROY / "tests" / "_coverage_runner.py"
COVERAGE_DATA = Path("/tmp/.coverage-runner")
HTML_DIR = Path("/tmp/hermes-coverage-html")

# Umbrales por archivo (fracción 0-100). Subilos con confianza.
THRESHOLDS = {
    "providers/base.py":      80.0,   # clase base
    "providers/__init__.py": 100.0,   # trivial
}


def _have_data():
    return COVERAGE_DATA.exists() and COVERAGE_DATA.stat().st_size > 100


def _ensure_data():
    """Si .coverage-runner no existe, lo genera corriendo el runner."""
    if _have_data():
        return
    if COVERAGE_DATA.exists():
        COVERAGE_DATA.unlink()
    r = subprocess.run(
        [VENV, str(RUNNER)],
        capture_output=True, text=True, timeout=120,
        cwd=str(BACKEND),
    )
    if not _have_data():
        raise RuntimeError(
            f"coverage runner falló. exit={r.returncode} "
            f"stdout[-200:]={r.stdout[-200:]} stderr[-200:]={r.stderr[-200:]}"
        )


class TestCoverage(unittest.TestCase):
    """Mide cobertura de código y verifica umbrales por archivo."""

    # Forzar orden: a → b → c → d → e
    def test_a01_coverage_disponible(self):
        """Verifica que coverage.py está instalado."""
        r = subprocess.run(
            [VENV, "-c", "import coverage; print(coverage.__version__)"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(r.returncode, 0, f"coverage.py no disponible: {r.stderr}")
        self.assertTrue(r.stdout.strip().startswith("7."),
                        f"version inesperada: {r.stdout}")

    def test_b01_suite_corre_bajo_coverage(self):
        """Corre la suite bajo coverage y guarda .coverage-runner en /tmp.

        Excluye test_coverage.py para evitar recursión."""
        if COVERAGE_DATA.exists():
            COVERAGE_DATA.unlink()
        r = subprocess.run(
            [VENV, str(RUNNER)],
            capture_output=True, text=True, timeout=120,
            cwd=str(BACKEND),
        )
        self.assertTrue(_have_data(),
                        f"coverage runner falló. exit={r.returncode} "
                        f"stdout[-300:]={r.stdout[-300:]} "
                        f"stderr[-300:]={r.stderr[-300:]}")
        # La suite se ejecuta en _coverage_runner.py con verbosity=0
        # (silenciosa); solo importa que pase exit=0
        self.assertEqual(r.returncode, 0,
                         f"suite bajo coverage falló: stderr={r.stderr[-300:]}")

    def test_c01_reporte_por_archivo(self):
        """Genera reporte tabular y verifica umbrales por archivo."""
        _ensure_data()
        r = subprocess.run(
            [VENV, "-m", "coverage", "report", "-m",
             "--data-file", str(COVERAGE_DATA)],
            capture_output=True, text=True, timeout=30,
            cwd=str(BACKEND),
        )
        self.assertEqual(r.returncode, 0, f"coverage report falló: {r.stderr}")
        report = r.stdout
        self.assertIn("servir.py", report, "reporte no incluye servir.py")
        self.assertIn("TOTAL", report, "reporte no incluye TOTAL")

        failures = []
        for filename, min_pct in THRESHOLDS.items():
            found = False
            for line in report.splitlines():
                if line.strip().startswith(filename):
                    found = True
                    parts = line.split()
                    pct_str = parts[-1].rstrip("%")
                    try:
                        pct = float(pct_str)
                    except ValueError:
                        continue
                    if pct < min_pct:
                        failures.append((filename, pct, min_pct))
                    break
            if not found:
                failures.append((filename, -1, min_pct))
        if failures:
            msg = "\n".join(f"  {f}: {p:.1f}% < {m:.1f}%"
                            for f, p, m in failures)
            self.fail(f"Cobertura por debajo del umbral:\n{msg}")

    def test_d01_reporte_html_se_genera(self):
        """Genera reporte HTML navegable en /tmp/hermes-coverage-html/."""
        _ensure_data()
        if HTML_DIR.exists():
            shutil.rmtree(HTML_DIR)
        r = subprocess.run(
            [VENV, "-m", "coverage", "html", "-d", str(HTML_DIR),
             "--data-file", str(COVERAGE_DATA)],
            capture_output=True, text=True, timeout=30,
            cwd=str(BACKEND),
        )
        self.assertEqual(r.returncode, 0, f"coverage html falló: {r.stderr}")
        self.assertTrue(HTML_DIR.exists(), f"HTML_DIR {HTML_DIR} no se creó")
        self.assertTrue((HTML_DIR / "index.html").exists(),
                        "index.html no existe")
        n = len(list(HTML_DIR.glob("*.html")))
        self.assertGreater(n, 5, f"solo {n} archivos HTML generados")

    def test_e01_resumen_legible(self):
        """Imprime resumen para inspección en logs de CI."""
        _ensure_data()
        r = subprocess.run(
            [VENV, "-m", "coverage", "report",
             "--data-file", str(COVERAGE_DATA)],
            capture_output=True, text=True, timeout=30,
            cwd=str(BACKEND),
        )
        lines = r.stdout.splitlines()
        summary = [l for l in lines if l.startswith("TOTAL") or
                   l.startswith("servir.py") or
                   l.startswith("providers/")]
        for l in summary:
            print(f"    {l}")
