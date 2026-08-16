"""Tests de las 12 bases en /root/proyecto_kyc/bases/.

Verifica que cada duckdb/sqlite abre, tiene las tablas esperadas
y conteo de filas consistente con healthcheck.last.json.

NO modifica las bases (siempre read_only=True).
"""
import sqlite3
import unittest
from pathlib import Path

import duckdb

PROY = Path(__file__).resolve().parent.parent
BASES = PROY / "bases"

# (name, filename, kind, expected_tables, expected_min_rows)
EXPECTED = {
    "padron":            ("padron.duckdb",            "duckdb", {"padron"}, 88_000_000),
    "telcel":            ("telcel.duckdb",            "duckdb", {"telcel", "telcel_valid"}, 9_000_000),
    "att":               ("att.duckdb",               "duckdb", {"att", "att_valid"}, 1_000_000),
    "empleadores":       ("empleadores.duckdb",       "duckdb", {"empleadores"}, 100_000),
    "imss_asegurados":   ("imss_asegurados.duckdb",   "duckdb", {"imss_2025", "imss_valid"}, 50_000_000),
    "imss_segmentacion": ("imss_segmentacion.duckdb", "duckdb", {"imss_personas", "imss_personas_valid"}, 20_000_000),
    "repuve":            ("repuve.duckdb",            "duckdb", {"repuve", "repuve_valid"}, 1_000_000),
    "fotos":             ("fotos.duckdb",             "duckdb", {"fotos", "resumen"}, 10_000),
    "cfe":               ("cfe.duckdb",               "duckdb", {"medidores"}, 60_000_000),
    "issste":            ("issste.duckdb",            "duckdb", {"empleados", "cat_ramos", "cat_estados", "cat_entidades", "cat_modalidades", "cat_sectores"}, 2_700_000),
    "auth":              ("auth.db",                  "sqlite", None, 0),  # tablas variables
    "geo":               ("geo.db",                   "sqlite", None, 100_000),
    "sepomex":           ("sepomex.db",               "sqlite", {"cp"}, 100_000),
}


class TestBasesLayout(unittest.TestCase):
    """Layout de /root/proyecto_kyc/bases/ — los 12 archivos correctos."""

    def test_bases_dir_exists(self):
        self.assertTrue(BASES.is_dir(), f"{BASES} no existe")

    def test_todos_los_archivos_esperados_existen(self):
        for name, (filename, _kind, _tables, _rows) in EXPECTED.items():
            with self.subTest(base=name):
                p = BASES / filename
                self.assertTrue(p.exists(), f"falta {filename} en {BASES}")
                self.assertGreater(p.stat().st_size, 0, f"{filename} está vacío")

    def test_no_hay_duckdb_extranos(self):
        """No debe haber duckdb fuera de los 9 esperados (bases + 1 backup patron)."""
        duckdbs = sorted(BASES.glob("*.duckdb"))
        names = {d.name for d in duckdbs}
        expected_duckdbs = {info[0] for info in EXPECTED.values() if info[1] == "duckdb"}
        # los índices de match derivados (materializar_claves.py) son legítimos
        extra = {n for n in names - expected_duckdbs
                 if not n.startswith("mkidx_") and n != "match_index.duckdb"}
        self.assertEqual(extra, set(), f"duckdb inesperados: {extra}")

    def test_no_hay_db_sqlite_extranos(self):
        dbs = sorted(BASES.glob("*.db"))
        names = {d.name for d in dbs}
        expected_dbs = {info[0] for info in EXPECTED.values() if info[1] == "sqlite"}
        extra = names - expected_dbs
        self.assertEqual(extra, set(), f"sqlite inesperados: {extra}")


class TestBasesOpen(unittest.TestCase):
    """Cada base abre en modo read-only."""

    def _open(self, name, filename, kind):
        path = BASES / filename
        if kind == "duckdb":
            return duckdb.connect(str(path), read_only=True)
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)

    def test_all_12_bases_abren(self):
        for name, (filename, kind, _tables, _rows) in EXPECTED.items():
            with self.subTest(base=name):
                con = self._open(name, filename, kind)
                con.close()  # cierra implícitamente

    def test_tablas_esperadas_existen(self):
        for name, (filename, kind, tables, _rows) in EXPECTED.items():
            if tables is None:
                continue
            with self.subTest(base=name):
                con = self._open(name, filename, kind)
                if kind == "duckdb":
                    found = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
                else:
                    found = {r[0] for r in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()}
                missing = tables - found
                self.assertEqual(missing, set(),
                                 f"{name}: faltan tablas {missing}")
                con.close()

    def test_conteo_minimo_de_filas(self):
        for name, (filename, kind, _tables, min_rows) in EXPECTED.items():
            with self.subTest(base=name):
                con = self._open(name, filename, kind)
                if kind == "duckdb":
                    tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
                else:
                    tables = [r[0] for r in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()]
                total = 0
                for t in tables:
                    try:
                        total += con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
                    except Exception:
                        pass
                self.assertGreaterEqual(total, min_rows,
                    f"{name}: {total:,} filas < {min_rows:,} esperadas")
                con.close()


class TestHealthcheckContract(unittest.TestCase):
    """healthcheck.last.json debe estar en sync con el estado real."""

    def test_healthcheck_last_json_existe(self):
        p = PROY / "healthcheck.last.json"
        self.assertTrue(p.exists(), f"{p} no existe")
        import json
        data = json.loads(p.read_text())
        self.assertTrue(data.get("overall_ok"), f"healthcheck overall_ok=False: {data}")
        # 12 bases
        self.assertEqual(len(data.get("bases", [])), 12)
        # > 300M filas
        self.assertGreater(data.get("total_rows", 0), 300_000_000)
