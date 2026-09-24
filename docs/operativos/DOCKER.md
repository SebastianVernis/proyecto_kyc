# Docker — Plataforma KYC

Guía operativa para desplegar la plataforma KYC con Docker Compose.

---

## Prerrequisitos

- Docker Engine 24+ con Compose v2
- 16 GB RAM disponible (el backend necesita ~4 GB; las bases se mapean como volumen)
- ~90 GB de espacio en disco (bases DuckDB)
- Copiar `.env.example` a `.env` y llenar las API keys

---

## Arranque rápido

```bash
cd /home/sebastianvernis/proyectos/kyc/proyecto_kyc

# 1. Copiar y configurar variables
cp .env.example .env
nano .env   # llenar API keys reales

# 2. Construir y levantar
docker compose up -d --build

# 3. Verificar
docker compose ps
curl http://localhost:8765/api/total
```

---

## Servicios

| Servicio | Puerto | Descripción |
|----------|--------|-------------|
| `backend` | 8765 | Backend Python (servir.py) — API REST + frontend estático |
| `gateway` | 8001 | DB-gateway (Fase 2) — encapsula acceso a DuckDB |

### Red interna

Los servicios se comunican por la red `kyc-net`. El backend puede hablar con el gateway via `http://gateway:8001`.

### Volúmenes

| Volumen | Montaje | Descripción |
|---------|---------|-------------|
| `./bases` → `/app/bases` | Lectura/escritura | Bases DuckDB + auth.db |
| `./frontend` → `/app/frontend` | Solo lectura | Frontend HTML estático |

---

## Comandos útiles

```bash
# Ver logs en tiempo real
docker compose logs -f backend

# Reiniciar solo el backend
docker compose restart backend

# Reconstruir después de cambios en el código
docker compose up -d --build backend

# Parar todo
docker compose down

# Parar y eliminar volúmenes (CUIDADO: no elimina archivos del host)
docker compose down -v
```

---

## Variables de entorno

Ver `.env.example` para la lista completa. Las más importantes:

| Variable | Descripción | Default |
|----------|-------------|---------|
| `PADRON_DB_PATH` | Ruta al padron dentro del contenedor | `/app/bases/padron_v1.duckdb` |
| `BASES_DIR` | Directorio de bases | `/app/bases` |
| `AUTH_DB_PATH` | Ruta a auth.db (SQLite) | `/app/bases/auth.db` |
| `API_PORT` | Puerto del backend | `8765` |
| `NOMINATIM_LOCAL_URL` | Nominatim local (geocodificación) | `http://host.docker.internal:8088` |

---

## Problemas conocidos

### El backend no arranca: "Could not set lock on file"

Otro proceso (ej: `servir.py` directo en el host) tiene la DB abierta. Detenerlo primero:

```bash
# Si corriendo como servicio
sudo systemctl stop cuartodepazsearch

# Si corriendo directo
kill $(pgrep -f "servir.py")
```

### El backend tarda en arrancar

Es normal: las 29 bases externas se attachean en el `startup` event. Puede tomar 10-30 segundos dependiendo del hardware.

### Nominatim local no responde desde el contenedor

Usar `host.docker.internal` en lugar de `127.0.0.1`. En Linux, asegurarse de que Nominatim escuche en `0.0.0.0:8088` (no solo `127.0.0.1`).

---

## Arquitectura (Fase 1)

```
┌─────────────────────────────────────────────┐
│                  Docker Host                 │
│                                             │
│  ┌──────────────────┐                       │
│  │   kyc-backend    │ ←── :8765             │
│  │   (servir.py)    │                       │
│  └────────┬─────────┘                       │
│           │ read/write                      │
│           ▼                                 │
│  ┌──────────────────┐                       │
│  │  /app/bases/     │ ← volumen del host   │
│  │  *.duckdb        │                       │
│  │  auth.db         │                       │
│  └──────────────────┘                       │
│                                             │
│  Nominatim local (:8088) ←── host network   │
└─────────────────────────────────────────────┘
```
