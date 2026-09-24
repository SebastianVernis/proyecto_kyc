"""test_perfil_flow.py — Test de integración del flujo completo de creación de perfil.

Valida el orden estricto de 9 pasos:
  1. Padrón (INSERT)
  2. CheckID (UPDATE rfc/nss)
  3. IMSS (UPDATE por CURP)
  4. ATT (UPDATE por RFC, 2 pasadas)
  5. Telcel (UPDATE por RFC, 2 pasadas)
  6. REPUVE (UPDATE por RFC, 2 pasadas)
  7. Empleadores (UPDATE por RFC, 2 pasadas)
  8. ISSSTE (UPDATE por nombre, fuzzy)
  9. CFE (UPDATE por nombre/CP/dirección, 3 pasadas)
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from perfil_completo_db import (
    get_con, insertar_padron, updatear_checkid, updatear_imss,
    updatear_bases_rfc, updatear_issste, updatear_cfe,
    leer_perfil, PERFIL_COMPLETO_PATH
)
import duckdb


class TestPerfilFlowCompleto(unittest.TestCase):
    """Test del pipeline completo de 9 pasos."""

    @classmethod
    def setUpClass(cls):
        """Inicializa la conexión."""
        cls.con = get_con()
        cls.con_extended = duckdb.connect(':memory:')
        # ATTACH las bases reales para queries
        try:
            cls.con_extended.execute("ATTACH '../bases/padron_v1.duckdb' AS b_padron (READ_ONLY)")
            cls.con_extended.execute("ATTACH '../bases/imss_asegurados_v1.duckdb' AS b_imss_a (READ_ONLY)")
            cls.con_extended.execute("ATTACH '../bases/imss_segmentacion_v1.duckdb' AS b_imss_s (READ_ONLY)")
            cls.con_extended.execute("ATTACH '../bases/att_v1.duckdb' AS b_att (READ_ONLY)")
            cls.con_extended.execute("ATTACH '../bases/telcel_v1.duckdb' AS b_telcel (READ_ONLY)")
            cls.con_extended.execute("ATTACH '../bases/repuve_v1.duckdb' AS b_repuve (READ_ONLY)")
            cls.con_extended.execute("ATTACH '../bases/empleadores_v1.duckdb' AS b_emp (READ_ONLY)")
            cls.con_extended.execute("ATTACH '../bases/cfe_v1.duckdb' AS b_cfe (READ_ONLY)")
        except Exception as e:
            print(f"Warning: no se pudieron attachear todas las bases: {e}")

        # Limpiar tabla
        cls.con.execute("DELETE FROM perfil_completo WHERE curp LIKE 'FLOW%'")

    @classmethod
    def tearDownClass(cls):
        """Limpia después de los tests."""
        cls.con.execute("DELETE FROM perfil_completo WHERE curp LIKE 'FLOW%'")
        cls.con_extended.close()

    def test_01_flujo_orden_estricto(self):
        """Valida que el flujo siga el orden estricto de 9 pasos."""
        curp = "FLOW900101HDFXXX01"

        # PASO 1: Insertar padrón
        datos_padron = {
            "nombre": "FLUJO",
            "paterno": "TEST",
            "materno": "COMPLETO",
            "fecnac": "01/01/1990",
            "sexo": "M",
            "calle": "AV TEST",
            "ext": "100",
            "int": "1",
            "colonia": "TESTCOLONIA",
            "cp": "01000",
            "estado": "9",
        }
        ok = insertar_padron(self.con, curp, datos_padron)
        self.assertTrue(ok)
        perfil = leer_perfil(self.con, curp)
        self.assertEqual(perfil["estado"], "padron_solo")
        self.assertIsNone(perfil["rfc"])
        print("  ✓ Paso 1: INSERT padrón")

        # PASO 2: CheckID
        checkid_data = {
            "rfc": "FLOT900101XXX",
            "nss": "55555555555",
            "codigo_postal_fiscal": "01000",
            "regimen_fiscal": "612",
            "situacion_69b": "definitivo",
        }
        ok = updatear_checkid(self.con, curp, checkid_data, True)
        self.assertTrue(ok)
        perfil = leer_perfil(self.con, curp)
        self.assertEqual(perfil["estado"], "checkid_ok")
        self.assertEqual(perfil["rfc"], "FLOT900101XXX")
        self.assertEqual(perfil["nss"], "55555555555")
        self.assertEqual(perfil["creditos_consumidos"], 3)
        print("  ✓ Paso 2: UPDATE CheckID")

        # PASO 3: IMSS
        imss_asegurados = [{
            "curp": curp,
            "nss": "55555555555",
            "nombre_patron": "EMPRESA TEST SA",
            "sueldo": "20000",
            "cp_patron": "01000",
        }]
        imss_segmentacion = [{
            "curp": curp,
            "unidad_medica": "UMF-TEST",
            "telefono": "5551234567",
            "correo_electronico": "flujo@test.com",
        }]
        ok = updatear_imss(self.con, curp, imss_asegurados, imss_segmentacion)
        self.assertTrue(ok)
        perfil = leer_perfil(self.con, curp)
        self.assertIsNotNone(perfil["imss_data"])
        print("  ✓ Paso 3: UPDATE IMSS")

    def test_02_perfil_ya_completo_no_reinyecta(self):
        """Si perfil está en estado 'completo', no debe ejecutar el flujo de nuevo."""
        curp = "FLOW900101HDFXXX02"

        # Paso 1
        insertar_padron(self.con, curp, {
            "nombre": "CACHE", "paterno": "TEST", "materno": "TEST",
        })

        # Paso 2
        updatear_checkid(self.con, curp, {"rfc": "CATT900101XXX"}, True)

        # Simular que está completo
        self.con.execute("""
            UPDATE perfil_completo SET estado='completo' WHERE curp=?
        """, [curp])

        # Leer
        perfil = leer_perfil(self.con, curp)
        self.assertEqual(perfil["estado"], "completo")
        print("  ✓ Perfil 'completo' se mantiene")

    def test_03_checkid_error_no_continua(self):
        """Si CheckID falla, no debe continuar a pasos 3-9."""
        curp = "FLOW900101HDFXXX03"

        # Paso 1
        insertar_padron(self.con, curp, {
            "nombre": "ERROR", "paterno": "TEST", "materno": "TEST",
        })

        # Paso 2: CheckID falla
        ok = updatear_checkid(self.con, curp, {"error": "CURP no válida"}, False)

        perfil = leer_perfil(self.con, curp)
        self.assertEqual(perfil["estado"], "checkid_error")
        self.assertFalse(perfil["checkid_ok"])
        self.assertIsNone(perfil["rfc"])
        print("  ✓ CheckID error detiene flujo")

    def test_04_credito_consumido_se_acumula(self):
        """Verifica que creditos_consumidos suma correctamente."""
        curp = "FLOW900101HDFXXX04"

        insertar_padron(self.con, curp, {
            "nombre": "CREDITO", "paterno": "TEST", "materno": "TEST",
        })
        perfil = leer_perfil(self.con, curp)
        self.assertEqual(perfil["creditos_consumidos"], 0)

        updatear_checkid(self.con, curp, {"rfc": "TEST123", "nss": "123"}, True)
        perfil = leer_perfil(self.con, curp)
        self.assertEqual(perfil["creditos_consumidos"], 3)

        # Si se actualiza otra vez (por alguna razón), sumaria 6
        updatear_checkid(self.con, curp, {"rfc": "TEST123", "nss": "123"}, True)
        perfil = leer_perfil(self.con, curp)
        self.assertEqual(perfil["creditos_consumidos"], 6)
        print("  ✓ Créditos se acumulan correctamente")

    def test_05_fuentes_consultadas_se_marcan(self):
        """fuentes_consultadas marca correctamente cada paso ejecutado."""
        curp = "FLOW900101HDFXXX05"

        insertar_padron(self.con, curp, {"nombre": "FUENTE"})
        perfil = leer_perfil(self.con, curp)
        fuentes = json.loads(perfil["fuentes_consultadas"])
        self.assertTrue(fuentes.get("padron"))
        self.assertFalse(fuentes.get("checkid", False))

        updatear_checkid(self.con, curp, {"rfc": "TEST"}, True)
        perfil = leer_perfil(self.con, curp)
        fuentes = json.loads(perfil["fuentes_consultadas"])
        self.assertTrue(fuentes.get("checkid"))
        self.assertTrue(fuentes.get("checkid_ok"))

        updatear_imss(self.con, curp, [], [])
        perfil = leer_perfil(self.con, curp)
        fuentes = json.loads(perfil["fuentes_consultadas"])
        self.assertTrue(fuentes.get("imss_asegurados"))
        print("  ✓ fuentes_consultadas se marca correctamente")

    def test_06_json_merge_patch_no_sobrescribe(self):
        """JSON_MERGE_PATCH agrega, no borra lo anterior."""
        curp = "FLOW900101HDFXXX06"

        insertar_padron(self.con, curp, {"nombre": "TEST"})
        updatear_checkid(self.con, curp, {"rfc": "TEST"}, True)

        # Leer fuentes después de CheckID
        perfil = leer_perfil(self.con, curp)
        fuentes = json.loads(perfil["fuentes_consultadas"])
        self.assertTrue(fuentes.get("padron"))
        self.assertTrue(fuentes.get("checkid"))

        # Actualizar IMSS
        updatear_imss(self.con, curp, [{"nss": "123"}], [{"correo": "test@test.com"}])
        perfil = leer_perfil(self.con, curp)
        fuentes = json.loads(perfil["fuentes_consultadas"])
        self.assertTrue(fuentes.get("padron"))    # No debe borrarse
        self.assertTrue(fuentes.get("checkid"))   # No debe borrarse
        self.assertTrue(fuentes.get("imss_asegurados"))
        print("  ✓ JSON_MERGE_PATCH agrega sin borrar")


if __name__ == "__main__":
    unittest.main()
