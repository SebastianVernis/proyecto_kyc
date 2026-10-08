# Flujo de búsqueda v2 — local primero, escalada a ConsultaÚnica

Estado: propuesta para revisión. No implementado.
Fecha: 2026-10-08
Sustituye el uso de CheckID en el flujo de identidad fiscal.

## 1. Problema

CheckID está **sin consultas** (E901 "tus consultas se han agotado"). Peor que
"no aportar": `POST /api/perfil/crear` **aborta en su paso 2** y nunca corre los
8 pasos locales (IMSS, ATT, Telcel, REPUVE, Empleadores, ISSSTE, CFE). Además
CheckID no pasa por ningún control de costo en tres de sus seis puntos de uso.

ConsultaÚnica sí está activa y fondeada. Pero no se trata de sustituir una API
por otra: se trata de **no consultar nada que la base local ya responda**, y
gastar solo cuando el dato local falta o es incoherente.

## 2. Principio

```
1. Barrido local (gratis)            → arma el perfil con las 29 bases
2. Evaluación de coherencia          → decide, dato por dato, si está concluyente
3. Escalada a ConsultaÚnica          → SOLO por los datos dudosos, el más barato primero
4. Persistir + cachear               → no pagar dos veces el mismo dato
```

Regla dura: **si el local es concluyente, no se llama a ConsultaÚnica.** El
gasto externo es una excepción justificada, no un paso del pipeline.

## 3. Cobertura local (lo que ya se puede resolver gratis)

| Dato | Fuente local | Notas |
|---|---|---|
| CURP, nombre, fecnac, CP, sección | `padron` (88.4M) | núcleo de identidad |
| **NSS** | `imss_asegurados` (`curp`,`rfc`,`nss`, 114.7M) y `imss_segmentacion` (`nss_clean`,`rfc_clean`, 47.3M) | dos fuentes → se pueden contrastar |
| **RFC** | `imss_personas.rfc_clean`, `empleadores`, `att`, `telcel`, bancos | 13 chars (con homoclave) o 10 (sin) |
| teléfono → identidad | `telcel`, `att` | |
| domicilio | `cfe`, `padron`, `sepomex`, `geo` | |
| **AFOR** | — | no existe en ninguna base local |

## 4. Compuerta de coherencia (el corazón del diseño)

Tras el barrido local, cada dato objetivo se marca en uno de tres estados.
Solo `DUDOSO` e `INCOHERENTE` escalan.

### NSS
- `CONCLUYENTE`: `nss` de 11 dígitos, presente y **consistente** en ≥1 fuente
  (si aparece en dos, que coincida).
- `DUDOSO`: ausente · menos de 11 dígitos · **el mismo CURP tiene NSS distintos**
  entre `imss_asegurados` e `imss_segmentacion`.
- `INCOHERENTE`: el NSS local existe pero el nombre/CURP asociado no cuadra.

### RFC
- `CONCLUYENTE`: 13 chars con homoclave y presente en ≥1 base cuyo nombre coincide.
- `DUDOSO`: solo 10 chars (**falta homoclave** — hoy el código ya lo etiqueta
  «local (incompleto, falta homoclave)») · RFC calculado local ≠ RFC en base ·
  homónimos con mismo nombre y distinto RFC.
- `INCOHERENTE`: nombre/fecha del padrón no cuadra con la CURP.

### AFOR
- Siempre `DUDOSO` (no hay fuente local). Se consulta **solo si se pide**; si no,
  se omite sin gastar.

### Señales transversales de incoherencia
- CURP no existe en el padrón (`found=False`) → sujeto huérfano.
- `resolver_desde_hint` devuelve `estrategia` con sufijo `+ambiguo`,
  `+apellidos` o `multiples` → varios candidatos.
- `source = "sin_match"` o `score` bajo.
- **Fecha de nacimiento embebida en la CURP ≠ `fecnac` del padrón** → dato duro,
  detectable sin red y gratis. Es un buen disparador temprano.

## 5. Matriz de escalada (más barato primero)

| Dato dudoso | Servicio ConsultaÚnica | Contrato | Costo |
|---|---|---|---|
| NSS ausente/dudoso | **NSS** (rápido) | `POST /v3/imss` `{"type":"nss","nss":{"curp":...},"userEmail":...}` | **1 crédito** |
| RFC sin homoclave | **Búsqueda de RFC** | `POST /v3/sat` `{"variant":"rfc_search","rfcSearch":{name,paternalName,maternalName,birthDate}}` | **1 crédito** |
| RFC a confirmar | **Validación de RFC** | `POST /v3/sat` `{"variant":"rfc_validation","rfcValidation":{"rfc":...}}` | **1 crédito** |
| AFOR (si se pide) | **Detalles de Afore** | `POST /v3/afore` `{"variant":"encrypt","curp":...}` | **1 crédito** |
| NSS a correo (evitar) | NSS regular | `POST /v3/imss?_v=2` | no usar (más caro, redundante) |

**Costo por servicio: 1 crédito (todos los servicios simples).** El único más
caro es *semanas cotizadas* (3 créditos), que no usamos.

Con el plan actual (Básico $7/cr → Premium $4/cr → Empresarial $3/cr), el peor
caso de un sujeto es **3 créditos ≈ $9–21 MXN**, y solo cuando NSS, RFC y AFOR
salen los tres dudosos. Un sujeto bien cubierto por las bases locales cuesta
**0 créditos**.

Reglas de ahorro:
- Preferir la variante **rápida** de NSS (`sendNssToEmail: false`) sobre la de correo.
- Si ya se tiene un RFC de 13, **validar** (1 créd.) en vez de **buscar** (1 créd., pero solo aplica si falta el RFC).
- Nunca llamar dos servicios para el mismo dato en la misma corrida.
- **Si la consulta no encuentra nada, ConsultaÚnica no cobra** (verificado: dos
  llamadas 422/400 con costo 0). El gasto solo ocurre cuando sí hay dato.
- Todo el desarrollo/pruebas contra `/v3/mock/...` (gratis, mismo contrato).

## 6. Caché (no pagar dos veces)

`perfil_completo` ya guarda `checkid_data` + `checkid_fecha` con caducidad de 30
días. Se replica el patrón para ConsultaÚnica:

- columnas `cu_nss`, `cu_rfc`, `cu_afore`, `cu_fecha`, `cu_costo_creditos`
- reutilizar si `cu_fecha` < TTL (propuesto: 30 días, igual que CheckID)
- registrar en el perfil el costo real por sujeto (para auditar el gasto)

## 7. CheckID en el flujo v2

- Queda **pausado** (no hay consultas). Se saca de la ruta crítica.
- `perfil_crear` deja de abortar: si CheckID falla, **continúa con los pasos
  locales** (es el bug actual).
- Se conserva como último recurso opcional, detrás de un interruptor
  `CHECKID_ENABLED` (por defecto `false`), para reactivarlo si renuevas plan.
- Los tres puntos sin guard de costo pasan a respetar el interruptor.

## 8. Puntos de integración

| Punto | Hoy | v2 |
|---|---|---|
| `/api/sujeto`, `/api/curp/{}`, `/v1/sujeto/enriquecido`, `perfil_inicial` | fallback CheckID con guard | escalada CU por dato dudoso |
| `POST /v1/sujeto/mapear` | CheckID directo | local → CU solo si dudoso |
| `POST /api/perfil/crear` | **aborta en CheckID** | local primero; CU para lo dudoso; nunca aborta |
| `_run_validation` | CheckID cobra sin control | CU, con confirmación de costo |

Falta implementar en `providers/consultaunica.py` los dos servicios necesarios:
**NSS** (`/v3/imss`) y **RFC** (`/v3/sat`). Hoy solo tiene afore, ifetel y actas.

## 9. Pendiente para cerrar el diseño

1. **Costos por servicio** (me los das tú) → llenar la matriz §5.
2. Confirmar la diferencia real entre la variante rápida y la de correo de NSS.
3. TTL del caché (¿30 días como CheckID?).
4. ¿AFOR se consulta siempre o solo a pedido?
