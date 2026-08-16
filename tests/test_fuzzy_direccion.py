"""Tests de fuzzy_direccion: candidatos del padrón para decisión manual.

Caso ancla: CP 98400 (Zacatecas), la zona validada en la prueba de fuego del
handoff — `IGNACIO ZARAGOZA 5` existe en el padrón ahí.
"""
import sys
import unittest
from pathlib import Path

PROY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROY / "backend"))

import fuzzy_direccion as F


def _indice_disponible():
    return F._conexion() is not None


class TestNormalizarConsulta(unittest.TestCase):
    def test_claves_generadas(self):
        q = F._normalizar(calle="C IGNACIO ZARAGOZA", ext="5", cp="98400")
        self.assertEqual(q["cp"], "98400")
        self.assertEqual(q["ext"], "5")
        self.assertIsNotNone(q["claves"]["k_via_ext"])
        self.assertIn("98400", q["claves"]["k_via_ext"])

    def test_sin_datos_no_lanza(self):
        r = F.candidatos()
        self.assertEqual(r["candidatos"], [])
        self.assertIsNotNone(r["nota"])


@unittest.skipUnless(_indice_disponible(),
                     "mkidx_padron no disponible (¿materializando?)")
class TestCandidatos(unittest.TestCase):
    def test_exacto_puerta(self):
        r = F.candidatos(calle="C IGNACIO ZARAGOZA", ext="5", cp="98400")
        self.assertTrue(r["candidatos"], "sin candidatos para dirección conocida")
        self.assertIsNotNone(r["match_exacto"])
        top = r["candidatos"][0]
        self.assertTrue(top["nivel"].startswith("exacto:"))
        self.assertEqual(top["cp"], "98400")

    def test_fuzzy_typo_encuentra_opciones(self):
        # 'SARAGOSA' mal escrito: no hay clave exacta, pero el fuzzy debe
        # proponer ZARAGOZA en el mismo cp como opción para el usuario.
        r = F.candidatos(calle="IGNACIO SARAGOSA", ext="5", cp="98400")
        self.assertTrue(r["candidatos"], "fuzzy no propuso opciones")
        vias = " ".join((c["via"] or "") for c in r["candidatos"][:5])
        self.assertIn("ZARAGOZA", vias)
        self.assertEqual(r["candidatos"][0]["nivel"], "fuzzy")
        self.assertGreater(r["candidatos"][0]["score"], 0.6)

    def test_refs_traen_personas(self):
        r = F.candidatos(calle="C IGNACIO ZARAGOZA", ext="5", cp="98400")
        if not r["candidatos"]:
            self.skipTest("sin candidatos")
        refs = r["candidatos"][0]["refs"]
        personas = F.personas_de_refs(refs)
        self.assertEqual(len(personas), len(refs))
        # el padrón tiene filas con curp vacía; basta que traigan identidad
        self.assertTrue(all((p.get("nombre") or p.get("paterno")) for p in personas))


if __name__ == "__main__":
    unittest.main(verbosity=2)
