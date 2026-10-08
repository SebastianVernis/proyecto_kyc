# PLAN DE GESTIÓN — Escalabilidad KYC (Workers + FastAPI + DB-gateway)

**Documento para revisión.** No se ejecuta ninguna fase hasta aprobación explícita.

---

## 0. METADATOS

| Campo | Valor |
|---|---|
| Proyecto | Escalabilidad de la plataforma KYC (Argos OSINT) |
| Patrocinador | Sebastián Vernis |
| Autor del plan | Equipo técnico (opencode) |
| Fecha | 2026-09-05 |
| Versión | 1.0 (borrador para revisión) |
| Estado | **Pendiente de aprobación** |
| Repositorio | `/mnt/disco2/projects/kyc/proyecto_kyc` |
| Hardware objetivo | i5-12500 (12 vCPU), 31 GB RAM, NVMe 221 GB (96 GB libres) |

---

## 1. RESUMEN EJECUTIVO

Migrar la plataforma de un monolito `ThreadingHTTPServer` (`backend/servir.py`, 14 225 líneas, 0 tests) a una arquitectura desacoplada de tres capas:

1. **Cloudflare Worker** sirve los assets del frontend y proxeaa la API con caché edge y rate limiting.
2. **Backend Python (FastAPI)** mantiene la lógica de negocio (auth, audit, billing, orquestación de providers).
3. **DB-gateway (FastAPI)** encapsula el acceso a 30 DuckDB (~47 GB) + SQLite, exponiendo endpoints tipados y administrando un pool de conexiones precalentado.

La migración se ejecuta **en paralelo** con el sistema actual (`servir.py` se mantiene hasta Fase 7), sin downtime. Un prerrequisito crítico no negociable es la **creación de índices** en las tablas grandes antes de cualquier otra optimización: `padron_v1` (88 M filas) hace full scan y devuelve consultas por CURP en 200–300 ms; sin índice, ninguna capa superior aporta mejora.

**Resultado esperado:** consultas KYC p50 < 50 ms (cacheadas) / < 200 ms (frías), 100–1 000 usuarios concurrentes con picos, base lista para escalar a un VPS dedicado (≥64 GB RAM) cuando el producto lo justifique.

---

## 2. ALCANCE

### 2.1 Dentro del alcance

- Worker de Cloudflare con `Static Assets` y `Hono`.
- Reescritura de `backend/servir.py` a `backend/app/` con FastAPI por routers.
- Creación del servicio `db-gateway/` independiente.
- Creación de `docker-compose.yml` para orquestar backend + gateway + (opcional) Redis.
- Pool de DuckDB precalentado selectivo (8 DBs grandes).
- Índices en DuckDB para los campos consultados (`curp`, `nss`, `telefono`, `num_servicio`, `placa`, `rfc`).
- Suite mínima de tests `pytest` + `httpx.AsyncClient`.
- Caché edge en Cloudflare (Cache API + KV para queries de usuario).
- Rate limiting en el Worker (KV token bucket).
- Observabilidad: `/health` enriquecido, logs JSON estructurados.
- Documentación operativa: README, AGENTS.md actualizado, HANDOFF.

### 2.2 Fuera del alcance

- Migración de DuckDB a Postgres / ClickHouse (Fase futura, no acordada).
- Migración del frontend a SPA con Vite/React (se mantiene como HTML estático).
- Replicación multi-región.
- Cambio de providers externos (Singula, CheckID, Tlaloc, Apify).
- Rediseño del modelo de datos o schema de las DuckDB.
- Trabajo de normalización de las bases (proyecto `ANALISIS_BUSQUEDAS_LAYOUTS.md`, ya en curso paralelo).

---

## 3. OBJETIVOS Y CRITERIOS DE ÉXITO

### 3.1 Objetivos de negocio

| ID | Objetivo | Métrica | Baseline | Objetivo |
|---|---|---|---|---|
| ON-1 | Aceptar 100–1 000 usuarios concurrentes | RPS sostenido sin 5xx | ~50 (ThreadingHTTPServer) | ≥ 200 RPS |
| ON-2 | Latencia consistente en KYC | p50 `/api/sujeto?curp=` | ~300 ms (medido) | < 50 ms (cacheado) / < 200 ms (frío) |
| ON-3 | Cero downtime durante la migración | Disponibilidad | n/a | 99.5 % durante migración |
| ON-4 | Desacoplar presentación de datos | Routers testeables independientemente | 0 % routers testeables | 100 % routers críticos con test |

### 3.2 Objetivos técnicos

| ID | Objetivo | Métrica | Baseline | Objetivo |
|---|---|---|---|---|
| OT-1 | Reducir queries full-scan | `% de queries KYC con TABLE_SCAN` | ~100 % en padron, imss, telcel | < 10 % |
| OT-2 | Eliminar `connect()` por request | `duckdb.connect()` por segundo | 144/s en pico medido | ≤ 2/s (solo al prewarm) |
| OT-3 | Tener cobertura de tests mínima | `% routers con al menos 1 test` | 0 % | ≥ 80 % de routers migrados |
| OT-4 | Operación reproducible | Despliegue con un comando | Manual, 30+ pasos | `docker compose up -d` |

### 3.3 Criterios de éxito globales (gate de cierre)

El proyecto se considera **exitoso** cuando, durante 7 días consecutivos en producción:

- p95 de `/api/sujeto?curp=` ≤ 300 ms (sin caché), ≤ 80 ms (con caché edge).
- 0 incidentes de OOM o pérdida de conexión DuckDB.
- 0 regresiones funcionales detectadas por usuarios.
- `servir.py` apagado y eliminado del servicio systemd.

---

## 4. FASES Y ENTREGABLES

### Fase 0.5 — Índices DuckDB (PRERREQUISITO)

| Campo | Valor |
|---|---|
| Duración estimada | 1 día |
| Dependencias | Ninguna (debe ir primero) |
| Riesgo | Medio (espacio en disco, tiempo de creación, locks) |
| Aprobación especial | **Requerida antes de ejecutar** (impacto en espacio) |

**Acciones:**
1. Inventariar queries reales por campo desde `backend/servir.py` (grep `WHERE` por tabla).
2. Generar script `scripts/create_indexes.py` extendido para crear índices:
   - `padron_v1.padron(curp)`
   - `imss_asegurados_v1.imss_2025(nss, curp)`
   - `imss_segmentacion_v1.<tabla>(curp, nss)` — verificar nombre exacto
   - `telcel_v1..v4.<tabla>(telefono)`
   - `cfe_v1.medidores(num_servicio)`
   - `issste_v1.empleados(curp, rfc)`
   - `repuve_v1.<tabla>(placa)`
   - `att_v1.<tabla>(telefono)`
3. Modo `--dry-run` que liste tamaños estimados sin ejecutar.
4. Ejecución real, midiendo tiempo por índice.
5. Re-medir con benchmark: `padron by curp` debe pasar de 200–300 ms a ≤ 10 ms.

**Entregables:**
- `scripts/create_indexes.py` con soporte dry-run y reporte de tamaños.
- `reports/fase_0_5_benchmark.md` (antes/después).
- Confirmación de espacio en disco usado.

**Criterio de salida:** benchmark muestra `padron by curp` ≤ 10 ms en p95.

---

### Fase 1 — Docker Compose base

| Campo | Valor |
|---|---|
| Duración estimada | 0.5 día |
| Dependencias | Fase 0.5 |

**Acciones:**
1. Crear `docker-compose.yml` con servicios `backend`, `gateway`, red interna `kyc-net`, volumen `bases-vol` montado en `gateway` y `backend` (solo SQLite chicas).
2. Dockerfile multi-stage para `backend` (python:3.13-slim, sin duckdb si no lo necesita) y para `gateway` (python:3.13-slim + duckdb + uvicorn).
3. Variables de entorno vía `.env` (no commiteado): `GATEWAY_SHARED_SECRET`, `BASES_DIR`, `AUTH_DB_PATH`, etc.
4. Healthchecks: `curl localhost:8000/health` para backend, `curl localhost:8001/health` para gateway.

**Entregables:**
- `docker-compose.yml`, `backend/Dockerfile`, `db-gateway/Dockerfile`, `.env.example`.
- `docs/operativos/DOCKER.md`.

**Criterio de salida:** `docker compose up -d` levanta ambos contenedores, `/health` responde 200 en ambos.

---

### Fase 2 — DB-gateway skeleton

| Campo | Valor |
|---|---|
| Duración estimada | 1 día |
| Dependencias | Fase 1 |

**Acciones:**
1. Estructura `db-gateway/app/` con `main.py`, `pools.py`, `routers/`.
2. `DuckDBPool` con:
   - Lista configurable de DBs precalentadas (default: 8 grandes).
   - Lazy-load para el resto.
   - `asyncio.Lock` por archivo (duckdb read_only no es multi-thread-safe con threads>1 en escenarios complejos; serializar es más simple y suficiente).
3. `routers/padron.py` con `GET /q/padron/by-curp/{curp}`.
4. `routers/health.py` con `GET /health` y `GET /health/bases` (lista tamaño + mtime).
5. Auth: bearer `GATEWAY_SHARED_SECRET` por middleware.
6. Prewarm selectivo en `startup` event de FastAPI.

**Entregables:**
- `db-gateway/app/` con pool funcional.
- `db-gateway/tests/test_pools.py` y `test_padron_router.py`.

**Criterio de salida:** `/q/padron/by-curp/{curp}` devuelve ≤ 50 ms en local, ≤ 100 ms desde backend en LAN.

---

### Fase 3 — Tests base (pytest + httpx)

| Campo | Valor |
|---|---|
| Duración estimada | 1 día |
| Dependencias | Fase 2 |

**Acciones:**
1. Configurar `pytest.ini`, `conftest.py` con `httpx.AsyncClient`.
2. Mock del DB-gateway en tests del backend (fixture `mock_gateway`).
3. Smoke tests por router nuevo (geo, sepomex, padron).
4. CI local: hook pre-commit que corre `pytest -x` (opcional, no obligatorio en esta fase).

**Entregables:**
- `tests/conftest.py`, `tests/test_smoke.py`, fixtures por router migrado.

**Criterio de salida:** `pytest` corre en < 30 s, 100 % verde, sin red externa.

---

### Fase 4 — Worker de Cloudflare

| Campo | Valor |
|---|---|
| Duración estimada | 1 día |
| Dependencias | Ninguna (puede ir en paralelo con Fase 2-3) |

**Acciones:**
1. Crear `worker/wrangler.jsonc`, `worker/src/index.ts` con Hono.
2. `assets: { directory: "../frontend", binding: "ASSETS" }`.
3. `app.use("/api/*", proxyToBackend)` con `fetch()`.
4. Cache API para `/api/v1/sepomex/*`, `/api/v1/geo/*`, `/health` con TTL 1 h–24 h.
5. KV namespace `RATE_LIMIT` con token bucket por `user_id` (cookie) o `cf-connecting-ip`.
6. Secret `API_ORIGIN` con `wrangler secret put`.

**Entregables:**
- `worker/wrangler.jsonc`, `worker/src/index.ts`, `worker/package.json`.
- Despliegue en entorno de staging (`*.workers.dev` o subdominio de prueba).

**Criterio de salida:** `curl https://kyc-staging.tu-dominio.com/api/health` responde 200 desde el Worker, con `cf-cache-status: HIT` en el segundo hit.

---

### Fase 5 — Portar routers read-only (paralelo a `servir.py`)

| Campo | Valor |
|---|---|
| Duración estimada | 2 semanas |
| Dependencias | Fases 2 y 3 |

**Acciones (en este orden):**
1. `geo.py` + `sepomex.py` (más simples, validan el patrón).
2. `padron.py` (el más caliente, valida el rendimiento).
3. `telcel.py` (4 versiones, útil para probar el patrón "multi-base").
4. `cfe.py`, `issste.py`, `att.py`, `repuve.py`, `banco.py`, `empleadores.py`.
5. Cada router:
   - Nuevo en `backend/app/routers/<nombre>.py`.
   - Cliente HTTP al gateway (`backend/app/clients/gateway.py`).
   - Smoke test + test unitario.
   - Validación manual con curl comparando respuesta vieja vs nueva.
   - Switch del Worker para ese path específico: `path === "/api/v1/padron/*" → nuevo; resto → viejo`.

**Entregables:**
- Routers funcionales en `backend/app/routers/`.
- Cada uno con su test.

**Criterio de salida:** 100 % de los endpoints read-only migrados, validados manualmente y con tests.

---

### Fase 6 — Portar routers con escritura y orquestación

| Campo | Valor |
|---|---|
| Duración estimada | 2 semanas |
| Dependencias | Fase 5 |

**Acciones:**
1. `auth.py` (WebAuthn, sesión, login password).
2. `audit.py` (middleware de auditoría).
3. `billing.py` (suscripciones, Clip, payments).
4. `admin.py` (gestión de usuarios, KYC pending).
5. `reports.py` (generación, listado, archivado).
6. `oraculo.py` (motor + memoria + audit; usa SQLite local).
7. `providers.py` (Singula, CheckID, Tlaloc, Apify, Gemini).
8. `sujeto.py` (orquesta múltiples routers; el más complejo).
9. `kyc.py` (verificación).
10. Validación con `servir.py` viejo en paralelo: ambos responden idéntico para los mismos inputs.

**Entregables:**
- Todos los routers migrados.
- Suite de tests ampliada.

**Criterio de salida:** diff manual del 5 % de requests muestra respuestas equivalentes.

---

### Fase 7 — Apagar `servir.py`

| Campo | Valor |
|---|---|
| Duración estimada | 1 día |
| Dependencias | Fases 5 y 6 completas |

**Acciones:**
1. Apuntar Worker 100 % al backend FastAPI.
2. Apagar `cuartodepazsearch.service` (systemd).
3. Backup de `servir.py` en `docs/archive/servir.py.2026-XX-XX.bak`.
4. Eliminar `backend/servir.py` del repo (commit separado, mensaje claro).
5. Actualizar `AGENTS.md`, `HANDOFF_PROYECTO.md`.

**Entregables:**
- Commit de eliminación de `servir.py`.
- Docs actualizados.

**Criterio de salida:** 0 referencias a `servir.py` en código o configuración.

---

### Fase 8 — Hardening y observabilidad

| Campo | Valor |
|---|---|
| Duración estimada | 1 semana |
| Dependencias | Fase 7 |

**Acciones:**
1. Logs JSON estructurados (`structlog` en backend, `loguru` en gateway).
2. `/health` enriquecido en backend y gateway: latencia p50/p95 por endpoint, último rsync, espacio en disco.
3. Cloudflare Analytics + Logpush configurado.
4. Dashboards básicos (Grafana opcional, Cloudflare Analytics nativo por ahora).
5. Rate limits ajustados según métricas reales (60 rpm anónimo, 600 rpm autenticado).
6. Runbook de incidentes en `docs/operativos/RUNBOOK.md`.

**Entregables:**
- Dashboards, runbook, alertas configuradas.

**Criterio de salida:** equipo puede responder a un incidente de latencia sin abrir código.

---

## 5. CRONOGRAMA

| Fase | Inicio | Fin | Duración | Hito de cierre |
|---|---|---|---|---|
| 0.5 Índices | T0 | T0+1d | 1 d | Benchmark `padron by curp` ≤ 10 ms |
| 1 Docker Compose | T0+1d | T0+1.5d | 0.5 d | `docker compose up` levanta ambos |
| 2 DB-gateway skeleton | T0+1.5d | T0+2.5d | 1 d | `/q/padron/by-curp` funcional |
| 3 Tests base | T0+2.5d | T0+3.5d | 1 d | `pytest` verde |
| 4 Worker CF | T0+3.5d | T0+4.5d | 1 d | Staging en `*.workers.dev` |
| 5 Routers read-only | T0+4.5d | T0+16.5d | 2 sem | 100 % read-only migrados |
| 6 Routers write/orq | T0+16.5d | T0+28.5d | 2 sem | Diff 5 % idéntico |
| 7 Apagar `servir.py` | T0+28.5d | T0+29.5d | 1 d | Servicio systemd dado de baja |
| 8 Hardening | T0+29.5d | T0+34.5d | 1 sem | Runbook + dashboards |

**T0 = fecha de aprobación de este plan.**

---

## 6. RIESGOS Y MITIGACIONES

| ID | Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|---|
| R-1 | Crear índices llena el disco (96 GB libres, +25 GB estimados) | Media | Alto | Dry-run obligatorio; checkpoints DuckDB para compactar; monitoreo de `df` durante ejecución |
| R-2 | Crear índice tarda más de lo estimado (DB lockea durante creación) | Media | Medio | Ejecutar fuera de horario pico; medir primero en una DB pequeña; tener rollback (los índices se pueden borrar) |
| R-3 | DuckDB read_only con `asyncio.Lock` se vuelve cuello de botella bajo carga | Media | Medio | Benchmark con `wrk` o `locust` antes de Fase 5; si es cuello, evaluar `threads=2` con serialización fina por tabla |
| R-4 | Workers hace fans-out y satura backend con caché MISS al inicio | Alta | Bajo | Cache warming en Fase 8; KV cache con TTL 5 min evita re-petición |
| R-5 | El monolito `servir.py` cambia durante la migración (parches urgentes) | Media | Alto | Branch separada para migración; merges selectivos; cualquier cambio en `servir.py` se evalúa para portarlo a FastAPI directamente |
| R-6 | `imss_segmentacion_v1` (7.1 GB) tiene schema distinto y no se le puede crear índice | Baja | Medio | Inventario de schema en Fase 0.5; si falla, se prioriza otro endpoint |
| R-7 | Tailscale o cambios de red rompen comunicación backend↔gateway | Baja | Alto | En Opción A no aplica (mismo host); si se migra a VPS dedicado, Tailscale se prueba en Fase de provisionamiento |
| R-8 | Cloudflare Worker tiene costo por request que excede presupuesto | Baja | Bajo | Workers free = 100k req/día; plan actual de Cloudflare cubre; supervisar tras Fase 4 |
| R-9 | Providers externos (Singula, CheckID) cambian API y rompen orquestación | Baja | Alto | Tests con mocks; los cambios se aíslan en `providers/` que ya tiene factory pattern |
| R-10 | Regresión funcional: nuevo backend devuelve datos diferentes al viejo | Alta | Alto | Diff automatizado por muestra (Fase 6); switch gradual por router; switch atrás inmediato si diff falla |

---

## 7. DEPENDENCIAS

### 7.1 Técnicas

- Python 3.13 (ya disponible).
- DuckDB con soporte para índices `CREATE INDEX` (verificar versión; si < 0.10, actualizar).
- Docker + docker compose v2.
- Cloudflare Workers (cuenta activa, dominio delegado).
- Node 20+ para build del Worker.

### 7.2 De personas

- **Aprobación del patrocinador** para Fase 0.5 (espacio en disco).
- Acceso a Cloudflare (credenciales, dominio) — gestionar antes de Fase 4.
- Acceso al VPS para `docker compose` — gestionar antes de Fase 1.

### 7.3 De otros proyectos

- **Normalización de bases** (en paralelo, `ANALISIS_BUSQUEDAS_LAYOUTS.md`): si cambia schema de `padron_v1`, los routers deben regenerar índices. Sincronizar antes de Fase 5.

---

## 8. MÉTRICAS Y MONITOREO

### 8.1 Métricas de operación (recolectar desde Fase 4)

| Métrica | Fuente | Frecuencia | Umbral alerta |
|---|---|---|---|
| RPS por endpoint | Cloudflare Analytics | Continuo | > 500 RPS sostenido |
| Latencia p50/p95/p99 | Backend + Worker logs | Continuo | p95 > 500 ms |
| 5xx ratio | Backend + Worker | Continuo | > 1 % |
| Uso de RAM | `docker stats` | 1 min | > 80 % |
| Conexiones DuckDB activas | Gateway `/health` | Continuo | = número de archivos |
| Espacio en disco | `df` | 5 min | < 20 GB libres |
| Cache hit ratio | Worker logs | Continuo | < 30 % (señal de TTL muy bajo) |

### 8.2 Métricas de proyecto (recolectar cada viernes)

- Routers migrados / total.
- Tests pasando / total.
- Issues abiertos / cerrados en la semana.
- Tiempo desde último incidente de producción.

---

## 9. CRITERIOS DE SALIDA Y ROLLBACK

### 9.1 Criterios de salida del proyecto

- 7 días consecutivos con métricas ON-2 y OT-1 en objetivo (Sección 3).
- 0 incidentes abiertos de severidad alta o crítica.
- Documentación operativa completa y revisada.

### 9.2 Criterios de rollback por fase

| Fase | Si falla… | Rollback a… | Tiempo estimado |
|---|---|---|---|
| 0.5 Índices | Disco lleno o query no mejora | `DROP INDEX` y volver a versión anterior | 5 min |
| 1 Docker Compose | Contenedor no levanta | Eliminar compose, volver a `python3 servir.py` directo | 15 min |
| 2-6 Routers | Diff con `servir.py` | Apuntar Worker al backend viejo (`servir.py`) | 5 min (cambiar `API_ORIGIN` del Worker) |
| 7 Apagar `servir.py` | Regresión crítica en producción | Restaurar `servir.py` desde `docs/archive/`, reactivar systemd | 30 min |

El rollback **siempre es posible** mientras `servir.py` siga vivo. De ahí la importancia de no eliminarlo antes de Fase 7.

---

## 10. PLAN DE COMUNICACIÓN

| Audiencia | Qué necesita saber | Cuándo | Cómo |
|---|---|---|---|
| Patrocinador | Hitos de fase, riesgos materializados | Al cierre de cada fase | Resumen ejecutivo (este doc, sección "Criterio de salida") |
| Equipo técnico | Detalles de implementación, cambios de schema | Diario durante migración | Commits, mensajes en `HANDOFF_NORMALIZACION.md` |
| Usuarios finales | Nada (transparente) | — | — |

---

## 11. PRESUPUESTO

| Concepto | Costo |
|---|---|
| Cloudflare Workers | Incluido en plan actual |
| KV namespaces (2) | ~$0.50/mes |
| Docker en VPS actual | $0 (ya hay VPS) |
| Tiempo de ingeniería | ~5 semanas (1 persona) |
| **Total incremental** | **< $5/mes** |

Si se decide migrar a VPS dedicado en el futuro (Opción B del análisis previo): ~$40–80/mes en Hetzner/OVH/DO.

---

## 12. SUPUESTOS

- El VPS actual se mantiene como destino de despliegue.
- `servir.py` queda en producción hasta Fase 7 sin cambios.
- Cloudflare ya está configurado (cuenta, dominio delegado).
- No se creará un repo separado para `db-gateway/`; vivirá como carpeta del mismo repo.
- No hay presión regulatoria que requiera cifrado en reposo más allá de lo actual.
- El equipo tiene una sola persona técnica (Sebastián), por lo que las fases se secuencian, no se paralelizan salvo donde se indica.

---

## 13. CHECKLIST DE APROBACIÓN

Antes de pasar a implementación, el patrocinador debe confirmar:

- [ ] **Alcance** (Sección 2) está completo y correcto.
- [ ] **Criterios de éxito** (Sección 3) son aceptables.
- [ ] **Fase 0.5** (crear índices) está aprobada, incluyendo el consumo de +25 GB de disco.
- [ ] **Cronograma** (Sección 5) es realista.
- [ ] **Riesgos** (Sección 6) son aceptables o requieren mitigación adicional.
- [ ] **Rollback** (Sección 9.2) es viable.
- [ ] **Presupuesto** (Sección 11) está aprobado.
- [ ] Se confirma la **arquitectura de tres capas + DB-gateway** acordada.
- [ ] Se confirma **Docker Compose** como método de despliegue (no systemd puro).
- [ ] Se confirma **prewarm selectivo de 8 DBs grandes** (no las 30).
- [ ] Se confirma que **`servir.py` se mantiene hasta Fase 7**.

---

## 14. PRÓXIMOS PASOS TRAS APROBACIÓN

1. Apertura de branch `feat/escalabilidad-v1` en el repo (o directorio de trabajo si no es repo git).
2. Ejecución de Fase 0.5 con dry-run primero, esperando visto bueno antes del run real.
3. Reporte de resultados al patrocinador antes de continuar con Fase 1.

---

**Fin del documento.** Quedo a la espera de tus comentarios, ajustes o aprobación.
