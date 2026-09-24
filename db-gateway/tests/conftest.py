"""Fixtures compartidos para tests del db-gateway."""
import os
import sys
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

# Ajustar path para importar la app
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.main import app
from app import pools


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def bases_dir():
    """Directorio de bases DuckDB (del host o mock)."""
    d = os.getenv("BASES_DIR", "/home/sebastianvernis/proyectos/kyc/proyecto_kyc/bases")
    return Path(d)


@pytest.fixture(scope="session")
async def client(bases_dir):
    """Client HTTP asíncrono contra la app FastAPI.
    Precalienta el pool una sola vez para toda la sesión.
    """
    os.environ["BASES_DIR"] = str(bases_dir)
    os.environ["GATEWAY_SHARED_SECRET"] = ""  # sin auth en tests

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
