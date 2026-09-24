"""test_gateway_smoke.py — Smoke tests contra el db-gateway corriendo.

Ejecutar con el gateway levantado (docker compose up gateway).
"""
import httpx
import pytest

GATEWAY = "http://localhost:8001"
SECRET = "changeme"


@pytest.fixture(scope="module")
def client():
    with httpx.Client(
        base_url=GATEWAY,
        timeout=10,
        headers={"Authorization": f"Bearer {SECRET}"},
    ) as c:
        yield c


# ── Health ────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_200(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_health_bases_list(self, client):
        r = client.get("/health/bases")
        assert r.status_code == 200
        data = r.json()
        assert data["ready"] is True
        assert data["databases"] >= 8
        assert "padron_v1.duckdb" in data["bases"]


# ── Padrón ────────────────────────────────────────────────────────────────────

class TestPadron:
    def test_by_curp_returns_json(self, client):
        r = client.get("/q/padron/by-curp/AAAA010101MOCRRL09")
        assert r.status_code == 200
        data = r.json()
        assert data["curp"] == "AAAA010101MOCRRL09"
        assert isinstance(data["rows"], list)

    def test_by_curp_invalid_length(self, client):
        r = client.get("/q/padron/by-curp/ABC")
        assert r.status_code == 400

    def test_search_requires_filter(self, client):
        r = client.get("/q/padron/search")
        assert r.status_code == 400

    def test_search_with_cp(self, client):
        r = client.get("/q/padron/search?cp=06600&limit=3")
        assert r.status_code == 200
        data = r.json()
        assert "count" in data
        assert "rows" in data


# ── IMSS ──────────────────────────────────────────────────────────────────────

class TestIMSS:
    def test_by_curp_returns_sections(self, client):
        r = client.get("/q/imss/by-curp/LOVJ780901HDFPZR05")
        assert r.status_code == 200
        data = r.json()
        assert "asegurados" in data
        assert "salud" in data
        assert isinstance(data["asegurados"]["rows"], list)

    def test_by_curp_invalid(self, client):
        r = client.get("/q/imss/by-curp/SHORT")
        assert r.status_code == 400


# ── CFE ───────────────────────────────────────────────────────────────────────

class TestCFE:
    def test_by_num_servicio(self, client):
        r = client.get("/q/cfe/by-num-servicio/137021201613")
        assert r.status_code == 200
        data = r.json()
        assert data["numero_servicio"] == "137021201613"

    def test_by_num_servicio_invalid(self, client):
        r = client.get("/q/cfe/by-num-servicio/123")
        assert r.status_code == 400

    def test_buscar_requires_filter(self, client):
        r = client.get("/q/cfe/buscar")
        assert r.status_code == 400

    def test_buscar_with_nombre(self, client):
        r = client.get("/q/cfe/buscar?nombre=GARCIA&limit=3")
        assert r.status_code == 200
        data = r.json()
        assert "count" in data


# ── Genérico ──────────────────────────────────────────────────────────────────

class TestGeneric:
    def test_unknown_base(self, client):
        r = client.get("/q/no_existe/by-rfc/ABC123")
        assert r.status_code == 404

    def test_att_by_rfc(self, client):
        r = client.get("/q/att/by-rfc/AAAA4702158R2?limit=3")
        assert r.status_code == 200
        data = r.json()
        assert data["base"] == "att"


# ── Root ──────────────────────────────────────────────────────────────────────

class TestRoot:
    def test_root(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert r.json()["service"] == "kyc-gateway"
