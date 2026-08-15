#!/usr/bin/env python3
"""Runner que ejecuta toda la suite y devuelve exit code.

Uso:
    /root/ine_server/.venv/bin/python tests/run_all.py
    /root/ine_server/.venv/bin/python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

# Raíz del proyecto en sys.path
PROY = Path(__file__).parent.parent
sys.path.insert(0, str(PROY))

# Descubrir tests en este directorio
loader = unittest.TestLoader()
suite = loader.discover(start_dir=str(Path(__file__).parent), pattern="test_*.py")

# Verbose por defecto
runner = unittest.TextTestRunner(verbosity=2, buffer=True)
result = runner.run(suite)

# Exit code: 0 si todo OK, 1 si algo falló
sys.exit(0 if result.wasSuccessful() else 1)
