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
| **email / teléfono de contacto** | — | **ninguna base local tiene columna de correo**; solo los aporta AFOR |

> Nota: el corpus se revisó completo (63 archivos) y **no hay una sola columna de
> email/correo**. El único contacto telefónico local es el de Telcel/ATT (líneas
> a nombre del RFC), que no es lo mismo que el contacto personal de AFOR.

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
- Siempre `DUDOSO` (no hay fuente local) **mientras no haya contacto cacheado**.
- Su valor real no es solo la administradora: devuelve **email y teléfono**, y
  son los únicos datos de contacto de todo el flujo — el corpus local **no tiene
  ni una sola columna de correo** (verificado en las 63 bases). Con CheckID fuera,
  AFOR es la única fuente de correo/teléfono.
- Por eso, si ya hay `afore`, `email` o `telefono` cacheados, el dato pasa a
  `CONCLUYENTE` y **no se vuelve a pagar**.
- **Solo se consulta si: plan = `corporativo` Y validación previa.** Requiere
  además que la identidad del sujeto esté resuelta (fecha coherente y sin
  ambigüedad): no se gasta un crédito de AFOR en alguien cuya identidad no cuadra.
- En cualquier otro caso se omite sin gastar y se deja como aviso.

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
| AFOR (si se pide) | **Detalles de Afore** | `POST /v3/afore` `{"variant":"encrypt","curp":...}` | **1 crédito** — solo plan corporativo + validación previa |
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
días. Se replica el patrón para ConsultaÚnica. **Implementado** (columnas
aditivas, migración idempotente al arrancar):

- columnas `cu_data` (nss/rfc/afore), `cu_contacto` (email/teléfono),
  `cu_fecha`, `cu_costo_creditos`
- **el contacto de AFOR (email/teléfono) no caduca**: es el único dato de
  contacto del flujo y no se puede regenerar; solo se refresca a petición.
- se registra el costo real por sujeto (`cu_costo_creditos`) para auditar gasto.

## 7. CheckID en el flujo v2

- Queda **pausado** vía `CHECKID_ENABLED=false` (default en `.env`). Fuera de la
  ruta crítica.
- `perfil_crear` **ya no aborta**: si CheckID falla, registra el error y
  **continúa con los pasos locales** (era el bug).
- Se conserva como último recurso, detrás del interruptor, para reactivarlo si
  se renueva el plan.

## 8. Puntos de integración

| Punto | Antes | v2 |
|---|---|---|
| `POST /api/perfil/crear` | **abortaba en CheckID** | **hecho**: local primero; CU para lo dudoso; nunca aborta |
| `GET /api/sujeto` | fallback CheckID | **hecho**: vía `_checkid_lookup` → compuerta v2 |
| `GET /api/curp/{curp}` | fallback CheckID | **hecho**: vía `_checkid_lookup` → compuerta v2 |
| `GET /v1/sujeto/enriquecido` | sin CheckID | **hecho**: hereda la compuerta (no llamaba a CheckID) |
| `POST /v1/sujeto/mapear` | CheckID directo | **hecho**: paso de identidad vía v2 |
| `_run_validation` (provider `checkid`) | CheckID cobra sin control | **hecho**: vía v2, costo 0 si CheckID pausado |

**Palanca:** 3 de los 5 puntos pasan por `_checkid_lookup`; al hacerlo consciente
del flujo v2 (cuando `CHECKID_ENABLED=false`) quedan conectados a la vez. La
compuerta devuelve un dict con la MISMA forma que `CheckIdClient.get_full`, así
que ningún consumidor cambia.

**Tope de gasto real:** el resultado pagado se persiste (`updatear_consultaunica`)
y la compuerta siguiente lo ve concluyente, así que cada sujeto paga **como
máximo 1 crédito por dato, una sola vez** — el gasto lo acota el número de
sujetos distintos, no el tráfico. AFOR queda apagado en estos endpoints (plan
vacío), solo se enciende desde `perfil_crear` con plan corporativo.

**Hecho** en `providers/consultaunica.py`: NSS (`/v3/imss`), RFC
(`/v3/sat` rfc_search / rfc_validation) y saldo (`creditos_restantes()`), además
de afore, ifetel y actas.

## 9. Decisiones cerradas

1. **Costo por servicio: 1 crédito** (todos los servicios simples de un dato).
   El único más caro es *semanas cotizadas* (3 créditos), que no se usa.
2. **AFOR: solo plan `corporativo` y con validación previa.** Es el único dato
   sin fuente local, así que se trata como extra de plan alto. Requiere además
   identidad resuelta (fecha coherente, sin ambigüedad). En cualquier otro caso
   se omite sin gastar.
3. **Escalada automática.** Se dispara sola cuando el dato local es dudoso o
   incoherente; no hay confirmación intermedia. El tope de 3 créditos por
   corrida acota el gasto.

## 10. Modelo de UI: un clic = datos básicos + perfil local en segundo plano

El flujo de la interfaz se reorganizó alrededor de la idea de que **el cruce de
bases locales es gratis**, así que se hace solo, siempre, y el usuario decide
aparte si paga las validaciones externas.

Al hacer clic en una fila de resultado, el menú ofrece **solo dos opciones** (ya
no hay "Mapear sujeto" por separado):

| Opción | Qué hace | Costo |
|--------|----------|-------|
| 👁 **Ver datos básicos** | Abre el popup de inmediato con la data del padrón y, en segundo plano, lanza `POST /api/perfil/crear` con `fase:"local"`: cruza todas las bases locales y deja el perfil consolidado en `perfil_completo` (estado `parcial`). Cuando termina, el bloque "perfil preliminar" se inserta en el popup ya abierto. | 1 crédito (vistazo) |
| 👤 **Analizar perfil completo** | Toma ese mismo perfil (ya con toda la data local) y ejecuta la fase `completo`: valida NSS/RFC/CURP contra las fuentes externas según la compuerta de coherencia, y escala a ConsultaÚnica (y AFOR si aplica) solo lo dudoso. | 3 créditos |

**Por qué quitar "Mapear":** el mapeo (CheckID + clasificación CFE) ya lo produce
el perfil completo — `cfe_data` guarda la misma clasificación de domicilio. No
se pierde nada; era un botón que duplicaba trabajo y confundía.

### Fases en el backend (`crear_perfil(..., fase=)`)

- `fase="local"` — solo pasos 1-9 (padrón, IMSS, ATT, Telcel, REPUVE,
  empleadores, ISSSTE, CFE). Fuerza `checkid_enabled=False` y corta antes del
  PASO 10 con `_FaseLocal`. **Cero costo externo.** Deja estado `parcial`.
- `fase="completo"` (default) — lo anterior + PASO 10 (escalada v2). Deja estado
  `completo`.

El endpoint `POST /api/perfil/crear` acepta `fase` en el cuerpo; con `fase=local`
no exige suscripción ni consume cuota de uso (es solo lectura de bases locales).

## 11. Estado de implementación (2026-10-08)

**Hecho** (commits `fae0050`, `550159b`, `d2be6f8`, `6d9d59e`, `4c32c9d`):
- `backend/coherencia.py` — compuerta (lógica pura, sin red).
- `backend/flujo_busqueda_v2.py` — orquestador local→CU, tope 3 créditos,
  `dry_run`, `afore_habilitado(plan, validacion_previa)`.
- `providers/consultaunica.py` — NSS, RFC, afore con contacto, saldo.
- `perfil_crear.py` — PASO 10 de escalada; CheckID ya no aborta; parámetro
  `fase` (local/completo) y `set_estado_perfil`.
- `perfil_completo_db.py` — columnas `cu_*` + `updatear_consultaunica()`;
  `updatear_cfe` ya no fija el estado (lo hace `crear_perfil`).
- `config.py` — `CHECKID_ENABLED`; `servir.py` — migración al arranque.
- Frontend: `buscar.html` con el menú de dos opciones y el perfil local en
  segundo plano; `sujeto.html` muestra el contacto AFOR (`card-contacto`) y ya
  no tiene el botón "Mapear".

