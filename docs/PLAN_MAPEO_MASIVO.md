# Plan de Mapeo Masivo — 86M sujetos del Padrón

**Estado:** Pendiente de aprobación
**Fecha:** 2026-09-06

---

## Objetivo

Mapear los 86.1M CURPs únicos del padrón electoral contra las bases locales (IMSS, ATT, Telcel, REPUVE, Empleadores) y almacenar los resultados en la tabla D1 `perfil_completo`.

---

## Alcance

### Incluido
- Padrón Electoral → datos de identidad y dirección
- IMSS Asegurados → patrones, sueldos, NSS
- IMSS Segmentación → salud, unidad médica, prioridades
- Almacenamiento en D1 (o DuckDB local como alternativa)

### Excluido (por ahora)
- **CFE** — búsqueda por nombre = 1.2s/registro (no viable para 86M)
- **RFC-based** (ATT, Telcel, REPUVE, Empleadores) — requiere extraer RFC primero
- **CheckID** — costo de API externa ($$$)

---

## Benchmarks

| Operación | Tiempo/batch (1000) | Tiempo/registro |
|-----------|---------------------|-----------------|
| Padrón | 1,785ms | 1.79ms |
| IMSS Asegurados | 623ms | 0.62ms |
| IMSS Segmentación | 267ms | 0.27ms |
| **Total** | **2,676ms** | **2.68ms** |

---

## Estimación de tiempo

| Workers | Tiempo total | Días |
|---------|--------------|------|
| 1 | 64 horas | 2.7 días |
| 2 | 32 horas | 1.3 días |
| 4 | 16 horas | 0.7 días |
| 8 | 8 horas | 0.3 días |

---

## Almacenamiento

- ~276 columnas × 86M registros
- Promedio ~500 bytes/registro → **~43 GB**
- D1 límite: 10 GB por database
- **Alternativa:** DuckDB local como almacenamiento intermedio

---

## Fases de implementación

### Fase 1: Mapeo CURP-based (Padrón + IMSS)
- Script batch que procesa 1000 CURPs por vez
- Extrae datos de Padrón, IMSS Asegurados, IMSS Segmentación
- Almacena en tabla local (DuckDB o SQLite)
- Tiempo estimado: 64hr (1 worker) / 8hr (8 workers)

### Fase 2: Enriquecimiento RFC-based
- Extraer RFC del padrón o de IMSS
- Bulk lookups en ATT, Telcel, REPUVE, Empleadores
- Actualizar perfiles existentes

### Fase 3: CFE por nombre (opcional)
- Búsqueda por nombre en CFE (1.2s/registro)
- Solo para perfiles de alta prioridad
- Opcional: skip por volumen

### Fase 4: Migración a D1
- Migrar perfiles de DuckDB local a Cloudflare D1
- Requiere particionar por volumen (10GB límite)

---

## Recursos necesarios

- **CPU:** mínimo 4 cores para paralelismo
- **RAM:** ~4 GB (DuckDB in-memory para batches)
- **Disco:** ~50 GB libres para almacenamiento intermedio
- **Tiempo:** 8-64 horas dependiendo de workers

---

## Riesgos

| Riesgo | Probabilidad | Impacto | Mitigación |
|--------|--------------|---------|------------|
| Disco lleno durante escritura | Media | Alto | Monitorear `df`, batches pequeños |
| DuckDB lock durante lectura | Baja | Medio | Usar read_only, batches secuenciales |
| Memoria insuficiente | Baja | Medio | Batches de 1000, no cargar todo en RAM |
| Timeout en queries | Baja | Bajo | Índices ya creados (Fase 0.5) |

---

## Criterios de éxito

- [ ] 86M CURPs procesados sin errores
- [ ] Tiempo total < 72 horas (1 worker)
- [ ] Sin OOM ni crashes
- [ ] Almacenamiento < 50 GB
- [ ] Perfiles consultables después del mapeo

---

## Próximos pasos

1. Aprobar este plan
2. Implementar script de mapeo batch
3. Ejecutar en horario de baja demanda
4. Monitorear recursos durante ejecución
5. Generar reporte de resultados
