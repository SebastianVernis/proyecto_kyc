"""test_e2e_perfil.py — E2E tests del flujo completo de creación de perfil.

Usa datos reales de las bases (CURP RUVZ750427MOCZGT00) con CheckID mock.
Valida:
  1. Flujo completo 9 pasos con datos reales
  2. Segunda llamada reutiliza CheckID cacheado (<30 días)
  3. CheckID vencido (>30 días) re-ejecuta API
  4. Créditos siempre se cobran al usuario
  5. Estado transiciona correctamente
  6. fuentes_consultadas se marca correctamente
  7. CURP inexistente retorna error
  8. Validación de CURP inválida
"""

import json
import sys
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent))

import duckdb
from perfil_completo_db import (
    get_con, insertar_padron, updatear_checkid, updatear_imss,
    updatear_bases_rfc, updatear_issste, updatear_cfe,
    leer_perfil, PERFIL_COMPLETO_PATH,
)

BASES_PATH = str(Path(__file__).parent.parent / "bases")
TEST_CURP = "RUVZ750427MOCZGT00"
TEST_CURP_FAKE = "XXXX000000XXXXXXXX"


def _build_con_extended():
    con = duckdb.connect(":memory:")
    con.execute(f"ATTACH '{BASES_PATH}/padron_v1.duckdb' AS b_padron (READ_ONLY)")
    DBS = {
        "b_imss_a": "imss_asegurados_v1.duckdb",
        "b_imss_s": "imss_segmentacion_v1.duckdb",
        "b_att": "att_v1.duckdb",
        "b_telcel": "telcel_v1.duckdb",
        "b_repuve": "repuve_v1.duckdb",
        "b_emp": "empleadores_v1.duckdb",
        "b_cfe": "cfe_v1.duckdb",
        "b_issste": "issste_v1.duckdb",
    }
    for alias, fname in DBS.items():
        con.execute(f"ATTACH '{BASES_PATH}/{fname}' AS {alias} (READ_ONLY)")
    con.execute("CREATE SCHEMA IF NOT EXISTS api")
    con.execute("CREATE OR REPLACE VIEW padron AS SELECT * FROM b_padron.padron")
    return con


def _create_views(con):
    con.execute("""CREATE OR REPLACE VIEW api.imss_asegurado AS
        SELECT p.curp_clean AS curp, p.nss_clean AS nss, p.nombre AS nombre_patron,
               p.registro_patron, p.nombre_patron AS empresa_nombre,
               p.domicilio_patron, p.ciudad_estado, p.cp5 AS empresa_cp,
               p.empresa_giro, p.sueldo_raw AS sueldo, p.curp_kind
        FROM b_imss_a.main.imss_2025 p WHERE p.curp_kind = 'PF18'""")

    con.execute("""CREATE OR REPLACE VIEW api.imss_segmentacion AS
        SELECT s.curp_clean AS curp, s.nss AS nss, s.nombre AS nombre,
               s.apellido_paterno AS paterno, s.apellido_materno AS materno,
               s.fecha_de_nacimiento AS fecnac, s.genero AS sexo, s.edad,
               s.ooad, s.unidad_medica, s.modalidad, s.tipo_de_derechohabiente,
               s.segmento_hipertension AS hipertension,
               s.segmentacion_diabetes_mellitus AS diabetes,
               s.rfc AS rfc, s.telefono, s.ref_celular AS celular,
               s.correo_electronico AS correo, s.curp_kind
        FROM b_imss_s.main.imss_personas_valid s WHERE s.curp_kind = 'PF18'""")

    con.execute("""CREATE OR REPLACE VIEW api.att_persona AS
        SELECT a.rfc_clean AS rfc, a.nombres AS nombre_completo, a.nombre AS nombres,
               a.pat AS apellido_paterno, a.may AS apellido_materno,
               a.tel1 AS telefono_fijo, a.celular, a.direccion, a.interior AS num_interior,
               a.exterior AS num_exterior, a.colonia, a.municipio, a.estado, a.rfc_kind
        FROM b_att.main.att a WHERE a.rfc_kind IN ('PF10','PF13','PM12')""")

    con.execute("""CREATE OR REPLACE VIEW api.telcel_linea AS
        SELECT t.rfc_clean AS rfc, t.telefono, t.plan_actual AS plan,
               t.plan_orig AS plan_origen, t.marca, t.modelo, t.esn, t.imei, t.iccid,
               t.st_tel AS estado_linea, t.st_cta AS estado_cuenta,
               t.fecha_activ AS fecha_activacion, t.fecha_cancel AS fecha_cancelacion,
               t.fecha_term AS fecha_termino, t.nombre1, t.nombre2,
               t.domicilio, t.colonia, t.ciudad, t.edo AS estado, t.cp, t.rfc_kind
        FROM b_telcel.main.telcel t WHERE t.rfc_kind IN ('PF13','PM12','PF10','PM10')""")

    con.execute("""CREATE OR REPLACE VIEW api.repuve_vehiculo AS
        SELECT r.rfc_clean AS rfc, r."PLACA" AS placa, r.NO_SERIE AS no_serie,
               r.MARCA AS marca, r.TIPO AS tipo, r.modelo_int AS modelo,
               r.COLOR AS color, r.USO AS uso, r.nom_prop_fix AS propietario,
               r.dir_prop_fix AS direccion_propietario, r.TEL_PROP AS telefono_propietario,
               r.rfc_kind
        FROM b_repuve.main.repuve r WHERE r.rfc_kind IN ('PF13','PM12','PF10','PM10')""")

    con.execute("""CREATE OR REPLACE VIEW api.empleadores AS
        SELECT e.rfc_clean AS rfc, e."razonSocial" AS razon_social,
               e."nombreComercial" AS nombre_comercial, e."numeroEmpleados" AS num_empleados,
               e."descripcion" AS descripcion, e."correoElectronico" AS correo,
               e."nombreCompleto" AS nombre_completo,
               e."ubicacion.calle" AS dom_calle, e."ubicacion.colonia" AS dom_colonia,
               e."ubicacion.municipio" AS dom_municipio, e."ubicacion.entidad" AS dom_entidad,
               e."ubicacion.codigopostal" AS dom_cp,
               e.rfc_kind
        FROM b_emp.main.empleadores e WHERE e.rfc_kind IN ('PM12','PF13','PF10','PM10')""")

    con.execute("""CREATE OR REPLACE VIEW api.issste_empleado AS
        SELECT e.id, UPPER(TRIM(e.paterno)) AS paterno, UPPER(TRIM(e.materno)) AS materno,
               UPPER(TRIM(e.nombres)) AS nombres, e.cargo, e.sexo, e.sueldo,
               r.ramo, en.entidad, mo.modalidad, se.sector, es.estado
        FROM b_issste.main.empleados e
        LEFT JOIN b_issste.main.cat_ramos r ON e.ramo_id = r.id
        LEFT JOIN b_issste.main.cat_entidades en ON e.entidad_id = en.id
        LEFT JOIN b_issste.main.cat_modalidades mo ON e.modalidad_id = mo.id
        LEFT JOIN b_issste.main.cat_sectores se ON e.sector_id = se.id
        LEFT JOIN b_issste.main.cat_estados es ON e.estado_id = es.id""")

    con.execute("""CREATE OR REPLACE VIEW api.cfe_medidor_kyc AS
        SELECT m.numero_servicio, m.nombre, m.direccion, m.calle_adicional_1,
               m.calle_adicional_2, m.colonia, m.cp, m.division
        FROM b_cfe.main.medidores m""")


MOCK_CHECKID_DATA = {
    "exitoso": True,
    "rfc": "RUIV750427MOCZGT00",
    "nss": "02167504824",
    "curp": TEST_CURP,
    "nombre": "ZITA DEL CARMEN",
    "paterno": "RUIZ",
    "materno": "VEGA",
    "sexo": "M",
    "fecnac": "27/04/1975",
    "codigo_postal_fiscal": "20298",
    "regimen_fiscal": "612",
    "situacion_69b": "definitivo",
}


class TestE2EPerfilCrear(unittest.TestCase):
    con_perfil = None
    con_extended = None

    @classmethod
    def setUpClass(cls):
        cls.con_perfil = get_con()
        cls.con_extended = _build_con_extended()
        _create_views(cls.con_extended)
        cls.con_perfil.execute("DELETE FROM perfil_completo WHERE curp IN (?, ?)",
                               [TEST_CURP, TEST_CURP_FAKE])

    @classmethod
    def tearDownClass(cls):
        cls.con_perfil.execute("DELETE FROM perfil_completo WHERE curp IN (?, ?)",
                               [TEST_CURP, TEST_CURP_FAKE])
        cls.con_extended.close()

    def _run_flow(self, curp=TEST_CURP):
        from perfil_crear import crear_perfil
        with patch("perfil_crear.CheckIdClient") as MockClient:
            mock_client = MagicMock()
            mock_client.get_full.return_value = MOCK_CHECKID_DATA.copy()
            MockClient.return_value = mock_client
            result = crear_perfil(
                curp=curp,
                con_extended=self.con_extended,
                con_perfil=self.con_perfil,
            )
        return result

    def test_01_flujo_completo_9_pasos(self):
        result = self._run_flow()

        self.assertEqual(result["estado"], "completo")
        self.assertIsNone(result.get("error"))
        self.assertTrue(len(result["pasos"]) >= 7)

        pasos_nombres = [p["paso"] for p in result["pasos"]]
        self.assertIn("padron", pasos_nombres)
        self.assertIn("checkid", pasos_nombres)
        self.assertIn("imss", pasos_nombres)
        self.assertIn("bases_rfc", pasos_nombres)
        self.assertIn("issste", pasos_nombres)
        self.assertIn("cfe", pasos_nombres)

        perfil = result["perfil"]
        self.assertIsNotNone(perfil)
        self.assertEqual(perfil["curp"], TEST_CURP)
        self.assertIsNotNone(perfil["rfc"])

    def test_02_checkid_cache_reutiliza(self):
        from perfil_crear import crear_perfil
        result1 = self._run_flow()
        self.assertIn(result1["estado"], ("completo", "ya_completo"))

        with patch("perfil_crear.CheckIdClient") as MockClient:
            mock_client = MagicMock()
            mock_client.get_full.return_value = MOCK_CHECKID_DATA.copy()
            MockClient.return_value = mock_client

            result2 = crear_perfil(
                curp=TEST_CURP,
                con_extended=self.con_extended,
                con_perfil=self.con_perfil,
            )

        if result2["estado"] == "ya_completo":
            mock_client.get_full.assert_not_called()
            self.assertEqual(result2["metadata"]["creditos"]["total"], 3)
        else:
            mock_client.get_full.assert_not_called()
            checkid_step = [p for p in result2["pasos"] if p["paso"] == "checkid"][0]
            self.assertTrue(checkid_step.get("reutilizado"))
            self.assertEqual(checkid_step.get("creditos"), 0)

    def test_03_checkid_vencido_re_ejecuta(self):
        from perfil_crear import crear_perfil
        self.con_perfil.execute("DELETE FROM perfil_completo WHERE curp = ?", [TEST_CURP])
        result1 = self._run_flow()
        self.assertEqual(result1["estado"], "completo")

        self.con_perfil.execute(
            "UPDATE perfil_completo SET checkid_fecha = ? WHERE curp = ?",
            [datetime.now() - timedelta(days=31), TEST_CURP]
        )

        with patch("perfil_crear.CheckIdClient") as MockClient:
            mock_client = MagicMock()
            mock_client.get_full.return_value = MOCK_CHECKID_DATA.copy()
            MockClient.return_value = mock_client

            result2 = crear_perfil(
                curp=TEST_CURP,
                con_extended=self.con_extended,
                con_perfil=self.con_perfil,
            )
            mock_client.get_full.assert_called_once()

        checkid_step = [p for p in result2["pasos"] if p["paso"] == "checkid"][0]
        self.assertFalse(checkid_step.get("reutilizado", False))
        self.assertEqual(checkid_step.get("creditos"), 3)

    def test_04_creditos_siempre_se_cobran(self):
        self.con_perfil.execute("DELETE FROM perfil_completo WHERE curp = ?", [TEST_CURP])
        result1 = self._run_flow()
        creditos1 = result1["metadata"]["creditos"]
        self.assertEqual(creditos1["checkid"], 3)
        self.assertEqual(creditos1["bases_locales"], 2)
        self.assertEqual(creditos1["total"], 5)

        result2 = self._run_flow()
        creditos2 = result2["metadata"]["creditos"]
        self.assertEqual(creditos2["checkid"], 3)
        self.assertEqual(creditos2["bases_locales"], 0)
        self.assertEqual(creditos2["total"], 3)

    def test_05_fuentes_consultadas_marcadas(self):
        result = self._run_flow()
        perfil = result["perfil"]
        fuentes = json.loads(
            self.con_perfil.execute(
                "SELECT fuentes_consultadas FROM perfil_completo WHERE curp = ?",
                [TEST_CURP]
            ).fetchone()[0]
        )
        self.assertTrue(fuentes.get("checkid"))
        self.assertTrue(fuentes.get("imss_asegurados") or fuentes.get("imss"))
        self.assertTrue(fuentes.get("att") or fuentes.get("telcel"))

    def test_06_curp_inexistente_error(self):
        result = self._run_flow(curp=TEST_CURP_FAKE)
        self.assertEqual(result["estado"], "error")
        self.assertTrue(len(result["errores"]) > 0)
        self.assertIn("no encontrado", result["errores"][0].lower())

    def test_07_curp_invalida_rechazada(self):
        from perfil_crear import crear_perfil
        result = crear_perfil(
            curp="CORTA",
            con_extended=self.con_extended,
            con_perfil=self.con_perfil,
        )
        self.assertIn("error", result)

    def test_08_estado_transicion_padron_a_completo(self):
        self.con_perfil.execute("DELETE FROM perfil_completo WHERE curp = ?", [TEST_CURP])

        result = self._run_flow()
        perfil_db = leer_perfil(self.con_perfil, TEST_CURP)
        self.assertIsNotNone(perfil_db)
        self.assertEqual(perfil_db["estado"], "completo")
        self.assertIsNotNone(perfil_db["rfc"])

    def test_09_parcial_checkid_error_detiene(self):
        from perfil_crear import crear_perfil
        self.con_perfil.execute("DELETE FROM perfil_completo WHERE curp = ?", [TEST_CURP])

        with patch("perfil_crear.CheckIdClient") as MockClient:
            mock_client = MagicMock()
            mock_client.get_full.return_value = {"exitoso": False, "error": "CURP no encontrada"}
            MockClient.return_value = mock_client

            result = crear_perfil(
                curp=TEST_CURP,
                con_extended=self.con_extended,
                con_perfil=self.con_perfil,
            )

        self.assertEqual(result["estado"], "checkid_error")
        pasos_nombres = [p["paso"] for p in result["pasos"]]
        self.assertNotIn("imss", pasos_nombres)
        self.assertNotIn("cfe", pasos_nombres)

    def test_10_elasticsearch_payload_structure(self):
        result = self._run_flow()
        perfil = result["perfil"]

        self.assertIn("curp", perfil)
        self.assertIn("rfc", perfil)
        self.assertIn("nss", perfil)
        self.assertIn("estado", perfil)
        self.assertIn("creado_en", perfil)
        self.assertIn("actualizado_en", perfil)

        for key in ["padron_data", "checkid_data", "imss_data", "att_data",
                     "telcel_data", "repuve_data", "empleadores_data",
                     "issste_data", "cfe_data"]:
            self.assertIn(key, perfil)


if __name__ == "__main__":
    unittest.main()
