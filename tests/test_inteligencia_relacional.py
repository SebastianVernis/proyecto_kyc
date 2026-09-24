import sys
import unittest
from pathlib import Path

PROY = Path(__file__).resolve().parent.parent
BACKEND = PROY / "backend"
sys.path.insert(0, str(BACKEND))

import inteligencia_completa as ic


class TestConstruirMapeoDomicilioCfe(unittest.TestCase):
    def test_mapea_titular_cfe_a_familiar_por_nombre_y_domicilio(self):
        sujeto = {
            "curp": "SUJT800101HDFABC01",
            "nombre": "JUAN",
            "paterno": "PEREZ",
            "materno": "LOPEZ",
            "nombre_completo": "JUAN PEREZ LOPEZ",
            "calle": "AV REFORMA",
            "ext": "100",
            "colonia": "CENTRO",
            "cp": "06060",
        }
        familiares = [{
            "curp": "FAML820202MDFABC02",
            "nombre": "MARIA",
            "paterno": "PEREZ",
            "materno": "LOPEZ",
            "nombre_completo": "MARIA PEREZ LOPEZ",
            "tipo_relacion": "consanguineo_directo",
            "calle": "AV REFORMA",
            "ext": "100",
            "colonia": "CENTRO",
            "cp": "06060",
        }]
        convivientes = []
        cfe = [{
            "numero_servicio": "12345678901",
            "titular": "MARIA PEREZ LOPEZ",
            "direccion": "AV REFORMA 100",
            "colonia": "CENTRO",
            "cp": "06060",
            "division": "DIVISION CENTRO",
            "zona": "ZONA 1",
            "agencia": "AGENCIA 2",
        }]

        out = ic._construir_mapeo_domicilio_cfe(sujeto, familiares, convivientes, cfe)

        self.assertEqual(out["total_servicios"], 1)
        self.assertEqual(out["servicios"][0]["tipo_relacion_titular"], "consanguineo_directo")
        self.assertEqual(out["servicios"][0]["titular_curp_relacionado"], "FAML820202MDFABC02")
        self.assertTrue(out["servicios"][0]["coincidencia_domicilio"])

    def test_si_titular_cfe_es_el_sujeto_lo_marca_correctamente(self):
        sujeto = {
            "curp": "SUJT800101HDFABC01",
            "nombre": "JUAN",
            "paterno": "PEREZ",
            "materno": "LOPEZ",
            "nombre_completo": "JUAN PEREZ LOPEZ",
            "calle": "AV REFORMA",
            "ext": "100",
            "colonia": "CENTRO",
            "cp": "06060",
        }
        out = ic._construir_mapeo_domicilio_cfe(sujeto, [], [], [{
            "numero_servicio": "12345678901",
            "titular": "JUAN PEREZ LOPEZ",
            "direccion": "AV REFORMA 100",
            "colonia": "CENTRO",
            "cp": "06060",
        }])

        self.assertEqual(out["servicios"][0]["tipo_relacion_titular"], "consanguineo_directo")
        self.assertEqual(out["servicios"][0]["titular_curp_relacionado"], "SUJT800101HDFABC01")

    def test_si_no_hay_match_deja_sin_relacion(self):
        sujeto = {
            "curp": "SUJT800101HDFABC01",
            "nombre_completo": "JUAN PEREZ LOPEZ",
            "calle": "AV REFORMA",
            "ext": "100",
            "colonia": "CENTRO",
            "cp": "06060",
        }
        out = ic._construir_mapeo_domicilio_cfe(sujeto, [], [], [{
            "numero_servicio": "12345678901",
            "titular": "PERSONA DESCONOCIDA",
            "direccion": "OTRA CALLE 55",
            "colonia": "ROMA",
            "cp": "06700",
        }])

        self.assertEqual(out["servicios"][0]["tipo_relacion_titular"], "sin_match")
        self.assertIsNone(out["servicios"][0]["titular_curp_relacionado"])
        self.assertFalse(out["servicios"][0]["coincidencia_domicilio"])


if __name__ == "__main__":
    unittest.main()
