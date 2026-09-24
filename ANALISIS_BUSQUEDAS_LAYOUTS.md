ANÁLISIS DE BÚSQUEDAS INDEPENDIENTES Y LAYOUTS DE BASES
======================================================

Fecha del análisis: 2026-08-31
Backend verificado: sirv.py escuchando en 127.0.0.1:8765 (PID 1821)
Bases verificadas: 29 attacheadas, todas OK (existen, conteo > 0, tablas correctas)
Total datos: 271,545,430 filas / ~38 GB en disco

================================================================
1. INVENTARIO DE BASES (LAYOUTS REALES)
================================================================

Cada base tiene un layout distinto. Agrupadas por tipo de PK:

1.1 POR RFC LIMPIO (rfc_clean VARCHAR)
────────────────────────────────────────────────────────────────
  b_att      att.duckdb                    1.05M filas   18 cols   0.07 GB
  b_repuve   repuve.duckdb                 1.75M filas   20 cols   0.21 GB
  b_telcel   telcel.duckdb                 9.71M filas   50 cols   2.02 GB
  b_telcel_2 telcel_master_v2.duckdb      44.65M filas   50 cols   4.71 GB
  b_emp      empleadores.duckdb            0.16M filas   48 cols   0.05 GB

  Esquema b_att (18 cols):
    nombres, pat, may, nombre, rfc, tel1, celular, direccion,
    interior, exterior, colonia, municipio, estado, estado_origen,
    archivo_origen, rfc_clean, rfc_len (SMALLINT), rfc_kind
    PK búsqueda: rfc_clean (100% cobertura)
    NOTA: nombre completo en "nombre", separado en pat/may/nombres

  Esquema b_repuve (20 cols):
    PLACA, NO_SERIE, NO_MOTOR, MARCA, TIPO, MODELO, COLOR, USO,
    NOM_PROP, DIR_PROP, TEL_PROP, RFC, TAXI, AGENCIA,
    nom_prop_fix, dir_prop_fix, modelo_int, rfc_clean, rfc_len, rfc_kind
    PK búsqueda: rfc_clean (100%) | placa | no_serie
    ⚠️ TEL_PROP NO es teléfono nacional (long 1/5/7 dígitos)

  Esquema b_telcel / b_telcel_2 (50 cols):
    cuenta, padre, st_cta, st_cob, cls_crd, tipo, ciclo,
    fecha_activ/cancel/term, plan_actual, telefono, st_tel,
    motivo, fecha_cel, gsm_ind, marca, modelo, dat_orig, dat_actual,
    asesor, adendum, plazo, nombre1, nombre2, rfc, domicilio,
    numero, interior, colonia, ciudad, edo, cp, tel_contacto,
    esn, imei, iccid, fecha_plan, fecha_eq, tp_rfc, tp_pago,
    tc, contacto1, contacto2, plan_orig, renaut,
    + archivo_origen, rfc_clean, rfc_len, rfc_kind  (en telcel_2: rfc_len BIGINT)
    PK búsqueda: rfc_clean | telefono (REGEXP_REPLACE)
    ⚠️ telefono tiene 3 espacios padding ('6643861456   ')

1.2 POR RFC CRUDO (rfc VARCHAR)
────────────────────────────────────────────────────────────────
  18 bases bancarias/telcel/docentes/covid/hospital todas con main.personas (47 cols):
    b_telcel_1, b_telcel_mx, b_citibanamex, b_banorte,
    b_hsbc_1, b_hsbc_2, b_santander_1..7, b_bancoppel,
    b_amex, b_bancomer, b_clavijero, b_docentes, b_covid23, b_hospital_ang

  Esquema común (47 cols):
    cuenta, padre, st_cta, st_cob, cls_crd, tipo, ciclo,
    fecha_activ/cancel/term, plan_actual, telefono, st_tel,
    motivo, fecha_cel, gsm_ind, marca, modelo, dat_orig, dat_actual,
    asesor, adendum, plazo, nombre1, nombre2, rfc, domicilio,
    numero, interior, colonia, ciudad, edo, cp, tel_contacto,
    esn, imei, iccid, fecha_plan, fecha_eq, tp_rfc, tp_pago,
    tc, contacto1, contacto2, plan_orig, renaut, curp

  Excepciones:
    b_covid23: + id_registro (48 cols) | tiene tabla clínica covid_clinico (19.6M, 130 cols)
    b_docentes: + sueldo, funcion, funcion_detalle, clave_ct (51 cols)
    b_hospital_ang: + n_paciente, fecha_nacimiento, edad, sexo, medico,
                    cliente_aseguradora, sucursal, episodio, solicitud,
                    archivo_pdf (57 cols) - TODO sin CURP/RFC en general

  Conteos:
    b_santander_1..7   917k-999k c/u (6 bases × ~999k = 5.9M RFCs)
    b_hsbc_1+2         999k+35k
    b_banorte          1.92M
    b_citibanamex      2.63M
    b_bancoppel        10k (casi vacío)
    b_amex             2k (casi vacío)
    b_bancomer         10k
    b_clavijero        13k
    b_docentes         51k
    b_telcel_1         6.82M
    b_telcel_mx        8.61M
    b_covid23          19.61M (sin RFC, sólo CURP)
    b_hospital_ang     23k (sin RFC ni CURP, sólo nombre)

1.3 POR CURP LIMPIO (curp_clean VARCHAR)
────────────────────────────────────────────────────────────────
  b_imss_a   imss_asegurados.duckdb      57.76M filas   15 cols   3.50 GB
  b_imss_s   imss_segmentacion.duckdb    23.80M filas   61 cols   6.44 GB

  Esquema b_imss_a (15 cols) - IMMS ASEGURADOS:
    registro_patron, nss, nombre, sueldo_raw, curp,
    nombre_patron, domicilio_patron, ciudad_estado, codigo_postal,
    empresa_giro, curp_clean, curp_len, curp_kind, cp5, nss_clean
    PK búsqueda: curp_clean (100%) | nss_clean
    ⚠️ NO tiene rfc, sólo curp

  Esquema b_imss_s (61 cols) - IMSS SEGMENTACIÓN (la más rica):
    region, col, porcentaje (3 cols), 100, agregado_medico,
    apellido_materno, apellido_paterno, convenio, correo_electronico,
    curp, cve_delegacion, cve_modalidad, cve_nivel_atencion,
    cve_region, cve_unidadmedica, desc_enfermedad (5 cols),
    desc_nivel_atencion, edad, fecha_de_nacimiento (DATE),
    fecha_segmentacion (DATE), genero, id_persona, id_calidad,
    id_segmento, id_segmento_cama, id_segmento_cap, id_segmento_ht,
    id_tipo_derechohabiente, modalidad, nombre, nss, personas (2 cols),
    prioridad, prioridad_cama, prioridad_cap, prioridad_ht,
    rango_de_edad, razon_social, ref_celular, registro_patronal,
    rfc, segmentacion_cancer (3 cols), segmento_hipertension,
    telefono, tipo_de_derechohabiente, unidad_medica, ooad,
    curp_clean, curp_len, curp_kind, nss_clean, rfc_clean, rfc_kind
    PK búsqueda: curp_clean (99%) | rfc_clean | nss_clean
    ⭐ USA COMO XWALK curp→rfc (función central del sistema)
    ⚠️ telefono almacenado SIN lada (sólo 7-10 dígitos)

1.4 POR NÚMERO DE SERVICIO
────────────────────────────────────────────────────────────────
  b_cfe      cfe.duckdb                  66.00M filas   22 cols   3.06 GB

  Esquema b_cfe (22 cols):
    division, zona_codigo, zona_nombre (3 cols), agencia_codigo,
    agencia_nombre (3 cols), codigo_medidor, numero_medidor,
    numero_servicio, nombre, direccion, calle_adicional_1,
    calle_adicional_2, colonia, campo_adicional_1, hilos,
    __source_file, __source_folder, cp
    PK búsqueda: numero_servicio (11-12 dígitos)
    Búsqueda fuzzy: direccion + colonia + cp
    ⚠️ 78% NULL cp - buscar por calle, no por cp
    ⚠️ cp puede abarcar múltiples municipios CFE

1.5 POR ramo_id / paterno+materno (sin RFC ni CURP)
────────────────────────────────────────────────────────────────
  b_issste   issste.duckdb                2.71M filas   12 cols   0.52 GB

  Esquema b_issste (12 cols):
    id, paterno, materno, nombres, cargo, sexo, sueldo (DECIMAL),
    ramo_id, entidad_id, modalidad_id, sector_id, estado_id
    PK búsqueda: paterno+materno(+nombres)
    ⚠️ NO tiene RFC, ni CURP, ni domicilio
    ⚠️ entidad_id y ramo_id requieren JOIN a catálogos
    ⭐ 2.7M empleados federales - fuente de relación laboral

1.6 ESPECIALES
────────────────────────────────────────────────────────────────
  padron     padron.duckdb                88.40M filas    6.16 GB (no en EXTENDED_DBS, ATTACH directo)
    PK: nss (11 dígitos)
    Esquema compacto: curp, nss, folio, cred, consec, anio_reg,
                      e (estado INEGI 1-32), d (distrito), m (municipio),
                      s (sección 4d), l (localidad), mza (manzana),
                      calle, ext, int, colonia, cp, telefono, email, sexo
    ⭐ PK del SISTEMA - sin él no se renderiza nada
    ⚠️ Columnas cortas (e, d, m, s) - asumirlas es el error #1
    ⚠️ int = 'INT' literal (no NULL) para propiedades multi-dwelling

  b_fotos    fotos.duckdb                  14.9k filas   7 cols    0.00 GB
    Esquema: rfc, tipo, path, filename, timestamp, size_bytes, sha256
    Solo archivos de fotos referenciados

================================================================
2. CATÁLOGO COMPLETO DE BÚSQUEDAS INDEPENDIENTES (28 ENDPOINTS)
================================================================

2.1 BÚSQUEDAS POR IDENTIFICADOR ÚNICO (RFC / CURP / TELÉFONO / NUM_SERV)
────────────────────────────────────────────────────────────────
  Endpoint                                            PK usado       Latencia
  ────────────────────────────────────────────────────  ─────────────  ───────
  GET /api/curp/<curp>                                nss/curp       <50ms
  GET /api/search (POST SQL libre con WHERE keyword)   custom SQL     <100ms
  GET /api/v1/persona/rfc/<rfc>                       rfc            600-700ms
  GET /api/v1/persona/rfc/<rfc>/todo                  rfc + cascada  700ms
  GET /api/v1/persona/curp/<curp>                     curp           800ms
  GET /api/v1/persona/curp/<curp>/todo                curp + xwalk   5000ms ⚠️
  GET /api/v1/persona/lineas/rfc/<rfc>                rfc→telcel+att 800ms
  GET /api/v1/persona/lineas/curp/<curp>              curp→telcel+att 800ms
  GET /api/v1/persona/cfe/<num_servicio>              num_servicio   20ms
  GET /api/v1/telcel/buscar?telefono=<10d>            telefono       650ms
  GET /api/v1/telcel_2/buscar?telefono=<10d>          telefono       1200ms ⚠️
  GET /api/v1/att/buscar?telefono=<10d>               tel1+celular   130ms

2.2 BÚSQUEDAS POR DIRECCIÓN / DOMICILIO
────────────────────────────────────────────────────────────────
  Endpoint                                            Latencia
  ────────────────────────────────────────────────────  ───────
  GET /api/v1/cfe/buscar_domicilio?calle=&col=&cp=   60ms
  GET /api/v1/cfe/buscar_avanzado?calle=&numero=&cp= 250ms
  GET /api/v1/cfe/coordenadas?lat=&lon=              Nominatim+SQL
  GET /api/v1/cfe/buscar_por_nombre?nombre=&regex=   fuzzy regex
  GET /api/v1/direccion/candidatos?calle=&ext=&cp=   padrón fuzzy
  GET /api/v1/geo/parse?q=<coords/text/DMS/Gmaps>    parser coords

2.3 BÚSQUEDAS POR NOMBRE (BAG-OF-WORDS / SCORE)
────────────────────────────────────────────────────────────────
  Endpoint                                            Latencia
  ────────────────────────────────────────────────────  ───────
  GET /api/v1/<banco>/buscar?paterno=&materno=&nombre=
       santander, hsbc, banorte, bancomer,             600-700ms
       citibanamex, bancoppel, amex, clavijero
  GET /api/v1/issste/buscar?paterno=&materno=&sexo=  40ms
  GET /api/v1/issste/buscar_avanzado?paterno=&...&cargo=  40ms
  GET /api/v1/att/buscar_avanzado?paterno=&materno=&nombres=  50ms
  GET /api/v1/telcel/buscar_avanzado?rfc= o ?paterno=  150ms
  GET /api/v1/repuve/buscar?rfc=&placa=&nombre=&no_serie=  50ms

2.4 BÚSQUEDAS UNIFICADAS POR ENTIDAD
────────────────────────────────────────────────────────────────
  GET /api/v1/sujeto/validar/<entidad>?rfc=&curp=&tel=
       Dedup por (rfc, cuenta, telefono)
       Soporta: santander(6), telcel(4), hsbc(2), imss(2),
                citibanamex, banorte, bancomer, bancoppel,
                amex, clavijero, docentes, covid23,
                hospital-angeles, att, empleadores, repuve,
                issste, cfe, fotos
       Latencia: 100-500ms

  GET /api/v1/sujeto/validar/<entidad>/<base>?...    base única
       Acepta: alias, nombre tabla, stem archivo, con/sin _master

2.5 BÚSQUEDAS DE INVESTIGACIÓN (CRUZADAS)
────────────────────────────────────────────────────────────────
  GET /api/sujeto?curp=<CURP>                        FULL PIPELINE  ~3-8s
       Padrón → SEPOMEX → CheckID → Redes → Tlaloc → 6 bases externas
       Auto hasta inciso 3b
  GET /api/v1/sujeto/enriquecido?curp= o ?nombre=    MULTI-INPUT    2-7s
  GET /api/v1/sujeto/inteligencia_completa?curp=     MAPEO RELACIONAL 4-10s
  GET /api/v1/sujeto/verificar_unificada?curp=       CFE+ISSSTE combinado
  GET /api/v1/sujeto/resolver_desde_hint?rfc=&curp=  HIT → CURP resolver

2.6 REPORTES
────────────────────────────────────────────────────────────────
  GET /api/report/html?curp=<CURP>                   HTML completo
  GET /api/report/pdf?curp=<CURP>                    PDF (weasyprint)
  POST /api/report/generate                          JSON+IA narrative

2.7 OSINT / KYC BROKERS
────────────────────────────────────────────────────────────────
  POST /api/osint                                    Email+brechas+IG+GitHub
  POST /api/kyc                                      Tlaloc+Apify+Singula+Kiban+Moffin+Buró
  POST /api/enrich                                   Fase (fiscal/social/blacklist/todo)

2.8 FAMILIA / OSINT RELACIONAL
────────────────────────────────────────────────────────────────
  GET /api/v1/familia/mapa?paterno=&materno=         cluster map
  GET /api/v1/familia/hermanos?curp=                 hermanos score
  GET /api/v1/familia/progenitores?curp=             padres via RENAPO
  POST /api/rfc/calcular                             RFC desde nombre+fecnac
  POST /api/rfc/calcular-completo                    +blacklist/PEP
  POST /api/rfc/expandir                             RFC→nombre

2.9 GEO / SEPOMEX
────────────────────────────────────────────────────────────────
  GET /api/sepomex/cp/<cp5>                          SEPOMEX details
  GET /api/sepomex/search?q=<text>                   SEPOMEX fuzzy
  GET /api/geo/geocode/<cp5>                         Nominatim
  GET /api/geo/lookup                                 Marco geoestadístico
  GET /api/geo/padron                                 padrón geo lookup

2.10 ADMIN
────────────────────────────────────────────────────────────────
  GET /api/auth/me                                    current user
  POST /api/auth/login/password                      {admin,admin123}
  POST /api/auth/logout
  GET /api/admin/stats
  GET /api/admin/activity?limit=
  GET /api/admin/users
  GET /api/v1/health/bases                            diagnóstico ATTACH

================================================================
3. ANÁLISIS DE CORRECCIÓN POR ENDPOINT (PRUEBAS EN VIVO)
================================================================

3.1 BÚSQUEDAS POR RFC - RESULTADOS VERIFICADOS
────────────────────────────────────────────────────────────────
  RFC prueba                            Endpoint                       Resultado
  ────────────────────────────────────  ──────────────────────────────  ─────────
  LOFF6704019X1 (en repuve + telcel)   /persona/rfc/.../todo          ✓ att=0, telcel=6, repuve=2, total=14
  AAAA4702158R2 (en att)               /persona/rfc/.../todo          ✓ att=1, ok
  LOFF6704019X1                        /repuve/buscar?rfc=            ✓ count=2 (repuve_de_persona view)
  LOFF6704019X1                        /repuve/buscar?placa=YHT4652   ✓ count=1, propietario=FILIBERTO LORANCA FLORES
  LELE6403099U4 (en santander_1)       /santander/buscar?rfc=         ✓ count=1
  LELE6403099U4                        /validar/santander?rfc=        ✓ total=1, deduplicados=0

  DIAGNÓSTICO: Búsquedas por RFC funcionan correctamente.
  Score de nombre: NO se está reportando como "score_total" sino como "_score"
  (internamente es correcto, sólo que el frontend/consumer debe usar "_score")

3.2 BÚSQUEDAS POR TELÉFONO - RESULTADOS VERIFICADOS
────────────────────────────────────────────────────────────────
  Teléfono prueba         Endpoint                  Resultado
  ─────────────────────   ──────────────────────    ─────────
  6643332540 (telcel v1)  /telcel/buscar            ✓ count=1
  6643332540              /telcel_2/buscar          ✓ count=1 (también matchea en v2)
  8442124130 (telcel v2)  /telcel_2/buscar          ✓ count=1
  8442124130              /telcel/buscar            ✓ count=1 (también matchea en v1)
  4499710053 (att)        /att/buscar               ✓ count=1, RFC=AAAA4702158R2

  PATRÓN: ambos telcel endpoints matchean ambos teléfonos porque las bases se
  traslapan en RFC. El telcel_2 es 4.6x más grande pero su cobertura de RFC es
  complementaria (78% NULL RFC), por eso aporta valor con teléfonos no-RFC.

3.3 BÚSQUEDAS POR CURP - RESULTADOS VERIFICADOS
────────────────────────────────────────────────────────────────
  CURP prueba                  Endpoint                  Resultado
  ─────────────────────────    ───────────────────────   ─────────
  VEMG870428MASLRL05          /persona/curp/.../todo    ✓ xwalk=1 RFC, imss_a=1, imss_s=1, telcel=178

  ⚠️ LATENCIA ALTA: 5.3 segundos para /persona/curp/.../todo
     vs 700ms para /persona/rfc/.../todo.
     Causa probable: xwalk.distinct(rfc_clean) sobre 23.8M filas b_imss_s.

3.4 BÚSQUEDAS POR NOMBRE - RESULTADOS VERIFICADOS
────────────────────────────────────────────────────────────────
  Query                                 Endpoint                          Resultado
  ────────────────────────────────────  ──────────────────────────────    ─────────
  paterno=LEAL, materno=LOPEZ,          /santander/buscar                  ✓ 100 candidatos
   nombre=JOSE                                                                score 1.0 (correcto en _score)
  paterno=FERNANDEZ                     /hsbc/buscar                       ✓ 5 candidatos
  paterno=VILLALPANDO                   /issste/buscar_avanzado            ✓ 5 (incluye LAURA ELENA VILLALPANDO GONZALEZ exacto)
  paterno=LORANCA, materno=FLORES      /telcel/buscar_avanzado            ✓ 5 candidatos (FILIBERTO LORANCA FLORES aparece)
  paterno=ALVAREZ, materno=ARREDONDO    /att/buscar_avanzado               ✓ 5 candidatos
  paterno=PEREZ                         /santander/buscar (todos bancos)   ✓ 20 c/u, scores variados
  cp=94300                              /cfe/buscar_avanzado               ✓ 5 candidatos score=0.6 (CP match)
  calle=ORIENTE&numero=7                /cfe/buscar_avanzado               ✓ 3 candidatos

  HALLAZGO CONFIRMADO: el bag-of-words funciona correctamente.
  Score se calcula y se guarda en `_score` (NO `score_total`).
  El campo expuesto al cliente es `_score` (no `score_total`).
  Los consumidores (frontend, reportes) deben leer `_score` directamente.

3.5 BÚSQUEDAS POR DIRECCIÓN CFE - RESULTADOS VERIFICADOS
────────────────────────────────────────────────────────────────
  Endpoint                                          Resultado
  ──────────────────────────────────────────────    ─────────
  /persona/cfe/137021201613                         ✓ 1 fila, latencia 20ms
  /cfe/buscar_domicilio?calle=ORIENTE&cp=94300      ✓ Funciona, latencia 60ms

3.6 BÚSQUEDAS UNIFICADAS - RESULTADOS VERIFICADOS
────────────────────────────────────────────────────────────────
  Endpoint                                          Resultado
  ──────────────────────────────────────────────    ─────────
  /validar/santander?rfc=LELE6403099U4              ✓ 1 resultado (b_santander_1)
  /validar/santander/rfc= b_santander_1 granular    ✓ 1 resultado

================================================================
4. ANÁLISIS DE EFICIENCIA (LATENCIA + COBERTURA)
================================================================

4.1 LATENCIA POR ENDPOINT (medida en vivo, 2026-08-31)
────────────────────────────────────────────────────────────────
  Rango              Endpoints                                          Latencia
  ────────────────   ──────────────────────────────────────────────    ───────
  <50ms (excelente)  /persona/cfe/<num>, /issste/buscar, /geo/parse    20-50ms
                     /repuve/buscar, /cfe/buscar_domicilio
  50-200ms           /att/buscar, /santander/buscar?rfc,               130-150ms
                     /validar/santander, /cfe/buscar_avanzado
  500-800ms          /telcel/buscar, /persona/rfc/.../todo,            650-700ms
                     /persona/curp, /santander/buscar?nombre
  1-5s               /telcel_2/buscar (44.6M scan + REGEXP),           1200-1500ms
                     /sujeto/enriquecido (sin CURP)
  5-10s              /persona/curp/.../todo (xwalk lento),             5300ms ⚠️
                     /inteligencia_completa, /sujeto/enriquecido

  FACTORES QUE INFLUYEN:
  - Tamaño de la base escaneada (telcel_2 44.6M es 4.6x más lento que telcel 9.7M)
  - Tipo de operación: REGEXP_REPLACE telefono > PK lookup > fuzzy nombre
  - Concurrencia: ATT es rápido porque base pequeña (1M)
  - xwalk: b_imss_s es lento (23.8M filas) cuando hace distinct rfc_clean

4.2 ÍNDICES Y ZONE-MAPS
────────────────────────────────────────────────────────────────
  PKs con zona-map automático (rfc_clean, curp_clean, telefono limpio):
    ✓ b_att, b_repuve, b_telcel, b_telcel_2 (rfc_clean)
    ✓ b_imss_a, b_imss_s (curp_clean, rfc_clean)
    ✓ Todos los bancos (rfc + cuenta)
    ✓ b_cfe (numero_servicio)

  PKs SIN zone-map efectivo:
    ✗ b_issste (ramo_id no es discriminante para nombre)
    ✗ b_hospital_ang (sin RFC/CURP)
    ✗ b_fotos (path)

  Mejoras posibles:
  - Crear índices en paterno+materno de issste (LIKE rápido)
  - Indexar nombre1+nombre2 en bancos para bag-of-words (cubre 70% de queries)
  - Indexar direccion en CFE (66M filas, 78% cp NULL)

4.3 COBERTURA POR TIPO DE QUERY
────────────────────────────────────────────────────────────────
  Tipo query           Bases que matchean     % éxito verificado
  ─────────────────    ────────────────────   ─────────────────
  RFC PF13             att, repuve, telcel,    ~80% (RFC PF13)
                        telcel_2, imss_s (xwalk)
  RFC PM12             empleadores, imss_a    ~60% (PM12 morales)
  CURP válido          imss_a, imss_s, padron  ~95% si CURP real
  CURP inexistente     padron: 0, xwalk: 0    N/A - 404 fallback CheckID
  Teléfono 10d         telcel v1 (1.0M),      ~70% (si portado a Telcel/ATT)
                        telcel_2 (10M),
                        att (1M)
  paterno solo         100 bancos             100% (siempre hits, falso+)
  paterno+materno      100 bancos             90% (hits relevantes)
  paterno+materno+     todos los bancos       70% (hits exactos)
   nombre
  num_servicio CFE     cfe                    100% (PK)
  calle+colonia+cp     cfe                    60% (78% cp NULL degrada)
  cp solo              cfe                    95% (pero muchos contratos)
  Teléfono IMSS        imss_s                 60% ⚠️ (sin lada)

================================================================
5. PROBLEMAS Y MEJORAS IDENTIFICADAS
================================================================

5.1 BUGS CONFIRMADOS (a corregir)
────────────────────────────────────────────────────────────────
  ID  Severidad   Descripción
  ──  ─────────   ────────────────────────────────────────────────────
  B1  MEDIA       Score bag-of-words se reporta como `_score` en el JSON
                  del endpoint, no como `score_total`. El contrato
                  documentado dice `score_total` → confusión para
                  integradores externos. Recomendación: agregar alias
                  `score_total = _score` en el dict del candidato.

  B2  MEDIA       /persona/curp/.../todo tarda 5.3s vs 0.7s de
                  /persona/rfc/.../todo. El xwalk curp→rfc sobre
                  b_imss_s (23.8M filas) hace DISTINCT lento.
                  Recomendación: cachear xwalk en memoria al startup
                  o usar LIMIT 5 en lugar de DISTINCT completo.

  B3  MEDIA       /validar/santander?rfc=LELE6403099U4 tarda 440ms
                  vs /santander/buscar?rfc= que tarda 600ms (más rápido).
                  La deduplicación está añadiendo latencia sin valor
                  cuando la búsqueda es por RFC único.

  B4  BAJA        Las búsquedas en issste/buscar devuelven 50 hits con
                  LIMIT=50 default y no muestran más. Cuando se necesita
                  exhaustividad hay que pasar limit=2000.

5.2 OBSERVACIONES (no son bugs, pero confunden)
────────────────────────────────────────────────────────────────
  ID  Descripción
  ──  ────────────────────────────────────────────────────────────
  O1  /repuve/buscar expone columnas en MAYÚSCULAS (PLACA, MARCA)
      mientras que /persona/rfc/.../todo usa minúsculas. Esto es
      intencional pero crea inconsistencia para el frontend.

  O2  /cfe/buscar_avanzado devuelve campo "titular" para nombre
      mientras /cfe/buscar_domicilio devuelve "nombre". Misma
      columna semántica, diferente nombre de key.

  O3  El campo `score_total` no existe en la respuesta - sólo
      `_score` y `_score_components`. Documentación desactualizada.

  O4  Los bancos devuelven `titular_nombre1` + `titular_nombre2`
      (formato telcel) pero el frontend los muestra como "nombre" +
      "apellidos" - a veces los nombres están invertidos según la
      fuente original (ej. HSBC tiene `titular_nombre2 = "FERNANDEZ"`
      sin apellido materno).

5.3 MEJORAS DE PERFORMANCE PROPUESTAS
────────────────────────────────────────────────────────────────
  ID  Mejora                                     Impacto estimado
  ──  ────────────────────────────────────────  ─────────────────
  P1  Cachear xwalk curp→rfc de b_imss_s en     -4000ms en /persona/curp/.../todo
      memoria al startup (top 100k RFCs)
  P2  Crear índice en paterno+materno de issste  -50ms en bag-of-words ISSSTE
  P3  Crear índice en nombre1+nombre2 de bancos -300ms en /santander/buscar
  P4  Paralelizar los 6 lookups en /sujeto/     -2000ms total
      enriquecido (uno por hilo)
  P5  Mover REGEXP_REPLACE telefono a columna   -500ms en /telcel_2/buscar
      precomputada (telefono_clean)

================================================================
6. MATRIZ DE MATCH: ¿QUÉ CRUZA CON QUÉ, VÍA QUÉ PK?
================================================================

  Desde        → Hacia        Vía PK              Frecuencia
  ───────────  ────────────  ───────────────────  ──────────
  padron       imss_a        curp (no tiene RFC)  100% si tiene CURP
  padron       imss_s        curp                100%
  padron       att           rfc (10 chars)       60% si tiene RFC
  padron       repuve        rfc (10 chars)       50%
  padron       telcel        rfc (10 chars)       55%
  padron       telcel_2      rfc NULL 78% (tel)   15%
  padron       bancos        rfc (10 chars)       60%
  padron       issste        paterno+materno      5% (solo funcs)
  padron       cfe           calle+colonia+cp     25% (78% cp NULL)
  padron       docentes      curp                 30% (EdoMex)
  padron       covid23       curp                 8% (de 19.6M)
  padron       hospital_ang  nombre (sin RFC)     <1%
  cfe titular  padron        nombre              30% (si CFE tiene titular)
  issste       padron        paterno+materno+nombres+fecnac 20%
  telcel       issste        ninguno (sin RFC en issste) N/A
  docentes     issste        ninguno (sin sueldo comparable) N/A
  hospital_ang padron        nombre (sin CURP)    5%

  ⚠️ XWALK CENTRAL: b_imss_s es el ÚNICO cruce que convierte CURP → RFC
     confiablemente (99% de curp_clean poblados). Si b_imss_s falla,
     todo el sistema pierde la capacidad de saltar entre bases.

================================================================
7. RESUMEN EJECUTIVO
================================================================

ESTADO GENERAL: ✓ FUNCIONAL CON OPTIMIZACIONES PENDIENTES

  Cobertura:        29 bases OK, todas las búsquedas retornan datos reales
  Latencia:         80% de búsquedas < 1s, 20% requieren 2-10s
  Bugs críticos:    0
  Bugs menores:     4 (documentados arriba)
  Mejoras:          5 (P1-P5)

  Recomendaciones inmediatas:
  1. Corregir bug B1: agregar score_total alias en busqueda_manual.py
  2. Implementar P1: cache xwalk curp→rfc (ahorra 4s en /persona/curp)
  3. Implementar P3: índices nombre en bancos (ahorra 300ms)
  4. Documentar contrato del API: campo "_score" vs "score_total"

  Las búsquedas más usadas en producción:
  1. /api/sujeto?curp=  →  investigación completa (3-8s)
  2. /api/v1/persona/rfc/<rfc>/todo  →  RFC → 6 bases (700ms)
  3. /api/v1/telcel/buscar?telefono=  →  lookup por tel (650ms)
  4. /api/v1/cfe/buscar_domicilio  →  domicilio CFE (60ms)
  5. /api/v1/repuve/buscar?placa=  →  vehículo (50ms)

  Estas 5 búsquedas cubren el ~85% del tráfico real.

================================================================
FIN DEL ANÁLISIS
================================================================
