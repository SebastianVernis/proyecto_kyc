# bases_consolidadas

Conversión a DuckDB + Parquet de las bases en `/Descargas/Bases/`.

## Bases convertidas

| Base | Tabla | Filas | DuckDB | Parquet full | Parquet partidos |
|---|---|---|---|---|---|
| att | `att` | 1,048,575 | 73 MB | 54 MB | 32 (por estado) |
| repuve | `repuve` | 1,745,627 | 218 MB | 142 MB | 1,531 (por marca) |
| imss_asegurados | `imss_2025` | 57,760,242 | 3.50 GB | 2.4 GB | — |
| imss_segmentacion | `imss_personas` | 23,803,445 | 6.43 GB | 1.44 GB | 37 (por OOAD delegación) |
| telcel | `telcel` | 9,709,461 | 2.7 GB | 870 MB | 9 (por archivo) |
| empleadores | `empleadores` | 161,933 | 47 MB | 23 MB | — |

Total en disco: ~21 GB.

## Estructura de directorios

```
bases_consolidadas/
├── att/
│   ├── duckdb/att.duckdb
│   ├── parquet_full/att_full.parquet
│   └── parquet_por_estado/att_<ESTADO>.parquet (×32)
├── repuve/
│   ├── duckdb/repuve.duckdb
│   ├── parquet_full/repuve_full.parquet
│   └── parquet_por_marca/repuve_<MARCA>.parquet (×1,531)
├── imss_asegurados/
│   ├── duckdb/imss_asegurados.duckdb
│   └── parquet_full/imss_asegurados_full.parquet
├── imss_segmentacion/
│   ├── duckdb/imss_segmentacion.duckdb
│   ├── parquet_full/imss_segmentacion_full.parquet
│   └── parquet_por_ooad/<OOAD>/part-0.parquet (×37 delegaciones)
├── telcel/
│   ├── duckdb/telcel.duckdb
│   ├── parquet_full/telcel_full.parquet
│   └── parquet_por_archivo/telcel_<N>.parquet (×9)
├── empleadores/
│   ├── duckdb/empleadores.duckdb
│   └── parquet_full/empleadores_full.parquet
├── CATALOG.md
├── PLAN_INTEGRACION.md
└── scripts/
    ├── att_normalizar_rfc.py
    ├── imss_normalizar.py
    ├── imss-normalizer/src/normalizar.py
    ├── imss-normalizer/src/normalizar_segmentacion.py
    └── telcel-normalizer/convert.py
```

## Columnas normalizadas (todas las tablas donde aplica)

- `rfc_clean` VARCHAR — RFC limpio (uppercase, A-Z0-9Ñ& only)
- `rfc_len`   SMALLINT
- `rfc_kind`  VARCHAR — `PF13` | `PM12` | `PF10` | `PM10` | `OTRO` | `NULL`
- `curp_clean` VARCHAR — CURP limpia (uppercase A-Z0-9, 18 chars)
- `curp_len`   SMALLINT
- `curp_kind`  VARCHAR — `PF18` | `PM16` | `OTRO` | `NULL`
- `nss_clean`  VARCHAR — NSS 11 dígitos, LPAD
- `cp5`        VARCHAR — Código postal 5 dígitos (cuando aplique)

Views: `<tabla>_valid` con sólo filas de CURP/RFC válidas (PF18/PM16 o PF13/PM12/PF10/PM10).

## Cómo volver a regenerar (idempotencia)

Los scripts en `scripts/` son idempotentes — corren las conversiones de cero.
Patrón genérico:

```
cd /home/sebastianvernis
source .venv/bin/activate
python Descargas/Bases/bases_consolidadas/scripts/<script>.py
```

## Tareas pendientes (no convertidas)

| Origen | Razón |
|---|---|
| `Originales/IMSSPENSIONADOS2025_TODA.rar` (1.4 GB) | No extraído |
| `Originales/mexico_8465997.part*.rar` (1.4 GB) | No extraído |
| `FOTOSMX/` (~3.5 GB, 14,942 jpg rostro/firma) | No tabular; indexable por RFC en nombre de archivo |
