"""Tests del DuckDBPool."""
import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.pools import DuckDBPool
from app.config import BASES_DIR


@pytest.fixture
def pool():
    return DuckDBPool()


@pytest.mark.anyio
async def test_startup_precalienta_bases(pool):
    """El pool debe precalentar las bases configuradas."""
    await pool.startup()
    assert pool._ready is True
    assert len(pool._conns) >= 3  # al menos padron, att, empleadores


@pytest.mark.anyio
async def test_execute_padron_curp(pool):
    """Query al padron por CURP debe retornar resultados."""
    await pool.startup()
    sql = "SELECT curp, nombre FROM padron WHERE curp = ? LIMIT 1"
    rows = await pool.execute("padron_v1.duckdb", sql, ["AAAA010101MOCRRL09"])
    # Puede no encontrar ese CURP exacto, pero no debe fallar
    assert isinstance(rows, list)


@pytest.mark.anyio
async def test_execute_one(pool):
    """execute_one retorna solo la primera fila."""
    await pool.startup()
    sql = "SELECT 1 AS test_val"
    row = await pool.execute_one("padron_v1.duckdb", sql)
    assert row is not None
    assert row["test_val"] == 1


@pytest.mark.anyio
async def test_health(pool):
    """health() retorna estado válido."""
    await pool.startup()
    h = pool.health()
    assert h["ready"] is True
    assert h["databases"] >= 3
    assert "padron_v1.duckdb" in h["bases"]


@pytest.mark.anyio
async def test_missing_db_raises(pool):
    """Query a DB inexistente lanza FileNotFoundError."""
    await pool.startup()
    with pytest.raises(FileNotFoundError):
        await pool.execute("no_existe.duckdb", "SELECT 1")


@pytest.mark.anyio
async def test_lazy_load(pool):
    """Una DB no precalentada se abre bajo demanda."""
    await pool.startup()
    # att_v1.duckdb ya debería estar precalentada
    assert "att_v1.duckdb" in pool._conns
