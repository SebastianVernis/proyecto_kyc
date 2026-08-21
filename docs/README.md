# Proyecto KYC consolidado

Directorio maestro de la plataforma KYC / OSINT fiscal para personas físicas
en México. Todo el código, las bases y la interfaz viven aquí — sin symlinks.

```
    cd /home/sebastianvernis/proyectos/kyc/backend
    /home/sebastianvernis/.venv/bin/python servir.py [--port 8765]
```

El backend abre en http://127.0.0.1:8765. Login: admin/admin123 (passkey+pwd).

Para verificar todo:
```
    /home/sebastianvernis/.venv/bin/python /home/sebastianvernis/proyectos/kyc/healthcheck.py
```

```
    proyectos/kyc/
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
```



## PATHS CANÓNICOS HARDCODEADOS EN EL CÓDIGO

Estos paths están escritos en el código (NO son configurables):
```
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
```

## CÓMO REGENERAR ESTE DIRECTORIO DESDE CERO

Asume que las 12 bases originales YA NO están donde estaban. Para regenerar
desde fuentes originales (Excel INE, CSVs, etc.) se necesita el flujo de
normalización documentado en scripts/.

Para clonar este directorio (mismo estado) en otra máquina:
```
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



.env (API keys), bases de runtime, logs. Ver hermes-backups/README.md.
```
