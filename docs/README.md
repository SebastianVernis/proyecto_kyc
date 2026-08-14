# Proyecto KYC consolidado

Directorio maestro de la plataforma KYC / OSINT fiscal para personas físicas
en México. Todo el código, las bases y la interfaz viven aquí — sin symlinks.

============================================================================
PATH CANÓNICO
============================================================================

    /home/sebastianvernis/proyectos/kyc/

============================================================================
CÓMO ARRANCAR EL BACKEND
============================================================================

    cd /home/sebastianvernis/proyectos/kyc/backend
    /home/sebastianvernis/.venv/bin/python servir.py [--port 8765]

El backend abre en http://127.0.0.1:8765. Login: admin/admin123 (passkey+pwd).

Para verificar todo:

    /home/sebastianvernis/.venv/bin/python /home/sebastianvernis/proyectos/kyc/healthcheck.py

============================================================================
ESTRUCTURA
============================================================================

    proyectos/kyc/
    ├── bases/                        <- 12 archivos reales (21.48 GB)
    │   ├── padron.duckdb             <- Padrón INE fusionado 2018+2022 (88.4M filas)
    │   ├── telcel.duckdb             <- 9.7M líneas Telcel
    │   ├── att.duckdb                <- 1M líneas AT&T
    │   ├── empleadores.duckdb        <- 162k patrones IMSS
    │   ├── imss_asegurados.duckdb    <- 57.7M asegurados IMSS
    │   ├── imss_segmentacion.duckdb  <- 23.8M segmentación IMSS
    │   ├── repuve.duckdb             <- 1.7M vehículos REPUVE
    │   ├── fotos.duckdb              <- 14,942 fotos FOTOSMX
    │   ├── cfe.duckdb                <- 66M medidores CFE
    │   ├── cfe_parquet/              <- parquet CFE por región
    │   ├── auth.db                   <- SQLite: usuarios + passkeys + sesiones
    │   ├── geo.db                    <- SQLite: Marco Geoestadístico INEGI EdoMex
    │   └── sepomex.db                <- SQLite: 155k CP/colonia SEPOMEX
    │
    ├── hermes-backups/               <- tarballs de respaldo del estado del agente
    │   ├── hermes-state-YYYYMMDD_HHMMSS.tar.gz
    │   └── README.md
    │
    ├── backend/                      <- código Python del backend
    │   ├── servir.py                 <- servidor HTTP principal (puerto 8765)
    │   ├── kyc_broker.py             <- orquestador de proveedores KYC
    │   ├── apify_broker.py           <- broker Apify
    │   ├── osint.py / osint_scorer.py
    │   ├── auth.py                   <- WebAuthn + fallback password
    │   ├── config.py                 <- carga .env (dotenv override=True)
    │   ├── deploy.py                 <- ciclo de vida local + cloudflared
    │   ├── report_generator.py       <- reporte AI HTML/PDF de 8 páginas
    │   ├── rfc_utils.py              <- cálculo local de RFC
    │   ├── sepomex_download.py       <- descarga catálogo SEPOMEX
    │   ├── normalizar*.py            <- variantes del script de normalización
    │   ├── providers/                <- clientes externos (CheckID, Singula, etc.)
    │   ├── .env                      <- API keys (singula, tlaloc, apify, etc.)
    │   ├── .env.example
    │   ├── install-service.sh
    │   └── cuartodepazsearch.service.template
    │
    ├── frontend/                     <- HTMLs de la UI
    │   ├── buscar.html               <- listado de búsqueda con filtros
    │   ├── sujeto.html               <- ficha del sujeto + validaciones
    │   └── login.html                <- login WebAuthn/passkey + password
    │
    ├── scripts/                      <- scripts de normalización one-shot
    │   ├── att_normalizar_rfc.py
    │   ├── att_to_duckdb.py
    │   ├── cfe_normalizer.py         <- normalizador CFE (21 cols reales)
    │   ├── cfe_verify.py
    │   ├── empleadores_convert.py
    │   ├── imss_normalizar_asegurados.py
    │   ├── imss_normalizar_segmentacion_37.py
    │   ├── imss_normalizar_segmentacion_limpia.py
    │   └── normalizar*.py            <- variantes para padrón
    │
    ├── docs/
    │   ├── README_consolidadas.md    <- descripción bases_consolidadas
    │   ├── PLAN_INTEGRACION.md
    │   ├── DEPLOY_kyc.md
    │   ├── CFE_flujo_coordenadas.md
    │   └── src_docs/                 <- docs originales de src/ (análisis, reportes)
    │
    ├── healthcheck.py
    ├── healthcheck.last.json
    └── README.md


============================================================================
CATÁLOGO DE BASES (verificado con healthcheck)
============================================================================

Base                     | Tipo   | Filas     | Uso principal
-------------------------+--------+-----------+------------------------------------------
padron.duckdb            | DuckDB | 88,402,547| Búsqueda por nombre/CURP/RFC/CP/colonia.
telcel.duckdb            | DuckDB |  9,709,461| Tabla telcel cruda + telcel_valid.
att.duckdb               | DuckDB |  1,048,575| att + att_valid.
empleadores.duckdb       | DuckDB |    161,933| Patrones IMSS empleadores.
imss_asegurados.duckdb   | DuckDB | 57,760,242| Asegurados IMSS, imss_valid limpio.
imss_segmentacion.duckdb | DuckDB | 23,803,445| Segmentación IMSS.
repuve.duckdb            | DuckDB |  1,745,627| Vehículos emplacados, repuve_valid.
fotos.duckdb             | DuckDB |     14,942| Índice FOTOSMX (FIRMAS+ROSTROS).
cfe.duckdb               | DuckDB | 66,003,291| Medidores CFE con headers reales.
auth.db                  | SQLite |        43| 5 usuarios, passkeys, sesiones.
geo.db                   | SQLite |    319,586| 160k manzanas + 155k CP + 125 mpios.
sepomex.db               | SQLite |    155,040| Catálogo SEPOMEX.

TOTAL: 21.48 GB, 341,909,320 filas consultables.


============================================================================
PATHS CANÓNICOS HARDCODEADOS EN EL CÓDIGO
============================================================================

Estos paths están escritos en el código (NO son configurables):

  config.py:
    DEFAULT_PADRON_DB = /home/sebastianvernis/proyectos/kyc/bases/padron.duckdb
    PROJECT_ROOT      = /home/sebastianvernis/proyectos/kyc
    GEO_DB_PATH       = PROJECT_ROOT/bases/geo.db
    SEPOMEX_DB_PATH   = PROJECT_ROOT/bases/sepomex.db
    AUTH_DB_PATH      = PROJECT_ROOT/bases/auth.db
    INEGI_BASE        = /home/sebastianvernis/Descargas/inegi/15_mexico/conjunto_de_datos

  auth.py:
    DB_PATH           = /home/sebastianvernis/proyectos/kyc/bases/auth.db

  servir.py:
    GEO_DB            = /home/sebastianvernis/proyectos/kyc/bases/geo.db
    INEGI_BASE        = /home/sebastianvernis/Descargas/inegi/15_mexico/conjunto_de_datos
    KYC_ROOT          = /home/sebastianvernis/proyectos/kyc
    --db default      = /proyectos/kyc/bases/padron.duckdb
    --html default    = /proyectos/kyc/frontend/buscar.html

  osint.py:
    --db default      = /proyectos/kyc/bases/padron.duckdb

.env (en backend/):
    PADRON_DB_PATH    = /home/sebastianvernis/proyectos/kyc/bases/padron.duckdb
    API_PORT          = 8765


============================================================================
ARCHIVOS NO CONSOLIDADOS (informativo)
============================================================================

Estos quedan en su ubicación original por seguridad o decisión explícita:

- /Descargas MEGA/source/_converted/   (2 GB CSVs)
    CSVs intermedios de CDMX/EDOMEX del workflow de normalización.
    Conservados por si se necesita regenerar el padrón.

- /Descargas MEGA/source/_polars_tmp/  (9.6 GB parquet)
    Artefactos del workflow de fusión. El fusionado final ya está en bases/.

- /Descargas MEGA/source/_duckdb_tmp/  (vacío)
    Temporal de DuckDB.

- /Descargas MEGA/source/uxmal/  (repositorio clonado con .git/)
    Repositorio con código fuente.

- /Descargas MEGA/BASE INE 2021/.../src/ine.duckdb.bak_pre_fusion_20260802  (25.9 GB)
    Respaldo pre-fusión julio 2026. Conservar como rollback.

- /Descargas/CFE.7z  (4.3 GB)
    Archivo fuente comprimido de CFE, regenerable.

- /Descargas/CFE/1_BC_Tij_Mexic_Etc_2_333_954/ etc. (14 dirs)
    Carpetas con CSV originales CFE. NO borrar hasta confirmar
    que cfe.duckdb se regenera correctamente desde ellos.

- /Descargas MEGA/source.zip  (8.5 GB)
    Backup zip del source/, regenerable.

- /Descargas MEGA/BASE INE 2021.rar  (20.6 GB)
    RAR original del padrón INE, regenerable.

- /Descargas MEGA/BASE INE 2021/BASE INE 2018/src/
    Carpeta original con bak pre-fusión (25GB), docs/, y build/.
    El bak NO se borró (es el respaldo de julio 2026).
    Las docs se copiaron a docs/src_docs/ aquí.
    El build (cuartodepazsearch-build/) quedó allí.


============================================================================
COPYERS BORRADOS EN ESTA SESIÓN (2026-08-10)
============================================================================

- /Descargas MEGA/source/padron.duckdb  (8.9 GB, obsoleto confirmado)
    Dump 2022+ crudo, omite registros pre-2022 (CP 01710 = 0 filas).
    Reemplazado por bases/padron.duckdb (88.4M filas completas).

- /Descargas MEGA/BASE INE 2021/.../src/ine.duckdb  (6.6 GB, MD5-duplicado)
    MD5-idéntico a padron_fusion.duckdb. Borrado por ser duplicado exacto.

- /Descargas MEGA/source/uxmal/run-v3.3.sh.x.c  (52 KB, script cifrado)
    shc v4.0.3 cifrado. NO ejecutar. Borrado por seguridad.


============================================================================
CÓMO REGENERAR ESTE DIRECTORIO DESDE CERO
============================================================================

Asume que las 12 bases originales YA NO están donde estaban. Para regenerar
desde fuentes originales (Excel INE, CSVs, etc.) se necesita el flujo de
normalización documentado en scripts/.

Para clonar este directorio (mismo estado) en otra máquina:

    #!/usr/bin/env bash
    set -euo pipefail
    ROOT=/home/sebastianvernis/proyectos/kyc

    mkdir -p "$ROOT"/{bases,backend,frontend,scripts,docs,hermes-backups}

    # 1) Copiar bases desde fuentes externas (CSVs, normalizer.py)
    #    Ver scripts/normalizar.py y scripts/cfe_normalizer.py

    # 2) Backend: cp -r src/ del backup (o reescribir desde cero)
    #    Contiene: servir.py, kyc_broker.py, providers/, .env, etc.

    # 3) Frontend y scripts son archivos individuales (cp normal)

    # 4) Verificar:
    /home/sebastianvernis/.venv/bin/python "$ROOT/healthcheck.py"


============================================================================
TARRASTAS DE RESPALDO
============================================================================

Estado del agente empaquetado en hermes-backups/:

    hermes-state-YYYYMMDD_HHMMSS.tar.gz  (~25 MB)

Contiene: skills (198 SKILL.md), memoria (MEMORY.md, USER.md), cron jobs
declarados, hooks, scripts del usuario. NO incluye: auth.json (credenciales),
.env (API keys), bases de runtime, logs. Ver hermes-backups/README.md.
