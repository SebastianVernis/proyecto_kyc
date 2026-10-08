# proyecto_kyc — índice

Plataforma KYC / OSINT para personas físicas en México. Backend Python
(`servir.py`) + frontend estático + ~29 bases DuckDB/SQLite federadas (~342M
filas, ~38 GB) + motor LLM (Oráculo).

## Por dónde empezar

| Si quieres… | Lee |
|---|---|
| **Desplegar** (backend en Docker, Worker en Cloudflare) | `docs/DEPLOY.md` |
| **Buscar a una persona** (metodologías, endpoints, pitfalls) | `docs/METODOLOGIAS_BUSQUEDA.md` |
| **Entender qué bases hay y cómo están armadas** | `ANALISIS_BUSQUEDAS_LAYOUTS.md` (layouts y endpoints) |
| **Conocer el corpus base a base** | `INVENTARIO_BASES.md` → hoy en `docs/handoffs/` |
| **Tocar el código** | `docs/AGENTS.md` |

## Estructura

```
proyecto_kyc/
├── AGENTS.md / CLAUDE.md      índices de GitNexus para agentes (raíz)
├── ANALISIS_BUSQUEDAS_LAYOUTS.md   inventario de layouts de bases y endpoints
├── docker-compose.yml         backend (8765) + gateway (8001)
├── healthcheck.py             comprobación de salud del sistema
├── backend/                   código del servidor (servir.py y módulos)
├── frontend/                  HTML/CSS/JS estático
├── worker/                    Cloudflare Worker (sirve el frontend y proxea la API)
├── db-gateway/                gateway de bases (Fase 2)
├── bases/                     las bases de datos (montadas en /bases)
├── scripts/                   herramientas en uso (barrido_universal.py)
├── docs/                      DEPLOY.md, METODOLOGIAS_BUSQUEDA.md, AGENTS.md
│   ├── handoffs/              cierres de etapa anteriores (histórico)
│   └── operativos/            casos y reportes de investigación
├── obsoleto/                  despliegue antiguo (VPS/systemd/Pages) — archivado
└── reportes/                  informes generados (PDF + DOCX)
```

## Los dos despliegues

1. **Backend** — Docker Compose en este servidor. Un cambio en `backend/`
   requiere `docker compose build backend && docker compose up -d backend`.
2. **Frontend + proxy** — Cloudflare Worker. **Se despliega solo al hacer
   `git push`** (Cloudflare Workers Builds está conectado al repo).

El detalle, en `docs/DEPLOY.md`.
