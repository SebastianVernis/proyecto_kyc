# Handoff — Normalización de direcciones + clave de match multi-base

**Fecha:** 2026-08-16
**Motivo del handoff:** este server tiene solo **7.7 GB RAM** — insuficiente para
materializar las bases grandes (padrón 88M, CFE 66M) sin OOM. Continuar en el
server personal de **32 GB**, donde se puede paralelizar sin problema.

---

## 1. Qué se construyó (todo en `backend/`)

| Archivo | Qué hace |
|---|---|
| `normalizar_direccion.py` | Núcleo: layout canónico + 7 adapters + clave de match en cascada |
| `materializar_claves.py` | Materializa las claves de cada base como UDF DuckDB → `mkidx_<base>.duckdb` |
| `cruzar.py` | Cruce multi-base por clave en cascada (con derrame a disco) |

Ningún archivo existente fue modificado por esta fase (solo se agregaron estos 3).
*(Aparte, antes en la sesión se arreglaron bugs en `frontend/buscar.html` y
`backend/servir.py` — no relacionados con normalización.)*

---

## 2. Layout canónico (22 campos)

```
tipo_vialidad · tipo_vialidad_desc · nombre_vialidad
num_ext · num_ext_alfa · num_int · lote · entrecalles
tipo_asentamiento · tipo_asentamiento_desc · nombre_asentamiento
cp · municipio · entidad · entidad_clave
cve_distrito · cve_municipio · seccion · localidad · manzana
direccion_completa · _flags
```

Base ancla = **padrón INE 2018** (el domicilio más limpio y descompuesto).

### Adapters (base → canónico)
`from_padron`, `from_cfe`, `from_telcel`, `from_att`, `from_empleadores`,
`from_imss_patron`, `from_repuve`.
IMSS-segmentación, REPUVE-solo-vehículo y Singula **no** aportan dirección.

---

## 3. Clave de match en cascada

Niveles de más estricto a más laxo (en `clave_match()` / `mejor_clave()`):

| Clave | Componentes | Sentido |
|---|---|---|
| `k_via_ext` | cp · vialidad · ext | misma puerta |
| `k_col_via_ext` | colonia[:15] · vialidad · ext | puerta sin cp (puente CFE) |
| `k_via_cp` | cp · vialidad | misma calle en el CP |
| `k_col_via` | colonia[:15] · vialidad | misma calle en la colonia |
| `k_geo` | entidad · sección · manzana | micro-geo electoral (solo padrón) |

Normalización de match: sin acentos, cardinales (`OTE→ORIENTE`), stopwords
(`DE/LA/Y`), y **bag-of-words** (tokens ordenados) para absorber variación de
orden. Whitelist de tipos de vialidad/asentamiento (SAN/SANTA NO son tipos).

---

## 4. Cobertura medida (muestras reales)

| Base | Con clave | Puerta-exacta | Nota |
|---|---|---|---|
| Padrón (88M) | ~88% | ~45% | `ext` falta en rural (`S/N`) |
| CFE (66M) | ~60% | — | `cp` solo en ~9% de filas |
| Telcel (9.7M) | 100% | 86.7% | |
| ATT (1M) | ~100% | 69.9% | `exterior` en columna `interior` |
| Empleadores (162k) | 94.7% | 79.8% | muy limpio |
| IMSS patrón (57.8M) | 99.3% | 77.8% | dirección del patrón, no de la persona |
| REPUVE (1.75M) | 99.5% | 72% | **sin cp** → solo nivel colonia+vialidad |

### Prueba de fuego (validada) — CFE↔padrón en CP 98400 (Zacatecas)
**78.8% de las 11,256 filas CFE encontraron su gemela en el padrón.**
Ejemplos reales cruzados: `IGNACIO ZARAGOZA 5`, `20 NOVIEMBRE 33`, `ALFONSO MEDINA 9`.
→ El enfoque funciona de punta a punta. Falta correrlo a **escala nacional**.

---

## 5. Estado de la materialización

**Completo** (transferir tal cual):
- `bases/match_index.duckdb` (113 MB) → tablas `dir_att`, `dir_empleadores`, `dir_repuve`
- `bases/mkidx_telcel.duckdb` (601 MB) → tabla `dir_telcel`

**Pendiente** (se cayó por RAM aquí — correr en el server de 32GB):
- CFE, padrón, IMSS patrón

Cada tabla `dir_<base>` tiene: `ref, cp, via, ext, col, ent, k_via_ext,
k_col_via_ext, k_via_cp, k_col_via`.

---

## 6. Retomar en el server de 32 GB

```bash
cd /root/proyecto_kyc/backend
PY=python3   # o el venv que uses (aquí: /root/ine_server/.venv/bin/python)

# Con 32GB SÍ se puede paralelizar. Subir límites por env:
export MK_MEM=8GB MK_THREADS=4

# Materializar las 3 pendientes (en paralelo o secuencial, da igual con 32GB):
$PY materializar_claves.py out=../bases/mkidx_cfe.duckdb    cfe    &
$PY materializar_claves.py out=../bases/mkidx_padron.duckdb padron &
$PY materializar_claves.py out=../bases/mkidx_imss.duckdb   imss_patron &
wait

# PRUEBA DE FUEGO NACIONAL (lo interesante):
$PY cruzar.py cfe padron        # ¿cuántas de las 66M de CFE cruzan al padrón?
$PY cruzar.py --list            # ver bases disponibles
$PY cruzar.py telcel padron     # cualquier par
```

Rendimiento observado del UDF: ~18-24k filas/s → padrón ~60-80 min, CFE ~45-60 min
(en paralelo con 32GB, todas terminan en el tiempo de la más grande).

---

## 7. Comando rsync (correr desde el server de 32GB, o push desde aquí)

```bash
# PULL desde el server de 32GB (recomendado) — excluye _backups (65GB innecesarios):
rsync -avz --progress --exclude '_backups' \
  usuario@ESTE_SERVER:/root/proyecto_kyc/  /ruta/destino/proyecto_kyc/

# ...o TODO tal cual (110GB, incluye _backups):
rsync -avz --progress \
  usuario@ESTE_SERVER:/root/proyecto_kyc/  /ruta/destino/proyecto_kyc/
```

Tamaños: total 110 GB · `bases/` 23 GB (datos reales) · `_backups/` 65 GB (respaldos
viejos, probablemente omitibles) · `backend/` 2.5 MB (el código de esta fase).

Si solo quieres lo mínimo para continuar: `backend/` + `bases/` (~23 GB).

---

## 8. Pendientes / mejoras conocidas

1. **Puente sin-cp de CFE** (frente débil): CFE trae cp solo en ~9%; el resto
   depende de `k_col_via`. Opciones: derivar cp desde la geografía CFE
   (division/zona/agencia) o fuzzy de colonia.
2. **Municipio real**: falta catálogo código INE (`e`/`m`) → nombre de municipio.
   El resolvedor de `entidad` (estado) ya existe; el de municipio no.
3. **REPUVE**: separación colonia/municipio imperfecta (municipios multi-palabra
   como "POZA RICA" con punto interno). No afecta el match (va por colonia+vialidad).
4. **Validación nacional del catálogo**: los tipos de vialidad/asentamiento se
   midieron con ~94-96% cobertura pero en muestras sesgadas a AGS/CDMX/Chiapas.
   Verificar con muestreo estratificado por estado.
5. **Integrar** `normalizar_direccion` al endpoint `/api/v1/direccion/buscar` y al
   `report_generator.py` para que la salida al usuario ya use el layout limpio.
6. **Materializar índices unificados**: opcional, juntar todos los `dir_<base>` en
   un solo `match_index.duckdb` para queries de cruce más simples.
