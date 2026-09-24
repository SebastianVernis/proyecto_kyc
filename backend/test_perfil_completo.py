"""test_perfil_completo.py — Validación del flujo de mapeo orgánico.

Tests:
  1. Base de datos creada correctamente
  2. INSERT de padrón funciona
  3. UPDATE de CheckID funciona
  4. UPDATE de IMSS funciona
  5. Lectura de perfil funciona
  6. Transición de estados es correcta
  7. Queries específicas de cada base
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from perfil_completo_db import (
    get_con, insertar_padron, updatear_checkid, updatear_imss,
    leer_perfil, existe_en_padron,
    PERFIL_COMPLETO_PATH
)
import duckdb


class TestPerfilCompleto(unittest.TestCase):
    """Test del flujo de creación y actualización de perfiles."""

    @classmethod
    def setUpClass(cls):
        """Inicializa la base."""
        cls.con = get_con()
        # Limpiar tabla de tests
        cls.con.execute("DELETE FROM perfil_completo WHERE curp LIKE 'TEST%'")

    @classmethod
    def tearDownClass(cls):
        """Limpia después de los tests."""
        cls.con.execute("DELETE FROM perfil_completo WHERE curp LIKE 'TEST%'")
        cls.con.commit()

    def test_1_tabla_existe(self):
        """Tabla perfil_completo existe."""
        tablas = self.con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
        nombres = [t[0] for t in tablas]
        self.assertIn("perfil_completo", nombres)
        self.assertIn("perfil_completo_flat", nombres)

    def test_2_insertar_padron(self):
        """INSERT de datos del padrón funciona."""
        curp = "TEST900101HDFXXX01"
        datos = {
            "nombre": "MARIO ALBERTO",
            "paterno": "AGUILAR",
            "materno": "CASTILLO",
            "fecnac": "01/01/1990",
            "sexo": "M",
            "calle": "AV INSURGENTES SUR",
            "ext": "1673",
            "int": "904",
            "colonia": "GUADALUPE INN",
            "cp": "03100",
            "estado": "9",
            "distrito": "09",
            "municipio": "014",
        }

        ok = insertar_padron(self.con, curp, datos)
        self.assertTrue(ok)

        # Verificar que fue insertado
        perfil = leer_perfil(self.con, curp)
        self.assertIsNotNone(perfil)
        self.assertEqual(perfil["curp"], curp)
        self.assertEqual(perfil["estado"], "padron_solo")
        self.assertIsNone(perfil["rfc"])

    def test_3_updatear_checkid(self):
        """UPDATE de CheckID funciona (agrega rfc, nss, cp_fiscal)."""
        curp = "TEST900101HDFXXX01"
        checkid_data = {
            "rfc": "MARA900101XXX",
            "nss": "12345678901",
            "codigo_postal_fiscal": "03100",
            "regimen_fiscal": "612",
            "situacion_69b": "definitivo",
            "resultado_crudo": {"exitoso": True}
        }

        ok = updatear_checkid(self.con, curp, checkid_data, True)
        self.assertTrue(ok)

        # Verificar actualización
        perfil = leer_perfil(self.con, curp)
        self.assertEqual(perfil["rfc"], "MARA900101XXX")
        self.assertEqual(perfil["nss"], "12345678901")
        self.assertEqual(perfil["estado"], "checkid_ok")
        self.assertTrue(perfil["checkid_ok"])
        self.assertEqual(perfil["creditos_consumidos"], 3)

    def test_4_updatear_imss(self):
        """UPDATE de IMSS funciona (agrega empleadores y salud por CURP)."""
        curp = "TEST900101HDFXXX01"
        imss_asegurados = [{
            "curp": "TEST900101HDFXXX01",
            "nss": "12345678901",
            "nombre_patron": "EMPRESA SA",
            "sueldo": "15000",
            "cp_patron": "03100",
            "giro_patron": "Servicios"
        }]
        imss_segmentacion = [{
            "curp": "TEST900101HDFXXX01",
            "unidad_medica": "UMF-123",
            "modalidad": "Familiar",
            "telefono": "5512345678",
            "correo": "mario@example.com"
        }]

        ok = updatear_imss(self.con, curp, imss_asegurados, imss_segmentacion)
        self.assertTrue(ok)

        # Verificar actualización
        perfil = leer_perfil(self.con, curp)
        imss_data = json.loads(perfil["imss_data"]) if isinstance(perfil["imss_data"], str) else perfil["imss_data"]
        self.assertEqual(len(imss_data["asegurado"]), 1)
        self.assertEqual(len(imss_data["salud"]), 1)
        self.assertEqual(imss_data["salud"][0]["telefono"], "5512345678")

    def test_5_estado_transicion(self):
        """Verificar que el estado transite correctamente."""
        curp = "TEST900101HDFXXX02"
        datos = {
            "nombre": "JUAN",
            "paterno": "PEREZ",
            "materno": "GARCIA",
            "fecnac": "05/05/1995",
        }

        # Paso 1: INSERT
        insertar_padron(self.con, curp, datos)
        perfil = leer_perfil(self.con, curp)
        self.assertEqual(perfil["estado"], "padron_solo")

        # Paso 2: UPDATE CheckID
        checkid_data = {"rfc": "PEGJ950505XXX", "nss": "98765432101"}
        updatear_checkid(self.con, curp, checkid_data, True)
        perfil = leer_perfil(self.con, curp)
        self.assertEqual(perfil["estado"], "checkid_ok")

    def test_6_leer_perfil_inexistente(self):
        """leer_perfil retorna None para CURP inexistente."""
        perfil = leer_perfil(self.con, "NOEXISTE18HDFXXX99")
        self.assertIsNone(perfil)

    def test_7_existe_en_padron(self):
        """existe_en_padron verifica rápidamente."""
        curp = "TEST900101HDFXXX01"
        self.assertTrue(existe_en_padron(curp))
        self.assertFalse(existe_en_padron("NOEXISTE18HDFXXX98"))

    def test_8_fuentes_consultadas(self):
        """fuentes_consultadas se actualiza correctamente."""
        curp = "TEST900101HDFXXX01"
        perfil = leer_perfil(self.con, curp)
        fuentes = json.loads(perfil["fuentes_consultadas"]) if isinstance(perfil["fuentes_consultadas"], str) else perfil["fuentes_consultadas"]
        self.assertTrue(fuentes.get("padron"))
        self.assertTrue(fuentes.get("checkid"))
        self.assertTrue(fuentes.get("imss_asegurados"))

    def test_9_creditos_consumidos(self):
        """creditos_consumidos suma correctamente."""
        curp = "TEST900101HDFXXX01"
        perfil = leer_perfil(self.con, curp)
        # Después de 1 CheckID exitoso: 3 créditos
        self.assertEqual(perfil["creditos_consumidos"], 3)

    def test_10_timestamp_actualiza(self):
        """actualizado_en cambia en cada UPDATE."""
        import time
        curp = "TEST900101HDFXXX03"
        datos = {"nombre": "TEST", "paterno": "USER", "materno": "TEST"}
        insertar_padron(self.con, curp, datos)

        perfil1 = leer_perfil(self.con, curp)
        ts1 = perfil1["actualizado_en"]

        time.sleep(0.1)
        updatear_checkid(self.con, curp, {"rfc": "TESTXXX", "nss": "999"}, True)
        perfil2 = leer_perfil(self.con, curp)
        ts2 = perfil2["actualizado_en"]

        # ts2 debe ser más reciente que ts1
        self.assertGreater(ts2, ts1)


if __name__ == "__main__":
    unittest.main()
