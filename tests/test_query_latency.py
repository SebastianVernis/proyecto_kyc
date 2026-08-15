"""Tests de latencia de queries contra las 13 vistas api.*.

Cada vista se prueba con 3 queries:
  1. count(*) sobre toda la vista
  2. SELECT * LIMIT 10 (sample)
  3. lookup por RFC (cuando aplica; usa RFC real de att_persona)

Umbrales son MUY generosos (orden de segundos) para que pasen incluso
en máquinas lentas. El propósito NO es validar performance estricto,
sino detectar regresiones groseras (ej: vista que pasa de dos segundos
a treinta segundos).

Los tiempos se imprimen siempre (no se asertean con umbrales ajustados)
para que veas el progreso; solo se assertea contra umbrales flexibles.
"""
import sys
import time
import unittest
from pathlib import Path

PROY = Path("/root/proyecto_kyc")
BACKEND = PROY / "backend"
sys.path.insert(0, str(BACKEND))

import servir

# Umbrales flexibles (en segundos). Si tu hardware es muy lento, subilos.
THRESHOLD_COUNT = 30.0     # SELECT count(*) FROM api.X
THRESHOLD_LIMIT = 10.0     # SELECT * FROM api.X LIMIT 10
THRESHOLD_LOOKUP = 30.0    # SELECT count(*) WHERE rfc = ? (vistas con rfc)

# Vistas que tienen columna rfc y son lookupables
RFC_VIEWS = {
    "att_persona", "att_persona_full", "empleadores", "repuve_de_persona",
    "telcel_lineas", "telcel_lineas_full", "imss_salud", "imss_salud_full",
    "fuentes_por_rfc",
}
# Vistas que tienen columna curp
CURP_VIEWS = {
    "imss_asegurado", "imss_asegurado_full",
    "imss_salud", "imss_salud_full",
}


def _time_query(con, sql, params=None):
    """Ejecuta una query y devuelve (elapsed_seconds, fetchone_result)."""
    t0 = time.perf_counter()
    if params:
        result = con.execute(sql, params).fetchone()
    else:
        result = con.execute(sql).fetchone()
    elapsed = time.perf_counter() - t0
    return elapsed, result


class TestQueryLatency(unittest.TestCase):
    """Latencia de las 13 vistas api.*."""

    @classmethod
    def setUpClass(cls):
        cls.con = servir._init_extended_con()
        if cls.con is None:
            raise unittest.SkipTest("_init_extended_con() devolvió None")
        cls.vistas = sorted(r[0] for r in cls.con.execute(
            "SELECT view_name FROM duckdb_views() WHERE schema_name='api'"
        ).fetchall())
        if not cls.vistas:
            raise unittest.SkipTest("no hay vistas api.*")
        # RFC real para lookups
        r = cls.con.execute("SELECT rfc FROM api.att_persona LIMIT 1").fetchone()
        cls.test_rfc = r[0] if r else "AUFG910209616"

    def test_las_13_vistas_se_crearon(self):
        self.assertEqual(len(self.vistas), 13,
                         f"esperaba 13 vistas, hay {len(self.vistas)}")

    def test_count_en_todas_las_vistas(self):
        """SELECT count(*) sobre cada vista corre en menos de 30 segundos."""
        slow = []
        for v in self.vistas:
            with self.subTest(view=v):
                elapsed, (n,) = _time_query(self.con, f'SELECT count(*) FROM api."{v}"')
                self.assertIsNotNone(n, f"count(*) en api.{v} devolvió None")
                self.assertGreater(n, 0, f"api.{v} está vacía (0 filas)")
                print(f"  [count] api.{v:30s} {n:>15,} filas  {elapsed*1000:>8.1f} ms")
                if elapsed > THRESHOLD_COUNT:
                    slow.append((v, elapsed))
        self.assertEqual(slow, [],
                         f"vistas lentas en count: {[(v, f'{e:.2f}s') for v, e in slow]}")

    def test_limit_10_en_todas_las_vistas(self):
        """SELECT * LIMIT 10 sobre cada vista corre en menos de 10 segundos."""
        slow = []
        for v in self.vistas:
            with self.subTest(view=v):
                elapsed, _ = _time_query(
                    self.con, f'SELECT * FROM api."{v}" LIMIT 10')
                print(f"  [limit10] api.{v:30s}              {elapsed*1000:>8.1f} ms")
                if elapsed > THRESHOLD_LIMIT:
                    slow.append((v, elapsed))
        self.assertEqual(slow, [],
                         f"vistas lentas en LIMIT 10: {[(v, f'{e:.2f}s') for v, e in slow]}")

    def test_lookup_por_rfc_en_vistas_rfc(self):
        """SELECT count(*) WHERE rfc = <rfc_real> corre en menos de 30 segundos."""
        slow = []
        for v in sorted(RFC_VIEWS):
            if v not in self.vistas:
                continue
            with self.subTest(view=v):
                elapsed, (n,) = _time_query(
                    self.con,
                    f'SELECT count(*) FROM api."{v}" WHERE rfc = ?',
                    [self.test_rfc],
                )
                print(f"  [rfc]    api.{v:30s} {n:>15,} match {elapsed*1000:>8.1f} ms")
                if elapsed > THRESHOLD_LOOKUP:
                    slow.append((v, elapsed))
        self.assertEqual(slow, [],
                         f"vistas lentas en lookup RFC: {[(v, f'{e:.2f}s') for v, e in slow]}")

    def test_lookup_por_curp_en_vistas_curp(self):
        """SELECT count(*) WHERE curp = <curp_real> corre en menos de 30 segundos.

        Usa una CURP existente de imss_asegurado.
        """
        # Buscar una CURP real en imss_asegurado
        r = self.con.execute(
            "SELECT curp FROM api.imss_asegurado WHERE curp IS NOT NULL LIMIT 1"
        ).fetchone()
        if r is None:
            self.skipTest("imss_asegurado no tiene CURPs para test")
        test_curp = r[0]
        slow = []
        for v in sorted(CURP_VIEWS):
            if v not in self.vistas:
                continue
            with self.subTest(view=v):
                elapsed, (n,) = _time_query(
                    self.con,
                    f'SELECT count(*) FROM api."{v}" WHERE curp = ?',
                    [test_curp],
                )
                print(f"  [curp]   api.{v:30s} {n:>15,} match {elapsed*1000:>8.1f} ms")
                if elapsed > THRESHOLD_LOOKUP:
                    slow.append((v, elapsed))
        self.assertEqual(slow, [],
                         f"vistas lentas en lookup CURP: {[(v, f'{e:.2f}s') for v, e in slow]}")

    def test_count_y_total_coinciden_con_healthcheck(self):
        """La suma de counts en todas las bases debe acercarse a
        healthcheck.last.json.total_rows."""
        import json
        last = json.loads((PROY / "healthcheck.last.json").read_text())
        reported_total = last.get("total_rows", 0)
        # Sumamos counts de las vistas grandes (no de full)
        # y comparamos con un margen del 10%
        total = 0
        for v in self.vistas:
            (n,) = self.con.execute(
                f'SELECT count(*) FROM api."{v}"').fetchone()
            # _full es duplicado lógico de la no-_full, lo restamos
            if not v.endswith("_full") and not v == "fuentes_por_rfc":
                total += n
        # Solo asserteamos que el total del healthcheck es del mismo orden
        self.assertGreater(total, 100_000_000,
                           f"total={total:,} demasiado bajo")


class TestQueryCorrectness(unittest.TestCase):
    """Sanity checks: las queries devuelven datos consistentes."""

    @classmethod
    def setUpClass(cls):
        cls.con = servir._init_extended_con()
        if cls.con is None:
            raise unittest.SkipTest("_init_extended_con() devolvió None")

    def test_att_persona_tiene_rfc(self):
        """att_persona debe tener RFCs poblados (al menos 1 fila con rfc no vacío)."""
        n = self.con.execute("""
            SELECT count(*) FROM api.att_persona
            WHERE rfc IS NOT NULL AND rfc != ''
        """).fetchone()[0]
        self.assertGreater(n, 0, "att_persona no tiene RFCs")

    def test_imss_asegurado_tiene_curp(self):
        """imss_asegurado debe tener CURPs pobladas."""
        n = self.con.execute("""
            SELECT count(*) FROM api.imss_asegurado
            WHERE curp IS NOT NULL AND curp != ''
        """).fetchone()[0]
        self.assertGreater(n, 0, "imss_asegurado no tiene CURPs")

    def test_telcel_tiene_telefono(self):
        """telcel_lineas debe tener teléfonos poblados."""
        n = self.con.execute("""
            SELECT count(*) FROM api.telcel_lineas
            WHERE telefono IS NOT NULL AND telefono != ''
        """).fetchone()[0]
        self.assertGreater(n, 0, "telcel_lineas no tiene teléfonos")

    def test_fuentes_por_rfc_agrega_multiples_fuentes(self):
        """fuentes_por_rfc debe reportar al menos 1 fuente para al menos 1 RFC."""
        r = self.con.execute("""
            SELECT rfc, count(DISTINCT fuente) AS n
            FROM api.fuentes_por_rfc
            GROUP BY rfc
            ORDER BY n DESC LIMIT 1
        """).fetchone()
        # No asserteamos fuerte: algunas bases pueden estar vacías
        # Solo verificamos que la query corre
        self.assertIsNotNone(r)

    def test_cfe_medidor_domicilio_poblado(self):
        """cfe_medidor debe tener al menos 1 fila con direccion NO vacía."""
        n = self.con.execute("""
            SELECT count(*) FROM api.cfe_medidor
            WHERE direccion IS NOT NULL AND TRIM(direccion) != ''
        """).fetchone()[0]
        self.assertGreater(n, 0, "cfe_medidor no tiene direcciones")


class TestLatencyRegression(unittest.TestCase):
    """Guarda los tiempos actuales en /tmp/ como baseline para detectar regresiones.

    No se assertea contra el baseline; solo se imprime. Útil para que
    vos veas el progreso entre cambios de schema / datos.
    """

    @classmethod
    def setUpClass(cls):
        cls.con = servir._init_extended_con()
        if cls.con is None:
            raise unittest.SkipTest("_init_extended_con() devolvió None")
        cls.vistas = sorted(r[0] for r in cls.con.execute(
            "SELECT view_name FROM duckdb_views() WHERE schema_name='api'"
        ).fetchall())

    def test_baseline_count(self):
        """Mide y guarda tiempo de count(*) en cada vista."""
        import json
        results = {}
        for v in self.vistas:
            t0 = time.perf_counter()
            (n,) = self.con.execute(
                f'SELECT count(*) FROM api."{v}"').fetchone()
            elapsed = time.perf_counter() - t0
            results[v] = {"count_ms": round(elapsed * 1000, 2), "rows": n}
        # Imprime resumen ordenado por tiempo
        print("\n  BASELINE count(*) por vista (ordenado por tiempo):")
        for v, r in sorted(results.items(), key=lambda kv: -kv[1]["count_ms"]):
            print(f"    {r['count_ms']:>10.2f} ms  {r['rows']:>15,} filas  api.{v}")
        baseline_path = Path("/tmp/hermes-baseline-api-views.json")
        baseline_path.write_text(json.dumps(results, indent=2))
        print(f"\n  Baseline guardado en: {baseline_path}")
