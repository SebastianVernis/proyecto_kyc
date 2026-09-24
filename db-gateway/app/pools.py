"""DuckDBPool — pool de conexiones con prewarm selectivo y lazy-load.

Cada archivo DuckDB se abre una vez (read_only) y se cachea.
Un asyncio.Lock por archivo serializa las queries (duckdb read_only
no es multi-thread-safe con queries complejas).
"""
import asyncio
import os
import time
from pathlib import Path
from typing import Optional

import duckdb

from .config import BASES_DIR, PREWARM_DBS


class DuckDBPool:
    """Pool de conexiones DuckDB con prewarm selectivo."""

    def __init__(self):
        self._conns: dict[str, duckdb.DuckDBPyConnection] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._meta: dict[str, dict] = {}
        self._ready = False

    async def startup(self):
        """Precalentar las bases configuradas."""
        t0 = time.time()
        for db_file in PREWARM_DBS:
            path = BASES_DIR / db_file
            if path.exists():
                self._open(db_file, path)
                print(f"  ✓ prewarm {db_file} ({self._meta[db_file]['size_mb']:.0f} MB)")
            else:
                print(f"  ⚠ {db_file} no encontrado en {BASES_DIR}")
        elapsed = time.time() - t0
        self._ready = True
        print(f"  Pool listo: {len(self._conns)} bases precalentadas en {elapsed:.1f}s")

    def _open(self, db_file: str, path: Path):
        """Abrir una DB y cachear la conexión."""
        if db_file in self._conns:
            return
        conn = duckdb.connect(str(path), read_only=True)
        self._conns[db_file] = conn
        self._locks[db_file] = asyncio.Lock()
        stat = os.stat(path)
        self._meta[db_file] = {
            "path": str(path),
            "size_bytes": stat.st_size,
            "size_mb": stat.st_size / (1024 * 1024),
            "mtime": stat.st_mtime,
        }

    def _ensure(self, db_file: str) -> duckdb.DuckDBPyConnection:
        """Obtener conexión (lazy-load si no está precalentada)."""
        if db_file not in self._conns:
            path = BASES_DIR / db_file
            if not path.exists():
                raise FileNotFoundError(f"DB no encontrada: {path}")
            self._open(db_file, path)
        return self._conns[db_file]

    async def execute(
        self, db_file: str, sql: str, params: list | None = None
    ) -> list[dict]:
        """Ejecutar query y retornar filas como lista de dicts."""
        lock = self._locks.setdefault(db_file, asyncio.Lock())
        async with lock:
            conn = self._ensure(db_file)
            try:
                if params:
                    result = conn.execute(sql, params)
                else:
                    result = conn.execute(sql)
                cols = [desc[0] for desc in result.description] if result.description else []
                rows = result.fetchall()
                return [dict(zip(cols, row)) for row in rows]
            except Exception as e:
                raise RuntimeError(f"Query error en {db_file}: {e}") from e

    async def execute_one(
        self, db_file: str, sql: str, params: list | None = None
    ) -> Optional[dict]:
        """Ejecutar query y retornar solo la primera fila."""
        rows = await self.execute(db_file, sql, params)
        return rows[0] if rows else None

    def health(self) -> dict:
        """Estado del pool."""
        return {
            "ready": self._ready,
            "databases": len(self._conns),
            "prewarm": PREWARM_DBS,
            "bases": self._meta,
        }

    async def shutdown(self):
        """Cerrar todas las conexiones."""
        for conn in self._conns.values():
            try:
                conn.close()
            except Exception:
                pass
        self._conns.clear()
        self._locks.clear()
        self._meta.clear()
        self._ready = False


# Singleton global
pool = DuckDBPool()
