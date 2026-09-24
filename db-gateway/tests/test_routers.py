"""Tests de los routers del db-gateway."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.mark.anyio
async def test_health_endpoint(client):
    """GET /health debe retornar 200."""
    r = await client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"


@pytest.mark.anyio
async def test_health_bases_endpoint(client):
    """GET /health/bases debe listar bases."""
    r = await client.get("/health/bases")
    assert r.status_code == 200
    data = r.json()
    assert data["ready"] is True
    assert data["databases"] >= 3


@pytest.mark.anyio
async def test_padron_by_curp_valid(client):
    """GET /q/padron/by-curp con CURP válida."""
    r = await client.get("/q/padron/by-curp/AAAA010101MOCRRL09")
    assert r.status_code == 200
    data = r.json()
    assert data["curp"] == "AAAA010101MOCRRL09"
    assert "count" in data
    assert "rows" in data


@pytest.mark.anyio
async def test_padron_by_curp_invalid_length(client):
    """GET /q/padron/by-curp con CURP de longitud incorrecta."""
    r = await client.get("/q/padron/by-curp/ABC123")
    assert r.status_code == 400


@pytest.mark.anyio
async def test_padron_search_requires_filter(client):
    """GET /q/padron/search sin filtros retorna 400."""
    r = await client.get("/q/padron/search")
    assert r.status_code == 400


@pytest.mark.anyio
async def test_padron_search_with_cp(client):
    """GET /q/padron/search con CP."""
    r = await client.get("/q/padron/search?cp=06600&limit=5")
    assert r.status_code == 200
    data = r.json()
    assert "count" in data
    assert "rows" in data


@pytest.mark.anyio
async def test_imss_by_curp(client):
    """GET /q/imss/by-curp."""
    r = await client.get("/q/imss/by-curp/AAAA010101MOCRRL09")
    assert r.status_code == 200
    data = r.json()
    assert "asegurados" in data
    assert "salud" in data


@pytest.mark.anyio
async def test_cfe_by_num_servicio(client):
    """GET /q/cfe/by-num-servicio."""
    r = await client.get("/q/cfe/by-num-servicio/137021201613")
    assert r.status_code == 200
    data = r.json()
    assert data["numero_servicio"] == "137021201613"


@pytest.mark.anyio
async def test_cfe_by_num_servicio_invalid(client):
    """GET /q/cfe/by-num-servicio con dato inválido."""
    r = await client.get("/q/cfe/by-num-servicio/123")
    assert r.status_code == 400


@pytest.mark.anyio
async def test_cfe_buscar_requires_filter(client):
    """GET /q/cfe/buscar sin filtros retorna 400."""
    r = await client.get("/q/cfe/buscar")
    assert r.status_code == 400


@pytest.mark.anyio
async def test_generic_by_rfc_unknown_base(client):
    """GET /q/no_existe/by-rfc/... retorna 404."""
    r = await client.get("/q/no_existe/by-rfc/AAAA0101018A1")
    assert r.status_code == 404


@pytest.mark.anyio
async def test_generic_att_by_rfc(client):
    """GET /q/att/by-rfc."""
    r = await client.get("/q/att/by-rfc/AAAA4702158R2?limit=5")
    assert r.status_code == 200
    data = r.json()
    assert data["base"] == "att"
    assert "count" in data


@pytest.mark.anyio
async def test_root_endpoint(client):
    """GET / retorna info del servicio."""
    r = await client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert data["service"] == "kyc-gateway"
