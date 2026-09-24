"""Router de healthcheck."""
from fastapi import APIRouter

from ..pools import pool

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    """Healthcheck básico — responde 200 si el pool está listo."""
    return {"status": "ok", "ready": pool._ready}


@router.get("/health/bases")
async def health_bases():
    """Estado detallado de cada base en el pool."""
    return pool.health()
