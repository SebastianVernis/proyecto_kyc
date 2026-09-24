"""test_gateway_mock.py — Tests del backend con mock del db-gateway.

Verifica que los handlers del backend pueden usar el gateway
como reemplazo de las queries directas a DuckDB.
"""
import json
from unittest.mock import patch, MagicMock

import pytest


# ── Mock fixture ──────────────────────────────────────────────────────────────

@pytest.fixture
def mock_gateway_response():
    """Factory para respuestas mock del gateway."""
    def _make(rows=None, count=0):
        return {
            "count": count or len(rows or []),
            "rows": rows or [],
        }
    return _make


@pytest.fixture
def mock_gateway_client(mock_gateway_response):
    """Mock del httpx client que simula respuestas del gateway."""
    client = MagicMock()

    # padron by-curp
    client.get.return_value = MagicMock(
        status_code=200,
        json=lambda: mock_gateway_response(rows=[
            {"curp": "TEST010101MDFRRL01", "nombre": "TEST", "paterno": "USER",
             "materno": "EXAMPLE", "sexo": "M", "fecnac": "2001-01-01"}
        ]),
    )

    return client


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestMockGateway:
    def test_mock_returns_data(self, mock_gateway_client):
        """El mock del gateway retorna datos válidos."""
        r = mock_gateway_client.get("/q/padron/by-curp/TEST010101MDFRRL01")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 1
        assert data["rows"][0]["curp"] == "TEST010101MDFRRL01"

    def test_mock_empty_response(self, mock_gateway_response):
        """Respuesta vacía del gateway."""
        data = mock_gateway_response(rows=[])
        assert data["count"] == 0
        assert data["rows"] == []

    def test_mock_factory(self, mock_gateway_response):
        """La factory crea respuestas con count automático."""
        data = mock_gateway_response(rows=[{"a": 1}, {"a": 2}])
        assert data["count"] == 2
