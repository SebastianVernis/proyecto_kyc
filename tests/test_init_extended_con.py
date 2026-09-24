"""Tests del init extendido: ATTACH de las 8 bases externas + 12 vistas api.*.

Cubre:
  - EXTENDED_DBS tiene 8 alias apuntando a /root/proyecto_kyc/bases/
  - _init_extended_con() crea exactamente las 12 vistas esperadas
  - Cada vista devuelve un número razonable de filas
  - _enriquecer_bases_externas() corre sin errores
"""
import os
import sys
import unittest
from pathlib import Path

PROY = Path(__file__).resolve().parent.parent
BACKEND = PROY / "backend"

# Paths a bases que las 13 vistas deben poder leer
EXPECTED_VIEWS = {
    "att_persona", "att_persona_full", "empleadores",
    "telcel_lineas", "telcel_lineas_full", "repuve_de_persona",
    "imss_asegurado", "imss_asegurado_full",
    "imss_salud", "imss_salud_full",
    "cfe_medidor", "fuentes_por_rfc",
    "issste_empleado",
}

# Conteo mínimo de filas (conservador; lo real es mucho mayor)
EXPECTED_MIN_ROWS = {
    "att_persona": 1_000_000,
    "att_persona_full": 1_000_000,
    "empleadores": 100_000,
    "telcel_lineas": 9_000_000,
    "telcel_lineas_full": 9_000_000,
    "repuve_de_persona": 1_000_000,
    "imss_asegurado": 50_000_000,
    "imss_asegurado_full": 50_000_000,
    "imss_salud": 20_000_000,
    "imss_salud_full": 20_000_000,
    "cfe_medidor": 60_000_000,
    "fuentes_por_rfc": 5_000_000,
    "issste_empleado": 2_700_000,
}


class TestExtendedDBSConfig(unittest.TestCase):
    """EXTENDED_DBS en servir.py apunta a /root/proyecto_kyc/bases/."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(BACKEND))
        import servir
        cls.servir = servir

    def test_8_alias_attacheables(self):
        eds = self.servir.EXTENDED_DBS
        self.assertGreaterEqual(len(eds), 9, f"EXTENDED_DBS tiene {len(eds)} alias, esperaba >=9")
        for alias in ("b_att", "b_emp", "b_repuve", "b_imss_a", "b_imss_s",
                       "b_telcel", "b_cfe", "b_fotos", "b_issste"):
            self.assertIn(alias, eds, f"falta alias {alias} en EXTENDED_DBS")

    def test_todos_los_paths_apuntan_a_proyecto_kyc(self):
        eds = self.servir.EXTENDED_DBS
        for alias, (path, _tbl) in eds.items():
            with self.subTest(alias=alias):
                self.assertTrue(str(path).startswith(str(PROY / "bases")),
                                f"{alias} path={path} no apunta a {PROY / 'bases'}")
                self.assertTrue(Path(path).exists(),
                                f"{alias} archivo {path} no existe")


class TestInitExtendedCon(unittest.TestCase):
    """_init_extended_con() crea las 13 vistas api.* esperadas."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(BACKEND))
        import servir
        cls.con = servir._init_extended_con()
        if cls.con is None:
            raise unittest.SkipTest("_init_extended_con() devolvió None")

    def test_con_no_es_none(self):
        self.assertIsNotNone(self.con)

    def test_12_vistas_creadas(self):
        found = {r[0] for r in self.con.execute(
            "SELECT view_name FROM duckdb_views() WHERE schema_name='api'"
        ).fetchall()}
        missing = EXPECTED_VIEWS - found
        self.assertEqual(missing, set(), f"faltan vistas: {missing}")
        self.assertGreaterEqual(len(found), 13, f"esperaba >=13 vistas, encontré {len(found)}: {found}")

    def test_vistas_tienen_filas(self):
        for view, min_rows in EXPECTED_MIN_ROWS.items():
            with self.subTest(view=view):
                n = self.con.execute(f'SELECT count(*) FROM api."{view}"').fetchone()[0]
                self.assertGreaterEqual(n, min_rows,
                    f"api.{view} tiene {n:,} filas, esperaba ≥{min_rows:,}")


class TestEnriquecerBasesExternas(unittest.TestCase):
    """_enriquecer_bases_externas() corre sin errores sobre un RFC real."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(BACKEND))
        import servir
        cls.servir = servir
        cls.con = servir._init_extended_con()
        if cls.con is None:
            raise unittest.SkipTest("_init_extended_con() devolvió None")
        r = cls.con.execute("SELECT rfc FROM api.att_persona LIMIT 1").fetchone()
        if r is None:
            raise unittest.SkipTest("no hay RFC en att_persona para test")
        cls.test_rfc = r[0]

    def test_enriquecer_por_rfc_corre(self):
        out = self.servir._enriquecer_bases_externas(curp=None, rfc=self.test_rfc, cap=2)
        for key in ("att", "empleadores", "telcel", "repuve",
                    "imss_asegurado", "imss_salud"):
            self.assertIn(key, out, f"falta {key} en output")
            self.assertIsInstance(out[key], dict)
            self.assertIn("error", out[key])
            self.assertIsNone(out[key]["error"],
                             f"error en {key}: {out[key]['error']}")
            self.assertIn("count", out[key])

    def test_att_matchea_el_rfc_consultado(self):
        out = self.servir._enriquecer_bases_externas(curp=None, rfc=self.test_rfc, cap=2)
        # att tiene la PK rfc_clean, debe matchear al menos 1 fila
        self.assertGreaterEqual(out["att"]["count"], 1,
            f"att no devolvió filas para RFC {self.test_rfc}")
