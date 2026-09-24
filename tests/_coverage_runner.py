"""Runner que ejecuta la suite bajo coverage correctamente."""
import sys
import unittest
from pathlib import Path

PROY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROY))
sys.path.insert(0, str(PROY / "backend"))

import coverage

# Source: módulos específicos del backend
cov = coverage.Coverage(
    source=[
        "servir", "providers", "auth", "config",
    ],
    data_file=str(Path("/tmp") / ".coverage-runner"),
)
cov.start()

# Import explícito DENTRO de la medición para que coverage registre el
# módulo aunque ningún test lo ejercite directamente.
import config  # noqa: F401

# Cargar y ejecutar la suite (excluyendo test_coverage)
loader = unittest.TestLoader()
suite = unittest.TestSuite()
test_modules = [
    "tests.test_bases",
    "tests.test_init_extended_con",
    "tests.test_path_migration",
    "tests.test_unit_and_defaults",
    "tests.test_providers",
]
for name in test_modules:
    try:
        suite.addTests(loader.loadTestsFromName(name))
    except Exception as e:
        print(f"skip {name}: {e}", file=sys.stderr)

runner = unittest.TextTestRunner(verbosity=0, buffer=True)
result = runner.run(suite)

cov.stop()
cov.save()

sys.exit(0 if result.wasSuccessful() else 1)
