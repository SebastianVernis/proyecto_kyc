-- init_extended_sources.sql
-- Carga las 8 bases externas (att, empleadores, imss_asegurados, imss_segmentacion,
-- telcel, repuve, cfe, fotos) como ATTACH READ_ONLY y crea vistas en el schema "api"
-- para enriquecer la base del padrón INE.
--
-- Ejecutar UNA VEZ contra ine.duckdb (= padron.duckdb) con permisos de escritura.
-- Las vistas NO materializan datos: solo exponen SELECTs sobre las bases ATTACH,
-- así que añadir/quitar una base es barato.
--
-- Uso:
--   cd /root/proyecto_kyc
--   /root/ine_server/.venv/bin/python -c \
--     "import duckdb; c=duckdb.connect('bases/padron.duckdb'); \
--      c.execute(open('backend/init_extended_sources.sql').read()); c.close()"
--
-- Convención de ATTACH:
--   b_<base>  →  nombre corto del schema
--   *_valid   →  tablas con RFC/CURP limpio y validado
--   rfc_clean →  presente en att, telcel, repuve, empleadores
--   curp_clean → presente en imss_asegurados, imss_segmentacion
--
-- Llaves de unión con el padrón (padron.curp → curp_clean; rfc NO está en padrón):
--   - imss_asegurados:  por curp_clean
--   - imss_segmentacion: por curp_clean
--   - att, telcel, repuve, empleadores: por rfc_clean (sin puente padrón↔rfc hoy)
--   - cfe: por numero_servicio / direccion (no tiene RFC/CURP)
--   - fotos: por rfc (índice de FOTOSMX, sin padrón)

SET autoinstall_known_extensions=true;
SET autoload_known_extensions=true;

-- =========================================================================
-- 1) ATTACH de las 9 bases externas (READ_ONLY — cero riesgo de modificar)
-- =========================================================================
ATTACH '/root/proyecto_kyc/bases/att.duckdb'               AS b_att     (READ_ONLY);
ATTACH '/root/proyecto_kyc/bases/empleadores.duckdb'       AS b_emp     (READ_ONLY);
ATTACH '/root/proyecto_kyc/bases/imss_asegurados.duckdb'   AS b_imss_a  (READ_ONLY);
ATTACH '/root/proyecto_kyc/bases/imss_segmentacion.duckdb' AS b_imss_s  (READ_ONLY);
ATTACH '/root/proyecto_kyc/bases/telcel.duckdb'            AS b_telcel  (READ_ONLY);
ATTACH '/root/proyecto_kyc/bases/repuve.duckdb'            AS b_repuve  (READ_ONLY);
ATTACH '/root/proyecto_kyc/bases/cfe.duckdb'               AS b_cfe     (READ_ONLY);
ATTACH '/root/proyecto_kyc/bases/fotos.duckdb'             AS b_fotos   (READ_ONLY);
-- 2026-08-13: padrón de empleados del ISSSTE (2.7M filas). Sin RFC/CURP.
ATTACH '/root/proyecto_kyc/bases/issste.duckdb'            AS b_issste  (READ_ONLY);

-- =========================================================================
-- 2) Schema "api" para las vistas de enriquecimiento
-- =========================================================================
CREATE SCHEMA IF NOT EXISTS api;

-- =========================================================================
-- 3) Vistas por base
-- =========================================================================

-- 3.1 ATT — dirección + teléfono por RFC
--      att: 1,048,575 filas | att_valid: 1,048,570 (rfc_kind ∈ {PF10,PF13,PM12})
CREATE OR REPLACE VIEW api.att_persona AS
SELECT
    a.rfc_clean        AS rfc,
    a.nombres          AS nombre_completo,
    a.nombre           AS nombres,
    a.pat              AS apellido_paterno,
    a.may              AS apellido_materno,
    a.tel1             AS telefono_fijo,
    a.celular          AS celular,
    a.direccion        AS direccion,
    a.interior         AS num_interior,
    a.exterior         AS num_exterior,
    a.colonia,
    a.municipio,
    a.estado,
    a.estado_origen,
    a.rfc_kind
FROM b_att.main.att a
WHERE a.rfc_kind IN ('PF10','PF13','PM12');

-- 3.2 Empleadores — catálogo de empresas por RFC
--      empleadores: 161,933 filas; rfc_kind ∈ {PM12,PF13,PF10,PM10}
CREATE OR REPLACE VIEW api.empleadores AS
SELECT
    e.rfc_clean                       AS rfc,
    e."razonSocial"                   AS razon_social,
    e."nombreComercial"               AS nombre_comercial,
    e."numeroEmpleados"               AS num_empleados,
    e."descripcion"                   AS descripcion,
    e."correoElectronico"             AS correo,
    e."paginaWeb"                     AS web,
    e."contacto.cargo"                AS contacto_cargo,
    e."contacto.nombre"               AS contacto_nombre,
    e."contacto.primerApellido"       AS contacto_paterno,
    e."contacto.segundoApellido"      AS contacto_materno,
    e."contacto.telefono"             AS contacto_telefono,
    e."contacto.correoElectronico"    AS contacto_correo,
    e."contacto.extension"            AS contacto_extension,
    e."ubicacion.calle"               AS dom_calle,
    e."ubicacion.numero_exterior"     AS dom_ext,
    e."ubicacion.numero_interior"     AS dom_int,
    e."ubicacion.colonia"             AS dom_colonia,
    e."ubicacion.municipio"           AS dom_municipio,
    e."ubicacion.entidad"             AS dom_entidad,
    e."ubicacion.codigopostal"        AS dom_cp,
    e.rfc_kind
FROM b_emp.main.empleadores e
WHERE e.rfc_kind IN ('PM12','PF13','PF10','PM10');

-- 3.3 IMSS Asegurados — persona × patrón, vinculado por CURP
--      imss_2025: 57,760,242 | imss_valid: 56,948,742 (curp_kind válido)
CREATE OR REPLACE VIEW api.imss_asegurado AS
SELECT
    a.curp_clean       AS curp,
    a.nss              AS nss,
    a.nss_clean        AS nss_clean,
    a.nombre           AS nombre,
    a.sueldo_raw       AS sueldo,
    a.registro_patron  AS registro_patronal,
    a.nombre_patron    AS nombre_patron,
    a.domicilio_patron AS domicilio_patron,
    a.ciudad_estado    AS ciudad_estado,
    a.codigo_postal    AS cp_patron,
    a.empresa_giro     AS giro_patron,
    a.curp_kind
FROM b_imss_a.main.imss_valid a;

-- 3.4 IMSS Segmentación — personas con diagnóstico segmentado, por CURP
--      imss_personas: 23,803,445 | imss_personas_valid: 23,480,424
CREATE OR REPLACE VIEW api.imss_segmentacion AS
SELECT
    s.curp_clean            AS curp,
    s.nss                   AS nss,
    s.nombre                AS nombre,
    s.apellido_paterno      AS paterno,
    s.apellido_materno      AS materno,
    s.fecha_de_nacimiento   AS fecnac,
    s.genero                AS sexo,
    s.edad                  AS edad,
    s.ooad                  AS ooad,
    s.unidad_medica         AS unidad_medica,
    s.modalidad             AS modalidad,
    s.tipo_de_derechohabiente AS tipo_derechohabiente,
    s.segmento_hipertension AS hipertension,
    s.segmentacion_diabetes_mellitus AS diabetes,
    s.segmentacion_cancer_de_mama    AS cancer_mama,
    s.segmentacion_cancer_de_prostata AS cancer_prostata,
    s.rfc                   AS rfc,
    s.telefono              AS telefono,
    s.ref_celular           AS celular,
    s.correo_electronico    AS correo,
    s.curp_kind
FROM b_imss_s.main.imss_personas_valid s;

-- 3.5 Telcel — línea telefónica, vinculado por RFC del titular
--      telcel: 9,709,461 | telcel_valid: 9,561,315
--      telcel_valid NO tiene 'nombre' unificado, tiene nombre1 + nombre2.
CREATE OR REPLACE VIEW api.telcel_linea AS
SELECT
    t.rfc_clean        AS rfc,
    t.cuenta           AS cuenta,
    TRIM(COALESCE(t.nombre1,'') || ' ' || COALESCE(t.nombre2,'')) AS nombre,
    t.domicilio        AS direccion,
    t.numero           AS num_exterior,
    t.interior         AS num_interior,
    t.colonia          AS colonia,
    t.ciudad           AS municipio,
    t.edo              AS estado,
    t.cp               AS cp,
    t.telefono         AS telefono_linea,
    t.tel_contacto     AS telefono_contacto,
    t.marca,
    t.modelo,
    t.esn,
    t.imei,
    t.iccid,
    t.fecha_activ      AS fecha_activacion,
    t.fecha_plan       AS fecha_plan,
    t.fecha_eq         AS fecha_equipo,
    t.tc               AS tipo_cliente,
    t.plan_actual      AS plan_actual,
    t.plan_orig        AS plan_original,
    t.renaut           AS renaut,
    t.rfc_kind
FROM b_telcel.main.telcel_valid t;

-- 3.6 REPUVE — vehículos emplacados, vinculados al dueño por RFC
--      repuve: 1,745,627 | repuve_valid: 1,745,536
CREATE OR REPLACE VIEW api.repuve_vehiculo AS
SELECT
    v.rfc_clean        AS rfc,
    v.PLACA            AS placa,
    v.NO_SERIE         AS no_serie,
    v.NO_MOTOR         AS no_motor,
    v.MARCA            AS marca,
    v.TIPO             AS tipo,
    v.MODELO           AS modelo,
    v.COLOR            AS color,
    v.USO              AS uso,
    v.NOM_PROP         AS nombre_propietario,
    v.DIR_PROP         AS direccion_propietario,
    v.TEL_PROP         AS telefono_propietario,
    v.TAXI             AS es_taxi,
    v.AGENCIA          AS agencia,
    v.rfc_kind
FROM b_repuve.main.repuve_valid v;

-- 3.7 CFE — medidores con dirección (no tiene RFC/CURP, se une por dirección)
--      medidores: 66,003,291 filas
CREATE OR REPLACE VIEW api.cfe_medidor AS
SELECT
    m.division,
    m.zona_codigo,
    m.zona_nombre,
    m.agencia_codigo,
    m.agencia_nombre,
    m.codigo_medidor,
    m.numero_medidor,
    m.numero_servicio,
    m.nombre,
    m.direccion,
    m.colonia,
    m.hilos
FROM b_cfe.main.medidores m;

-- 3.8 FOTOS — índice de fotos FOTOSMX por RFC
--      fotos: 14,942; resumen: 1 fila con agregados
CREATE OR REPLACE VIEW api.fotos_indice AS
SELECT
    f.rfc,
    f.tipo,                  -- 'firma' | 'perfil' | etc
    f.path,
    f.filename,
    f.timestamp,
    f.size_bytes,
    f.sha256
FROM b_fotos.main.fotos f;

CREATE OR REPLACE VIEW api.fotos_resumen AS
SELECT * FROM b_fotos.main.resumen;

-- 3.9 ISSSTE — padrón de empleados con sueldo y sector
--      empleados: 2,706,651; cat_ramos: 453; cat_estados: 32
--      SIN RFC/CURP/dirección — solo nombre + estructura del ISSSTE.
--      Útil para confirmar employment en sector público federal.
CREATE OR REPLACE VIEW api.issste_empleado AS
SELECT
    e.id,
    UPPER(TRIM(e.paterno))  AS paterno,
    UPPER(TRIM(e.materno))  AS materno,
    UPPER(TRIM(e.nombres))  AS nombres,
    e.cargo,
    e.sexo,
    e.sueldo,
    r.ramo,
    en.entidad,
    mo.modalidad,
    se.sector,
    es.estado
FROM b_issste.main.empleados e
LEFT JOIN b_issste.main.cat_ramos       r  ON e.ramo_id       = r.id
LEFT JOIN b_issste.main.cat_entidades   en ON e.entidad_id    = en.id
LEFT JOIN b_issste.main.cat_modalidades mo ON e.modalidad_id  = mo.id
LEFT JOIN b_issste.main.cat_sectores    se ON e.sector_id     = se.id
LEFT JOIN b_issste.main.cat_estados     es ON e.estado_id     = es.id;

-- =========================================================================
-- 4) Resúmenes agregados
-- =========================================================================

-- 4.1 Conteo de registros por RFC en cada base externa
CREATE OR REPLACE VIEW api.fuentes_por_rfc AS
SELECT rfc_clean AS rfc, 'att'        AS fuente, COUNT(*) AS n FROM b_att.main.att          WHERE rfc_clean IS NOT NULL GROUP BY 1
UNION ALL
SELECT rfc_clean,        'empleadores',          COUNT(*)   FROM b_emp.main.empleadores    WHERE rfc_clean IS NOT NULL GROUP BY 1
UNION ALL
SELECT rfc_clean,        'telcel',               COUNT(*)   FROM b_telcel.main.telcel_valid WHERE rfc_clean IS NOT NULL GROUP BY 1
UNION ALL
SELECT rfc_clean,        'repuve',               COUNT(*)   FROM b_repuve.main.repuve_valid WHERE rfc_clean IS NOT NULL GROUP BY 1;

-- 4.2 Conteo de registros por CURP en bases con curp_clean
CREATE OR REPLACE VIEW api.fuentes_por_curp AS
SELECT curp_clean AS curp, 'imss_asegurados'   AS fuente, COUNT(*) AS n FROM b_imss_a.main.imss_valid         WHERE curp_clean IS NOT NULL GROUP BY 1
UNION ALL
SELECT curp_clean,        'imss_segmentacion',          COUNT(*)   FROM b_imss_s.main.imss_personas_valid WHERE curp_clean IS NOT NULL GROUP BY 1;

-- 4.3 Resumen: ¿en cuántas bases externas aparece este RFC?
CREATE OR REPLACE VIEW api.rfc_presencia AS
SELECT rfc, COUNT(DISTINCT fuente) AS n_fuentes,
       LIST(DISTINCT fuente ORDER BY fuente) AS fuentes
FROM api.fuentes_por_rfc
GROUP BY rfc;

-- =========================================================================
-- 5) NOTA sobre la unión con el padrón INE
-- =========================================================================
-- Padrón (padron.duckdb) tiene CURP pero NO RFC.
-- Bases con rfc_clean: att, telcel, repuve, empleadores.
-- Bases con curp_clean: imss_asegurados, imss_segmentacion.
--
-- JOIN directo padrón ↔ imss_* (por curp). ✓
-- JOIN padrón ↔ bases por RFC requiere tabla puente rfc↔curp
--   (de SAT, RENAPO, o derivada). Cuando exista:
--     CREATE TABLE IF NOT EXISTS api.rfc_curp_xwalk (rfc VARCHAR, curp VARCHAR);
--   y entonces activar la vista api.persona_unificada.
