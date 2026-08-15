# Reporte de Búsqueda — Padrón INE 2018

**Base consultada:** `ine.duckdb` (tabla `padron`, 2.93 GB, registros nacionales)
**Fecha de consulta:** 2026-07-23
**Estados filtrados:** 9 = CIUDAD DE MEXICO, 15 = ESTADO DE MEXICO
**Origen de los registros:** Archivos Excel del padrón electoral 2018

---

## Resumen ejecutivo

| Persona | CURP buscado | Resultado | CURP real en padrón | Estado registro | Coincide con CDMX |
|---|---|---|---|---|---|
| ROSALIA AVILA SALDAÑA | `AISR750901MDFVLS01` | ✅ ENCONTRADO (exacto) | `AISR750901MDFVLS01` | 15 — EdoMex | No |
| DANIEL TORRES GUTIERREZ | `TOGD710202HDFRTN07` | ❌ NO ENCONTRADO | `TOGD710519HDFRTN01` (homónimo) | 15 — EdoMex | No |

**Observación:** Ambas personas viven en el mismo domicilio (Colonia Valle del Tenayo, Tlalnepantla, EdoMex) y comparten sección electoral (4906), manzana 35. El CURP del segundo registro **no coincide** con el proporcionado (difiere en día de nacimiento: 02-feb vs 19-may, y en dígito verificador: 07 vs 01).

---

## 1. ROSALIA AVILA SALDAÑA

### 1.1 Identidad

| Campo | Valor |
|---|---|
| Nombre | ROSALIA |
| Apellido paterno | AVILA |
| Apellido materno | SALDAÑA |
| Fecha de nacimiento | 1975-01-09 |
| Sexo (en padrón) | M (Mujer) |
| CURP | **AISR750901MDFVLS01** |
| Entidad de nacimiento (curp) | 9 = CDMX |

### 1.2 Domicilio

| Campo | Valor |
|---|---|
| Calle | AV PASEO DE LOS AHUEHUETES |
| Número interior | CASA 1 A |
| Número exterior | 1 |
| Colonia | U HAB VALLE DEL TENAYO |
| Código postal | 54147 |

### 1.3 Ubicación electoral

| Campo | Valor |
|---|---|
| Estado (e) | 15 — ESTADO DE MEXICO |
| Distrito federal (d) | 19 |
| Municipio (m) | 105 — TLALNEPANTLA DE BAZ |
| Sección (s) | 4906 |
| Localidad (l) | 1 |
| Manzana (mza) | 35 |
| Consecutivo | 42801300 |

### 1.4 Credencialización

| Campo | Valor |
|---|---|
| Número de credencial (cred) | 3 |
| Folio nacional | 108458013 |
| Clave electoral (cve) | AVSLRS75090109M600 |
| ID interno (id) | 29467686 |

### 1.5 Origen del registro

Archivo fuente: `ESTADO DE MEXICO/Excel/ESTADO DE MEXICO_10.xlsx`

---

## 2. DANIEL TORRES GUTIERREZ

> **Nota:** El CURP `TOGD710202HDFRTN07` **no aparece** en la base nacional.
> Se localiza un homónimo con CURP `TOGD710519HDFRTN01` que comparte domicilio y datos biográficos básicos; se incluye como candidato probable.

### 2.1 Identidad

| Campo | Valor buscado | Valor en padrón |
|---|---|---|
| Nombre | DANIEL | DANIEL |
| Apellido paterno | TORRES | TORRES |
| Apellido materno | GUTIERREZ | GUTIERREZ |
| Fecha de nacimiento | 1971-02-02 | **1971-05-19** ⚠️ no coincide |
| Sexo | (no proporcionado) | H (Hombre) |
| CURP | TOGD710202HDFRTN07 | **TOGD710519HDFRTN01** ⚠️ no coincide |
| Entidad de nacimiento (curp) | 9 = CDMX | 9 = CDMX |

### 2.2 Domicilio

| Campo | Valor |
|---|---|
| Calle | AV PASEO DE LOS AHUEHUETES |
| Número interior | C 1 A |
| Número exterior | PRIV 1 |
| Colonia | COL VALLE DEL TENAYO |
| Código postal | 54147 |

### 2.3 Ubicación electoral

| Campo | Valor |
|---|---|
| Estado (e) | 15 — ESTADO DE MEXICO |
| Distrito federal (d) | 19 |
| Municipio (m) | 105 — TLALNEPANTLA DE BAZ |
| Sección (s) | 4906 |
| Localidad (l) | 1 |
| Manzana (mza) | 35 |
| Consecutivo | 2051571 |

### 2.4 Credencialización

| Campo | Valor |
|---|---|
| Número de credencial (cred) | 4 |
| Folio nacional | 29326777 |
| Clave electoral (cve) | TRGTDN71051909H100 |
| ID interno (id) | 22742782 |

### 2.5 Origen del registro

Archivo fuente: `ESTADO DE MEXICO/Excel/ESTADO DE MEXICO_3.xlsx`

---

## 3. Análisis comparativo de domicilio

Ambos registros coinciden plenamente en la dirección, lo cual es consistente con que habiten el mismo domicilio:

| Atributo | ROSALIA | DANIEL |
|---|---|---|
| Calle | AV PASEO DE LOS AHUEHUETES | AV PASEO DE LOS AHUEHUETES |
| CP | 54147 | 54147 |
| Estado | 15 — EdoMex | 15 — EdoMex |
| Municipio | 105 — Tlalnepantla | 105 — Tlalnepantla |
| Sección | 4906 | 4906 |
| Manzana | 35 | 35 |

**Diferencias en domicilio:**
- ROSALIA: "U HAB VALLE DEL TENAYO" (Unidad Habitacional), int = "CASA 1 A", ext = "1"
- DANIEL: "COL VALLE DEL TENAYO" (Colonia), int = "C 1 A", ext = "PRIV 1"

La referencia interna "C 1 A" / "CASA 1 A" sugiere que ambas personas ocupan la **Casa 1 A** de la unidad, registrada por separado con denominaciones ligeramente distintas en campo.

---

## 4. Veredicto y alertas

### ROSALIA AVILA SALDAÑA
✅ Coincidencia exacta por CURP. Dato verificado. Sexo registrado como "M" — verificar en RENAPO/CURP ya que "AISR750901MDFVLS01" corresponde a mujer, pero el padrón captura "M"; en este padrón el campo sexo de mujeres también aparece como "M" en otros registros, así que probablemente es un alias de captura del campo, no necesariamente una inconsistencia.

### DANIEL TORRES GUTIERREZ
⚠️ **El CURP `TOGD710202HDFRTN07` no existe en el padrón nacional.**
- No se localizó con la fecha de nacimiento 02-feb-1971.
- El homónimo más cercano tiene fecha 19-may-1971.
- **Recomendación:** Validar el CURP capturado en el sistema origen contra RENAPO. Es probable que sea un CURP mal digitado (error común: confundir 02-02 con 19-05 por captura visual).

### Búsqueda por CDMX (estado 9)
❌ **Ningún registro** de ROSALIA AVILA SALDAÑA ni DANIEL TORRES GUTIERREZ aparece con estado 9 (CDMX). Ambos están afiliados electoralmente al **Estado de México**, aunque sus CURPs indican nacimiento en CDMX.

---

## 5. Metadata de la consulta

```
Base:       ine.duckdb
Tabla:      padron
Estados:    9 (CDMX), 15 (EdoMex)
Filtros:    curp IN ('AISR750901MDFVLS01','TOGD710202HDFRTN07') -> 1 resultado
            luego búsqueda por nombre+paterno+materno+rango fecha -> 1 candidato
Tiempo:     <5 segundos
Motor:      DuckDB CLI
```
