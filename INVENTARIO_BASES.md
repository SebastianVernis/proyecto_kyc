================================================================================
INVENTARIO COMPLETO DE BASES DE DATOS — proyecto_kyc
================================================================================
Fecha: 2026-08-23
Estado: bases materializadas integradas al backend; el runtime adjunta 29 fuentes y expone vistas uniformes para las búsquedas por RFC/CURP/nombre.

| Total bases: 29 archivos DuckDB/SQLite de runtime attacheados + 4 archivos de infraestructura (sepomex, geo, auth, singula_cache) + directorios de trazabilidad (_catalog_nuevas, _logs) |
| Total datos: ~341.9M filas, ~38 GB en disco (post-limpieza 2026-08-24) |

================================================================================
SECCION 1: BASES ATTACHEADAS AL BACKEND (se consultan al renderizar el sujeto)
================================================================================

Las 29 bases de abajo se ATTACHEAN como READ_ONLY al arrancar el servidor
(servir.py, EXTENDED_DBS). Las vistas `api.*` normalizan los esquemas y los
handlers pueden consultar las fuentes clásicas, Telcel adicionales, bancarias,
docentes, COVID23 y Hospital Ángeles sin cambiar el endpoint.

--------------------------------------------------------------------------------
#   ALIAS        ARCHIVO                      FILAS         SIZE_GB   TIPO_PK
--------------------------------------------------------------------------------
1.  padron       padron.duckdb                88,402,547    6.16      nss
2.  att          att.duckdb                    1,048,575    0.07      RFC 100%
3.  empleados    empleadores.duckdb              161,933    0.05      RFC  95%
4.  repuve       repuve.duckdb                 1,745,627    0.21      RFC 100%
5.  imss_a       imss_asegurados.duckdb       57,760,242    3.50      CURP 100%
6.  imss_s       imss_segmentacion.duckdb     23,803,445    6.44      CURP  99%
7.  telcel       telcel.duckdb                 9,709,461    2.01      RFC 100%
8.  cfe          cfe.duckdb                   66,003,291    3.06      num_servicio
9.  issste       issste.duckdb                 2,706,651    0.52      ramo
10. fotos        fotos.duckdb                     14,942    0.00      curp
--------------------------------------------------------------------------------
TOTAL ATTACHEADAS:  251,356,714 filas,  ~22.0 GB
================================================================================


--------------------------------------------------------------------------------
1. padron (88.4M filas) — TABLA MAESTRA de identidad
--------------------------------------------------------------------------------
Archivo:    padron.duckdb (6.16 GB)
Tabla:     main.padron
PK:        nss (Número de Seguridad Social) — 11 dígitos
Origen:    Datos abiertos del padrón de IMSS, RENAPO, SAT consolidados
Cols:      curp, nombre, paterno, materno, fecnac, nss, domicilio, cp,
           municipio, estado, telefono, email, sexo, edad, etc.

Aporta al render del sujeto:
  - Identidad base (nombre completo, CURP, fecha de nacimiento)
  - Domicilio completo (calle, colonia, CP, municipio, estado)
  - Teléfono y email de contacto
  - RFC si está disponible (no todas las filas)
  - NSS para xwalk contra IMSS_S (b_imss_s)
  - Sin esto, no se renderiza nada — es la PK de la app

Usado por: /api/sujeto (endpoint principal)
           /api/curp/<curp> (búsqueda directa)
           /api/v1/persona/curp/<curp> (enriquecimiento)
           /api/v1/persona/rfc/<rfc> (vía xwalk)


--------------------------------------------------------------------------------
2. att (1.0M filas) — Líneas fijas y celulares de AT&T México
--------------------------------------------------------------------------------
Archivo:    att.duckdb (0.07 GB)
Tabla:     main.att
PK:        rfc_clean (100% cobertura)
Cols:      rfc, nombres, pat, may, tel1, tel2, celular, direccion,
           interior, exterior, colonia, municipio, estado, estado_origen

Aporta al render del sujeto:
  - Líneas fijas (tel1) y celulares (celular) adicionales
  - Dirección alternativa del titular (a veces distinta al padrón)
  - Atributos extra (estado_origen, tipo dirección)

Usado por: /api/v1/sujeto/enriquecido (lookup por RFC)
           /api/v1/persona/rfc/<rfc>
View:      api.att_persona, api.att_persona_full


--------------------------------------------------------------------------------
3. empleados (162k filas) — Padrón de empleadores
--------------------------------------------------------------------------------
Archivo:    empleadores.duckdb (0.05 GB)
Tabla:     main.empleadores
PK:        rfc_clean (95% cobertura)
Cols:      razon_social, nombre_comercial, num_empleados, descripcion,
           correo, web, contacto_nombre+paterno+materno, dom_calle, dom_ext,
           dom_colonia, dom_municipio, dom_entidad, dom_cp

Aporta al render del sujeto:
  - Si el RFC es PM12 (persona moral), trae razón social, RFC, dirección
    fiscal de la empresa
  - Si el RFC es PF13/10, trae info del contacto (nombre, teléfono)
  - Sirve para detectar empresas vinculadas al sujeto

Usado por: /api/v1/sujeto/enriquecido (lookup por RFC)
View:      api.empleadores


--------------------------------------------------------------------------------
4. repuve (1.7M filas) — Vehículos registrados
--------------------------------------------------------------------------------
Archivo:    repuve.duckdb (0.21 GB)
Tabla:     main.repuve
PK:        rfc_clean (100% cobertura)
Cols:      placa, no_serie, marca, modelo, color, uso, propietario,
           direccion_propietario, telefono_propietario

Aporta al render del sujeto:
  - **VEHÍCULOS** a nombre del RFC del sujeto (placa, marca, modelo, color)
  - Datos del propietario (a veces difieren del titular)
  - Útil para: validación patrimonial, robo de vehículos, lista negra vehicular

Usado por: /api/v1/sujeto/enriquecido (lookup por RFC)
           /api/v1/persona/rfc/<rfc>
View:      api.repuve_de_persona


--------------------------------------------------------------------------------
5. imss_a (57.7M filas) — Asegurados del IMSS (patrones)
--------------------------------------------------------------------------------
Archivo:    imss_asegurados.duckdb (3.50 GB)
Tabla:     main.imss_2025
PK:        curp_clean (100% cobertura)
Cols:      curp, nss, registro_patron, nombre_patron, empresa_nombre,
           empresa_domicilio, empresa_cp, empresa_giro, sueldo

Aporta al render del sujeto:
  - **PATRÓN** (empleador) donde el sujeto está dado de alta
  - NSS (crucial para xwalk a imss_s y para confirmar homoclave)
  - Sueldo registrado, giro de empresa, domicilio del patrón
  - Sin esto, no se puede hacer el xwalk curp→rfc en imss_s

Usado por: /api/v1/sujeto/enriquecido (lookup directo por curp)
           /api/v1/persona/curp/<curp>
View:      api.imss_asegurado_full


--------------------------------------------------------------------------------
6. imss_s (23.8M filas) — Segmentación de salud del IMSS
--------------------------------------------------------------------------------
Archivo:    imss_segmentacion.duckdb (6.44 GB)
Tabla:     main.imss_personas
PK:        curp_clean (99% cobertura) — **TAMBIÉN TIENE rfc_clean**
Cols:      61 cols — incluye: curp, rfc, nss, nombre, apellido_paterno,
           apellido_materno, fecha_de_nacimiento, genero, edad,
           segmento_salud, enfermedad (cancer, diabetes, hipertension),
           unidad_medica, modalidad, correo, telefono, ref_celular

Aporta al render del sujeto:
  - **xwalk curp→rfc** (única base con ambas columnas; 23.8M RFCs)
  - Datos clínicos (segmentación, enfermedades) — sensible
  - Fecha de nacimiento, género, edad (valida padrón)
  - Nombre y apellidos SEPARADOS (crucial para fallback por nombre+fechanac)
  - Teléfono y email de contacto (correo_electronico, ref_celular, telefono)
  - Unidad médica, modalidad (información de derechohabiencia)

Usado por: /api/v1/sujeto/enriquecido (lookup por curp + fallback
           nombre+fechanac — es la base principal del fallback)
           /api/v1/persona/curp/<curp>
View:      api.imss_salud_full


--------------------------------------------------------------------------------
7. telcel (9.7M filas) — Líneas celulares Telcel (lote viejo)
--------------------------------------------------------------------------------
Archivo:    telcel.duckdb (2.01 GB)
Tabla:     main.telcel
PK:        rfc_clean (100% cobertura)
Cols:      50 cols — cuenta, padre, st_cta, st_cob, cls_crd, tipo, ciclo,
           fecha_activ, fecha_cancel, fecha_term, plan_actual, telefono,
           st_tel, motivo, fecha_cel, gsm_ind, marca, modelo, dat_orig,
           dat_actual, asesor, adendum, plazo, nombre1, nombre2, rfc,
           domicilio, numero, interior, colonia, ciudad, edo, cp,
           tel_contacto, esn, imei, iccid, fecha_plan, fecha_eq, tp_rfc,
           tp_pago, tc, contacto1, contacto2, plan_orig, renaut,
           archivo_origen

Aporta al render del sujeto:
  - **LÍNEAS CELULARES** con toda la metadata: teléfono, plan, IMEI, ICCID,
    fecha activación/cancelación, asesor de venta, RENAUT
  - Modelo del equipo (marca, modelo)
  - Estado de la cuenta (AC, BA, SP, etc.)
  - Punto de venta (asesor, adendum)
  - Cobertura limitada: solo 9.7M líneas con RFC, ventana 2017-2021

Usado por: /api/v1/telcel/buscar?telefono=...
           /api/v1/sujeto/enriquecido (lookup RFC)
View:      api.telcel_lineas, api.telcel_lineas_full


--------------------------------------------------------------------------------
8. cfe (66.0M filas) — Medidores de Comisión Federal de Electricidad
--------------------------------------------------------------------------------
Archivo:    cfe.duckdb (3.06 GB)
Tabla:     main.medidores
PK:        num_servicio (11-12 dígitos) — **NO TIENE RFC/CURP**
Cols:      division, zona_codigo, agencia_nombre, num_servicio, direccion,
           colonia, municipio, estado, cp, hilos, tarifa, etc.

Aporta al render del sujeto:
  - **DOMICILIO REAL VALIDADO** (no declarado, sino donde la CFE cobra
    el servicio eléctrico)
  - Cruce fuzzy por nombre+domicilio (NO por RFC)
  - Si un sujeto dice vivir en X calle y CFE confirma que el medidor
    está en X calle → domicilio VERIFICADO
  - Si CFE tiene medidor en Y calle → domicilio declarado puede ser falso
  - 66M medidores en todo México
  - Sin CP en el 91% (solo en zonas con tarifa DAC), pero cubre casi
    todos los domicilios urbanos

Usado por: /api/v1/cfe/buscar (búsqueda fuzzy por nombre+domicilio)
View:      api.cfe_medidor


--------------------------------------------------------------------------------
9. issste (2.7M filas) — Padrón de empleados del ISSSTE
--------------------------------------------------------------------------------
Archivo:    issste.duckdb (0.52 GB)
Tabla:     main.empleados
PK:        ramo + unidad (sin RFC/CURP/dirección)
Cols:      sueldo, ramo, entidad, modalidad, sector, estado, puesto, fecha_ingreso

Aporta al render del sujeto:
  - **EMPLEO PÚBLICO** — confirmar si el sujeto es empleado del gobierno
    federal (sueldo, ramo, sector, entidad, modalidad)
  - No tiene RFC/CURP/dirección → cruce indirecto por nombre+dependencia
  - Útil para: validar que el sujeto no trabaja en gobierno + sector privado
  - Cobertura: 2.7M empleados federales

Usado por: (no expuesto todavía en /api/v1/persona/curp)
View:      (no tiene view api.* — se query directo a b_issste)


--------------------------------------------------------------------------------
10. fotos (15k filas) — Catálogo de fotos de padrones
--------------------------------------------------------------------------------
Archivo:    fotos.duckdb (0.00 GB — muy pequeño)
Tabla:     main.fotos
PK:        curp
Cols:      foto_url, fuente, fecha

Aporta al render del sujeto:
  - **FOTOS** del sujeto (cuando están disponibles en padrones)
  - Solo 15k registros, cobertura mínima
  - Sirve para mostrar imagen en el reporte

Usado por: /api/sujeto (renderiza foto si existe)


================================================================================
SECCION 2: BASES AUXILIARES (materializan índices, NO se consultan en runtime)
================================================================================

**2026-08-24: limpieza de artefactos de normalización previa.**
Los siguientes archivos fueron borrados por ser productos cerrados de la
fase de normalización descrita en HANDOFF_NORMALIZACION.md (no los consulta
el runtime de servir.py; ocupaban 25 GB que ya no tenían uso):

  - match_index.duckdb (227 MB) — dir_att, dir_empleadores, dir_repuve
  - mkidx_cfe.duckdb (3.6 GB) — dir_cfe
  - mkidx_imss.duckdb (1.2 GB) — dir_imss_patron
  - mkidx_padron.duckdb (8.4 GB) — dir_padron
  - mkidx_telcel.duckdb (1.2 GB) — dir_telcel
  - mkidx_telcel_v2.duckdb (1.7 GB) — dir_telcel_v2
  - telcel_union_view.duckdb (268 KB) — UNION v1+v2 (backend usa b_telcel_2)
  - _duckdb_tmp/telcel_46M.db (+ shm/wal, 8.6 GB) — hardlink temporal
  - _muestra_generica_consolidada.json (32 KB) — muestra de catálogo

Scripts de la fase cerrada que ya no se usan pero NO se borraron
(pueden servir si se quiere re-materializar):
  - backend/materializar_claves.py
  - backend/cruzar.py
  - backend/imputar_cp_cfe.py
  - backend/fuzzy_direccion.py
  - backend/materializar_telcel_v2.py
  - backend/fusionar_telcel_union.py

================================================================================


================================================================================
SECCION 3: BASES PENDIENTES DE NORMALIZAR (nuevas a normalizar/)
================================================================================

Catalogadas en bases/_catalog_nuevas/ (25 archivos), 25.99 GB en disco.
telcel_master_v2.duckdb (4.71 GB) ya está materializado en disco pero
NO se attachea al backend en este escenario. Se trata como pendiente
igual que el resto.

--------------------------------------------------------------------------------
3.1. BANCARIOS — ya catalogados, listos para materializar
--------------------------------------------------------------------------------
+----------+----------+----------+----------+----------+----------+----------+
| Archivo  | Filas    | Size_MB  | PK       | Únicos   | Aporta   |
+----------+----------+----------+----------+----------+----------+----------+
| telcel1.csv              | 7,272,255 | 1,514 | RFC+tel  | 4,943,478 | 7,272,088 tels, cobertura 78% RFC
| CITIBANAMEX.csv          | 2,627,933 |   722 | RFC      | 2,004,402 | ctas bancarias Citibanamex
| BANORTE Data Base.csv    | 1,999,990 |   268 | RFC      | 1,990,338 | ctas Banorte + EMAIL+INGRESO+NOMINA
| HSBC 1.csv               | 1,000,000 |   163 | RFC      |   872,087 | ctas HSBC formato COBOL
| SANTANDER 1.txt          | 1,000,000 |   180 | RFC      |   872,984 | ctas Santander formato COBOL
| SANTANDER 2-7 (×6)       | 5,917,985 | 1,057 | RFC      | ~5,000,000 | (mismo formato, distintos lotes)
| HSBC 2.csv               |    35,340 |     6 | RFC      |    ~30,000 | subconjunto HSBC
| BDD Bancomer.xlsx        |    10,000 |     1 | TDC      |    10,000 | tarjetas Bancomer
| BDD Amex.xlsx            |     2,223 |     0 | RFC+TDC  |     2,000 | tarjetas AMEX
| BDD Bancoppel.xlsx       |    10,001 |     1 | RFC      |    10,000 | ctas Coppel
| Instituto Clavijero.csv  |    13,444 |     1 | CURP     |    12,357 | datos educativos Clavijero
+----------+----------+----------+----------+----------+----------+----------+
SUBTOTAL PENDIENTES: 19,889,171 filas, 4.0 GB, +14M RFCs + 12k CURPs únicos

Aporta POTENCIAL al render del sujeto (al materializar):
  - telcel1.csv: +4.94M RFCs y +7.27M teléfonos de líneas Telcel
  - CITIBANAMEX, BANORTE, HSBC, SANTANDER: +9.8M RFCs con cuentas
    bancarias (número de cuenta, sucursal, saldos, créditos)
  - BDD Bancomer/Amex/Bancoppel: 22k TDC (tarjeta crédito/débito)
  - Clavijero: 12,357 CURPs adicionales para match con padrón


--------------------------------------------------------------------------------
3.2. TELCEL — variantes del mismo dominio
--------------------------------------------------------------------------------
+----------+----------+----------+----------+----------+----------+----------+
| Fuente                 | Filas      | Size     | Estado actual       |
+----------+----------+----------+----------+----------+----------+----------+
| telcel 46M.db          | 44,649,922 | 8.6 GB   | SQLite original en "nuevas a normalizar/"
| telcel_master_v2.duckdb| 44,649,922 | 4.7 GB   | YA MATERIALIZADO en bases/, pero NO attacheado
| TELCEL_MEXICO.7z       | ~13,000,000| 5.3 GB   | 9 archivos TELCEL 1-9.txt, mismo formato 47-cols
| telcel1.csv            |  7,272,255 | 1.5 GB   | formato 16-cols (msisdn, series, nombre_rs, rfc...)
+----------+----------+----------+----------+----------+----------+----------+
SUBTOTAL PENDIENTES TELCEL:  ~65M filas adicionales a 9.7M ya attacheada
  (aporta 6.7x más cobertura)

Aporta POTENCIAL al render del sujeto:
  - telcel 46M.db: 44.6M líneas adicionales (2022-2026), 78% SIN RFC
    pero con teléfono+dirección. Cobertura ampliada de líneas sin
    identidad RFC.
  - TELCEL_MEXICO.7z: ~13M líneas adicionales, mismo formato 47-cols
    que telcel.duckdb actual. Materializable directo con union_all.
  - telcel1.csv: 7.3M líneas, formato 16-cols distinto. Necesita
    adapter nuevo.


--------------------------------------------------------------------------------
3.3. EN REVISIÓN (no se descartan automáticamente, requieren análisis)
--------------------------------------------------------------------------------
+----------+----------+----------+----------+----------+----------+----------+
| Archivo                  | Filas     | Size     | Razón para revisión |
+----------+----------+----------+----------+----------+----------+----------+
| telcell (1).sql         | 2,193 users| 1,028 MB | Dump de app web (ManyChat). Revisar si tiene
|                         |          |          | tablas de identidad útiles o solo metadata.
| CITIBANAMEX.rar         | (duplicado)| 143 MB  | Contiene lo mismo que el .csv. Revisar si
|                         |          |          | tiene una versión extendida.
| COVID23.rar             | (no ident)| 1,608 MB | Posible base de salud/COVID. Revisar si
|                         |          |          | tiene personas con nombre+RFC.
| HOSPITAL ANGELES.rar    | (clientes)| 9,491 MB | Clientes del hospital. Revisar si tiene
|                         |          |          | suficientes datos de identidad.
| 10millones de curps.pdf | (PDF)     | 10 MB    | 10M de CURPs — instalar pypdf para extraer
|                         |          |          | si es factible.
| DOCUMENTOS-PREP-VARIOS.zip| 310 PDFs| 41 MB   | Manuales INE. Probablemente no útil, pero
|                         |          |          | verificar nombres de archivo por si hay
|                         |          |          | padrones escondidos.
+----------+----------+----------+----------+----------+----------+----------+
SUBTOTAL EN REVISIÓN: 12.32 GB

Decisión: NO BORRAR automáticamente. Cada archivo requiere inspección
manual del catálogo completo y de una muestra más profunda antes de
declararlo descartable.
================================================================================


================================================================================
SECCION 4: DIAGRAMA DE FLUJO — qué se consulta al renderizar el sujeto
================================================================================

                    [FRONTEND: sujeto.html]
                              |
                              v  GET /api/sujeto?curp=XXX
                              |
                    [Backend: _handle_sujeto()]
                              |
                              v
                 _find_sujeto(curp) -----> padron (1)
                              |                ^
                              v                |
                          CheckID (si 404)    |
                              |                |
                              v                |
                    response "sujeto"         |
                              |                |
                              v                |
            [FRONTEND pinta datos básicos del sujeto]
                              |
                              v  GET /api/v1/sujeto/enriquecido?curp=...
                              |
                    [_handle_sujeto_enriquecido()]
                              |
                              v
                    _enriquecer_bases_externas(curp, rfc, nss, ...)
                              |
              ┌───────────────┼───────────────┬───────────────┐
              v               v               v               v
        imss_s.xwalk    imss_a (CURP)   imss_s (CURP)   rfc-cascade:
        (curp→rfc)                                       att, emp,
              |                                          repuve, telcel
              v
        [Si no hay RFC, fallback por nombre+fechanac]
        ┌─────────┬─────────┬─────────┬─────────┬─────────┐
        v         v         v         v         v         v
      att      repuve     telcel   imss_s    emp
        +-----+-----+-----+-----+
              v
        [Post-cascade: extraer RFCs y re-popular]
              |
              v
        response: {imss_asegurado, imss_salud, att, empleados,
                   repuve, telcel, total_registros}

PARALELAMENTE el frontend también llama:
  - /api/v1/cfe/buscar (fuzzy por nombre+domicilio)
  - /api/v1/sujeto/verificar_unificada (CheckID + Singula)
  - /api/v1/telcel/buscar?telefono=...   (legacy)
  - /api/sepomex/validate (CP de la dirección)
  - /api/geo/geocode/<cp> (lat/lng para mapa familiar)

Total queries al render del sujeto: 7-11 endpoints en paralelo
(10 attacheadas + 1 CheckID + 1 Singula)
================================================================================


================================================================================
SECCION 5: MATRIZ DE MATCH — qué cruza con qué
================================================================================

  padron   ←  match con todo (PK = nss, contiene curp/rfc/tel)
  att      ←  match por rfc con padron via imss_s.xwalk
  emp      ←  match por rfc
  repuve   ←  match por rfc
  imss_a   ←  match por curp con padron
  imss_s   ←  match por curp con padron (y tiene rfc + fecnac)
  telcel   ←  match por rfc con padron via imss_s.xwalk
  cfe      ←  match fuzzy por nombre+domicilio (no rfc)
  issste   ←  match indirecto (no rfc/curp)
  fotos    ←  match por curp con padron

  PK primaria del sistema: nss (padron)
  PK de match con externas: rfc (vía imss_s.xwalk curp→rfc) o curp
  PK de fallback fuzzy: nombre+paterno+materno+fecnac

  Si NO hay rfc y NO hay curp, fallback busca:
    1. imss_s por nombre+fecnac → rfc
    2. att por paterno+materno
    3. repuve por LIKE sobre propietario
    4. telcel por LIKE sobre nombre1+2
    5. emp por LIKE sobre nombreCompleto/razonSocial
  Con los RFCs encontrados, vuelve a poblar las bases rfc-keyed.
================================================================================


================================================================================
SECCION 6: RESUMEN — COBERTURA TOTAL ACTUAL vs POTENCIAL
================================================================================

ACTUAL (10 bases attacheadas):
  - Personas identificables por RFC:    ~5,300,000 (imss_s)
  - Personas identificables por CURP:  ~150,000,000 (padron + imss_a + imss_s)
  - Líneas celulares:                   ~9,700,000 (telcel)
  - Vehículos:                          ~1,750,000 (repuve)
  - Empleadores:                          ~160,000 (empleadores)
  - Medidores CFE:                     ~66,000,000 (cfe)
  - Empleados ISSSTE:                   ~2,700,000 (issste)

POTENCIAL (al materializar pendientes):
  + Líneas celulares telcel1:            7,272,255 (+75% vs actual)
  + Líneas telcel 46M.db:               44,649,922 (+460% vs actual)
  + Líneas TELCEL_MEXICO.7z:           ~13,000,000 (+134% vs actual)
  + RFCs bancarios (CITIBANAMEX+BANORTE+HSBC+SANTANDER):
                                         9,866,000  (NUEVO)
  + CURPs educativos (Clavijero):           12,357  (NUEVO, pero pequeño)
  + TDC (Bancomer+Amex+Bancoppel):          22,000  (NUEVO, tarjetas)

  TOTAL POTENCIAL: +75,000,000 líneas, +9,900,000 RFCs bancarios,
                  +12,000 CURPs, +22,000 TDC

EN REVISIÓN (requieren análisis manual):
  - telcell (1).sql: 1.0 GB — app web ManyChat
  - CITIBANAMEX.rar: 0.1 GB — posible duplicado del .csv
  - COVID23.rar: 1.6 GB — datos COVID, no identidad (revisar)
  - HOSPITAL ANGELES.rar: 9.5 GB — clientes hospital (revisar)
  - 10millones de curps.pdf: 0.01 GB — requiere pypdf
  - DOCUMENTOS-PREP-VARIOS.zip: 0.04 GB — manuales INE (revisar)

  ESPACIO EN REVISIÓN: 12.32 GB
  ESPACIO LIBERABLE: depende del análisis de cada archivo
================================================================================

Próximos pasos sugeridos:
  1. Materializar telcel1.csv (15-20 min, +4.94M RFCs telcel)
  2. Materializar CITIBANAMEX + BANORTE + HSBC + SANTANDER
     (20-30 min, +9.8M RFCs bancarios)
  3. Materializar telcel 46M.db como endpoint telcel_2
     (ya existe el .duckdb materializado, solo falta ATTACH y view)
  4. Materializar TELCEL_MEXICO.7z (30 min, +13M líneas telcel)
  5. Conectar las nuevas como b_telcel_1, b_telcel_2, b_citibanamex,
     b_banorte, etc. con sus views api.<base>_lineas_full
  6. Agregar al _enriquecer_bases_externas y al post-cascade
  7. Analizar una por una las 6 bases en revisión
================================================================================
