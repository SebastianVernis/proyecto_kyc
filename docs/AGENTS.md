# AGENTS.md — Proyecto KYC consolidado

Plataforma KYC / OSINT para personas físicas en México. Backend Python +
frontend estático + 29 bases de datos federadas + motor LLM (Oráculo).
Integra Padrón INE con 28 bases extendidas (ATT, Telcel ×4, REPUVE,
IMSS Asegurados, IMSS Segmentación, Empleadores, CFE, FOTOSMX, ISSSTE,
7 bancos, Docentes, COVID23, Hospital Ángeles) y proveedores externos
(Tlaloc/RENAPO, Singula, CheckID, Apify, Moffin, Kiban, Buró, Círculo,
Gemini, Ollama Cloud).

---

## 1. Entorno

```bash
# venv
VENV=/home/sebastianvernis/.venv/bin/python

# healthcheck
$VENV /home/sebastianvernis/proyectos/kyc/proyecto_kyc/healthcheck.py

# tests (270 tests, 22 archivos, ~45s)
cd /home/sebastianvernis/proyectos/kyc/proyecto_kyc
$VENV -m unittest discover -s tests -v

# arrancar backend
cd /home/sebastianvernis/proyectos/kyc/proyecto_kyc/backend && $VENV servir.py [--port 8765]
```

Login por defecto: `admin / admin123` (Bearer token).
El runtime vive en `/home/sebastianvernis/.venv/` (DuckDB, pandas, polars,
openpyxl, pyarrow, requests, python-dotenv, pymupdf, weasyprint, etc).

### Nominatim local (geocodificación offline)

Stack: servidor local de Nominatim 5.3.2 con extracto OSM de México.

- DB: PostgreSQL 15 + PostGIS 3.3, DB `nominatim`
- Server HTTP: `nominatim serve` corriendo en `http://127.0.0.1:8088`
- Latencia: **50-300ms** (vs ~3-5s del público OSM, sin rate limit)

**Variables de entorno** (defaults en `servir.py`):
- `NOMINATIM_LOCAL_URL=http://127.0.0.1:8088` (default; vacío desactiva)
- `NOMINATIM_PUBLIC_URL=https://nominatim.openstreetmap.org` (fallback)
- Los handlers de geocodificación intentan local primero, fallback al público.
- La respuesta JSON incluye `geocode_source: "local" | "public"`.

### Servicio systemd

```bash
# Unit: kyc-backend.service
# User: sebastianvernis
# WorkingDir: /home/sebastianvernis/proyectos/kyc/proyecto_kyc/backend
# ExecStart: /home/sebastianvernis/.venv/bin/python -u servir.py

# Diagnóstico 502:
sudo systemctl status kyc-backend.service
journalctl -u kyc-backend -n 50

# Reinicio seguro:
PID=$(ps -ef | grep servir.py | grep -v grep | awk '{print $2}')
sudo kill -9 $PID; sleep 2; sudo systemctl start kyc-backend.service; sleep 10
ss -ltnp | grep 8765

# Tunnel: cloudflared → kyc.sebastianvernis.space (HTTP2 obligatorio)
```

### Suite de tests

270 tests en 22 archivos. Sin dependencias externas para tests (usa `unittest`
de stdlib). Coverage.py se instala con `pip install coverage`.

| Archivo | Tests | Descripción |
|---------|-------|-------------|
| test_bases.py | 9 | Layout, apertura y conteo de bases |
| test_init_extended_con.py | 7 | EXTENDED_DBS, vistas api.*, _enriquecer |
| test_path_migration.py | 7 | Paths viejos fuera de código activo |
| test_unit_and_defaults.py | 7 | Unit systemd, --db, --html, .env |
| test_backend_http.py | 4 | servir.py arranca y responde HTTP |
| test_providers.py | 41 | Providers externos con requests mockeado |
| test_query_latency.py | 12 | Latencia count/limit/lookup RFC+CURP |
| test_coverage.py | 5 | Suite bajo coverage.py + reporte HTML |
| test_cfe_flujo_coordenadas.py | 22 | CFE coordenadas GPS → Nominatim → CFE |
| test_maps_per_subject.py | 31 | Mapas individuales en reporte IA |
| test_issste.py | 22 | ISSSTE vista, handler, reporte sección 06b |
| test_cfe_regex_unificada.py | — | Regex unificado CFE |
| test_domicilio_buscar.py | — | Búsqueda domicilio |
| test_fuzzy_direccion.py | — | Fuzzy dirección |
| test_gemini.py | — | Gemini provider |
| test_ia_filter.py | — | Filtro IA |
| test_inteligencia_relacional.py | — | Inteligencia relacional |
| test_parse_coord_input.py | — | Parse coordenadas |
| test_provider_keys.py | — | Provider keys |
| test_resolver_desde_hint.py | — | Resolver desde hint |
| test_gateway_mock.py | — | DB Gateway mock |
| test_gateway_smoke.py | — | DB Gateway smoke |

---

## 2. Estructura

```
proyecto_kyc/
├── AGENTS.md / CLAUDE.md          # instrucciones para agentes
├── HANDOFF_PROYECTO.md            # estado del proyecto (2026-08-22)
├── HANDOFF_NORMALIZACION.md       # handoff normalización (38KB)
├── RESUMEN_PROYECTO.md            # resumen completo actualizado
├── INVENTARIO_BASES.md            # inventario completo (26KB)
├── ANALISIS_BUSQUEDAS_LAYOUTS.md  # layouts de búsqueda
├── healthcheck.py                 # verificador de salud
├── healthcheck.last.json          # último estado
├── requirements.txt
├── conftest.py
│
├── backend/                       # código Python (~55 archivos .py)
│   ├── servir.py                  # servidor HTTP principal (11,773 líneas)
│   ├── report_generator.py        # generador reportes HTML/PDF
│   ├── inteligencia_completa.py   # mapeo relacional (familiares + convivientes)
│   ├── busqueda_manual.py         # búsquedas bag-of-words
│   ├── rastreo_relacion.py        # rastreo entre sujetos
│   ├── sujeto_mapear.py           # mapeo de sujeto
│   ├── auth.py                    # Bearer tokens + multi-tenant
│   ├── config.py                  # configuración (.env)
│   ├── audit.py                   # auditoría
│   ├── deploy.py                  # ciclo de vida local + cloudflared
│   ├── kyc_broker.py              # broker paralelo de providers
│   ├── osint.py / osint_scorer.py # pipeline OSINT + scoring
│   ├── rfc_utils.py               # cálculo RFC
│   ├── telegram_bot.py            # notificaciones Telegram
│   ├── clip.py                    # utilidades CLIP
│   ├── perfil_*.py                # gestión de perfiles
│   ├── normalizar_direccion.py    # normalización direcciones
│   ├── cruzar.py                  # cruce multi-base por clave
│   ├── imputar_cp_cfe.py          # imputar CP desde CFE
│   ├── fuzzy_direccion.py         # fuzzy matching direcciones
│   ├── generar_catalogo_municipios.py
│   ├── materializar_*.py          # 6 scripts de materialización
│   ├── extraer_hospital_angeles.py
│   ├── fusionar_telcel_union.py
│   │
│   ├── oraculo/                   # motor LLM (ReAct + tool-use nativo)
│   │   ├── engine.py              # loop principal, system prompt, audit
│   │   ├── memory.py              # preferencias/aliases por tenant (SQLite)
│   │   └── tools/                 # 8 herramientas core
│   │       ├── padron.py          # búsqueda padrón
│   │       ├── curp.py            # validación CURP
│   │       ├── rfc.py             # lookup RFC
│   │       ├── base_search.py     # búsqueda en bases federadas
│   │       ├── cfe.py             # búsqueda CFE por domicilio
│   │       ├── kyc_broker.py      # broker KYC
│   │       ├── osint.py           # OSINT pipeline
│   │       ├── wiki_recall.py     # recall de wiki
│   │       └── reconciliacion/    # 7 capas de reconciliación
│   │           ├── capa1_normalize.py
│   │           ├── capa2_padron_exact.py
│   │           ├── capa3_padron_variants.py
│   │           ├── capa4_federadas.py
│   │           ├── capa5_cohabitacion.py
│   │           ├── capa6_broker.py
│   │           ├── capa7_sintetizar.py
│   │           └── register.py
│   │
│   ├── providers/                 # 16 providers externos
│   │   ├── singula.py / singula_store.py
│   │   ├── checkid.py / tlaloc.py
│   │   ├── apify_osint.py
│   │   ├── moffin.py / kiban.py / buro.py
│   │   ├── circulo.py
│   │   ├── gemini.py              # validación IA de matches
│   │   ├── ollama_cloud.py        # LLM para Oráculo
│   │   ├── keyring.py             # gestión de keys
│   │   ├── provider_factory.py    # factory de providers
│   │   └── base.py
│   │
│   └── scripts/                   # utilidades backend
│       └── bootstrap_wiki.py      # ingesta metadata → wiki
│
├── frontend/                      # UI estática (13 HTML + assets)
│   ├── login.html / account.html
│   ├── admin.html / dashboard.html
│   ├── index.html
│   ├── buscar.html                # búsqueda por nombre/RFC/CURP
│   ├── sujeto.html                # ficha individual (endpoint principal)
│   ├── buscar_direccion.html      # búsqueda geográfica CFE
│   ├── cfe_coordenadas.html       # GPS → CFE (Nominatim local)
│   ├── issste.html                # empleados federales
│   ├── oraculo.html               # chat con Oráculo LLM
│   ├── m.html                     # mobile
│   ├── css/ js/ static/ pages/ assets/
│
├── bases/                         # 29 DuckDB + 5 infra (~38 GB)
│   ├── padron.duckdb              # 88.4M filas, 6.2 GB (TABLA MAESTRA)
│   ├── att.duckdb                 # 1M filas (AT&T)
│   ├── empleadores.duckdb         # 162k empresas
│   ├── repuve.duckdb              # 1.7M vehículos
│   ├── imss_asegurados.duckdb     # 57.7M asegurados
│   ├── imss_segmentacion.duckdb   # 23.8M segmentación (xwalk curp→rfc)
│   ├── telcel.duckdb              # 9.7M líneas (v1)
│   ├── telcel_master_v2.duckdb    # 44.6M líneas (v2)
│   ├── telcel1_master.duckdb      # 7.3M líneas
│   ├── telcel_mexico_master.duckdb # 13M líneas
│   ├── cfe.duckdb                 # 66M medidores
│   ├── issste.duckdb              # 2.7M empleados federales
│   ├── fotos.duckdb               # 15k fotos
│   ├── citibanamex_master.duckdb  # 2.6M cuentas
│   ├── banorte_master.duckdb      # 1.9M cuentas
│   ├── hsbc_master.duckdb         # 1M cuentas
│   ├── hsbc_2_master.duckdb       # 35k cuentas
│   ├── santander_{1,2,3,5,6,7}_master.duckdb  # ~110 MB c/u
│   ├── bancoppel_master.duckdb    # 10k cuentas
│   ├── amex_master.duckdb         # 2k cuentas
│   ├── bancomer_master.duckdb     # 10k cuentas
│   ├── clavijero_master.duckdb    # 13k personas
│   ├── docentes_master.duckdb     # 52k docentes EdoMex
│   ├── covid23_master.duckdb      # 19.6M filas (identidad + 130 cols clínicas)
│   ├── hospital_angeles_master.duckdb  # 23k pacientes (PDFs parseados)
│   ├── sepomex.db                 # CP → colonia, municipio, estado
│   ├── geo.db                     # INEGI Marco Geoestadístico
│   ├── auth.db                    # sesiones, users, API keys
│   ├── auth.key                   # Fernet master key (NUNCA BORRAR)
│   ├── singula_cache.db           # cache local Singula
│   ├── catalogo_municipios_ine.json
│   ├── _catalog_nuevas/           # catálogos de 25 fuentes
│   └── _logs/                     # logs históricos
│
├── db-gateway/                    # Gateway HTTP para acceso a bases DuckDB
│   ├── app/ (config, main, pools)
│   ├── tests/
│   ├── Dockerfile
│   └── requirements.txt
│
├── scripts/                       # normalización one-shot
├── tests/                         # 270 tests en 22 archivos
├── docs/                          # documentación
│   ├── AGENTS.md                  # este archivo
│   ├── PLAN_ESCALABILIDAD.md
│   ├── PLAN_MAPEO_MASIVO.md
│   ├── analisis_biometrico_ine.md
│   ├── requisitos_servidor_biometrico.md
│   └── operativos/                # reportes reales (privados)
│
├── wiki/                          # GitNexus knowledge base
│   ├── raw/ entities/ concepts/ queries/ _meta/
│   ├── index.md / SCHEMA.md / log.md
│
└── presentacion/                  # presentaciones HTML
```

---

## 3. Path canónicos (no son configurables)

`servir.py` usa paths relativos a `ROOT` (= directorio del script) y
`ROOT.parent` = `/home/sebastianvernis/proyectos/kyc/proyecto_kyc/`.

- `EXTENDED_DBS` en `servir.py:9854` — 29 entradas, apunta a `bases/*.duckdb`
- `ENTITY_TO_BASES` en `servir.py:9903` — mapeo entidad→aliases para endpoints unificados
- `TABLE_FOR_BASE` en `servir.py:9947` — alias→tabla principal
- `auth.py` usa `bases/auth.db` (relativo al script)
- Venv: `/home/sebastianvernis/.venv/bin/python` (NO python3 del sistema)

---

## 4. Inicialización de bases externas

`servir.py::_init_extended_con()` (línea 9952) abre una conexión DuckDB
in-memory y hace ATTACH READ_ONLY a las 29 bases externas. Después crea
las vistas en el schema `api.*`.

### EXTENDED_DBS — 29 aliases (servir.py:9854-9898)

| Alias | Archivo | Tabla |
|-------|---------|-------|
| `b_att` | att_v1.duckdb | main.att |
| `b_emp` | empleadores_v1.duckdb | main.empleadores |
| `b_repuve` | repuve_v1.duckdb | main.repuve |
| `b_imss_a` | imss_asegurados_v1.duckdb | main.imss_2025 |
| `b_imss_s` | imss_segmentacion_v1.duckdb | main.imss_personas |
| `b_telcel` | telcel_v1.duckdb | main.telcel |
| `b_cfe` | cfe_v1.duckdb | main.medidores |
| `b_fotos` | fotos_v1.duckdb | main.fotos |
| `b_issste` | issste_v1.duckdb | main.empleados |
| `b_telcel_2` | telcel_v3.duckdb | main.telcel_46m_proc |
| `b_telcel_1` | telcel_v2.duckdb | main.personas |
| `b_telcel_mx` | telcel_v4.duckdb | main.personas |
| `b_citibanamex` | citibanamex_v1.duckdb | main.personas |
| `b_banorte` | banorte_v1.duckdb | main.personas |
| `b_hsbc_1` | hsbc_v1.duckdb | main.personas |
| `b_hsbc_2` | hsbc_v2.duckdb | main.personas |
| `b_santander_1` | santander_v1.duckdb | main.personas |
| `b_santander_2` | santander_v2.duckdb | main.personas |
| `b_santander_3` | santander_v3.duckdb | main.personas |
| `b_santander_5` | santander_v5.duckdb | main.personas |
| `b_santander_6` | santander_v6.duckdb | main.personas |
| `b_santander_7` | santander_v7.duckdb | main.personas |
| `b_bancoppel` | bancoppel_v1.duckdb | main.personas |
| `b_amex` | amex_v1.duckdb | main.personas |
| `b_bancomer` | bancomer_v1.duckdb | main.personas |
| `b_clavijero` | clavijero_v1.duckdb | main.personas |
| `b_docentes` | docentes_v1.duckdb | main.personas |
| `b_covid23` | covid_v1.duckdb | main.personas |
| `b_hospital_ang` | hospital_angeles_v1.duckdb | main.personas |

**Nota:** santander_4 excluido — verificado 100% duplicado de hsbc_1.

### ENTITY_TO_BASES — mapeo entidad→aliases (servir.py:9903)

Permite que `/api/v1/sujeto/validar/<entidad>` unifique consultas:

| Entidad | Bases |
|---------|-------|
| att | b_att |
| empleadores | b_emp |
| repuve | b_repuve |
| imss | b_imss_a, b_imss_s |
| telcel | b_telcel, b_telcel_1, b_telcel_2, b_telcel_mx |
| cfe | b_cfe |
| issste | b_issste |
| santander | b_santander_{1,2,3,5,6,7} |
| hsbc | b_hsbc_1, b_hsbc_2 |
| citibanamex | b_citibanamex |
| banorte | b_banorte |
| bancomer | b_bancomer |
| bancoppel | b_bancoppel |
| amex | b_amex |
| clavijero | b_clavijero |
| docentes | b_docentes |
| covid23 | b_covid23 |
| hospital-angeles | b_hospital_ang |

### Vistas api.* — estáticas

| Vista | PK | Filas | Descripción |
|-------|----|-------|-------------|
| `api.att_persona` | rfc | 1.05M | ATT dirección + teléfono |
| `api.att_persona_full` | rfc | 1.05M | ATT full (18 cols) |
| `api.empleadores` | rfc | 154k | Empresas (22 cols) |
| `api.telcel_lineas` | rfc | 9.6M | Telcel v1 (16 cols) |
| `api.telcel_lineas_full` | rfc | 9.6M | Telcel v1 full (48 cols) |
| `api.telcel_2_lineas_full` | tel | 44.6M | Telcel v2 (50 cols) |
| `api.repuve_de_persona` | rfc | 1.7M | REPUVE vehículos |
| `api.imss_asegurado` | curp | 56.9M | IMSS patrones |
| `api.imss_asegurado_full` | curp | 56.9M | IMSS full |
| `api.imss_salud` | curp | 23.5M | IMSS segmentación |
| `api.imss_salud_full` | curp | 23.5M | IMSS full (59 cols) |
| `api.cfe_medidor` | num_serv | 66M | CFE medidores (24 cols) |
| `api.issste_empleado` | — | 2.7M | Empleados ISSSTE |
| `api.fuentes_por_rfc` | rfc | 8.5M | Conteo por RFC |
| `api.bancarias_todas` | rfc | — | UNION ALL de todas las bancarias |
| `api.imss_segmentacion` | — | — | Segmentación IMSS |
| `api.telcel_linea` | — | — | Telcel individual |
| `api.repuve_vehiculo` | — | — | REPUVE individual |
| `api.cfe_medidor_kyc` | — | — | CFE para KYC |

### Vistas api.* — dinámicas (19 bases nuevas, servir.py:10366-10386)

Generadas automáticamente por loop sobre `_nuevas_bases_personas`:

`telcel_1_lineas_full`, `telcel_mx_lineas_full`,
`citibanamex_cuentas_full`, `banorte_cuentas_full`,
`hsbc_1_cuentas_full`, `hsbc_2_cuentas_full`,
`santander_{1,2,3,5,6,7}_cuentas_full`,
`bancoppel_cuentas_full`, `amex_cuentas_full`, `bancomer_cuentas_full`,
`clavijero_personas_full`, `docentes_personas_full`,
`covid23_personas_full`, `hospital_angeles_personas_full`

---

## 5. Motor LLM — Oráculo

Dirección principal del proyecto desde 2026-08-26.

**Stack:** gpt-oss:120b vía Ollama Cloud con tool-use nativo (NO ReAct
con JSON manual). Endpoint: `POST /api/oraculo {"query": "..."}`.

**Flujo:** 7 capas de reconciliación ejecutadas automáticamente:
1. `recon_normalize` — parsear nombre (paterno, materno, partículas)
2. `recon_padron_exact` — match EXACTO en padrón
3. `recon_padron_variants` — LIKE por palabra, invertir apellidos
4. `recon_federadas` — RFC base → 29 bases
5. `recon_cohabitacion` — padrón domicilio → CFE titular + familiares
6. `recon_broker` — RENAPO + Singula (~$52 consulta)
7. `recon_sintetizar` — dedupe + score compuesto + veredicto

**Veredictos:** LOCALIZADO (≥0.5) | MULTIPLE | INCIERTO | NO_LOCALIZADO

**Endpoints Oráculo:**
- `POST /api/oraculo` — chat con el LLM
- `GET /api/oraculo/memoria` — memoria del tenant
- `GET /api/oraculo/audit` — audit log

**⚠️ NO usar gpt-oss:20b** — falla en tool-use. Solo gpt-oss:120b.

---

## 6. Convenciones

- `from __future__ import annotations` en casi todo
- Type hints parciales; sin formatter ni linter
- Docstrings estilo Google en español al inicio de cada módulo
- Mensajes de log/print con emojis de estado (✓ ✗ ⚠)
- Singletons lazy para conexiones pesadas (`_init_extended_con()`,
  `get_sepomex()`, `get_inegi()`)
- SQL: parámetros con `?` para valores; identificadores validados
  contra whitelist antes de interpolar (`servir.py:147` `FORBIDDEN`)
- Frontend renderiza HTML directamente desde backend (no markdown regex)

---

## 7. Gotchas

- `config.py` carga `.env` con `override=True` — una variable en
  `.env` gana sobre el entorno del shell.
- `MAX_LIMIT=1000` en `servir.py` trunca cualquier `limit` mayor; el
  frontend pagina con `offset`.
- `auth.py` hace `init_db()` y `ensure_default_admin()` **al import** —
  importar `auth` tiene side effects sobre `bases/auth.db`.
- Las ATTACH de las bases externas son **por-sesión** en DuckDB.
  `_init_extended_con()` las recrea en cada arranque.
- **MÚLTIPLES dicts `attached`** en servir.py: uno en `_init_extended_con`
  (servir.py ~9987) y otro DISTINTO en `_enriquecer_bases_externas`
  (servir.py ~11053). Si omites una base en cualquiera de los dos,
  la base se attachea pero NUNCA se consulta (fallo silencioso).
- Padrón columnas cortas: `e` (estado), `d` (distrito), `m` (municipio),
  `s` (sección), `l` (localidad), `mza` (manzana).
- Teléfono exact match: `REGEXP_REPLACE`, NUNCA `LIKE '%x%'`.
- CFE cp 78% NULL: buscar por calle, no por cp.
- CheckID `exitoso=True` NO significa que existe el sujeto.
- xwalk.nombre_rfcs es ÍNDICE de apellidos, NO resuelve identidad.
- `report_generator.py` con `rfc_singula=None` causa bug → usar `{}`.
- PDF: weasyprint, NUNCA LibreOffice (destruye CSS).
- Backend restart: `sudo kill -9` + `sudo systemctl start` (no restart solo).
- DuckDB SQL string quotes: usar `'texto'` no `"texto"` (DuckDB interpreta
  `"` como identificador de columna).
- Encoding ISO-8859/Latin-1: convertir con `iconv` ANTES de DuckDB (más
  rápido y confiable que el parámetro `encoding=` de `read_csv`).
- `docs/operativos/` contiene datos reales de sujetos. No subir a repos
  públicos.

---

## 8. Integración de nuevas bases (5 pasos)

Para agregar una nueva base al runtime:

1. **Catalogar** (`scripts/catalogar_nuevas_bases.py`)
   → Genera `.catalog.txt` con schema, filas, PII

2. **Materializar** (`backend/materializar_*.py`)
   → Para >5M filas usar SQL puro (20x más rápido que UDF Python)
   → Para paths con espacios: hardlink primero

3. **EXTENDED_DBS** en `servir.py:9854`
   → `"b_<name>": (str(ROOT.parent / "bases" / "<file>.duckdb"), "main.<table>")`

4. **View api.<name>_lineas_full**
   → Mismo shape que views existentes (loop dinámico en `_nuevas_bases_personas`
   si la base usa `main.personas`)

5. **Wire lookup en 3 lugares:**
   - dict `attached` en `_init_extended_con` (servir.py ~9987)
   - dict `attached` en `_enriquecer_bases_externas` (servir.py ~11053)
   - out dict init + bloque de lookup específico

---

## 9. Endpoints HTTP principales

### Padrón
- `GET /api/curp/<curp>` — fila padrón
- `GET /api/search` — búsqueda SQL
- `GET /api/total` — total padrón

### Sujeto (endpoints principales)
- `GET /api/sujeto?curp=<curp>` — endpoint principal
- `GET /api/v1/persona/rfc/<rfc>/todo` — RFC → extendido
- `GET /api/v1/persona/curp/<curp>/todo` — CURP → extendido
- `GET /api/v1/sujeto/enriquecido` — multi-input (nombre/RFC/CURP)
- `GET /api/v1/sujeto/inteligencia_completa` — mapeo relacional
- `GET /api/v1/sujeto/validar/<entidad>` — unificado por entidad
- `GET /api/v1/sujeto/validar/<entidad>/<base>` — granular
- `GET /api/v1/sujeto/resolver_desde_hint` — resolver RFC

### Búsqueda manual (bag-of-words)
- `GET /api/v1/cfe/buscar_domicilio` — CFE fuzzy
- `GET /api/v1/cfe/buscar_avanzado` — CFE bag-of-words
- `GET /api/v1/<banco>/buscar` — 8 bancos (santander, hsbc, banorte, etc.)
- `GET /api/v1/issste/buscar_avanzado` — ISSSTE
- `GET /api/v1/att/buscar_avanzado` — ATT
- `GET /api/v1/telcel/buscar_avanzado` — Telcel

### Teléfono (exacto)
- `GET /api/v1/telcel/buscar?telefono=` — Telcel v1 (9.7M)
- `GET /api/v1/telcel_2/buscar?telefono=` — Telcel v2 (44.6M)
- `GET /api/v1/att/buscar?telefono=` — ATT
- `GET /api/v1/sujeto/validar/telcel?telefono=` — 4 bases Telcel unificadas

### CFE
- `GET /api/v1/persona/cfe/<num_servicio>` — por número
- `GET /api/v1/cfe/coordenadas` — GPS → Nominatim → CFE

### Oráculo
- `POST /api/oraculo` — chat LLM
- `GET /api/oraculo/memoria` — memoria tenant
- `GET /api/oraculo/audit` — audit log

### Reportes
- `GET /api/report/html?curp=` — HTML
- `GET /api/report/pdf?curp=` — PDF (weasyprint)
- `POST /api/report/generate` — JSON + IA

### Auth / Admin
- `POST /api/auth/login/password` — login
- `GET /api/admin/stats` — estadísticas

---

## 10. Providers externos

| Provider | Tipo | Costo | Descripción |
|----------|------|-------|-------------|
| Tlaloc (RENAPO) | API | Gratis | Validación CURP oficial |
| CheckID | API | Por consulta | RFC, régimen fiscal, 69/69B SAT |
| Singula | API | $10-$42 | Judicial, blacklist, dossier premium |
| Apify | API | Por uso | Instagram scraping |
| Moffin | API | Por consulta | Buró de crédito |
| Kiban | API | Por consulta | Buró de crédito |
| Buró | API | Por consulta | Buró de crédito |
| Círculo | API | Por consulta | Buró de crédito |
| Gemini | API | Por uso | Validación IA de matches CFE |
| Ollama Cloud | API | Por uso | LLM para Oráculo (gpt-oss:120b) |

---

## 11. Autenticación y multi-tenant

- Auth: Bearer token (no sesión persistente)
- `POST /api/auth/login/password {username, password}` → token
- Multi-tenant (oráculo): tabla `tenants` en auth.db
- Roles: owner | admin | member | viewer
- API keys por usuario encriptadas con Fernet (auth.key — NUNCA BORRAR)
- Memory DB: `oraculo_mem_<tenant_id>.db`
- Audit DB: `oraculo_audit.db` (compartido)

---

## 12. Métricas

- 29 bases attacheadas, ~342M filas, ~38 GB en disco
- 88.4M padrón electoral (tabla maestra)
- servir.py: 11,773 líneas
- 270 tests en 22 archivos
- Lookup por RFC: <1s
- Inteligencia completa (102 sujetos): ~8s
- Oráculo simple: <5s
- Puerto: 127.0.0.1:8765
- Tunnel: kyc.sebastianvernis.space (HTTP2)

---

## 13. Documentos de referencia

| Documento | Contenido |
|-----------|-----------|
| `RESUMEN_PROYECTO.md` | Resumen completo actualizado (secciones 1-18) |
| `HANDOFF_PROYECTO.md` | Estado al 2026-08-22, 3 rondas completadas |
| `HANDOFF_NORMALIZACION.md` | Fase de normalización + integración masiva (38KB) |
| `INVENTARIO_BASES.md` | Inventario detallado de las 29 bases |
| `ANALISIS_BUSQUEDAS_LAYOUTS.md` | Layouts de búsqueda |
| `docs/PLAN_ESCALABILIDAD.md` | Plan de escalabilidad |
| `docs/PLAN_MAPEO_MASIVO.md` | Plan de mapeo masivo |
| `wiki/` | Knowledge base GitNexus |
