# DEPLOY — Cuarto de Paz Search (Padrón Fusionado 88M)

Fecha: 2026-08-02
Cambio: `ine.duckdb` reemplazado por `padron_fusion.duckdb` (versión fusionada con +1M CURPs nuevos).

---

## 1. RESUMEN DEL CAMBIO

| Métrica | Antes | Después |
|---|---|---|
| Registros en padrón | 87,381,584 | 88,402,547 |
| Tamaño `.duckdb` | 25 GB | 6.2 GB |
| CURPs únicos | 85,080,173 | 86,101,136 |
| CURPs nuevos (no estaban antes) | — | 1,020,963 (source) |
| Códigos de fuente | (origen = path Excel) | `fuente = 'ine'` o `'source'` |
| Campos `cp`, `folio`, `cred`, `consec` | BIGINT | VARCHAR (preserva ceros a la izquierda) |
| Campo `fecnac` | DATE | VARCHAR `YYYY-MM-DD` |
| Completitud de `fecnac` | 78% | 99.42% |
| Completitud de dirección | 85% | 91.52% |
| Edades negativas | 214K | 0 |

---

## 2. ARCHIVOS INVOLUCRADOS

```
src-deploy.tar.gz                   (22 MB)  — código + HTMLs + scripts
ine.duckdb                          (6.2 GB) — base de datos fusionada
DEPLOY.md                           (este archivo)
```

Ruta local de origen (en el VPS del usuario):
- `/home/sebastianvernis/Descargas MEGA/BASE INE 2021/BASE INE 2018/src-deploy.tar.gz`
- `/home/sebastianvernis/Descargas MEGA/BASE INE 2021/BASE INE 2018/src/ine.duckdb`

---

## 3. ENTORNO DEL SERVIDOR BOTS

Ya verificado:
- IP: `108.175.4.67`
- Usuario: `root` (vía SSH con clave)
- Python 3.13.5 instalado (`/usr/bin/python3`)
- 81 GB libres en `/`
- Sin venv previo (necesita crearse)
- Sin servicio systemd previo
- Sin Cloudflare tunnel previo

Estado actual del directorio `/root/`:
```
ine.duckdb          (25 GB - VIEJO, debe reemplazarse)
src-deploy.tar.gz   (3.6 MB - VIEJO, debe reemplazarse)
```

---

## 4. PASOS DE DESPLIEGUE

### 4.1. Reemplazar `src-deploy.tar.gz` y `ine.duckdb` vía rsync

**Importante**: usar flag `-T` para evitar problemas de PTY con fish (shell del usuario remoto). El rsync se hace desde la máquina del usuario, NO desde el servidor bots.

Desde la máquina local (`sebastianvernis`):

```bash
# Backup de los archivos viejos en bots (por si algo sale mal)
ssh -T root@108.175.4.67 '
  mv /root/ine.duckdb /root/ine.duckdb.bak_pre_fusion_$(date +%Y%m%d)
  mv /root/src-deploy.tar.gz /root/src-deploy.tar.gz.bak_pre_fusion_$(date +%Y%m%d)
'

# Subir el tarball (rápido, 22 MB)
rsync -ah -e 'ssh -T -o StrictHostKeyChecking=accept-new' \
  --progress \
  "/home/sebastianvernis/Descargas MEGA/BASE INE 2021/BASE INE 2018/src-deploy.tar.gz" \
  root@108.175.4.67:/root/

# Subir la base (6.2 GB, tarda 1-2 min)
rsync -ah -e 'ssh -T -o StrictHostKeyChecking=accept-new' \
  --progress \
  "/home/sebastianvernis/Descargas MEGA/BASE INE 2021/BASE INE 2018/src/ine.duckdb" \
  root@108.175.4.67:/root/

# Verificación de checksums (opcional)
ssh -T root@108.175.4.67 '
  ls -lh /root/ine.duckdb /root/src-deploy.tar.gz
  md5sum /root/ine.duckdb /root/src-deploy.tar.gz
'
```

### 4.2. Descomprimir el tarball

En el servidor bots:

```bash
ssh -T root@108.175.4.67 '
  set -e
  cd /root
  # Backup de la estructura anterior si existe
  if [ -d /root/ine.duckdb.bak_pre_fusion_* ]; then
    echo "Backup ya existe, OK"
  fi

  # Descomprimir el tarball (crea /root/src/ con todo)
  tar -xzf src-deploy.tar.gz

  # Verificar
  ls -la /root/src/
  ls -la /root/src/*.duckdb /root/src/*.db 2>/dev/null
'
```

**Importante**: el tarball NO contiene `ine.duckdb`, `auth.db`, ni `.env` (todos están excluidos por seguridad). Hay que mover la base manualmente y crear el `.env`.

### 4.3. Mover base y crear estructura de directorios

```bash
ssh -T root@108.175.4.67 '
  set -e

  # Crear estructura de directorios según el estándar del proyecto
  mkdir -p "/root/Descargas MEGA/BASE INE 2021/BASE INE 2018"

  # Mover src/ a su ubicación final
  mv /root/src "/root/Descargas MEGA/BASE INE 2021/BASE INE 2018/src"

  # Mover la base de datos
  mv /root/ine.duckdb "/root/Descargas MEGA/BASE INE 2021/BASE INE 2018/src/ine.duckdb"

  # Limpiar archivos sueltos en /root
  rm -f /root/src-deploy.tar.gz
  # Dejar ine.duckdb.bak_pre_fusion_* y src-deploy.tar.gz.bak_pre_fusion_* como respaldo

  # Verificación
  ls -la "/root/Descargas MEGA/BASE INE 2021/BASE INE 2018/src/" | head -10
  ls -lh "/root/Descargas MEGA/BASE INE 2021/BASE INE 2018/src/ine.duckdb"
'
```

### 4.4. Crear venv e instalar dependencias

```bash
ssh -T root@108.175.4.67 '
  set -e

  # Crear venv
  python3 -m venv /root/.venv
  source /root/.venv/bin/activate
  pip install --upgrade pip

  # Dependencias necesarias para servir.py
  pip install duckdb python-dotenv

  # Si vas a regenerar la base desde CSVs (no es necesario, pero por si acaso)
  # pip install pandas polars pyarrow python-calamine openpyxl

  # Verificación
  python -c "import duckdb; print(\"duckdb:\", duckdb.__version__)"
  python -c "from dotenv import load_dotenv; print(\"dotenv OK\")"
'
```

### 4.5. Crear `.env` desde `.env.example`

El tarball trae `.env.example` (plantilla sin secrets). El servidor bots necesita su propio `.env` con API keys reales.

```bash
ssh -T root@108.175.4.67 '
  cd "/root/Descargas MEGA/BASE INE 2021/BASE INE 2018/src"

  # Copiar plantilla
  cp .env.example .env
  chmod 600 .env

  # Editar .env y rellenar las API keys
  nano .env
  # O usar vi/vim: vi .env
'
```

API keys requeridas (las tiene el usuario en su VPS local):
- `APIFY_TOKEN`
- `TLALOC_API_KEY`
- `SINGULA_API_KEY`
- `SINGULA_ORG_ID`
- `CHECKID_API_KEY`
- `OLLAMA_API_KEY`

Si el usuario no proporciona las keys, dejar vacías — el servidor arranca igual pero los servicios externos fallarán.

### 4.6. Crear servicio systemd

```bash
ssh -T root@108.175.4.67 '
  set -e

  # Instalar plantilla
  cp "/root/Descargas MEGA/BASE INE 2021/BASE INE 2018/src/cuartodepazsearch.service.template" \
     /etc/systemd/system/cuartodepazsearch@root.service

  # Reemplazar %I por root
  sed -i "s/%I/root/g" /etc/systemd/system/cuartodepazsearch@root.service

  # Verificar el service
  cat /etc/systemd/system/cuartodepazsearch@root.service

  # Recargar systemd y arrancar
  systemctl daemon-reload
  systemctl enable cuartodepazsearch@root.service
  systemctl start cuartodepazsearch@root.service

  # Verificar que arrancó
  sleep 5
  systemctl status cuartodepazsearch@root.service --no-pager | head -10
  echo "---"
  ss -tlnp 2>/dev/null | grep 8765
'
```

### 4.7. Verificación funcional

```bash
ssh -T root@108.175.4.67 '
  # Health check
  curl -s -m 10 http://127.0.0.1:8765/api/health
  # Esperado: {"error": "no autenticado"}  (401 es OK, significa que el server responde)

  # Login + test de endpoint
  COOKIE=/tmp/cookies.txt
  rm -f $COOKIE
  curl -s -m 10 -c $COOKIE -X POST http://127.0.0.1:8765/api/auth/login/password \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"admin\",\"password\":\"admin123\"}"

  # Test /api/total (debe devolver 88,402,547)
  curl -s -m 30 -b $COOKIE http://127.0.0.1:8765/api/total

  # Test /api/curp con una CURP de fuente=source
  curl -s -m 30 -b $COOKIE "http://127.0.0.1:8765/api/curp/JIVM821110MJCMLN05" | head -c 500
'
```

Resultado esperado del último:
- `"total": 88402547`
- Una fila con `"fuente": "source"`, `"fecnac": "1982-11-10"`, `"cp": "00022480"`

### 4.8. Frontend (Cloudflare Pages)

Si `wrangler` no está instalado en el servidor bots:

```bash
ssh -T root@108.175.4.67 '
  # Instalar node/npm si no está
  which node || (apt update && apt install -y nodejs npm)

  # Instalar wrangler
  npm install -g wrangler

  # Login (requiere interactividad, mejor hacerlo desde local con auth)
  # wrangler login
'
```

**Recomendación**: deployar el frontend desde la máquina local del usuario (que ya tiene Cloudflare auth configurado), no desde bots:

Desde local:
```bash
cd "/home/sebastianvernis/Descargas MEGA/BASE INE 2021/BASE INE 2018/src"
python deploy.py --pages-only
```

### 4.9. Cloudflare Tunnel

Si no hay un tunnel configurado, instalarlo:

```bash
ssh -T root@108.175.4.67 '
  # Instalar cloudflared
  ARCH=$(uname -m)
  curl -L "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64" \
    -o /usr/local/bin/cloudflared
  chmod +x /usr/local/bin/cloudflared

  # Login (requiere interactividad)
  cloudflared tunnel login

  # Crear tunnel (si no existe)
  cloudflared tunnel create cuartodepazsearch

  # Configurar DNS (requiere el tunnel ID)
  cloudflared tunnel route dns cuartodepazsearch search.cuartodepaz.org

  # Arrancar tunnel como servicio
  cloudflared service install
  systemctl enable cloudflared
  systemctl start cloudflared

  # Verificar
  systemctl status cloudflared --no-pager | head -5
'
```

---

## 5. ROLLBACK

Si algo sale mal, restaurar la versión anterior:

```bash
ssh -T root@108.175.4.67 '
  set -e
  cd "/root/Descargas MEGA/BASE INE 2021/BASE INE 2018"

  # Detener servicio
  systemctl stop cuartodepazsearch@root.service

  # Restaurar base vieja
  if [ -f "/root/ine.duckdb.bak_pre_fusion_"* ]; then
    mv /root/Descargas\ MEGA/BASE\ INE\ 2021/BASE\ INE\ 2018/src/ine.duckdb \
       /root/ine.duckdb.fusion_discard
    cp /root/ine.duckdb.bak_pre_fusion_* /root/Descargas\ MEGA/BASE\ INE\ 2021/BASE\ INE\ 2018/src/ine.duckdb
  fi

  # Restaurar tarball viejo (código)
  if [ -f /root/src-deploy.tar.gz.bak_pre_fusion_* ]; then
    rm -rf /root/Descargas\ MEGA/BASE\ INE\ 2021/BASE\ INE\ 2018/src
    tar -xzf /root/src-deploy.tar.gz.bak_pre_fusion_* -C /root/
    mv /root/src /root/Descargas\ MEGA/BASE\ INE\ 2021/BASE\ INE\ 2018/src
  fi

  # Re-arrancar
  systemctl start cuartodepazsearch@root.service
  systemctl status cuartodepazsearch@root.service --no-pager | head -5
'
```

---

## 6. RESUMEN DE CAMBIOS EN CÓDIGO

Archivos modificados localmente para compatibilidad con el nuevo esquema:

### `src/servir.py`
- Lista `COLUMNAS`: `"origen"` → `"fuente"` (línea 92-94)
- Exclusión de columnas internas: `"origen"` → `"fuente"` (líneas 706, 759)
- `params.append(int(cp_n))` → `params.append(cp_n)` con validación `cp_n.isdigit() and len(cp_n) == 5` (línea 906-908)

### `src/buscar.html`
- `add("cp", "=", parseInt(f.cp, 10))` → `add("cp", "=", f.cp.trim())` (línea 790)
- `add("folio", "=", parseInt(f.folio, 10))` → `add("folio", "=", f.folio.trim())` (línea 793)

### `src/sujeto.html`
- `s.cp.padStart(5, "0").slice(0, 5)` → `s.cp.slice(0, 5)` con comentario explicativo (línea 695)

### `cuartodepazsearch.service.template` y `.service`
- Ruta de `servir.py`: añadida `/src/` (antes incorrecta, apuntaba a `BASE INE 2018/servir.py`)

---

## 7. UBICACIÓN DEL BACKUP DE SEGURIDAD

En el VPS local del usuario (`/home/sebastianvernis`):

```
/home/sebastianvernis/Descargas MEGA/source/_backup_20260802_004944/
  src/                                              (66 MB - código completo pre-cambio)
  ine.duckdb.original                               (25 GB - ine.duckdb antes de la fusión)
  padron_fusion.duckdb.original                     (6.2 GB - snapshot post-fusión)
```

Si necesitas volver al estado original del VPS local:

```bash
# Restaurar ine.duckdb viejo en el VPS local
cp "/home/sebastianvernis/Descargas MEGA/source/_backup_20260802_004944/ine.duckdb.original" \
   "/home/sebastianvernis/Descargas MEGA/BASE INE 2021/BASE INE 2018/src/ine.duckdb"
```

---

## 8. SCRIPTS ÚTILES EN EL PROYECTO

Para regenerar la base si es necesario (en el VPS local, no en bots):

```bash
cd "/home/sebastianvernis/Descargas MEGA/source"
# Re-cargar source/ a DuckDB (los 25 GB de CSVs originales):
python normalizar_csv.py --rebuild

# Re-fusionar ine + source:
python fusionar_polars.py

# Aplicar correcciones (fecnac desde CURP, edad, CP padding):
python corregir_limitaciones.py
```

---

## 9. CHECKLIST DE VALIDACIÓN POST-DEPLOY

- [ ] `systemctl status cuartodepazsearch@root.service` muestra `active (running)`
- [ ] Puerto 8765 escuchando (`ss -tlnp | grep 8765`)
- [ ] `curl /api/health` responde con `{"error": "no autenticado"}` (401)
- [ ] Login con admin/admin123 funciona
- [ ] `curl /api/total` devuelve `{"total": 88402547}`
- [ ] `curl /api/curp/JIVM821110MJCMLN05` devuelve registro con `fuente: source`
- [ ] Logs sin errores: `journalctl -u cuartodepazsearch@root.service -n 50`
- [ ] Tunnel activo: `systemctl status cloudflared`
- [ ] Frontend deployado: `https://search.cuartodepaz.org` carga
- [ ] Búsqueda desde UI funciona (probar con admin/admin123)
