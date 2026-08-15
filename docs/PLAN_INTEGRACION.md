# Planeación para integración con el sistema de consultas del padrón

Estado: 2026-08-04 · bases_consolidadas/ en /Descargas/Bases/

## Contexto

El sistema de consultas del padrón (KYC/OSINT, "cuartodepazsearch") ya usa 3 bases como núcleo:

  - **Padrón INE local** (DuckDB) — base principal de identidad
  - **SEPOMEX** (SQLite) — códigos postales
  - **Marco Geoestadístico INEGI 2020** (SQLite + R-Tree) — manzanas

Las 6 nuevas bases convertidas extienden el grafo de inteligencia de
manera complementaria. Cada una aporta una capa distinta y todas
coinciden en identificador `curp_clean` o `rfc_clean`.

## Tabla de unión universal: personas (curp)

| Base | PK primaria | Granularidad | Mejor join con padrón INE |
|---|---|---|---|
| `att`                | `rfc_clean`        | 1 fila = persona física | `curp_clean` (no hay CURP, sólo RFC) |
| `repuve`             | `rfc_clean`        | 1 fila = vehículo | join por `rfc_clean` (rfc del dueño) |
| `imss_asegurados`    | `curp_clean`       | 1 fila = persona × patrón | `curp_clean` |
| `imss_segmentacion`  | `curp_clean`       | 1 fila = persona × enfermedad segmentada | `curp_clean` |
| `telcel`             | `rfc_clean`        | 1 fila = línea telefónica | join por `rfc_clean` |
| `empleadores`        | `rfc_clean` (empresa) | 1 fila = empresa | join por `rfc_clean` empresa contra patrón IMSS |

## Diagrama de capas (ascii)

```
            ┌─────────────────────┐
            │  Padrón INE (núcleo) │  124M ciudadanos
            │  curp, nss, nombre   │
            └──────────┬──────────┘
                       │ curp / nss
       ┌───────────────┼───────────────┬──────────────┐
       ▼               ▼               ▼              ▼
  imss_asegurados  imss_segmentación   att         repuve
  (57.7M filas)    (23.8M filas)     (1.05M)    (1.75M)
  patrón + sueldo   diagnóstico      dirección+  vehículo +
                    segmentación      teléfono    dueño (RFC)
       │                              │
       │ rfc empresa                  │ rfc_clean (PF13)
       ▼                              ▼
   empleadores                       telcel
   (162k empresas)                  (9.7M líneas)
   catálogo contactos              plan +ESN + IMEI
```

## Tablas / archivos a importar en el sistema de consultas

Toda la integración se hace copiando el `*.duckdb` a un directorio del
servidor bots. Los scripts SQL de abajo asumen que el archivo vive en
`<DATASETS>/bases_consolidadas/`.

### 1. Definir ATTACH de cada DuckDB

```sql
-- cargar todas como bases de sólo lectura en una sola sesión DuckDB
ATTACH '/srv/bots/datasets/bases_consolidadas/att/duckdb/att.duckdb'               AS att             (READ_ONLY);
ATTACH '/srv/bots/datasets/bases_consolidadas/repuve/duckdb/repuve.duckdb'         AS repuve          (READ_ONLY);
ATTACH '/srv/bots/datasets/bases_consolidadas/imss_asegurados/duckdb/imss_asegurados.duckdb' AS imss_a (READ_ONLY);
ATTACH '/srv/bots/datasets/bases_consolidadas/imss_segmentacion/duckdb/imss_segmentacion.duckdb' AS imss_s (READ_ONLY);
ATTACH '/srv/bots/datasets/bases_consolidadas/telcel/duckdb/telcel.duckdb'         AS telcel          (READ_ONLY);
ATTACH '/srv/bots/datasets/bases_consolidadas/empleadores/duckdb/empleadores.duckdb' AS empleadores (READ_ONLY);
```

### 2. Vistas de enriquecimiento en la API

```sql
-- 2.1 Teléfonos y direcciones ATT vinculados a un ciudadano por RFC
-- (atención: ATT tiene sólo RFC PF13/PF10, no CURP — usar cross-walk RFC)
CREATE VIEW IF NOT EXISTS api.att_persona AS
SELECT
    a.rfc_clean        AS rfc,
    a.nombres,
    a.tel1,
    a.celular,
    a.direccion,
    a.colonia,
    a.municipio,
    a.estado
FROM att.att a
WHERE a.rfc_kind IN ('PF13','PF10');

-- 2.2 Vehículos a nombre de una persona (vía RFC)
CREATE VIEW IF NOT EXISTS api.repuve_de_persona AS
SELECT
    r.rfc_clean          AS rfc,
    r.placa,
    r.no_serie,
    r.marca,
    r.tipo,
    r.modelo_int         AS modelo,
    r.color,
    r.nom_prop_fix       AS nom_prop,
    r.dir_prop_fix       AS dir_prop
FROM repuve.repuve r
WHERE r.rfc_kind IN ('PF13','PM12','PF10','PM10');

-- 2.3 Patrones IMSS en los que aparece una persona
CREATE VIEW IF NOT EXISTS api.imss_asegurado AS
SELECT
    p.curp_clean         AS curp,
    p.nombre,
    p.nss_clean          AS nss,
    p.cp5,
    p.ciudad_estado,
    p.nombre_patron,
    p.registro_patron,
    p.sueldo_raw         AS sueldo
FROM imss_a.imss_2025 p
WHERE p.curp_kind='PF18';

-- 2.4 Segmentación de salud (riesgo cáncer/diabetes/hipertensión)
CREATE VIEW IF NOT EXISTS api.imss_salud AS
SELECT
    s.curp_clean,
    s.id_persona,
    s.edad,
    s.genero,
    s.ooad,
    s.unidad_medica,
    s.segmentacion_cancer_de_mama,
    s.segmentacion_cancer_de_prostata,
    s.segmentacion_diabetes_mellitus,
    s.segmento_hipertension,
    s.desc_enfermedad_diabetes,
    s.desc_enfermedad_hipertension
FROM imss_s.imss_personas s
WHERE s.curp_kind='PF18';

-- 2.5 Telcel: líneas activas asociadas a un RFC
CREATE VIEW IF NOT EXISTS api.telcel_lineas AS
SELECT
    t.rfc_clean          AS rfc,
    t.telefono,
    t.plan_actual,
    t.marca,
    t.modelo,
    t.st_tel,
    t.nombre1,
    t.nombre2
FROM telcel.telcel t
WHERE t.rfc_kind IN ('PF13','PM12','PF10','PM10');

-- 2.6 Catálogo de empresas (empleadores)
CREATE VIEW IF NOT EXISTS api.empleadores AS
SELECT
    e.rfc_clean          AS rfc,
    e.razonSocial,
    e.nombreComercial,
    e.numeroEmpleados,
    e.ubicacion.entidad,
    e.ubicacion.municipio,
    e.ubicacion.colonia,
    e.ubicacion.codigopostal,
    e.correoElectronico,
    e.paginaWeb
FROM empleadores.empleadores e
WHERE e.rfc_kind IN ('PM12','PM10');
```

### 3. Endpoint de búsqueda unificada

```sql
-- v_persona_unificada — une padrón INE con las 6 capas por curp o rfc
-- Une primero por curp_clean (cuando hay), luego complementa con rfc_clean.
CREATE OR REPLACE VIEW api.v_persona_unificada AS
WITH
ine AS (
    SELECT curp, nss, nombre_completo, fecha_nacimiento
    FROM main.padron_ine  -- el padrón ya existente en el sistema
),
att_join AS (
    SELECT a.rfc_clean, a.nombres, a.celular, a.colonia, a.municipio, a.estado
    FROM att.att a WHERE a.rfc_kind IN ('PF13','PF10')
),
rfc_xwalk AS (
    -- tabla canónica precomputada: rfc_clean -> curp_clean (cuando exista mapeo)
    -- cargar de un dataset anexo si se tiene; si no, se omite este join
    SELECT rfc_clean, curp_clean FROM rfc_curp_xwalk.rfc_curp_xwalk
)
SELECT
    ine.curp,
    ine.nss,
    ine.nombre_completo,
    att_join.celular,
    att_join.colonia,
    att_join.municipio,
    att_join.estado,
    (SELECT COUNT(*) FROM repuve.repuve r
        JOIN rfc_xwalk x ON x.rfc_clean = r.rfc_clean
        WHERE x.curp_clean = ine.curp) AS vehiculos_count,
    (SELECT COUNT(*) FROM imss_a.imss_2025 m WHERE m.curp_clean = ine.curp) AS patrones_count,
    (SELECT COUNT(*) FROM imss_s.imss_personas s
        WHERE s.curp_clean = ine.curp
          AND (s.segmentacion_diabetes_mellitus IS NOT NULL
            OR s.segmento_hipertension IS NOT NULL)
    ) > 0 AS tiene_segmentacion_salud,
    (SELECT COUNT(*) FROM telcel.telcel t
        JOIN rfc_xwalk x ON x.rfc_clean = t.rfc_clean
        WHERE x.curp_clean = ine.curp) AS lineas_telefonicas_count,
    (SELECT LIST(DISTINCT e.razonSocial)
        FROM empleadores.empleadores e
        JOIN rfc_xwalk x ON x.rfc_clean = e.rfc_clean
        WHERE x.curp_clean = ine.curp)     AS empleadores_conocidos
FROM ine;
```

(Tabla `rfc_curp_xwalk` es externa al bundle — se debe poblar aparte
cuando se tenga. Mientras tanto, las subconsultas se pueden comentar
sin perder funcionalidad, y las 5 bases que ya tienen `curp_clean`
poblada se usan directamente.)

## Pendientes para que esto funcione end-to-end

1. **Tabla `rfc_curp_xwalk`** — mapa RFC → CURP (de SAT, INE, o derivado
   de los propios datasets donde ambas columnas coexisten). Sin esto, las
   uniones RFC→CURP fallan. Está disponible en att.duckdb (1.04M RFC con
   nombre), repuve.duckdb (1.6M RFC), e imss_segmentacion.duckdb (1.87M
   RFC PF + 18.3M RFC PM). Se puede construir un xwalk parcial vía los
   patrones de imss_asegurados.

2. **Carga inicial** — copiar bases_consolidadas.tar.gz al servidor
   bots y descomprimir bajo `/srv/bots/datasets/`.

3. **DDL ejecutable** — empaquetar los `ATTACH` + `CREATE VIEW` arriba
   como `init_extended_sources.sql` y correrlo una sola vez.

4. **API Python** — si tu backend es Flask/FastAPI, añadir un endpoint
   `/api/v1/persona/<curp>` que ejecute la vista `v_persona_unificada`
   con DuckDB en modo lectura (polars conecta el parquet si el DuckDB
   no es accesible).

## Estimación de almacenamiento en el servidor bots

| Componente | Tamaño en disco |
|---|---|
| DuckDB (6 archivos)         | ~13 GB |
| Parquet full (6 archivos)    |  ~5 GB |
| Parquet particionados (1,609 archivos) |  ~3 GB |
| **Total**                   | **~21 GB** |
| Tar.gz de todo el bundle (estimado, gzip -9) | **~8-10 GB** |

## Comandos SSH para desplegar

(Esto no se ejecuta en esta planeación — son los comandos que el
operador correrá en el servidor bots.)

```sh
# en el servidor bots
mkdir -p /srv/bots/datasets
cd /srv/bots/datasets
# recibir vía scp:
#   bases_consolidadas.tar.gz
tar xzf bases_consolidadas.tar.gz
cd bases_consolidadas
ls -la  # debe contener los 6 subdirectorios

# opcional: instalar CLI de DuckDB
pip install duckdb

# ejecutar el DDL inicial (debe existir init_extended_sources.sql en el bundle)
python -c "
import duckdb
con = duckdb.connect('/srv/bots/padron/padron_main.duckdb')  # tu DB principal
con.execute(open('init_extended_sources.sql').read())
print('init ok')
con.close()
"
```
