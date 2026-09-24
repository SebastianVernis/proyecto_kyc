"""DB-gateway — FastAPI app para acceso a DuckDB.

Expone endpoints tipados para lookup por CURP, RFC y nombre
sobre las bases DuckDB del proyecto KYC.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException

from .config import GATEWAY_SHARED_SECRET
from .pools import pool
from .routers import health, padron, imss, cfe, generic


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: precalentar pool de DuckDB. Shutdown: cerrar conexiones."""
    print("[gateway] Arrancando pool de DuckDB...")
    await pool.startup()
    yield
    print("[gateway] Cerrando pool...")
    await pool.shutdown()


app = FastAPI(
    title="KYC DB-Gateway",
    description="Gateway de acceso a bases DuckDB del proyecto KYC",
    version="1.0.0",
    lifespan=lifespan,
)


# ── Auth middleware ────────────────────────────────────────────────────────────
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Validar Bearer token si GATEWAY_SHARED_SECRET está configurado."""
    # Healthcheck y docs sin auth
    if request.url.path in ("/health", "/health/bases", "/docs", "/openapi.json", "/"):
        return await call_next(request)

    if GATEWAY_SHARED_SECRET:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != GATEWAY_SHARED_SECRET:
            from starlette.responses import JSONResponse
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)


# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(health.router)
app.include_router(padron.router)
app.include_router(imss.router)
app.include_router(cfe.router)
app.include_router(generic.router)


@app.get("/")
async def root():
    return {"service": "kyc-gateway", "status": "running"}
