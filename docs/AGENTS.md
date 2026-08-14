# AGENTS.md — Proyecto KYC consolidado (`/root/proyecto_kyc/`)

Plataforma KYC / OSINT para personas físicas en México. Backend Python +
frontend estático + 12 bases de datos. Integra Padrón INE con 11 bases
extendidas (ATT, Telcel, REPUVE, IMSS Asegurados, IMSS Segmentación,
Empleadores, CFE, FOTOSMX, INEGI Marco Geo, SEPOMEX) y proveedores
externos (Tlaloc, Singula, Moffin, Kiban, Apify, Buró, Círculo).

---

## 1. Entorno

```bash
# venv (externo, no en el repo)
VENV=/root/ine_server/.venv/bin/python

# healthcheck
$VENV /root/proyecto_kyc/healthcheck.py

# tests
$VENV /root/proyecto_kyc/tests/run_all.py
# o equivalentemente:
$VENV -m unittest discover -s /root/proyecto_kyc/tests -v

# arrancar backend
cd /root/proyecto_kyc/backend && $VENV servir.py [--port 8765]
```

Login por defecto: `admin / admin123` (WebAuthn passkey + fallback
password). El runtime vive en `/root/ine_server/.venv/` (DuckDB,
pandas, polars, openpyxl, pyarrow, requests, python-dotenv, webauthn,
pyshp, etc).

### Nominatim local (geocodificación offline)

**Stack instalado 2026-08-13**: servidor local de Nominatim 5.3.2 con
el extracto OSM de México (613 MB PBF). Tarda ~45 min en importar;
DB final ~17 GB. Datos OSM actualizados al 2026-08-12.

- DB: PostgreSQL 15 + PostGIS 3.3, DB `nominatim`
- Server HTTP: `nominatim serve` corriendo en `http://127.0.0.1:8088`
- Latencia: **50-300ms** (vs ~3-5s del público OSM, sin rate limit)
- Datos: 81.7M nodos, 9.7M ways, 5.4M places, 10k postcodes

**Integración con el backend:**

- Variables de entorno (defaults en `servir.py`):
  - `NOMINATIM_LOCAL_URL=http://127.0.0.1:8088` (default; vacío desactiva)
  - `NOMINATIM_PUBLIC_URL=https://nominatim.openstreetmap.org` (fallback)
- `_handle_cfe_coordenadas` y `_handle_geo_geocode` intentan el local primero
  (timeout 2-5s), fallback al público si falla.
- La respuesta JSON incluye `geocode_source: "local" | "public"`.

**Para mantenerlo vivo entre reboots:**

```bash
cp /root/nominatim-data/nominatim.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable nominatim
systemctl start nominatim
```

**Cobertura de tests** (en `tests/test_cfe_flujo_coordenadas.py`):

- `TestCFECoordenadasLocalNominatim` (3 tests): local primero, fallback a
  público, local desactivado.
- `TestCFECoordenadasUserAgent` (1 test): UA identificable solo al público.

### Suite de tests

166 tests en 10 archivos. Sin dependencias externas para tests (usa `unittest`
de stdlib). Coverage.py se instala con `pip install coverage` (ya está
en el venv). Tarda ~45s en ejecutarse.

- `tests/test_bases.py` (9 tests) — layout, apertura y conteo de las 13 bases
  (incluye issste.duckdb con cat_ramos/cat_estados/cat_entidades/cat_modalidades/cat_sectores/empleados)
- `tests/test_init_extended_con.py` (7 tests) — EXTENDED_DBS (9 alias), 13 vistas api.*, _enriquecer
- `tests/test_path_migration.py` (7 tests) — paths viejos fuera de código activo
- `tests/test_unit_and_defaults.py` (7 tests) — unit systemd, --db, --html, .env
- `tests/test_backend_http.py` (4 tests) — servir.py arranca y responde HTTP
- `tests/test_providers.py` (41 tests) — 8 providers externos con requests mockeado
- `tests/test_query_latency.py` (12 tests) — latencia de count/limit/lookup RFC+CURP
  sobre las 12 vistas api.*; detecta regresiones groseras (umbrales flexibles).
  También genera `/tmp/hermes-baseline-api-views.json` con tiempos actuales.
- `tests/test_coverage.py` (5 tests) — corre la suite bajo coverage.py,
  genera reporte tabular y HTML en `/tmp/hermes-coverage-html/`.
  Umbrales flexibles por archivo (base.py ≥80%, __init__.py 100%).
- `tests/test_cfe_flujo_coordenadas.py` (22 tests) — schema de `api.cfe_medidor`,
  handlers `/api/v1/cfe/*`, flujo coordenadas GPS → Nominatim → CFE.
  Mockea Nominatim (no internet en CI). Incluye tests de Nominatim local-first.
- `tests/test_maps_per_subject.py` (31 tests) — integración de mapas individuales
  en el reporte IA: `_collect_addresses_for_sujeto` (todas las bases),
  `_generate_maps_for_addresses` (geocodifica + imagen PNG), `geocode_address`
  local-first, sección 04c del HTML, fallback CFE cuando Nominatim falla.
- `tests/test_issste.py` (22 tests) — issste.duckdb (vista api.issste_empleado),
  handler `/api/v1/issste/buscar`, sección 06b del reporte (ISSSTE),
  `display_name` en endpoint CFE coordenadas, issste.html frontend.

Exit 0 = todo OK. Exit 1 = algún test falló (ver `tail -50` del output).

**Endpoints CFE probados:**

- `GET /api/v1/cfe/buscar_domicilio?calle=&colonia=&cp=&division=&limit=` —
  fuzzy match en `api.cfe_medidor` sobre las 4 columnas de domicilio
  (`direccion`, `calle_adicional_1`, `calle_adicional_2`, `colonia`).
  Usa `_search_cfe_by_domicilio()` (función reusable a nivel módulo).
- `GET /api/v1/persona/cfe/<num_servicio>` — lookup exacto por número
  de servicio (10-12 dígitos).
- `GET /api/v1/persona/cfe/buscar?nombre=&cp=&division=&limit=` —
  búsqueda por apellido paterno (primer token del nombre).
- `GET /api/v1/cfe/coordenadas?lat=&lon=&limit=&csv=` — flujo completo
  coordenadas GPS → Nominatim (local primero, fallback público) →
  CFE fuzzy match. Exporta CSV si `csv=1`.

**Fallback CFE en reporte IA** (2026-08-13):
Cuando una dirección de `_collect_addresses_for_sujeto` no se puede
geocodificar con Nominatim, `_generate_maps_for_addresses` llama
`_search_cfe_by_domicilio` con los componentes parseados. Si hay
matches, agrega `cfe_fallback` con los rows CFE al mapa individual y
se renderiza como tabla "Integración pasada" en el HTML del reporte.

Parser heurístico:
- `calle = primer componente`
- `colonia = cualquier elemento COL/FRACC/BARRIO/UNIDAD/EJIDO`
- `cp = 5 dígitos al final`

**Columna `cp` en `medidores`:** agregada 2026-08-13 con `ALTER TABLE`.
Poblada con `REGEXP_EXTRACT(...,'CP\s*(\d{5})',1)` sobre `direccion`,
`calle_adicional_1`, `calle_adicional_2`. Resultado:
- 6,246,667 filas con CP real (9.46% del total)
- 38,430 CPs distintos
- `cp IS NULL` cuando no hay match o es "00000" (dummy)

**Reporte HTML de cobertura:** `/tmp/hermes-coverage-html/index.html`
(3.9 MB). Se regenera en cada corrida del test_coverage.

**Cobertura actual (2026-08-13, baseline):**

| Archivo | Stmts | Cubierto |
|---|---|---|
| providers/__init__.py | 10 | **100%** ✓ |
| providers/base.py | 75 | **85%** ✓ |
| providers/apify_osint.py | 44 | 61% |
| providers/buro.py | 36 | 61% |
| providers/ollama_cloud.py | 61 | 49% |
| providers/kiban.py | 31 | 48% |
| providers/tlaloc.py | 58 | 33% |
| providers/circulo.py | 51 | 33% |
| providers/moffin.py | 69 | 28% |
| auth.py | 258 | 26% |
| providers/singula.py | 473 | 11% |
| servir.py | 3923 | 6% |
| providers/checkid.py | 78 | 0% |
| providers/singula_store.py | 79 | 0% |
| **TOTAL** | **5246** | **11%** |

**Por qué la cobertura total es 11% (y por qué NO asserteamos contra
un umbral global):**
- `servir.py` tiene 3923 statements; los tests actuales cubren la
  lógica core + endpoints que se llaman explícitamente (~6%).
- El resto del código son endpoints que requieren autenticación real
  con sesión (admin/admin123) y los handlers de OSINT.
- Assertear "cobertura > 50%" haría fallar este test cada vez que
  se agrega un endpoint nuevo sin tests E2E.

**Para correr solo coverage manualmente:**

```bash
# Generar data
/root/ine_server/.venv/bin/python /root/proyecto_kyc/tests/_coverage_runner.py

# Reporte tabular
cd /root/proyecto_kyc/backend && \
  /root/ine_server/.venv/bin/coverage report --data-file=/tmp/.coverage-runner

# Reporte HTML
cd /root/proyecto_kyc/backend && \
  /root/ine_server/.venv/bin/coverage html --data-file=/tmp/.coverage-runner
# → abrir /tmp/hermes-coverage-html/index.html
```

---

## 2. Estructura

```
/root/proyecto_kyc/
├── bases/                         # 13 archivos, 21.9 GB, 343.6M filas
│   ├── padron.duckdb              # Padrón INE fusionado (88.4M, 6.2 GB)
│   ├── telcel.duckdb              # 9.7M + 9.5M _valid (19.3M total, 2.0 GB)
│   ├── att.duckdb                 # 1.05M + 1.05M _valid (2.1M total, 73 MB)
│   ├── repuve.duckdb              # 1.7M + 1.7M _valid (3.5M total, 218 MB)
│   ├── imss_asegurados.duckdb     # 57.7M + 56.9M _valid (114.7M total, 3.5 GB)
│   ├── imss_segmentacion.duckdb   # 23.8M + 23.5M _valid (47.3M total, 6.4 GB)
│   ├── cfe.duckdb                 # 66M medidores (3.0 GB, normalizado)
│   ├── fotos.duckdb               # 14,942 + resumen (4 MB)
│   ├── issste.duckdb              # 2.7M empleados federales (400 MB)
│   ├── empleadores.duckdb         # 162k empresas (48 MB)
│   ├── auth.db                    # SQLite: users + passkeys + sesiones (294 KB)
│   ├── geo.db                     # SQLite: Marco Geoestadístico INEGI (45 MB)
│   └── sepomex.db                 # SQLite: catálogo SEPOMEX (18 MB)
│
├── backend/                       # código Python
│   ├── servir.py                  # servidor HTTP principal (puerto 8765)
│   ├── config.py                  # carga .env (override=True)
│   ├── auth.py                    # WebAuthn + fallback password
│   ├── kyc_broker.py              # orquestador de providers
│   ├── apify_broker.py            # broker Apify OSINT
│   ├── osint.py / osint_scorer.py
│   ├── report_generator.py        # reporte AI HTML/PDF
│   ├── rfc_utils.py               # cálculo local de RFC
│   ├── audit.py                   # bitácora de acciones
│   ├── telegram_bot.py            # bot de Telegram
│   ├── init_extended_sources.sql  # SQL manual de ATTACH+CREATE VIEW (referencia)
│   ├── providers/                 # 13 providers externos
│   │   ├── apify_osint.py
│   │   ├── base.py
│   │   ├── buro.py
│   │   ├── checkid.py
│   │   ├── circulo.py
│   │   ├── kiban.py
│   │   ├── moffin.py
│   │   ├── ollama_cloud.py
│   │   ├── singula.py
│   │   ├── singula_store.py       # cache SQLite del broker Singula
│   │   ├── tlaloc.py
│   │   └── singula_cache.db       # runtime, no es base de datos
│   ├── .env / .env.example        # API keys
│   ├── deploy.py                  # ciclo de vida local + cloudflared
│   ├── install-service.sh         # instala unit systemd
│   └── cuartodepazsearch.service.template
│
├── frontend/                      # UI estática (6 HTML)
│   ├── buscar.html                # listado con filtros
│   ├── sujeto.html                # ficha individual
│   ├── login.html                 # WebAuthn + password
│   ├── admin.html
│   ├── cfe_coordenadas.html       # GPS → dirección → CFE (Nominatim local)
│   └── issste.html                # padrón ISSSTE por nombre
│
├── scripts/                       # normalización one-shot
│   ├── cfe_normalizer.py          # normaliza los 21 headers de CFE
│   ├── cfe_verify.py              # verifica cfe.duckdb post-carga
│   ├── att_to_duckdb.py
│   ├── att_normalizar_rfc.py
│   ├── imss_normalizar_asegurados.py
│   ├── imss_normalizar_segmentacion_37.py
│   ├── imss_normalizar_segmentacion_limpia.py
│   ├── empleadores_convert.py
│   ├── sepomex_download.py
│   ├── normalizar.py + _csv + _fast + _final + _optimized + _polars
│   └── create_indexes.py
│
├── docs/
│   ├── AGENTS.md                  # este archivo
│   ├── README.md                  # catálogo y arquitectura
│   ├── DEPLOY_kyc.md
│   ├── PLAN_INTEGRACION.md
│   ├── README_consolidadas.md
│   ├── CFE_flujo_coordenadas.md
│   └── operativos/                # docs de análisis + reportes reales (privados)
│
├── healthcheck.py                 # script de validación
├── healthcheck.last.json          # último resultado
│
└── _backups/                      # preservado, no en runtime
    ├── parquet/
    │   ├── cfe_fuente/            # CSVs/parquet CFE originales (2.5 GB)
    │   ├── cfe_normalizado/       # parquet CFE particionado por región (2.9 GB)
    │   ├── parquet_full/          # parquet full de cada base (4.9 GB)
    │   └── parquet_partidos/      # att por estado, telcel por archivo, etc (3.0 GB)
    ├── tars/                      # bundles originales sin extraer
    │   ├── bases_consolidadas.tar.zst  (14 GB)
    │   ├── cfe.tar.gz                  (4 GB)
    │   ├── fotos_full_bundle.tar.zst   (399 MB)
    │   ├── ine_patron_pre_fusion.duckdb  (26 GB, pre-jul-2026)
    │   └── src-deploy.tar.gz.bak_pre_fusion_20260802_011415 (3.6 MB)
    ├── patron_pre_init_api.duckdb # snapshot de padron antes del init
    └── snapshots/                 # snapshots pre-modificaciones servir.py
```

---

## 3. Path canónicos (no son configurables)

`servir.py` usa paths relativos a `ROOT` (= directorio del script) y al
script padre (`ROOT.parent` = `/root/proyecto_kyc/`). En `config.py`:
- `PADRON_DB_PATH` se lee de `.env` (default `../bases/padron.duckdb`)
- `INEGI_BASE = /root/proyecto_kyc/inegi/15_mexico/conjunto_de_datos` (no existe data INEGI shapefile, sólo el SQLite `geo.db`)

En `servir.py:7364` `EXTENDED_DBS` apunta a `/root/proyecto_kyc/bases/*.duckdb`.

**9 bases externas attacheadas (2026-08-13)**:
- `b_att`, `b_emp`, `b_repuve`, `b_imss_a`, `b_imss_s`,
  `b_telcel`, `b_cfe`, `b_fotos`, **`b_issste`** ← nuevo

**13 vistas api.* creadas** (no 12): las 12 anteriores + **`api.issste_empleado`**.
Vista `issste_empleado` expone empleados con nombre, ramo, entidad,
modalidad, sector, estado (sin RFC/CURP/dirección). 2.7M filas.

`auth.py` usa `ROOT/"auth.db"` (relativo al script, queda en
`backend/auth.db` cuando servir.py corre desde `backend/`).

---

## 4. Inicialización de bases externas

`servir.py::_init_extended_con()` (línea 7384) abre una conexión
DuckDB in-memory y hace ATTACH READ_ONLY a las 9 bases externas
(att, empleadores, repuve, imss_asegurados, imss_segmentacion, telcel,
cfe, fotos, **issste**). Después crea 13 vistas en el schema `api.*`:

| Vista                          | PK     | Filas      | Descripción                            |
|--------------------------------|--------|------------|----------------------------------------|
| `api.att_persona`              | rfc    | 1,048,570  | ATT dirección + teléfono (14 cols)     |
| `api.att_persona_full`         | rfc    | 1,048,570  | ATT full (18 cols)                     |
| `api.empleadores`              | rfc    |   153,959  | Empresas (22 cols)                     |
| `api.telcel_lineas`            | rfc    | 9,561,315  | Telcel (16 cols)                       |
| `api.telcel_lineas_full`       | rfc    | 9,561,315  | Telcel full (48 cols)                  |
| `api.repuve_de_persona`        | rfc    | 1,745,536  | REPUVE vehículos (12 cols)             |
| `api.imss_asegurado`           | curp   | 56,948,742 | IMSS patrones (10 cols)                |
| `api.imss_asegurado_full`      | curp   | 56,948,742 | IMSS full (15 cols)                    |
| `api.imss_salud`               | curp   | 23,480,423 | IMSS segmentación (17 cols)            |
| `api.imss_salud_full`          | curp   | 23,480,423 | IMSS full (59 cols)                    |
| `api.cfe_medidor`              | num_serv | 66,003,291 | CFE medidores (24 cols, normalizado)  |
| `api.fuentes_por_rfc`          | rfc    | 8,500,917  | Conteo por RFC en cada base            |

El `init_extended_sources.sql` en `backend/` es una versión standalone
del mismo SQL para ejecución manual (requiere RW sobre `padron.duckdb`).

---

## 5. Convenciones

- `from __future__ import annotations` en casi todo
- Type hints parciales; sin formatter ni linter
- Docstrings estilo Google en español al inicio de cada módulo
- Mensajes de log/print con emojis de estado (✓ ✗ ⚠)
- Singletons lazy para conexiones pesadas (`_init_extended_con()`,
  `get_sepomex()`, `get_inegi()`)
- SQL: parámetros con `?` para valores; identificadores validados
  contra whitelist antes de interpolar (`servir.py:147` `FORBIDDEN`)

---

## 6. Gotchas

- `config.py` carga `.env` con `override=True` — una variable en
  `.env` gana sobre el entorno del shell.
- `MAX_LIMIT=1000` en `servir.py` trunca cualquier `limit` mayor; el
  frontend pagina con `offset`.
- `auth.py` hace `init_db()` y `ensure_default_admin()` **al import**
  desde `servir.py` — importar `auth` tiene side effects sobre
  `bases/auth.db`.
- WebAuthn: `RP_ID`/`RP_ORIGIN` se fijan en runtime vía
  `auth.set_rp()` desde `servir.py` según el hostname de la request.
- Las ATTACH de las bases externas son **por-sesión** en DuckDB.
  `_init_extended_con()` las recrea en cada arranque.
- `cfe.duckdb` (en `bases/`) es la versión **normalizada** con
  headers reales (`division`, `zona_codigo`, etc). La versión vieja
  con headers `col_1..col_19` está archivada en `_backups/parquet/`.
- `docs/operativos/` contiene PDFs/XLSX de sujetos reales. No
  subir a repos públicos.
- `_backups/` son ~62 GB preservados; no son runtime.

---

## 7. Deploy

```bash
# desarrollo local con cloudflared quick tunnel
$ROOT/../ine_server/.venv/bin/python backend/deploy.py --full

# systemd (server)
sudo backend/install-service.sh
```

NOTA: el `cuartodepazsearch.service.template` actual apunta a
`/home/%I/Descargas MEGA/...` (path viejo de sebastianvernis).
Para este server, el unit activo está en
`/etc/systemd/system/cuartodepazsearch@root.service` apuntando a
`/root/ine_server/Descargas MEGA/...` (viejo). Si querés migrar el
unit a `/root/proyecto_kyc/`, regenerar el template primero.

---

## 8. Convenciones de `b_*` alias

| Alias      | DB                      | Tabla principal             |
|------------|-------------------------|-----------------------------|
| `b_att`    | att.duckdb              | `main.att`                  |
| `b_emp`    | empleadores.duckdb      | `main.empleadores`          |
| `b_repuve` | repuve.duckdb           | `main.repuve`               |
| `b_imss_a` | imss_asegurados.duckdb  | `main.imss_2025`            |
| `b_imss_s` | imss_segmentacion.duckdb| `main.imss_personas`        |
| `b_telcel` | telcel.duckdb           | `main.telcel`               |
| `b_cfe`    | cfe.duckdb              | `main.medidores`            |
| `b_fotos`  | fotos.duckdb            | `main.fotos`                |
| `b_issste` | issste.duckdb           | `main.empleados`            |

---

## 9. Endpoints HTTP nuevos (2026-08-13)

**Geocodificación Nominatim** (`/api/v1/cfe/coordenadas?lat=&lon=&limit=&csv=`):
- Retorna `display_name` completo de Nominatim
- `geocode_source`: `local` (Nominatim local 127.0.0.1:8088, ~50-100ms) o `public` (3-5s)
- Fallback automático al público si local no responde

**Búsqueda ISSSTE** (`/api/v1/issste/buscar?nombre=&paterno=&materno=&sexo=&limit=`):
- LIKE %x% case-insensitive sobre `api.issste_empleado` (2.7M filas)
- Requiere al menos uno de (paterno, materno, nombre)
- Cap default 50, max 100
- Ordenado por `sueldo DESC` (empleados mejor pagados primero)
- Sin RFC/CURP/dirección (padrón solo tiene nombre + estructura organizacional)

**Reporte IA sección 06b**: padrón ISSSTE aparece automáticamente cuando
se genera el reporte si el sujeto tiene paterno con ≥3 caracteres.
Auto-búsqueda vía `api.issste_empleado WHERE paterno LIKE ?`. Sin cache.

**Frontend nuevo**:
- `frontend/issste.html` — buscador por nombre con auto-búsqueda desde
  `sujeto.html?auto=1&paterno=X` (paterno se pasa vía query param)
- `frontend/cfe_coordenadas.html` — mejorada con display_name completo,
  mini-mapa estático OSM embebido (staticmap.openstreetmap.de),
  link directo a OpenStreetMap.org, badge de Nominatim local/público
