# `obsoleto/` — material retirado

Nada de aquí se usa en producción. Se conserva solo como referencia histórica
de cómo se desplegaba el proyecto antes de Cloudflare Workers.

| Ruta | Qué era | Por qué está obsoleto |
|---|---|---|
| `deploy/deploy.py` | desplegaba el backend en la VPS, arrancaba el túnel cloudflared y publicaba el frontend en Cloudflare Pages | el frontend y el proxy los sirve ahora el Worker (`worker/`) |
| `deploy/install-service.sh` + `deploy/cuartodepazsearch.service.template` | instalaba `cuartodepazsearch@<user>.service` como unidad systemd | el backend corre en Docker Compose (`kyc-backend`), no como servicio systemd |
| `deploy/rsync_bases.sh` | copiaba las bases desde la máquina remota por `ssh`/`rsync` | las bases se montan por volumen (`./bases:/bases`) |
| `deploy/DEPLOY_kyc.md` | runbook del despliegue VPS + Pages + cloudflared | reemplazado por `docs/DEPLOY.md` |
| `auth.db.vacio` | SQLite de 0 bytes en la raíz del repo | sombra muerta; la real es `bases/auth.db` |
| `contact_log.txt`, `clip_webhooks.log` | logs sueltos de una integración antigua | sin valor operativo |

**Cómo se despliega hoy:** `docs/DEPLOY.md`.
