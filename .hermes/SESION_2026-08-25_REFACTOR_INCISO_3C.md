# KYC S — Refactor Inciso 3c (Búsqueda Manual)

**Fecha:** 2026-08-25
**Estado:** Backend funcional pero con bugs menores en algunos endpoints
**Pendiente:** Completar fallback RFC en UNION ALL + reverificar todo

---

## Objetivo de la sesión

El usuario pidió:

> "agregale vistas y frontend a todos los endpoints individuales, y modularizas la estructura de creación del sujeto para que se genere de la siguiente forma, va a buscar los rubros que actualmente valida, hasta el inciso 3b, a partir de ahi, optaremos por validacion manual de los resultados, con bag of words sobre el parametro determinante de cada base de datos"

Reglas:
- **Hasta 3b:** automático (sin cambios)
- **Desde 3c:** validación manual con bag of words multi-parámetro
- **Bancarias:** paterno/materno/nombre(s) individuales, REGEX en MÁS DE UNO
- **CFE:** bag of words sobre calle/número/colonia/CP, con limpieza
- **Endpoints individuales** con sus propias vistas frontend
- **8 bancos separados**, cada uno con su formulario
- **Inputs pre-rellenados** pero NO bloqueados (operador puede editar)
- **TODOS los candidatos** devueltos (sin score mínimo estricto) — operador elige

El usuario primero dijo "NO, hasta 3b", confirmando que el corte automático es 3b.

---

## Cambios realizados

### Backend

#### 1. `backend/inteligencia_completa.py` — nuevos helpers de normalización

Funciones agregadas (líneas ~1-200):
- `_quitar_acentos(s)`: `á → a`, `é → e`, etc.
- `_normalizar_basico(s)`: lowercase + sin acentos + trim
- `_normalizar_nombre(s)`: para paterno/materno/nombres
- `_normalizar_calle(s)`: expande abreviaciones (`AV → AVENIDA`, `C → CALLE`)
- `_normalizar_colonia(s)`: quita prefijos (`COL`, `FRACC`, `BARRIO`)
- `_normalizar_cp(s)`: 5 dígitos con padding
- `_tokenizar(s)`: split en palabras
- `_bow_score(query_tokens, record_tokens)`: bag of words
- `_regex_match_multi(query_tokens, record_tokens)`: match multi-parámetro

#### 2. `backend/busqueda_manual.py` (NUEVO, ~1100 líneas)

Funciones principales:
- `buscar_cfe_avanzado(calle, numero, colonia, cp, limit=200)`: bag of words sobre dirección
- `buscar_banco(entidad, paterno, materno, nombre, nombre_completo, rfc, curp, cp, telefono, limit=100)`: multi-modo
- `buscar_issste_avanzado(paterno, materno, nombre, cargo, sexo, limit=100)`
- `buscar_att_avanzado(paterno, materno, nombres, limit=100)`
- `buscar_telcel_avanzado(rfc, paterno, materno, nombre, limit=100)`

Handlers HTTP:
- `handle_cfe_buscar_avanzado(handler)`
- `handle_banco_buscar(handler, entidad)` — genérico, usado para los 8 bancos
- `handle_issste_buscar_avanzado(handler)`
- `handle_att_buscar_avanzado(handler)`
- `handle_telcel_buscar_avanzado(handler)`

Mapeo `BANCO_VISTAS`:
```python
BANCO_VISTAS = {
    "santander": ["api.santander_1_cuentas_full", "api.santander_2_cuentas_full",
                  "api.santander_3_cuentas_full", "api.santander_5_cuentas_full",
                  "api.santander_6_cuentas_full", "api.santander_7_cuentas_full"],
    "hsbc": ["api.hsbc_cuentas_full", "api.hsbc_1_cuentas_full", "api.hsbc_2_cuentas_full"],
    "banorte":     ["api.banorte_cuentas_full"],
    "bancomer":    ["api.bancomer_cuentas_full"],
    "citibanamex": ["api.citibanamex_cuentas_full"],
    "bancoppel":   ["api.bancoppel_cuentas_full"],
    "amex":        ["api.amex_cuentas_full"],
    "clavijero":   ["api.clavijero_cuentas_full"],
}
```

**Modos de búsqueda `buscar_banco` (AND entre ellos):**
1. RFC exacto (12-13 chars normalizados)
2. CURP exacta (18 chars)
3. Nombre completo (LIKE tokens sobre `titular_nombre1` + `titular_nombre2`)
4. paterno/materno/nombre(s) individuales (OR entre ellos, LIKE tokens)
5. CP exacto (5 dígitos normalizados)
6. Teléfono (10 dígitos normalizados)

SQL final hace UNION ALL sobre las vistas del banco (ej. las 6 de Santander) y replica los params N veces.

#### 3. `backend/servir.py`

Rutas nuevas agregadas después de `/api/v1/repuve/buscar` (línea ~778):
```python
if path == "/api/v1/cfe/buscar_avanzado": handle_cfe_buscar_avanzado(self)
for _entidad in _BME:
    if path == f"/api/v1/{_entidad}/buscar": handle_banco_buscar(self, _entidad)
if path == "/api/v1/issste/buscar_avanzado": handle_issste_buscar_avanzado(self)
if path == "/api/v1/att/buscar_avanzado": handle_att_buscar_avanzado(self)
if path == "/api/v1/telcel/buscar_avanzado": handle_telcel_buscar_avanzado(self)
```

**ELIMINADA** la llamada automática a `ejecutar_inteligencia_completa` en el handler del reporte (~línea 4321): el reporte ya NO carga la sección 08b automáticamente, queda como `inteligencia_data = None`.

#### 4. Estructura de `/bases/` confirmada

Vistas disponibles en `EXTENDED_DBS` (alias `b_<entidad>`):
```
b_att, b_emp, b_repuve, b_imss_a, b_imss_s, b_telcel, b_cfe, b_fotos,
b_issste, b_telcel_2, b_telcel_1, b_telcel_mx, b_citibanamex, b_banorte,
b_hsbc_1, b_hsbc_2, b_santander_1, b_santander_2, b_santander_3,
b_santander_5, b_santander_6, b_santander_7, b_bancoppel, b_amex,
b_bancomer, b_clavijero, b_docentes, b_covid23, b_hospital_ang
```

### Frontend

#### `frontend/sujeto.html` — refactor mayor

**ELIMINADAS** las llamadas automáticas a `fetchInteligenciaIntegral(true)` y `loadFamilia()` desde `loadBasesExternas()`. Ahora están comentadas con `/* ... */`.

**REEMPLAZADA** la sección 3c+ automática por cards manuales:

1. **3c. CFE** (inputs: calle, número, colonia, CP) → `/api/v1/cfe/buscar_avanzado`
2. **3c.b Bancos** — **8 cards separadas** (Santander, HSBC, Banorte, Bancomer, Citi, Bancoppel, AMEX, Clavijero), cada una con:
   - `nombre-completo`
   - `rfc` (exacto)
   - `curp` (exacto)
   - `paterno` / `materno` / `nombre` (separados)
   - `cp` (exacto)
   - `telefono` (exacto)
   - Botón individual
3. **3c. ISSSTE** (paterno, materno, nombre, cargo)
4. **3c. ATT** (paterno, materno, nombres)
5. **3c. Telcel** (RFC exacto + fallback nombre)
6. **3e. Mapeo Integral** — botón manual "🚀 Ejecutar motor de inteligencia integral"

**Cada card tiene:**
- Inputs pre-rellenados con datos del sujeto (verde claro)
- Inputs editables (sin readonly/disabled)
- Tabla de candidatos con score + checkbox
- Botón "📥 Exportar selección" (descarga TSV)

**Funciones nuevas:**
- `prefillManualInputs(s)`: llena automáticamente inputs al cargar sujeto
- `_renderCandidateTable(candidates, containerId, cols, emptyMsg)`: tabla con checkboxes
- `exportarSeleccionManual(containerId)`: TSV
- `_buscarManualBancoGen(entidad, prefijo)`: función genérica parametrizada
- 8 funciones individuales: `buscarManualSantander`, `buscarManualHSBC`, etc.
- `buscarManualCFE`, `buscarManualISSSTE`, `buscarManualATT`, `buscarManualTelcel`

**Tamaño HTML:** 163KB

---

## Estado actual / bugs pendientes

### ✅ Funcionando perfectamente (verificado E2E)

| Endpoint | Modo | Resultado |
|---|---|---|
| CFE avanzado | CP 01710 | **47 candidatos** score 0.6 |
| CFE avanzado | MEDANOS + CP | 3 candidatos |
| Santander | paterno GARCIA | **20 candidatos** score 1.0 |
| HSBC | GARCIA + LOPEZ | 10 candidatos |
| Citi | LOPEZ | 5 candidatos |
| AMEX | PEREZ | 5 candidatos |
| Bancoppel | MORA | 5 candidatos |
| ISSSTE | PEREZ | **10 candidatos** con cargo |
| ATT | GARCIA | 10 candidatos |
| Telcel | PEREZ | **10 candidatos** con RFC |
| Telcel | RFC exacto CME8507111Z9 | **3 candidatos** score 1.0 |

### ❌ Bugs pendientes (NO resueltos en esta sesión)

**Bug 1: Santander con RFC LELE6403099U4 devuelve 0**

Verificado manualmente:
```python
# Esto SÍ devuelve el RFC en santander_1
con.execute("SELECT rfc FROM api.santander_1_cuentas_full WHERE REGEXP_REPLACE(UPPER(TRIM(rfc)), '[^A-Z0-9]', '', 'g') = ?", ['LELE6403099U4'])
# → 1 row: ('LELE6403099U4',)
```

Pero el endpoint `/api/v1/santander/buscar?rfc=LELE6403099U4` devuelve count=0.

**Sospecha:** El UNION ALL con `*vista_origen` agregado al SELECT y los params duplicados puede no estar enlazando bien los placeholders.

**Fix tentativo:** Reemplazar `?` por concatenación directa de strings literales (después de sanitización), o usar `EXISTS` con CROSS JOIN, o ejecutar N queries separados y hacer merge en Python.

**Bug 2: Santander con `nombre_completo=JUAN PEREZ` devuelve 0**

Mismo síntoma. Posiblemente porque `titular_nombre1 LIKE '%JUAN%'` no matchea si el campo está vacío o tiene otro formato.

**Bug 3: Banorte con CP 01710 devuelve 0**

Verificar que `api.banorte_cuentas_full` tenga datos con titular_cp = "01710".

### Acciones a tomar al retomar la sesión

1. **Depurar el UNION ALL** — sospecho que los `?` placeholders no se enlazan correctamente cuando se replica la lista de params. Alternativa: usar N queries separados.
2. **Reverificar** HSBC y Banorte (bancos con 1 vista no deberían tener este bug).
3. **Revisar `titular_nombre1`** en cada vista — puede que el campo no exista y `DESCRIBE` retorne cols vacías.
4. **Verificar** el routing en `servir.py` para confirmar que `handle_banco_buscar` se llama.

---

## Comandos útiles para retomar

```bash
# Reiniciar backend
export XDG_RUNTIME_DIR=/run/user/$(id -u)
sudo systemctl restart kyc-backend.service
for i in $(seq 1 12); do
  s=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 http://localhost:8765/ 2>/dev/null)
  if [ "$s" = "302" ]; then echo "OK t+$((i*5))s"; break; fi
  sleep 5
done

# Login
COOKIE=/tmp/kyc.txt
rm -f $COOKIE
curl -s -c $COOKIE -X POST https://kyc.sebastianvernis.space/api/auth/login/password \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'

# Test endpoint
curl -s -b $COOKIE "https://kyc.sebastianvernis.space/api/v1/santander/buscar?rfc=LELE6403099U4&limit=10"

# Verificar logs
sudo journalctl -u kyc-backend.service --no-pager -n 50

# Probar directamente la UNION
/home/sebastianvernis/.venv/bin/python << 'PYEOF'
import sys
sys.path.insert(0, '/home/sebastianvernis/proyectos/kyc/proyecto_kyc/backend')
from importlib import import_module
mod = import_module('servir')
con = mod._init_extended_con()
# Probar UNION ALL directamente
tablas = ['api.santander_1_cuentas_full', 'api.santander_2_cuentas_full']
parts = [f"SELECT rfc FROM {t} WHERE REGEXP_REPLACE(UPPER(TRIM(rfc)), '[^A-Z0-9]', '', 'g') = ?" for t in tablas]
sql = " UNION ALL ".join(parts)
rows = con.execute(sql, ['LELE6403099U4'] * len(tablas)).fetchall()
print(f"UNION ALL result: {len(rows)} rows, sample: {rows[:3]}")
PYEOF
```

---

## Archivos modificados

| Archivo | Líneas | Cambio |
|---|---|---|
| `backend/inteligencia_completa.py` | +200 | Helpers de normalización |
| `backend/busqueda_manual.py` | +1100 (nuevo) | Módulo de búsqueda manual |
| `backend/servir.py` | +30 routing, -20 reporte | Wirear handlers + deshabilitar auto-IA en reporte |
| `frontend/sujeto.html` | 128K → 163K | Refactor: panel manual + prefill + 8 cards bancos |

---

## Mapeo final del frontend (lo que el usuario ve)

```
1. Datos del Padrón + RFC          ← AUTO (inciso 1)
1b. Validación SEPOMEX              ← AUTO
2. CheckID                          ← AUTO
2b. Redes Sociales                  ← AUTO
3. Tlaloc (RENAPO)                  ← AUTO
3b. Bases externas (6)              ← AUTO (stop aquí — inciso 3b)
3c. Búsqueda Manual por Base       ← MANUAL ✋
    ├─ CFE (calle, número, colonia, CP)
    ├─ Bancos (8 cards separadas)
    │   ├─ Santander
    │   ├─ HSBC
    │   ├─ Banorte
    │   ├─ Bancomer
    │   ├─ Citibanamex
    │   ├─ Bancoppel
    │   ├─ AMEX
    │   └─ Clavijero
    ├─ ISSSTE (paterno, materno, nombre, cargo)
    ├─ ATT (paterno, materno, nombres)
    └─ Telcel (RFC exacto + nombre)
3d. Análisis de familia              ← deshabilitado (manual)
3e. Mapeo Integral (motor IA)       ← botón manual
4. Validaciones opcionales           ← sin cambios
```

---

## Siguiente paso concreto

1. **Resolver el UNION ALL** que está dando 0 resultados cuando debería dar matches. El problema está en `replicated_params = where_params * n_tablas` — puede que DuckDB no esté aceptando esto correctamente, o que las vistas tengan campos NULL.
2. **Alternativa limpia:** ejecutar queries individuales por tabla y concatenar resultados en Python.
3. **Validar todo E2E** con Sebastian Vernis (VEMS980714HDFRRB04) y otros sujetos de prueba.

---

## Usuario pregunta para retomar

> "Hay que separar los formularios de busqueda de cada banco y mejorar la forma de busqueda, porque no veo que realmente busque bien, estructura como sea"

**Pendiente:**
- 8 cards separadas ✅ HECHO
- Mejorar la forma de búsqueda (que realmente busque bien) ⚠️ PARCIAL
  - Nuevos modos de búsqueda (RFC/CURP/CP/teléfono exactos) ✅
  - UNION ALL sobre todas las vistas del banco ⚠️ BUG (count=0 cuando debería haber matches)
  - Bag of words multi-parámetro ✅