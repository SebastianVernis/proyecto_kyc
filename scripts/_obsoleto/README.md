# `scripts/_obsoleto/` — scripts de normalización ya consumidos

Escribieron las bases que hoy están materializadas en `bases/`. No volver a
ejecutarlos: sobrescribirían bases en producción.

Se conservan porque documentan cómo se normalizó cada fuente (esquemas,
limpieza de RFC/CURP, conversión de CSV/XLSX/RAR a DuckDB).

| Grupo | Scripts |
|---|---|
| Normalizadores genéricos | `normalizar.py`, `normalizar_csv.py`, `normalizar_fast.py`, `normalizar_final.py`, `normalizar_optimized.py`, `normalizar_polars.py` |
| IMSS | `imss_normalizar_asegurados.py`, `imss_normalizar_segmentacion_37.py`, `imss_normalizar_segmentacion_limpia.py` |
| ATT | `att_normalizar_rfc.py`, `att_to_duckdb.py` |
| CFE | `cfe_normalizer.py`, `cfe_verify.py` |
| ISSSTE / empleadores | `normalizar_issste.py`, `empleadores_convert.py` |
| Soporte | `catalogar_nuevas_bases.py`, `create_indexes.py`, `indexar_p5.py`, `init_extended_con.py`, `sepomex_download.py`, `kyc_inv_tel.py`, `inv_tel.json` |

**Lo que sí se usa hoy:** `scripts/barrido_universal.py` (barrido de
identificadores sobre todo el corpus). Ver
`docs/METODOLOGIAS_BUSQUEDA.md`.
