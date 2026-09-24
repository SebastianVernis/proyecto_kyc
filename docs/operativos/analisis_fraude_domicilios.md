# Análisis — Probabilidad de Fraude Fiscal e Inconsistencia de Domicilios

**Fecha:** 2026-07-23
**Sujetos:** ROSALIA AVILA SALDAÑA (CURP AISR750901MDFVLS01) y DANIEL TORRES GUTIERREZ (CURPs TOGD710202HDFRTN07 y TOGD710519HDFRTN01)

---

## A. MATRIZ DE ANÁLISIS DE DOMICILIOS

### A.1 Domicilios registrados en cada fuente

| Fuente | Calle | Int | Ext | Colonia | CP | Estado |
|---|---|---|---|---|---|---|
| Padrón INE ROSALIA | AV PASEO DE LOS AHUEHUETES | CASA 1 A | 1 | U HAB VALLE DEL TENAYO | 54147 | 15 EdoMex |
| Padrón INE DANIEL "B" (710519) | AV PASEO DE LOS AHUEHUETES | C 1 A | **PRIV 1** | COL VALLE DEL TENAYO | 54147 | 15 EdoMex |
| SAT XLSX ROSALIA | — | — | — | — | 54147 | — |
| SAT XLSX DANIEL "A" (710202) | — | — | — | — | **01050** | CDMX |
| SAT XLSX DANIEL "B" (710519) | — | — | — | — | 54147 | EdoMex |
| Acta matrimonio 1993 | — | — | — | — | — | EdoMex (Tlalnepantla) |

### A.2 Coincidencias y discrepancias clave

**Coincidencias que sugieren misma ubicación física:**
- Ambos en AV PASEO DE LOS AHUEHUETES.
- Ambos en CP 54147 (mismo código postal = misma zona postal).
- Ambos con interior referenciando "CASA 1 A" / "C 1 A" (probablemente misma unidad habitacional).
- Acta de matrimonio 1993 en Tlalnepantla (municipio del CP 54147).

**Discrepancias que generan duda:**
- **DANIEL "B" tiene "PRIV 1" como número exterior.** Esto NO es un número exterior convencional; sugiere:
  - Una privada (callejón) numerada como 1 dentro del complejo.
  - O un error de captura del padrón.
- **ROSALIA tiene "1" como número exterior** (tampoco es referencia típica).
- **No hay cohabitantes registrados** en ninguna de las dos cadenas exactas de dirección — cada quien aparece solo en su dirección.
- En el padrón, "PRIV 1" como ext es extremadamente raro. De 60+ registros revisados en la misma calle, ninguno usa "PRIV" como ext.

### A.3 Búsqueda de cohabitantes en el padrón

| Dirección | Total empadronados | Coincide con nombre buscado |
|---|---|---|
| AV PASEO DE LOS AHUEHUETES, CASA 1 A, ext 1, CP 54147 | 1 | Solo ROSALIA |
| AV PASEO DE LOS AHUEHUETES, C 1 A, PRIV 1, CP 54147 | 1 | Solo DANIEL "B" |
| CP 01050, CDMX | 562 padrón total | **Cero DANIEL TORRES GUTIERREZ** |

**Conclusión geográfica:**
- ROSALIA y DANIEL "B" están registrados en **dos unidades distintas** del mismo complejo en Valle del Tenayo, no en la misma unidad.
- DANIEL "A" (CURP 710202) no tiene registro en padrón electoral en ningún CP — su CP fiscal 01050 es solo administrativo/SAT, sin domicilio físico real.

### A.4 ¿Quién vive en 01050 (CDMX)?

El CP 01050 corresponde a la colonia San Marcos / Barrio Santa Catarina, en la alcaldía Álvaro Obregón, CDMX. Hay 562 personas empadronadas ahí, pero **ninguna es DANIEL TORRES GUTIERREZ**. Esto significa que:
- Si el CURP TOGD710202HDFRTN07 tiene CP fiscal 01050 sin domicilio físico real, el RFC TOGD710202T6A es **un RFC "de papel"**: fue tramitado sin empadronamiento previo, probablemente para facturación o para un trámite específico, y nunca se actualizó al cambiar de domicilio.

---

## B. PROBABILIDAD DE FRAUDE FISCAL

### B.1 Indicadores observados

| Indicador | DANIEL "A" (710202) | DANIEL "B" (710519) |
|---|---|---|
| RFC activo | TOGD710202T6A | TOGD710519T63 |
| Situación fiscal | Cumplido | **Irregular** |
| Oficio art. 69 | Ninguno | **1 oficio "Créditos Firmes"** (publicado 2023-01-01, actualizado 2026-05-27) |
| Padrón electoral | NO empadronado | Empadronado EdoMex, sec 4906 |
| Domicilio físico verificable | NO (CP fiscal sin padrón asociado) | Sí (Valle del Tenayo, EdoMex) |
| Email declarado | (no capturado) | TOGD610519@YAHOO.COM.MX (prefijo 610519 vs CURP 710519 — discrepancia) |

### B.2 Escenarios posibles

#### Escenario 1: Persona única con dos CURPs (probabilidad ALTA)
La misma persona tramitó un primer CURP con fecha errónea (02/02/1971), sacó RFC, y luego corrigió la fecha con un segundo CURP (19/05/1971) con el que se empadronó.
- ⚠️ Riesgo fiscal BAJO: el oficio 69 "Créditos Firmes" del RFC 710519 es un crédito fiscal real que el ciudadano debe pagar o aclarar.
- ⚠️ Riesgo administrativo MEDIO: dos RFCs activos para una misma persona satura el sistema y puede bloquear trámites.

#### Escenario 2: Dos personas distintas con el mismo nombre (probabilidad BAJA)
- Padres en el acta de nacimiento de DANIEL "A": ISAAC TORRES y AGUSTINA GUTIERREZ. No se pueden contrastar con DANIEL "B" porque el padrón no tiene filiación.
- TORRES+GUTIERREZ es un apellido compuesto común, pero el patrón completo (CDMX, 1971, Valle del Tenayo) coincide demasiado.
- ⚠️ Si fueran personas distintas: una tendría CURP fantasma con RFC sin domicilio real (fraude de identidad fiscal).

#### Escenario 3: Suplantación de identidad fiscal (probabilidad MEDIA)
- Alguien pudo haber usado datos personales de Daniel "A" (acta de nacimiento real) para sacar un RFC paralelo con dirección falsa (01050 CDMX) sin empadronarse.
- Esto explicaría: el RFC 710202 está "Cumplido" (nunca usado, sin operaciones); el RFC 710519 está "Irregular" (operaciones reales con deuda).
- ⚠️ Riesgo ALTO de fraude si este es el caso: significa que alguien está usando la identidad fiscal del verdadero Daniel para un RFC paralelo.

### B.3 Probabilidades asignadas

| Escenario | Probabilidad | Justificación |
|---|---|---|
| 1 — Persona única con dos CURPs/RFCs | **70%** | Coincidencias de nombre, año, entidad de nacimiento, CURPs con misma raíz TOGD71, domicilio compartido con la esposa |
| 2 — Dos personas distintas (homónimos) | 15% | Posible pero poco probable por la convergencia de datos |
| 3 — Suplantación / uso indebido de identidad | **15%** | Explicaría el RFC paralelo sin padrón y el oficio 69 |

### B.4 Indicadores de fraude fiscal directo

**No hay indicadores directos de fraude fiscal** (defraudación fiscal específica, simulación, operaciones simuladas). Lo que sí hay son **anomalías administrativas** que pueden o no ser fraudulentas:

1. **Doble RFC activo** sin evidencia de cancelación del primero.
2. **RFC 710202 sin empadronamiento** y con domicilio fiscal que no corresponde al domicilio físico real.
3. **RFC 710519 con oficio 69 "Créditos Firmes"** sin resolución a la fecha (vigente desde 2023).
4. **Email declarado con prefijo erróneo** (610519 vs 710519) sugiere captura descuidada o dato de hace años.

**El "fraude" más probable es de naturaleza administrativa, no defraudatoria:**
- Tener dos RFCs simultáneamente NO es delito, pero sí una irregularidad administrativa.
- Estar en situación fiscal "Irregular" con crédito firme NO es fraude (todavía); se convierte en problema si no se paga.
- Si el CURP 710202 fue generado por duplicidad y nunca se usó para operaciones reales, el riesgo de defraudación específica es bajo.

---

## C. CONCLUSIÓN

### Sobre domicilios
ROSALIA y DANIEL "B" viven en la misma calle y CP, en unidades registradas de forma distinta. El padrón los trata como vecinos, no como cohabitantes del mismo domicilio exacto (aunque en la práctica pueden serlo — es el mismo complejo Valle del Tenayo).

DANIEL "A" (CURP TOGD710202HDFRTN07) **no tiene domicilio físico real** registrado en el padrón electoral; el CP 01050 del SAT es solo administrativo y no se corresponde con ningún empadronamiento en CDMX.

### Sobre fraude fiscal
- **Probabilidad de fraude fiscal activo: BAJA** (no hay operaciones simuladas, RFCs apagados, ni evidencias de defraudación específica).
- **Probabilidad de irregularidad administrativa: ALTA** — doble RFC activo, oficio 69 vigente en uno, domicilio fiscal fantasma en el otro.
- **El dato de mayor preocupación** es el oficio 69 "Créditos Firmes" del RFC TOGD710519T63 — bloquea cualquier trámite fiscal/patrimonial hasta regularizar.
- **Si el CURP 710202 fue una corrección posterior del 710519**, el riesgo es bajo. **Si es un CURP paralelo de otra persona o suplantación**, el riesgo es alto pero hay que demostrarlo con RENAPO.

### Acción mínima recomendada
1. Consultar al SAT el estado del RFC TOGD710202T6A (¿tiene operaciones? ¿está en uso?).
2. Verificar en RENAPO si ambos CURPs están asignados a la misma persona física o a dos personas distintas (RENAPO tiene unvisor de CURP que lo aclara).
3. Si son la misma persona: cancelar el RFC más antiguo (o el que no tenga operaciones) y mantener solo uno.
4. Regularizar el oficio 69 "Créditos Firmes" del RFC TOGD710519T63 antes de cualquier trámite.

---

## D. Metadata

```
Consultas:        DuckDB sobre ine.duckdb (read-only)
Búsquedas:        por CURP exacto + por paterno/materno + por calle+CP
Cross-checks:     CP 54147, CP 01050, EdoMex estado 15, CDMX estado 9
Documentos:       3 XLSX SAT + 3 PDFs Registro Civil
Tiempo proceso:   <1 minuto
```
