RESUMEN COMPLETO DEL PROYECTO KYC
==================================

Plataforma: "Plataforma Encuentra" (KYC / OSINT para personas mexicanas)
Ubicación: /home/sebastianvernis/proyectos/kyc/proyecto_kyc/
Estado: Producción estable desde 2026-08-24, 29 bases federadas, 88M padrón, ~38 GB totales

================================================================
1. PROPÓSITO Y ALCANCE
================================================================

Plataforma unificada de investigación de identidad que cruza 29 bases de datos
federadas contra un padrón electoral central, más 7 proveedores externos pagos
(RENAPO, Singula, Apify, Moffin, Kiban, Buró, CheckID), con generación de
reportes periciales en HTML/PDF y un agente LLM (Oráculo) que automatiza
investigaciones en lenguaje natural.

Casos de uso verificados:
- KYC jurídico (bufetes): validación de identidad fiscal, laboral, vehicular
- OSINT social: redes sociales, emails, brechas
- Inteligencia relacional: vínculos familiares, convivientes, domicilio
- Investigación patrimonial: vehículos, cuentas bancarias, líneas telefónicas
- Análisis forense: fraudes, ataques coordinados digitales, listas negras

================================================================
2. ARQUITECTURA GENERAL
================================================================

```
[Frontend HTML/JS]  →  [servir.py :8765]  →  [29 DuckDB ATTACHed]
       ↑                     ↓                     ↓
[Cloudflare Tunnel]    [Oráculo LLM]         [7 Proveedores API]
 kyc.sebastianvernis    gpt-oss:120b          Tlaloc/Singula/Apify/...
       ↓                     ↓
[Auth + Multi-tenant]  [Audit + Memory per-tenant]
```

================================================================
3. COMPONENTES PRINCIPALES
================================================================

3.1 BACKEND CORE
  - backend/servir.py (13,344 líneas): servidor HTTP principal, todas las APIs
  - backend/report_generator.py (1,762 líneas): generador HTML/PDF de reportes
  - backend/inteligencia_completa.py (1,284 líneas): módulo de inteligencia
  - backend/busqueda_manual.py (1,151 líneas): búsquedas bag-of-words

3.2 PROVEEDORES EXTERNOS (backend/providers/)
  - singula.py: API KYC pagada (~$42 consulta judicial, $10 blacklist)
  - checkid.py: RFC/homoclave, regimen fiscal, 69/69B SAT
  - tlaloc.py: RENAPO CURP validation (gratis)
  - apify_osint.py: Instagram scraping
  - moffin.py, kiban.py, buro.py: burós de crédito
  - ollama_cloud.py: LLM para Oráculo (gpt-oss:120b)
  - gemini.py: validación IA de matches CFE

3.3 ORÁCULO LLM (backend/oraculo/)
  - engine.py: motor ReAct con Ollama Cloud, tool-use loop
  - memory.py: preferencias/aliases por tenant (sqlite)
  - tools/: 15 herramientas (8 core + 7 reconciliación)
  - tools/reconciliacion/: 7 capas de investigación
  - herramientas: padron, curp, rfc, base_search, cfe, kyc_broker, osint, wiki_recall

3.4 FRONTEND (frontend/)
  - login.html: autenticación
  - admin.html: panel de administración
  - buscar.html: búsqueda por nombre/RFC/CURP
  - sujeto.html: investigación completa por CURP (endpoint principal)
  - buscar_direccion.html: búsqueda geográfica CFE
  - cfe_coordenadas.html: GPS → CFE
  - issste.html: búsqueda empleados federales
  - oraculo.html: chat con el Oráculo LLM
  - account.html: gestión de cuenta

3.5 SERVICIO SYSTEMD
  - kyc-backend.service: sirve en 127.0.0.1:8765
  - cloudflared: tunnel a kyc.sebastianvernis.space (HTTP2 obligatorio)

================================================================
4. BASES DE DATOS (29 ATTACHEADAS + INFRAESTRUCTURA)
================================================================

Total: ~341.9M filas, ~38 GB en disco
Motor: DuckDB (29 bases) + SQLite (5 infra)

4.1 PADRÓN ELECTORAL (tabla maestra)
  padron.duckdb (6.16 GB, 88.4M filas)
  PK: nss (11 dígitos)
  Columnas: curp, nombre, paterno, materno, fecnac, nss, domicilio, cp,
           municipio, estado, telefono, email, sexo, edad
  Esquema con columnas cortas: e (estado), d (distrito), m (municipio),
           s (sección), l (localidad), mza (manzana), folio, cred, consec
  ES LA TABLA MAESTRA - sin ella no se renderiza nada

4.2 BASES DE IDENTIDAD Y TRABAJO (4)
  att.duckdb (73 MB, 1M filas)
    PK: rfc_clean (100%)
    Cols: rfc, nombres, pat, may, tel1, tel2, celular, direccion, colonia
    Aporta: líneas fijas/celulares adicionales AT&T México

  empleadores.duckdb (48 MB, 162k filas)
    PK: rfc_clean (95%)
    Cols: razon_social, num_empleados, contacto, dom_calle, dom_cp
    Aporta: empresas vinculadas al sujeto

  repuve.duckdb (218 MB, 1.7M filas)
    PK: rfc_clean (100%)
    Cols: placa, no_serie, marca, modelo, propietario
    Aporta: vehículos registrados

  issste.duckdb (535 MB, 2.7M filas)
    PK: ramo (no RFC, no CURP)
    Tabla: b_issste.main.empleados
    Cols: id, paterno, materno, nombres, cargo, sexo, sueldo, ramo_id
    Aporta: empleados federales ISSSTE

4.3 BASES IMSS (2)
  imss_asegurados.duckdb (3.5 GB, 57.7M filas)
    PK: curp_clean (100%)
    Aporta: nómina, salario, patrón, registro patronal

  imss_segmentacion.duckdb (6.4 GB, 23.8M filas)
    PK: curp_clean (99%)
    Aporta: clínica UMF/HGZ, delegación OOAD, NSS
    USADO COMO XWALK curp→rfc (cruce fundamental)

4.4 BASES TELCEL (4)
  telcel.duckdb (2.0 GB, 9.7M filas)
    PK: rfc_clean (100%)
    Esquema: nombre1, nombre2, telefono, plan, marca, modelo, domicilio

  telcel_master_v2.duckdb (4.7 GB, 44.6M filas)
    PK: telefono (78% NULL RFC, 32% NULL estado)
    Aporta: cobertura masiva por teléfono, útil sin RFC

  telcel1_master.duckdb (723 MB)
    PK: telefono

  telcel_mexico_master.duckdb (1.7 GB)
    PK: telefono

4.5 CFE (1)
  cfe.duckdb (3.1 GB, 66M filas)
    PK: numero_servicio
    Tabla: b_cfe.main.medidores
    Cols: numero_servicio, nombre, direccion, colonia, cp, division, zona_nombre
    Aporta: medidores de electricidad, titular del contrato, domicilio
    78% NULL cp - búsqueda principal por calle

4.6 BANCOS (13 bases, 8 entidades)
  Santander: santander_1 a santander_7_master (7 bases, ~110 MB c/u)
  HSBC: hsbc_master (110 MB) + hsbc_2_master (4 MB)
  Banorte: banorte_master (202 MB)
  Bancomer: bancomer_master (1.3 MB)
  Citibanamex: citibanamex_master (285 MB)
  Bancoppel: bancoppel_master (2 MB)
  AMEX: amex_master (1 MB)
  Clavijero: clavijero_master (1.6 MB)
    PK: titular RFC + cuenta
    Esquema común: titular_nombre1, titular_nombre2, titular_domicilio,
                   titular_numero, titular_colonia, titular_cp
    Aporta: cuentas bancarias a nombre del sujeto

4.7 BASES ESPECIALIZADAS (4)
  docentes_master.duckdb (7.3 MB, 51k filas)
    Docentes EdoMex, 21,564 RFCs únicos

  covid23_master.duckdb (6.9 GB, 19.6M filas)
    Tabla: main.covid_clinico (130 cols clínicas) + main.personas (subset)
    Aporta: síntomas, comorbilidades, vacunación COVID

  hospital_angeles_master.duckdb (5 MB, 23k pacientes)
    Informes de laboratorio extraídos de 26,240 PDFs
    Esquema: nombre, fecha_nacimiento, medico, aseguradora, sucursal

  fotos.duckdb (4 MB, 15k filas)
    Fotos padrones

4.8 INFRAESTRUCTURA (5 archivos)
  sepomex.db (18 MB) - CP → colonia, municipio, estado
  geo.db (43 MB) - INEGI Marco Geoestadístico
  auth.db (278 KB) - sesiones, users, API keys
  auth.key (44 B) - Fernet master key (NUNCA BORRAR)
  singula_cache.db (412 KB) - cache local Singula
  catalogo_municipios_ine.json (64 KB) - código→nombre municipio

================================================================
5. FLUJOS PRINCIPALES DE BÚSQUEDA
================================================================

5.1 FLUJO "INVESTIGACIÓN COMPLETA POR CURP" (más usado)
  Frontend: sujeto.html → GET /api/sujeto?curp=XXX
  Pipeline (automático hasta inciso 3b):

    1. Padrón Electoral Federal (88.4M)
       └→ GET /api/curp/<curp> (servir.py:_handle_curp)
       └→ Si no existe: fallback a CheckID (1 crédito, fuente="checkid-fallback")

    2. SEPOMEX
       └→ Valida CP → colonia, municipio, estado

    3. CheckID (datos fiscales)
       └→ RFC + homoclave, regimen fiscal, emailContacto, CP, NSS, 69/69B

    4. Redes Sociales
       └→ Instagram (Apify) + email lookup + GitHub

    5. Tlaloc (RENAPO)
       └→ Validación CURP contra RENAPO

    6. Bases Externas (6) - RFC-keyed + CURP-keyed
       └→ ATT, Empleadores, REPUVE, Telcel, IMSS_S, IMSS_A
       └→ Endpoint: GET /api/v1/persona/curp/<curp>/todo

  Manual desde inciso 3c (operador decide):
    3c. Verificación unificada ISSSTE + CFE (candidatos con score)
    3d. Inteligencia integral (familiares + convivientes + CFE)

5.2 FLUJO "BÚSQUEDA POR RFC"
  GET /api/v1/persona/rfc/<rfc>/todo
  Cascada:
    1. xwalk: b_imss_s WHERE rfc_clean = ? (curp→rfc)
    2. RFC-keyed: att, empleadores, repuve, telcel (v1), telcel_2
    3. Si no hay RFC: fallback nombre+paterno+materno+fecnac

5.3 FLUJO "BÚSQUEDA POR TELÉFONO" (10 dígitos exactos)
  Patrón SQL canónico (NO LIKE):
    WHERE REGEXP_REPLACE(TRIM(telefono), '[^0-9]', '', 'g') = ?
       OR REGEXP_REPLACE(telefono, '[^0-9]', '', 'g') = ?

  Endpoints:
    GET /api/v1/telcel/buscar?telefono=XXX → b_telcel (9.7M)
    GET /api/v1/telcel_2/buscar?telefono=XXX → b_telcel_2 (44.6M)
    GET /api/v1/att/buscar?telefono=XXX → b_att (1M)
    GET /api/v1/sujeto/validar/telcel?telefono=XXX → 4 bases Telcel unificadas

  Para número solo sin RFC: hacer sweep completo de 17 bases
  (4 Telcel + ATT + 13 bancos + IMSS_segmentacion)

5.4 FLUJO "BÚSQUEDA POR DOMICILIO" (CFE)
  GET /api/v1/cfe/buscar_domicilio?calle=&colonia=&cp=
  Parser: calle=primer componente, colonia=COL/FRACC/BARRIO, cp=5 dígitos

  Variante fuzzy bag-of-words:
    GET /api/v1/cfe/buscar_avanzado?calle=&numero=&colonia=&cp=
    Score multi-parámetro (calle×0.5, colonia×0.3, cp×0.2)

  Para encontrar convivientes en régimen 616 (sin CFE a su nombre):
    └→ Query directo padron.duckdb con filtros e/s/m/l/mza/calle/ext

5.5 FLUJO "BÚSQUEDA POR NOMBRE" (solo nombre+fecnac)
  GET /api/v1/sujeto/enriquecido?nombre=&paterno=&materno=&fecnac=
  Cascada de 4 pasos:
    1. xwalk vía b_imss_s (si CURP)
    2. RFC-keyed en 6 bases
    3. CURP-keyed en 2 bases IMSS
    4. Fallback nombre+fecnac en att/repuve/telcel/telcel_2/imss_s/imss_a/empleadores
    5. Re-populate RFC-keyed desde RFCs descubiertos vía nombre

  ⚠️ ADVERTENCIA: xwalk.nombre_rfcs es un ÍNDICE DE APELLIDOS, no resuelve
     identidad. Cada RFC es una persona con ese apellido. Solo usar como
     pista de investigación, validar con CURP/RFC+homoclave/fecnac.

5.6 FLUJO "VERIFICACIÓN UNIFICADA POR ENTIDAD"
  GET /api/v1/sujeto/validar/<entidad>?rfc=&curp=&telefono=&limit=
  Entidades soportadas (21):
    santander (6 bases), telcel (4), hsbc (2), imss (2),
    citibanamex, banorte, bancomer, bancoppel, amex, clavijero,
    docentes, covid23, hospital-angeles, att, empleadores, repuve,
    issste, cfe, fotos
  Deduplica por (rfc, cuenta, telefono)

  Variante granular: GET /api/v1/sujeto/validar/<entidad>/<base>

5.7 FLUJO "INTELIGENCIA INTEGRAL" (mapeo relacional)
  GET /api/v1/sujeto/inteligencia_completa?curp=
  6 pasos:
    1. Resolución sujeto central (padrón + fallback)
    2. Auditoría CFE por CP+Calle+Colonia (titulares de medidor)
    3. Mapeo consanguíneo (paterno+materno exactos)
    4. Mapeo convivientes (CP+Calle+Ext exactos)
    5. Cruce en bloque 29 bases federadas
    6. Trazabilidad + dictamen pericial con Gemini (opcional)
  Salida: 102+ sujetos auditados, dictamen riesgo KYC (BAJO/REGULAR)

5.8 FLUJO "BÚSQUEDA MANUAL BAG-OF-WORDS" (post 2026-08-25)
  Para bancos, ATT, Telcel, ISSSTE - devuelve TODOS los candidatos con score:
    GET /api/v1/santander/buscar?paterno=&materno=&nombre=
    GET /api/v1/hsbc/buscar?paterno=&materno=&nombre=
    GET /api/v1/banorte/buscar?paterno=&materno=&nombre=
    GET /api/v1/bancomer/buscar?paterno=&materno=&nombre=
    GET /api/v1/citibanamex/buscar?paterno=&materno=&nombre=
    GET /api/v1/bancoppel/buscar?paterno=&materno=&nombre=
    GET /api/v1/amex/buscar?paterno=&materno=&nombre=
    GET /api/v1/clavijero/buscar?paterno=&materno=&nombre=
    GET /api/v1/issste/buscar_avanzado?paterno=&materno=&nombre=&cargo=&sexo=
    GET /api/v1/att/buscar_avanzado?paterno=&materno=&nombres=
    GET /api/v1/telcel/buscar_avanzado?rfc=  (o por paterno/materno/nombre)

  Defaults: CFE=200, bancos/ISSSTE/ATT/Telcel=100
  Sin score mínimo estricto - el operador revisa la lista completa

5.9 FLUJO "ORÁCULO LLM" (2026-08-26, dirección del proyecto)
  POST /api/oraculo {"query": "busca a TOBIAS MALDONADO en Oaxaca"}
  7 capas de reconciliación ejecutadas por gpt-oss:120b:

    Capa 1: recon_normalize - parsear {paterno, materno, nombre, partículas}
    Capa 2: recon_padron_exact - match EXACTO en padrón
    Capa 3: recon_padron_variants - LIKE por palabra, invertir apellidos
    Capa 4: recon_federadas - RFC base → 29 bases
    Capa 5: recon_cohabitacion - padrón.domicilio → CFE titular + familiares
    Capa 6: recon_broker - RENAPO + Singula (gasta ~$52)
    Capa 7: recon_sintetizar - dedupe + score compuesto + veredicto

  Score compuesto:
    0.50 padrón EXACTO + 0.30 padrón variantes + 0.10×N_bases_federadas
    + 0.10 CFE titular + 0.05 familiares + 0.05 RENAPO
    + 0.15 Singula limpio - 0.50 blacklist hit

  Veredictos: LOCALIZADO (≥0.5, 1 candidato) | MULTIPLE | INCIERTO | NO_LOCALIZADO

================================================================
6. PROVEEDORES EXTERNOS (APIs PAGADAS)
================================================================

6.1 TLALOC (RENAPO) - GRATIS
    Endpoint: /app/curp/validate
    Uso: validación oficial CURP contra RENAPO

6.2 CHECKID - PAGO POR CONSULTA
    Endpoints: RFC, regimen fiscal, 69/69B SAT, emailContacto
    Fuente principal para RFC + homoclave (no Singula)
    ⚠️ exitoso=True NO significa que existe el sujeto

6.3 SINGULA - PAGO POR CONSULTA (MÁS CARO)
    Endpoints:
      /app/status - gratis
      /customer - crear cliente ($10)
      /app/judicial/customer/{id} - antecedentes ($42)
      /app/blacklist/customer/{id} - listas negras ($10)
      /app/intel-premium/customer/{id} - dossier premium ($$$)
    ⚠️ No expone resultados pagados sin re-cobrar
    ⚠️ 401 = key inválida, 402 = saldo agotado
    Cache: providers/singula_cache.db (inyección manual posible)

6.4 APIFY (Instagram scraping)
    Devuelve 10-15 candidatos por nombre
    ⚠️ 0 resultados = posible ataque digital coordinado (bans)

6.5 MOFFIN, KIBAN, BURÓ - Burós de crédito
    Configurables vía factory (provider_factory.py)

6.6 OLLAMA CLOUD - LLM para Oráculo
    Modelo default: gpt-oss:120b (gpt-oss:20b falla en tool-use)
    Otros: qwen3.5:397b (rate-limited), llama3.3:70b

6.7 GEMINI - Validación IA
    Opcional en inteligencia_completa para validar matches CFE

================================================================
7. AUTENTICACIÓN Y MULTI-TENANT
================================================================

  Auth: token Bearer (no sesión)
    POST /api/auth/login/password {username, password}
    Token: kyc-backend.service, default admin/admin123

  Multi-tenant (oraculo):
    Tablas en auth.db: tenants(id, slug, nombre), tenants_users(rol)
    Roles: owner | admin | member | viewer
    Tenant default: "plataforma-encuentra"
    Memory DB: oraculo_mem_<tenant_id>.db
    Audit DB: oraculo_audit.db (compartido)

  API Keys por usuario encriptadas con Fernet (auth.key - NUNCA BORRAR)

================================================================
8. REPORTES (HTML/PDF)
================================================================

  9 secciones estándar (02-09):
    02 Resumen Ejecutivo
    03 Padrón Electoral (LOCAL)
    04 Mapa de Ubicación GEO
    04c Mapas de Domicilios
    04d Listado de Auditoría
    05 RENAPO (TLALOC)
    06 SAT/IMSS (CHECKID)
    06b ISSSTE
    07 Singula (badges: SINGULA, CACHE, PERSONALIZADO)
    08 Redes Sociales
    08b Inteligencia Integral (post 2026-08-24)
    09 Observaciones y Conclusiones

  Endpoints:
    GET /api/report/html?curp=XXX → HTML
    GET /api/report/pdf?curp=XXX → PDF (requiere weasyprint)
    POST /api/report/generate → JSON con narrativa IA

  ⚠️ REQUIERE padrón. Si no existe, usar report_generator.py directamente.
  ⚠️ PDF debe usar weasyprint (NO LibreOffice - destruye CSS).
  ⚠️ rfc_singula=None causa bug en report_generator.py:999 → usar {}.

================================================================
9. DIAGRAMA DE FLUJO DEL SISTEMA
================================================================

  ┌─────────────────────────────────────────────────────────────┐
  │ USUARIO                                                     │
  │   │                                                         │
  │   ├──→ sujeto.html (CURP) ──→ GET /api/sujeto?curp=XXX      │
  │   │                              │                          │
  │   │                              ├─ Padrón (88M)            │
  │   │                              ├─ SEPOMEX                 │
  │   │                              ├─ CheckID (1 crédito)     │
  │   │                              ├─ Redes Sociales         │
  │   │                              ├─ Tlaloc (RENAPO)         │
  │   │                              └─ 6 bases externas        │
  │   │                                                         │
  │   ├──→ buscar.html (nombre/RFC) ──→ GET /api/search         │
  │   │                                 └─ Búsqueda padrón      │
  │   │                                                         │
  │   ├──→ /api/v1/persona/rfc/<rfc>/todo ──→ xwalk + 6 bases  │
  │   │                                                         │
  │   ├──→ /api/v1/cfe/buscar_domicilio ──→ CFE fuzzy           │
  │   │                                                         │
  │   ├──→ Botón "Mapeo Integral" ──→ /api/v1/sujeto/inteligencia_completa
  │   │                                 └─ 6 pasos de mapeo     │
  │   │                                                         │
  │   └──→ oraculo.html (chat) ──→ POST /api/oraculo            │
  │                                 └─ 7 capas LLM             │
  └─────────────────────────────────────────────────────────────┘

================================================================
10. TABLA RESUMEN DE ENDPOINTS
================================================================

  CATEGORÍA          ENDPOINT                              FUNCIÓN
  ─────────────────  ────────────────────────────────────  ─────────────
  AUTH               /api/auth/login/password              Login
                     /api/auth/me                          Info usuario
                     /api/auth/logout                      Cerrar sesión

  PADRÓN             /api/curp/<curp>                      Fila padrón
                     /api/search                           Búsqueda SQL
                     /api/total                            Total padrón
                     /api/estados                          Catálogo estados

  SUJETO             /api/sujeto?curp=<curp>               Endpoint principal
                     /api/v1/persona/rfc/<rfc>/todo       RFC → extendido
                     /api/v1/persona/curp/<curp>/todo      CURP → extendido
                     /api/v1/sujeto/enriquecido            Multi-input
                     /api/v1/sujeto/inteligencia_completa  Mapeo relacional
                     /api/v1/sujeto/validar/<entidad>      Unificado
                     /api/v1/sujeto/validar/<entidad>/<base> Granular
                     /api/v1/sujeto/resolver_desde_hint    Resolver RFC

  BÚSQUEDA MANUAL    /api/v1/cfe/buscar_domicilio          CFE fuzzy
                     /api/v1/cfe/buscar_avanzado           CFE bag-of-words
                     /api/v1/<banco>/buscar                8 bancos
                     /api/v1/issste/buscar_avanzado        ISSSTE
                     /api/v1/att/buscar_avanzado           ATT
                     /api/v1/telcel/buscar_avanzado        Telcel

  TELÉFONO (EXACTO)  /api/v1/telcel/buscar?telefono=       Telcel v1
                     /api/v1/telcel_2/buscar?telefono=     Telcel v2
                     /api/v1/att/buscar?telefono=          ATT

  CFE                /api/v1/persona/cfe/<num_servicio>    Por número
                     /api/v1/persona/cfe/buscar            Fuzzy nombre+CP
                     /api/v1/cfe/coordenadas               GPS → CFE

  OSINT/KYC          /api/osint                            Pipeline OSINT
                     /api/kyc                              Brokers paralelos
                     /api/enrich                           Enriquecimiento fase

  RFC                /api/rfc/calcular                     Calcular RFC
                     /api/rfc/calcular-completo            + blacklist
                     /api/rfc/expandir                     RFC → nombre

  GEOLOCALIZACIÓN    /api/geo/geocode/<cp>                 SEPOMEX CP
                     /api/sepomex/cp/<cp>                  SEPOMEX
                     /api/sepomex/search                   SEPOMEX fuzzy
                     /api/geo/geocode                      Nominatim
                     /api/geo/padron                       INEGI

  FAMILIA            /api/v1/familia/hermanos              Hermanos
                     /api/v1/familia/progenitores          Padres
                     /api/v1/familia/mapa                  Mapa familiar

  REPORTES           /api/report/html?curp=                HTML
                     /api/report/pdf?curp=                 PDF (weasyprint)
                     /api/report/generate                  JSON+IA

  ORÁCULO            /api/oraculo                          Chat LLM
                     /api/oraculo/memoria                  Memoria tenant
                     /api/oraculo/audit                    Audit log

  ADMIN              /api/admin/stats                      Estadísticas
                     /api/admin/activity                   Audit log

================================================================
11. INTEGRACIÓN DE NUEVAS BASES (5 PASOS)
================================================================

  Para agregar una nueva base al runtime:

  1. Catalogar (scripts/catalogar_nuevas_bases.py)
     └→ Genera .catalog.txt con schema, filas, PII

  2. Materializar (backend/materializar_*.py)
     └→ Para >5M filas usar SQL puro (20x más rápido que UDF)
     └→ Para paths con espacios: hardlink primero

  3. EXTENDED_DBS en servir.py (~línea 10714)
     └→ "b_<name>": ("bases/<file>.duckdb", "main.<table>")

  4. View api.<name>_lineas_full
     └→ Mismo shape que views existentes

  5. Wire lookup en 3 lugares:
     - dict["attached"] en _init_extended_con
     - out[<name>] init en _enriquecer_bases_externas
     - bloque _nombre_lookup para att/repuve/telcel/imss

  ⚠️ BUG COMÚN: hay MÚLTIPLES dicts "attached" en servir.py. Si omites
     uno, la base aparece en startup pero NUNCA se consulta (silent fail).

================================================================
12. PITFALLS CRÍTICOS VERIFICADOS
================================================================

  • DuckDB singleton: NUNCA cerrar conexión de _init_extended_con()
  • SQL string quotes: usar 'texto' no "texto" (DuckDB interpreta " como column)
  • Padrón columnas cortas: e (estado), d (distrito), m (municipio), s (sección)
  • Teléfono exact match: REGEXP_REPLACE, NUNCA LIKE '%x%'
  • CFE cp 78% NULL: buscar por calle, no por cp
  • ATT tel1+celular: 1.7x teléfonos por fila (dos columnas)
  • REPUVE TEL_PROP: NO es teléfono nacional (longitudes 1/5/7 dígitos)
  • Singula 401 vs 402: 401=key mala, 402=sin saldo
  • CheckID exitoso=True: NO significa que existe el sujeto
  • xwalk.nombre_rfcs: es ÍNDICE de apellidos, NO resuelve identidad
  • report_generator rfc_singula=None: bug, usar {}
  • PDF: weasyprint, NUNCA LibreOffice (destruye CSS)
  • venv path: /home/sebastianvernis/.venv/bin/python (NO python3)
  • Padron int: literal 'INT' no NULL para propiedades multi-dwelling
  • Padron ext: doubles as lote number cuando calle contiene LOTE
  • Padron sexo: single char 'H'/'M', unisex names comunes
  • CFE cp: SEPOMEX postal code, no municipio INE; puede abarcar múltiples
  • Oraculo: gpt-oss:120b (NO 20b - falla tool-use)
  • Oraculo motor: capturar output de recon_sintetizar directamente
  • Backend restart: sudo kill -9 + sudo systemctl start (no restart solo)

================================================================
13. SERVICIOS Y OPERACIONES
================================================================

  Diagnóstico 502:
    1. Backend zombie → sudo systemctl restart kyc-backend.service
    2. Backend crashed → sudo systemctl start kyc-backend.service
    3. Tunnel crashed → sudo systemctl restart cloudflared@<n>.service
    4. Bind failure → journalctl -u kyc-backend.service -n 50

  Reinicio seguro:
    export XDG_RUNTIME_DIR=/run/user/$(id -u)
    PID=$(ps -ef | grep servir.py | grep -v grep | awk '{print $2}')
    sudo kill -9 $PID
    sleep 2
    sudo systemctl start kyc-backend.service
    sleep 10
    ss -ltnp | grep 8765

  Validación post-cambio:
    python3 -m unittest tests.test_bases   # 8/8 must pass
    python3 healthcheck.py                  # regenera healthcheck.last.json

  Credenciales default: admin/admin123

  Venv dependencies críticas:
    /home/sebastianvernis/.venv/bin/pip install staticmap Pillow weasyprint

================================================================
14. ESTRUCTURA DE DIRECTORIOS
================================================================

  proyecto_kyc/
  ├── AGENTS.md / CLAUDE.md        # instrucciones para agentes
  ├── HANDOFF_PROYECTO.md          # estado del proyecto (2026-08-22)
  ├── HANDOFF_NORMALIZACION.md     # handoff normalización (38KB)
  ├── INVENTARIO_BASES.md          # inventario completo (26KB)
  ├── healthcheck.py               # verificador de salud
  ├── healthcheck.last.json        # último estado
  ├── requirements.txt
  ├── backend/
  │   ├── servir.py                # servidor principal (13K líneas)
  │   ├── report_generator.py      # generador reportes
  │   ├── inteligencia_completa.py # mapeo relacional
  │   ├── busqueda_manual.py       # bag-of-words
  │   ├── auth.py                  # multi-tenant + tokens
  │   ├── config.py                # configuración
  │   ├── audit.py                 # auditoría
  │   ├── deploy.py                # despliegue
  │   ├── kyc_broker.py            # broker paralelo
  │   ├── osint.py                 # OSINT pipeline
  │   ├── osint_scorer.py          # scoring OSINT
  │   ├── rfc_utils.py             # cálculo RFC
  │   ├── rastreo_relacion.py      # rastreo entre sujetos
  │   ├── telegram_bot.py          # notificaciones
  │   ├── materializar_*.py        # scripts materialización
  │   ├── extraer_hospital_angeles.py
  │   ├── normalizar_direccion.py  # normalización dirs
  │   ├── imputar_cp_cfe.py
  │   ├── fuzzy_direccion.py
  │   ├── cruzar.py
  │   ├── fusionar_telcel_union.py
  │   ├── generar_catalogo_municipios.py
  │   ├── init_extended_sources.sql
  │   ├── oraculo/                 # agente LLM
  │   │   ├── engine.py
  │   │   ├── memory.py
  │   │   └── tools/
  │   │       ├── padron.py
  │   │       ├── curp.py
  │   │       ├── rfc.py
  │   │       ├── base_search.py
  │   │       ├── cfe.py
  │   │       ├── kyc_broker.py
  │   │       ├── osint.py
  │   │       ├── wiki_recall.py
  │   │       └── reconciliacion/  # 7 capas
  │   ├── providers/               # APIs externas
  │   │   ├── singula.py / singula_store.py
  │   │   ├── checkid.py / tlaloc.py
  │   │   ├── apify_osint.py
  │   │   ├── moffin.py / kiban.py / buro.py
  │   │   ├── circulo.py
  │   │   ├── gemini.py
  │   │   ├── ollama_cloud.py
  │   │   ├── keyring.py
  │   │   ├── provider_factory.py
  │   │   └── base.py
  │   └── scripts/                 # utilidades backend
  ├── bases/                       # 29 DuckDB + 5 infra (~38GB)
  │   ├── padron.duckdb            # 88M filas, 6.16 GB
  │   ├── att.duckdb               # 1M filas
  │   ├── cfe.duckdb               # 66M filas
  │   ├── *_master.duckdb          # materializaciones
  │   ├── sepomex.db / geo.db / auth.db / auth.key
  │   ├── singula_cache.db
  │   ├── oraculo_audit.db / oraculo_mem_1.db
  │   ├── catalogo_municipios_ine.json
  │   ├── _catalog_nuevas/         # 25 catalog.txt
  │   ├── _logs/                   # logs históricos
  │   └── _export_cfe/             # exports
  ├── frontend/                    # HTML estático
  │   ├── login.html / account.html / admin.html
  │   ├── buscar.html / buscar_direccion.html
  │   ├── sujeto.html              # vista principal
  │   ├── issste.html / cfe_coordenadas.html
  │   ├── oraculo.html             # chat LLM
  │   ├── m.html                   # mobile
  │   └── static/                  # CSS/JS
  ├── reportes/                    # outputs HTML/PDF
  ├── scripts/                     # scripts utilidad
  ├── tests/
  ├── nuevas a normalizar/         # archivos pendientes
  │   └── _extraidos/              # extraídos
  ├── docs/
  └── .gitnexus/                   # índice GitNexus

================================================================
15. MÉTRICAS Y VERIFICACIÓN
================================================================

  Capacidad:
    - 88.4M padrón electoral
    - 341.9M filas totales en bases federadas
    - 29 bases de datos DuckDB ATTACHed simultáneamente
    - ~38 GB en disco
    - ~7.7 GB RAM host (con host 32GB en Tailscale 100.94.150.42)

  Performance verificado:
    - Lookup por RFC: <1s
    - Inteligencia completa (102 sujetos): ~8s
    - Oráculo simple: <5s
    - Materialización 44.6M filas: 147s (SQL puro)
    - Materialización 19.6M filas COVID: similar

  Recursos:
    - Backend Python: /home/sebastianvernis/.venv/bin/python
    - Puerto: 127.0.0.1:8765
    - Tunnel: kyc.sebastianvernis.space (HTTP2)
    - Login default: admin/admin123

================================================================
16. CONVENCIONES Y PREFERENCIAS DEL USUARIO
================================================================

  Reportes:
    - Aserción factual (NO lenguaje tentativo)
    - Sin secciones vacías (<600 chars o placeholders)
    - Mapa correctamente renderizado (staticmap + halo blanco)
    - Verificación de redes completa (Apify + OSINT)
    - Mapeo relacional + árbol genealógico
    - Endpoints nuevos = herramientas LLM-callables
    - NO rediseñar UI de búsqueda - usar Oráculo

  Investigación:
    - Toda la data del sujeto, siempre (vehículos, bancos, teléfonos)
    - Fallback RFC→nombre+fecnac automático
    - Buscar convivientes vía padron.duckdb directo si CFE no tiene
    - Geocoding con validación bbox México (Nominatim)
    - Mapas: staticmap o SVG offline (no PIL/matplotlib)
    - PDF: weasyprint (nunca LibreOffice)

  Branding:
    - Plataforma Encuentra (NO "Padrón 2018" ni "Cuarto de Paz Search")

================================================================
17. SEGURIDAD Y PRIVACIDAD
================================================================

  - Tokens Bearer (no sesiones persistentes)
  - API keys por usuario encriptadas con Fernet (auth.key)
  - Multi-tenant con roles (owner/admin/member/viewer)
  - Audit log por tenant (oraculo_audit.db)
  - Memory per-tenant (oraculo_mem_<id>.db)
  - NUNCA enviar PII real fuera del dev box (anonimizar con <PLACEHOLDER>)
  - Privacidad: preguntas casuales sobre personas NO requieren dump completo

================================================================
18. PRÓXIMOS PASOS / DIRECCIÓN DEL PROYECTO
================================================================

  Decisión arquitectónica 2026-08-26: la dirección del proyecto es el ORÁCULO LLM,
  NO más UI de búsqueda. Toda nueva feature:
    1. Ofrecer primero la ruta Oráculo (endpoint + tools + chat)
    2. Endpoints diseñados para LLM, no humanos (veredicto, no tablas crudas)
    3. Trazabilidad obligatoria (query, tools, params, resultados, veredicto)

  Pendientes identificados:
    - Mover búsquedas 3c/3d a UI frontend (panel "Búsqueda Manual")
    - Mejorar parser hospital_angeles (89% actual, 11% variantes)
    - Limpiar /tmp/hospital_full (11GB ya extraídos)
    - Actualizar HANDOFF_NORMALIZACION con ronda 3
    - Integrar más bases del backlog (new a normalizar/_catalog_nuevas)

================================================================
FIN DEL RESUMEN
================================================================
