#!/usr/bin/env python3
"""provider_factory.py — crea instancias de providers por usuario.

Centraliza la lógica de resolver la API key de un usuario:
  1) Busca la key per-usuario en user_provider_keys.
  2) Si NO existe, hace fallback a la key global del .env.
  3) Si tampoco hay, devuelve None y el handler sabe qué hacer.

Cada provider expone la misma interfaz (`name`, `health`).
"""
from __future__ import annotations

from typing import Optional


def _resolve_user_key(username: str, provider: str) -> Optional[str]:
    """Busca la key del usuario; None si no tiene."""
    try:
        import auth
        return auth.get_provider_key(username, provider)
    except Exception:
        return None


def _resolve_global_key(provider: str) -> Optional[str]:
    """Fallback: key global del .env."""
    try:
        from config import config
        if provider == "singula":
            return config.singula_api_key
        if provider == "apify":
            return config.apify_token
        if provider == "tlaloc":
            return config.tlaloc_api_key
        if provider == "checkid":
            return config.checkid_api_key
        if provider == "consultaunica":
            return config.consultaunica_api_key
    except Exception:
        pass
    return None


def resolve_api_key(username: str, provider: str) -> tuple[Optional[str], str]:
    """Devuelve (api_key, source) donde source ∈ {'user', 'global', None}.

    Orden de preferencia: per-usuario → global.
    """
    user_key = _resolve_user_key(username, provider)
    if user_key:
        return user_key, "user"
    gkey = _resolve_global_key(provider)
    if gkey:
        return gkey, "global"
    return None, "none"


def get_singula_client(username: str):
    """Devuelve SingulaClient para el usuario, o None."""
    from providers.singula import SingulaClient
    api_key, _ = resolve_api_key(username, "singula")
    if not api_key:
        return None
    try:
        from config import config
        return SingulaClient(
            api_key=api_key,
            env=config.singula_env,
        )
    except Exception:
        return None


def get_apify_client(username: str):
    """Devuelve ApifyOSINTClient para el usuario, o None."""
    from providers.apify_osint import ApifyOSINTClient
    api_key, _ = resolve_api_key(username, "apify")
    if not api_key:
        return None
    return ApifyOSINTClient(api_key=api_key)


def get_tlaloc_client(username: str):
    """Devuelve TlalocClient para el usuario, o None."""
    from providers.tlaloc import TlalocClient
    api_key, _ = resolve_api_key(username, "tlaloc")
    if not api_key:
        return None
    return TlalocClient(api_key=api_key)


def get_consultaunica_client(username: str):
    """Devuelve ConsultaUnicaClient para el usuario, o None."""
    from providers.consultaunica import ConsultaUnicaClient
    api_key, _ = resolve_api_key(username, "consultaunica")
    if not api_key:
        return None
    try:
        from config import config
        return ConsultaUnicaClient(
            api_key=api_key,
            mock=getattr(config, "consultaunica_mock", False),
        )
    except Exception:
        return ConsultaUnicaClient(api_key=api_key)


# Helper para logging en servir.py
def touch_usage(username: str, provider: str, ok: bool, error: str = "") -> None:
    """Fire-and-forget: actualiza last_used/last_ok/last_error en DB."""
    try:
        import auth
        auth.touch_provider_key_usage(username, provider, ok, error)
    except Exception:
        pass
