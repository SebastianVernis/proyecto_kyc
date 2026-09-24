"""conftest.py — fixtures compartidos para pytest (proyecto KYC)."""
import os
import sys
from pathlib import Path

import pytest

# Ajustar sys.path para imports del proyecto
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


# ── Gateway fixtures ──────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def bases_dir():
    """Directorio de bases DuckDB del proyecto."""
    return ROOT / "bases"


@pytest.fixture(scope="session")
def gateway_url():
    """URL del db-gateway (default: localhost:8001)."""
    return os.getenv("GATEWAY_URL", "http://localhost:8001")


@pytest.fixture(scope="session")
def gateway_secret():
    """Shared secret del gateway (vacío en tests)."""
    return os.getenv("GATEWAY_SHARED_SECRET", "")


@pytest.fixture(scope="session")
def backend_url():
    """URL del backend (default: localhost:8765)."""
    return os.getenv("BACKEND_URL", "http://localhost:8765")
