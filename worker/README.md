# KYC Worker — Cloudflare

Worker de Cloudflare que sirve el frontend estático y proxea la API al backend.

## Funciones

1. **Static Assets** — sirve `frontend/` (HTML, CSS, JS)
2. **Proxy /api/*** — reenvía requests al backend (`API_ORIGIN`)
3. **Cache API** — cachea endpoints read-only (`/api/v1/sepomex/*`, `/api/v1/geo/*`, `/health`)
4. **Rate limiting** — token bucket por IP via KV (60 rpm anónimo, 600 rpm autenticado)

## Desarrollo local

```bash
cd worker
npm install
wrangler dev
```

El worker corre en `http://localhost:8787` y proxea a `http://localhost:8765` (backend).

## Deploy

```bash
# Login (una vez)
wrangler login

# Crear KV namespace (una vez)
wrangler kv namespace create RATE_LIMIT
# Copiar el ID a wrangler.jsonc

# Configurar secret
wrangler secret put API_ORIGIN
# Ingresar: https://tu-backend.example.com

# Deploy
wrangler deploy
```

## Estructura

```
worker/
├── src/index.ts        # Worker principal (Hono)
├── wrangler.jsonc      # Configuración de Cloudflare
├── package.json        # Dependencias
├── tsconfig.json       # TypeScript config
├── .dev.vars           # Variables para wrangler dev
└── README.md           # Este archivo
```

## Endpoints

| Path | Descripción |
|------|-------------|
| `/*` | Static Assets (frontend) |
| `/api/*` | Proxy → backend |
| `/webhook/consultaunica/actas` | Webhook público (auth HMAC) → backend |
| `/worker/health` | Healthcheck del worker |
