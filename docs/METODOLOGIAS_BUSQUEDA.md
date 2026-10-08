# Metodologías de búsqueda — proyecto_kyc

**Fecha:** 2026-10-08
**Alcance:** consolida los procedimientos que funcionaron en las investigaciones
reales de este corpus, los huecos que quedaron al descubierto y las herramientas
que los cierran. Sustituye como guía operativa a `ANALISIS_BUSQUEDAS_LAYOUTS.md`
(2026-08-31), que sigue siendo válido como inventario de layouts.

Casos que originaron estas metodologías:

| Caso | Insumo de entrada | Resultado |
|---|---|---|
| `5540553398` (Carolina Castro Gutiérrez) | un número de teléfono | identidad, hogar, dos líneas, un banco, informe PDF+DOCX |
| `5613772366` | un número de teléfono | barrido lanzado y **no concluido** (`sweep.json` quedó `[]`) |

---

## 1. Principio de entrada: de un solo identificador a la persona completa

Se entra por cualquiera de estos seis, y cada uno tiene su ruta de escalada:

| Entrada | Ruta |
|---|---|
| **teléfono** | telefonía → titular (RFC/CURP) → todo el corpus por RFC/CURP → hogar → contactos de la cuenta |
| **RFC** | telcel/att/repuve/empleadores → xwalk a CURP → resto |
| **CURP** | padrón → domicilio/hogar → xwalk a RFC vía `imss_segmentacion` → bancos |
| **nombre + apellidos** | bag-of-words por base → candidato → fijar con fecha de nacimiento / domicilio |
| **placa / no_serie** | REPUVE → RFC del propietario → resto |
| **número de servicio CFE** | medidor → dirección → residentes del inmueble |

La regla que evita rehacer trabajo: **en cuanto la ruta consigue CURP o RFC,
se abandona la búsqueda por nombre y se barre por identificador exacto.**

---

## 2. Los siete pasos

### Paso 1 — Inventario (metadata, nunca `select *`)

Enumerar bases, tablas y columnas reales antes de escribir un solo `WHERE`.

- `show tables` **no sirve** en todo el corpus: en `ine_2018.duckdb` devuelve
  vacío porque cada estado vive en su propio esquema. Hay que usar
  `duckdb_tables()` (schema_name, table_name).
- Las bases no comparten esquema: bancos usan `nombre1`/`nombre2`/`rfc`;
  ISSSTE `paterno`/`materno`/`nombres`; REPUVE `nom_prop_fix`/`rfc_clean`;
  empleadores nombres con punto (`"ubicacion.calle"`).
- Referenciar una columna que no existe **no devuelve vacío**: lanza
  `BinderException`. Un cero silencioso casi siempre es un esquema supuesto.

Herramienta: `scripts/barrido_universal.py` (paso de inventario con caché en
`scripts/inventario_bases.json`).

### Paso 2 — Fijar el sujeto por identificador exacto

CURP / RFC / NSS exactos en todas las bases. Se reporta **en qué bases aparece
y de cuáles está ausente**, y por separado los homónimos.

Trampas concretas de este corpus:

- **RFC de 13 vs 10.** El RFC de persona física son 13 con homoclave, pero
  muchas bases guardan los 10 primeros. Comparar siempre por los 10 primeros
  (`LIKE 'RFC10%'`) y usar el completo solo para desambiguar.
- **RFC atípicos que se citan verbatim.** En el caso Carolina el RFC de Telcel
  era `CAGC610211000` (13 caracteres, homoclave `000`), distinto del de
  Santander `CAGC6102112F2`. Ninguno es "el correcto": son dos RFC activos con
  la misma CURP. No normalizar ni "corregir".
- **`consec` es VARCHAR.** `where consec >= 5839880` revienta; comparar como
  texto.
- **Teléfonos con padding.** `'5540553398   '`; normalizar con
  `regexp_replace(col,'[^0-9]','','g')` y probar con y sin LADA.
- **Una CURP puede tener más de un RFC** y un mismo RFC puede aparecer con
  distintas homoclaves. Es hallazgo, no error.

### Paso 3 — Padrón y padrón 2018 (identidad y domicilio)

Dos padrones con roles distintos:

| Base | Qué aporta |
|---|---|
| `padron.duckdb::padron` (88.4M) | padrón fusionado: es la PK de identidad del sistema. 25 columnas. |
| `ine_2018.duckdb` (34 tablas, ~120M) | padrón 2018 **por estado-segmento**, mismas 23 columnas de identidad **más** el detalle electoral que el fusionado no expone: `e/d/m/s/l`, `mza`, `consec`, `cred`, `folio`, `nac`. |

Sirve para el caso "no empadronado": un sujeto que no está en el fusionado
puede estar en 2018 (y al revés). Endpoint nuevo:
`GET /api/v1/ine2018/buscar`.

### Paso 4 — Hogar y grafo de parentesco por co-residencia

El motor de familia es la **co-residencia**, en este orden de fuerza:

1. **Mismo `consec`** en el padrón (identificador de vivienda). Es el más
   fuerte: en el caso Carolina `consec='2343134'`, mza 12, CP 03610.
2. Mismo `mza` + misma colonia + mismo CP.
3. Misma calle + número exterior exactos.
4. Mismo RFC ligado a una persona (para empresas familiares).
5. Empleo cruzado bajo el mismo registro patronal.

Cada relación inferida se etiqueta como inferida y se le cuelga su cláusula de
evidencia (domicilio idéntico, RFC compartido, empleo cruzado). Los datos de
2018 añaden la vía electoral: mismos `e/d/m/s` y misma `mza`.

### Paso 5 — Patrimonio y arraigo (CFE, bancos, vehículos)

- **CFE** (`cfe.duckdb::medidores`, 66M): se entra **por dirección**
  (`calle`+`ext`+`colonia`+`CP`), no por nombre: `nombre` está truncado a ~30
  caracteres y viene apellido primero. El titular del medidor ≠ el sujeto; es
  un vecino/co-residente. Comparación fuzzy sobre dirección normalizada.
- **Bancos** (9 bases, esquema `main.personas` compartido): entrada por
  RFC/CURP exactos y por teléfono normalizado. La línea telefónica aparece como
  `telefono` de una cuenta bancaria a nombre de **otra persona** — es el hilo
  que enlaza dos hogares, no un error.
- **REPUVE**: `TEL_PROP` **no es un teléfono nacional** (tiene longitudes 1/5/7).
  No usarlo como teléfono.
- **IMSS segmentación**: es el único cruce CURP→RFC confiable (99% de
  `curp_clean` poblados). Si falla, el sistema no puede saltar entre bases.

### Paso 6 — Contactos y vínculos blandos

`tel_contacto`, `contacto1`, `contacto2` de la cuenta telefónica y
`contacto.*` de empleadores. En el caso Carolina, `contacto2 =
CONCEPCION GARRIDO CAZARES` fue el vínculo que abrió el segundo circulo. Se
busca a ese contacto como sujeto propio en todo el corpus (padrón, covid,
bancos, ATT).

### Paso 7 — Barrido y reporte

- Barrido por identificador sobre todas las bases: `scripts/barrido_universal.py`.
- Reporte: **siempre PDF + Word editable** en `reportes/`, en español, con
  secciones fijas: identificación → registro directo por base → hogar →
  hermanos → línea paterna → línea materna → patrimonio/CFE → apariciones por
  base con conteos → vínculos duros → notas metodológicas.
- El generador (`scripts/tel_5540553398/gen_informe.py`) sirve de plantilla.
- Verificar el PDF renderizando páginas a PNG y mirándolas antes de entregar.

---

## 3. Matriz de cruce: qué cruza con qué

```
                    padron(88M)   ine_2018(120M)  imss_s(23M)  cfe(66M)  telcel(9.7M+44.6M)  bancos  att  repuve
padron(88M)            —             CURP          curp→rfc      domic    rfc→curp             rfc    rfc   rfc
ine_2018(120M)       CURP             —           curp          domic      —                  rfc    —     —
imss_segmentacion    curp/rfc         curp          —            —        rfc_clean            rfc    rfc   rfc
cfe(66M)             domic+nombre     domic         —            —            —                 —     —     —
telefonia(54M)       rfc/curp         curp         rfc_clean      —            —              rfc    rfc   rfc
bancos               rfc/curp          —           rfc_clean      —          rfc              —      —     —
att                  rfc              —            rfc_clean      —          rfc              rfc    —     —
repuve               rfc              —            rfc_clean      —          rfc              rfc    rfc    —
covid_clinico        curp+nombre      curp         curp           —         domicilio          —      —     —
```

**El xwalk central es `imss_segmentacion`**: es el único que convierte CURP en
RFC de forma confiable. Todo salto entre el mundo CURP (padrón, covid, IMSS
asegurados) y el mundo RFC (telcel, att, repuve, bancos) pasa por ahí.

---

## 4. Huecos que quedaron y cómo los cierra el sistema

### 4.1 `ine_2018.duckdb` — no consultable

34 tablas en 34 esquemas (`AGS."Ags"`, …, `DF2."Df2"`, `EDM1."Edm1"`, …).
`show tables` devuelve vacío y `EXTENDED_DBS` no la incluye.
**Cerrado:** endpoint `GET /api/v1/ine2018/buscar` (busca en las 34 tablas y
reporta en qué estado aparece). Barrido completo por CURP: ~20 s.

### 4.2 `covid23_master.duckdb` — la tabla clínica era inalcanzable

El archivo expone dos tablas de 19 609 795 filas:

| Tabla | Columnas | Qué es |
|---|---|---|
| `covid_clinico` | 130 | clínico real: `CURP`, `APEPATER/APEMATER/NOMBRE`, `SEXO`, `FECNACI`, `ENTIDAD`, `DOMICILIO`, `TELEFONO`, `DIAGPROB`, `FECINGRE`, `ID_REGISTRO` |
| `personas` | 48 | **no es COVID**: volcado telefónico con `telefono`, `nombre1/nombre2`, `rfc`, `curp`, `domicilio` |

`EXTENDED_DBS` attachea el archivo con tabla principal `main.personas`, así que
todo lo clínico quedaba inaccesible.
**Cerrado:** endpoint `GET /api/v1/covid/buscar` sobre `covid_clinico`.

### 4.3 `cfe_SIN_ESTADO-003.duckdb` — huérfano

2.9 GB, **cero tablas y cero vistas** en `duckdb_tables()` y en
`information_schema`. No es consultable con el motor actual.
**Pendiente:** decidir si se re-exporta o se borra. No se toca en esta iteración.

### 4.4 `perfil_completo.duckdb` — no legible como archivo

El proceso del backend lo tiene ATTACHed en escritura, así que **abrir el
archivo directamente falla con `IOException: Could not set lock`**. No es
corrupción: hay que pasar por la API (`/api/perfiles`) o detener el backend.
Ojo al usar herramientas externas sobre él.

### 4.5 Telefonía dispersa

El corpus telefónico con CURP se repite en cuatro archivos `*_master`/`*_v1`.
**Cerrado:** endpoint `GET /api/v1/telefonia/buscar` que cruza
`telefono`+`tel_contacto`+`contacto1`+`contacto2`+`rfc`+`curp` en una pasada y
devuelve de qué base salió cada fila. El 44.6M (`telcel_master_v2`) queda tras
`incluir_46m=1` porque su primer escaneo en frío tarda ~20 s.

### 4.6 Doble conteo por twins

16 pares `*_master` ↔ `*_v1` con **conteo y tablas idénticos**:
`att`, `repuve`, `empleadores`, `telcel_v2/telcel1_master`,
`telcel_v3/telcel_master_v2`, `telcel_v4/telcel_mexico_master`,
`covid_v1/covid23_master` y los 9 bancos.
**Ojo:** `santander_v1..v7` **no** son twins entre sí pese a compartir stem
(`santander_v3` tiene una línea que no está en `v1`/`v2`). Deduplicar por
conteo verificado, nunca por nombre de archivo.

### 4.7 Bases que sí están cubiertas

`geo.db` y `sepomex.db` ya se usan (`/api/sepomex/cp/<cp>`,
`/api/sepomex/search`, `/api/v1/geo/parse`). `bases/Tecamac/` son copias de
bases ya montadas y `bases/zip-drive/` es respaldo comprimido: ninguno es
fuente nueva.

---

## 5. Herramientas

| Herramienta | Para qué |
|---|---|
| `scripts/barrido_universal.py` | barrido de cualquier identificador sobre todo el corpus, con inventario cacheado, detección de columnas por tipo real y avance por base |
| `backend/busqueda_multifuente.py` | las tres fuentes nuevas como endpoints (`ine2018`, `covid`, `telefonia`) |
| `backend/busqueda_manual.py` | bag-of-words por base para validación manual del operador (CFE, bancos, ISSSTE, ATT, Telcel) |
| `backend/inteligencia_completa.py` | normalización y scoring compartidos (`_normalizar_*`, `_tokenizar`, `_bow_score`) |
| `scripts/tel_5540553398/gen_informe.py` | plantilla del informe PDF+DOCX |
| `backend/rastreo_relacion.py` | dossier multi-sujeto → grafo → análisis narrativo |

Uso del barrido universal:

```bash
/opt/venv/bin/python scripts/barrido_universal.py --tel 5540553398 --curp CAGC610211MDFSTR04 --json out.json
/opt/venv/bin/python scripts/barrido_universal.py --nombre "CASTRO GUTIERREZ" --solo telcel,santander
/opt/venv/bin/python scripts/barrido_universal.py --solo-inventario
```

---

## 6. Pitfalls (los que costaron tiempo real)

- **Nunca `LIKE '%APELLIDO%'`.** `BUCIO` matchea "distri**BUCIO**nes". Usar
  `regexp_matches(upper(col),'(^|[^A-Z])APELLIDO')`.
- **No confundir `numero`/`interior`/`num_ext` con teléfonos**: en las bases
  bancarias son del domicilio. Igual `numero_servicio` (CFE) y `TEL_PROP`
  (REPUVE).
- **Un conteo en cero suele ser esquema supuesto**, no ausencia. Revisar
  `describe` antes de concluir.
- **No dumpear el corpus.** `select *` sobre las tablas nacionales cuelga
  minutos. Consultas dirigidas con conteo.
- **Homónimos ≠ parientes.** Los conteos de apellido están dominados por
  homónimos de otros estados; mantenerlos separados del sujeto y su familia en
  toda tabla y todo total.
- **No normalizar identificadores irregulares.** RFC de longitud rara, CURPs
  faltantes: se citan verbatim y se anota.
- **Reportar el avance.** Este usuario empuja contra los barridos largos y
  silenciosos: conviene soltar conteos preliminares pronto.

---

## 7. Estado de la verificación

Ejecutado sobre el caso `5540553398` para comprobar que las herramientas
reproducen lo que la investigación manual había encontrado:

| Comprobación | Resultado |
|---|---|
| `barrido_universal.py --tel 5540553398 5537172949 --curp … --rfc …` | reproduce: CURP en `padron` + `ine_2018.Df2/Df4`; ambas líneas y el RFC en las 4 variantes telcel; `5537172949` en `santander_v3` |
| `GET /api/v1/ine2018/buscar?curp=…` | 200, 2 filas (DF2, DF4), 34 tablas barridas |
| `GET /api/v1/covid/buscar?paterno=…&materno=…` | 200, filas de `covid_clinico` |
| `GET /api/v1/telefonia/buscar?telefono=…` | 200, 2 filas con `tel_contacto` y `contacto2` poblados |
| `/api/v1/ine2018/buscar` sin `Authorization` | 401 |

Regresión sobre 20 endpoints preexistentes (`/api/total`, `/api/curp/*`,
`/api/v1/persona/{curp,rfc}/*`, `/api/v1/{issste,att,repuve}/buscar`,
`/api/v1/sujeto/validar/*`, `/api/perfiles`, `/api/reports`, `/api/usage`,
`/api/history`, `/api/v1/health/bases`, `/api/admin/pending-users` y las
páginas HTML): todos 200, ninguno ensombrecido.

---

## 8. Correcciones de runtime aplicadas (2026-10-08)

Al desplegar las nuevas búsquedas aparecieron **dos defectos preexistentes** en
`backend/servir.py`, ninguno introducido por esta iteración:

### 8.1 El catch-all estático se tragaba toda la API

`do_GET` atendía `if path.startswith("/")` — que sirve páginas desde
`frontend/pages/` — **antes** de todo el bloque `/api/*`. Cualquier ruta no
listada explícitamente, incluida la API completa y los HTML de página plana,
moría en ese `404` de texto plano. En la práctica el servidor respondía
`/api/total no encontrado`, `/login.html no encontrado`, etc.

El healthcheck no lo detectaba porque solo comprueba que el socket TCP abra.

**Corrección:** el catch-all se movió al **final** de `do_GET`, antes del 404
JSON real. Todo lo que no casa arriba se intenta servir como página; si no
existe, 404 de texto plano como antes.

### 8.2 El directorio del frontend no existe dentro del contenedor

`_serve_html`, `_serve_static` y `_serve_frontend_file` resolvían siempre
`ROOT.parent/"frontend"` = `/frontend`, pero `docker-compose.yml` monta
`./frontend` en **`/app/frontend`**. Dentro del contenedor, todo el frontend
daba 404 aunque los archivos estuvieran ahí.

**Corrección:** nuevo helper `_frontend_dir()` que prueba, en orden,
`$KYC_FRONTEND_DIR`, `ROOT/"frontend"` (contenedor) y `ROOT.parent/"frontend"`
(local). Sobreescribible por entorno para el despliegue en el VPS.

### 8.3 Pendiente: el healthcheck es demasiado débil

Ambos defectos convivieron con un contenedor reportado como `healthy`. El
healthcheck (`docker-compose.yml` y `backend/Dockerfile`) y
`scripts/healthcheck.py` solo verifican que el puerto acepte conexión TCP, no
que un endpoint responda. **Recomendado en la siguiente iteración:** sustituir
por una comprobación de un endpoint real sin autenticación (p. ej. que
`/login.html` devuelva 200).

