# Despliegue — proyecto_kyc

**Fecha:** 2026-10-08
**Reemplaza a:** `obsoleto/deploy/DEPLOY_kyc.md` (runbook VPS + systemd +
Cloudflare Pages + cloudflared, ya no aplica).

Hay dos despliegues independientes. El **backend** va en Docker Compose en este
servidor; el **frontend + proxy** va en un Cloudflare Worker que se despliega
solo al hacer `git push`.

---

## 1. Backend (Docker Compose, este servidor)

```bash
cd /mnt/disco2/projects/kyc/proyecto_kyc
docker compose build backend      # reconstruye la imagen (copia backend/ dentro)
docker compose up -d backend      # recrea el contenedor kyc-backend
docker compose ps                 # debe quedar "Up ... (healthy)"
```

**La imagen hornea el código**: `backend/Dockerfile` hace `COPY backend/ /app/`,
así que un cambio en `backend/*.py` **no** se refleja hasta reconstruir. No hay
bind-mount del código, solo de las bases y el frontend:

| Volumen | Destino | Modo |
|---|---|---|
| `./bases` | `/bases` | lectura/escritura (el servidor escribe `perfil_completo.duckdb` y `auth.db`) |
| `./frontend` | `/app/frontend` | `ro` |

Servicios: `backend` (8765) y `gateway` (8001). El resto de la plataforma KYC
(engine, workers, redis, db) es **otro** proyecto Compose, ajeno a este.

### Verificación tras cada despliegue

El healthcheck del contenedor pide `GET /health` (liveness real, sin auth);
antes solo abría el socket TCP, así que un despliegue podía quedar "healthy" con
la API entera devolviendo 404. **Comprobar endpoints de verdad:**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/health       # 200
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/login.html   # 200
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/api/total    # 401 sin token (404 = API sombreada)
```

`healthcheck.py` incluye además el mismo chequeo de `/health` como parte de su
estado global (`KYC_HEALTH_URL` lo sobreescribe). `/api/v1/health/bases` (con
sesión) lista las bases attacheadas y sus conteos.

---

## 2. Worker de Cloudflare (frontend + proxy) — se despliega con el push

El Worker `kyc-worker` sirve `frontend/` (static assets) y proxea `/api/*` al
backend, con cache y rate limiting. Su configuración está en
`worker/wrangler.jsonc`.

**Cloudflare Workers Builds está conectado al repositorio de GitHub**, así que
el despliegue se dispara al hacer push a la rama conectada:

```bash
git add ... && git commit -m "..." && git push origin main
```

`main` es la rama publicada (`git branch -vv`). El push hace que Cloudflare
construya y publique el Worker. Para comprobar:

```bash
cd worker
npx wrangler deployments list
curl -s https://kyc-worker.oaxaca.workers.dev/worker/health
```

### Deploy manual del Worker (solo si Cloudflare no lo toma)

```bash
cd worker
npx wrangler deploy          # requiere sesión de wrangler
npx wrangler tail            # logs en vivo
```

Estado actual: cuenta `Oaxaca@cuartodepaz.org`, entorno `staging`, URL
`https://kyc-worker.oaxaca.workers.dev`.

---

## 3. Lo que ya NO se usa

| Antiguo | Reemplazado por |
|---|---|
| `backend/deploy.py` (VPS + cloudflared + Pages) | push a GitHub → Cloudflare Workers Builds |
| `backend/install-service.sh` + systemd | Docker Compose (`kyc-backend`) |
| cloudflared tunnel | el Worker hace de proxy |
| Cloudflare Pages | static assets del Worker |
| `rsync_bases.sh` | volumen `./bases:/bases` |

Todo ello está archivado en `obsoleto/deploy/` con su explicación.
