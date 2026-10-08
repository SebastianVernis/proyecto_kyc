# Fase 0.5 — Benchmark de Índices DuckDB

**Fecha:** 2026-09-05
**Estado:** ✅ COMPLETADA

---

## Resumen

Se crearon 5 índices en 5 bases DuckDB para eliminar full scans en las queries de búsqueda más frecuentes. El resultado supera las expectativas: **padron por CURP pasó de 205ms a 0.6ms (342x más rápido)**.

---

## Índices creados

| # | DB | Tabla | Columna | Índice | Filas | Tiempo creación | Disco +Δ |
|---|-----|-------|---------|--------|-------|-----------------|----------|
| 1 | padron_v1.duckdb | padron | curp | idx_padron_curp | 88.4M | 3.5 min | +4,190 MB |
| 2 | cfe_v1.duckdb | medidores | numero_servicio | idx_cfe_num_servicio | 66.0M | 29.6s | +2,794 MB |
| 3 | imss_asegurados_v1.duckdb | imss_2025 | curp_clean | idx_imss_a_curp | 57.7M | 2.3 min | +3,364 MB |
| 4 | att_v1.duckdb | att | rfc_clean | idx_att_rfc | 1.0M | <1s | ~60 MB |
| 5 | empleadores_v1.duckdb | empleadores | rfc_clean | idx_emp_rfc | 162K | 0.2s | ~5 MB |

**Total:** 5 índices, ~6.3 min de creación, +10.4 GB de disco

---

## Benchmark: Antes vs Después

| DB | Columna | ANTES (p50) | DESPUÉS (p50) | Mejora | ANTES (p95) | DESPUÉS (p95) |
|----|---------|-------------|---------------|--------|-------------|---------------|
| padron_v1 | curp | **204.6ms** | **0.6ms** | **342x** | 318.2ms | 2.5ms |
| cfe_v1 | numero_servicio | 6.2ms | 0.6ms | 10x | 9.2ms | 0.9ms |
| imss_asegurados_v1 | curp_clean | **130.6ms** | **0.6ms** | **218x** | 167.5ms | 0.8ms |
| att_v1 | rfc_clean | 3.6ms | 0.5ms | 7x | 5.3ms | 0.6ms |
| empleadores_v1 | rfc_clean | 11.2ms | 2.0ms | 6x | 19.9ms | 5.1ms |

### Objetivos cumplidos

| Objetivo | Baseline | Resultado | Estado |
|----------|----------|-----------|--------|
| padron by CURP ≤ 10ms | 204.6ms | 0.6ms | ✅ |
| imss by CURP ≤ 10ms | 130.6ms | 0.6ms | ✅ |
| Cero OOM o crashes | — | 0 incidentes | ✅ |
| Disco < 96 GB usado | 95.4 GB libres | 85.0 GB libres | ✅ |

---

## Espacio en disco

| Métrica | Antes | Después | Delta |
|---------|-------|---------|-------|
| padron_v1.duckdb | 6,311 MB | 10,501 MB | +4,190 MB |
| cfe_v1.duckdb | 3,131 MB | 5,925 MB | +2,794 MB |
| imss_asegurados_v1.duckdb | 3,589 MB | 6,953 MB | +3,364 MB |
| att_v1.duckdb | 175 MB | 235 MB | +60 MB |
| empleadores_v1.duckdb | 48 MB | 53 MB | +5 MB |
| **Total** | **13,254 MB** | **23,667 MB** | **+10,413 MB** |
| Disco libre | 95.4 GB | 85.0 GB | -10.4 GB |

---

## Bases con índices existentes (no modificadas)

| DB | Índices existentes |
|----|--------------------|
| telcel_v1.duckdb | rfc_clean, telefono_clean, nombre1, nombre2 |
| telcel_v2.duckdb | rfc, nombre1, nombre2, telefono_clean |
| telcel_v1.duckdb | rfc_clean, telefono_clean, telefono |
| telcel_v4.duckdb | rfc, nombre1, nombre2, telefono_clean |
| imss_segmentacion_v1.duckdb | curp_clean, nss_clean, rfc_clean, telefono_clean, id_persona, ooad |
| issste_v1.duckdb | paterno, materno, nombres, sueldo, entidad, modalidad, sector |
| repuve_v1.duckdb | rfc_clean, nom_prop_fix |

---

## Notas técnicas

- DuckDB 1.5.5 (ART indexes)
- `att_valid` es una vista → el índice se creó en la tabla base `att`
- `imss_valid` es una vista → el índice se creó en la tabla base `imss_2025`
- `servir.py` se detuvo (SIGTERM) antes de crear índices, se reinició después
- Downtime total: ~8 minutos (3.5 min creación padron + 2.3 min imss + 29s cfe + reinicio)

---

## Script

El script actualizado está en `scripts/create_indexes.py` con soporte para:
- `--dry-run`: muestra plan sin ejecutar
- `--benchmark`: ejecuta benchmark antes/después
- `--only <db>`: procesa solo una DB específica
- `--skip-existing`: salta índices que ya existen
