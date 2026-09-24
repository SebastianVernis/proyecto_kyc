#!/usr/bin/env python3
"""config.py — carga configuración centralizada desde .env y variables de sistema.

Uso:
  from config import config
  apify_token = config.apify_token
"""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
    HAS_DOTENV = True
except ImportError:
    HAS_DOTENV = False

# cargar .env desde el directorio del proyecto
ROOT = Path(__file__).parent.resolve()
ENV_PATH = ROOT / ".env"

if HAS_DOTENV and ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=True)
    print(f"[config] .env cargado desde {ENV_PATH} (override=True)")
elif ENV_PATH.exists():
    # fallback: parseo manual
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())
    print(f"[config] .env parseado manualmente (sin dotenv)")


# === helpers ===

def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _get_optional(key: str) -> Optional[str]:
    v = os.getenv(key, "").strip()
    return v if v else None


def _get_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _get_bool(key: str, default: bool = False) -> bool:
    v = os.getenv(key, str(default)).strip().lower()
    return v in ("1", "true", "yes", "on", "y", "s")


# === clase de configuración ===

class Config:
    """Configuración centralizada. Singleton — usar `config = Config()`."""

    def __init__(self):
        # === Apify ===
        self.apify_token: Optional[str] = _get_optional("APIFY_TOKEN")

        # === Padron DB (resolver a path absoluto) ===
        # 2026-08-13: WorkingDirectory del servicio systemd es /root/proyecto_kyc/,
        # entonces el path relativo "../bases/padron_v1.duckdb" (medido desde
        # /root/proyecto_kyc/backend/) se rompía a "/root/bases/padron_v1.duckdb"
        # (inexistente). Resolver a absoluto desde el directorio del proyecto,
        # NO desde el cwd del proceso.
        _padron_default = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "bases", "padron_v1.duckdb")
        _padron_raw = _get("PADRON_DB_PATH", _padron_default)
        if not os.path.isabs(_padron_raw):
            # El .env está escrito relativo al directorio del backend
            _backend_dir = os.path.dirname(os.path.abspath(__file__))
            _padron_abs = os.path.normpath(os.path.join(_backend_dir, _padron_raw))
        else:
            _padron_abs = _padron_raw
        self.padron_db_path: str = _padron_abs

        # === Tlaloc ===
        self.tlaloc_api_key: Optional[str] = _get_optional("TLALOC_API_KEY")

        # === Singula ===
        self.singula_api_key: Optional[str] = _get_optional("SINGULA_API_KEY")
        self.singula_org_id: Optional[str] = _get_optional("SINGULA_ORG_ID")
        self.singula_env: str = _get("SINGULA_ENV", "sandbox")

        # === Moffin ===
        self.moffin_api_key: Optional[str] = _get_optional("MOFFIN_API_KEY")
        self.moffin_base_url: str = _get("MOFFIN_BASE_URL", "https://sandbox.moffin.mx/api/v1")

        # === Kiban ===
        self.kiban_api_key: Optional[str] = _get_optional("KIBAN_API_KEY")

        # === GitHub ===
        self.github_token: Optional[str] = _get_optional("GITHUB_TOKEN")

        # === Ollama Cloud ===
        self.ollama_api_key: Optional[str] = _get_optional("OLLAMA_API_KEY")
        self.ollama_model: str = _get("OLLAMA_MODEL", "deepseek-v4-pro")

        # === Gemini (fallback de reportes IA) ===
        self.gemini_api_key: Optional[str] = _get_optional("GEMINI_API_KEY")
        self.gemini_model: str = _get("GEMINI_MODEL", "gemini-2.5-flash")

        # === CheckID ===
        self.checkid_api_key: Optional[str] = _get_optional("CHECKID_API_KEY")
        self.checkid_base_url: str = _get("CHECKID_BASE_URL", "https://www.checkid.mx/api")

        # === Consulta Única (Afore / Ifetel / Actas Registro Civil) ===
        self.consultaunica_api_key: Optional[str] = _get_optional("CONSULTAUNICA_API_KEY")
        self.consultaunica_mock: bool = _get_bool("CONSULTAUNICA_MOCK", False)
        # URL pública del webhook del worker que sube las actas a R2.
        self.consultaunica_webhook_url: str = _get("CONSULTAUNICA_WEBHOOK_URL", "")

        # === R2 (Cloudflare) para anexos del reporte ===
        # Base pública del bucket (p. ej. https://pub-xxxx.r2.dev o custom domain).
        # Se usa para linkar PDFs de actas en los reportes.
        self.r2_public_base_url: str = _get("R2_PUBLIC_BASE_URL", "")

        # === Clientes inicializados (lazy) ===
        self._broker_clients = {}

        # === Precios Singula (MXN) ===
        self.singula_precios = {
            # Identidad
            "curp_obtener": 3.00,
            "curp_validar": 3.00,
            "rfc_obtener_pf": 3.00,
            "rfc_obtener_pm": 3.00,
            "rfc_validar": 3.00,
            # Huella digital
            "intel_basic": 8.00,
            "intel_premium": 10.00,
            "email_lookup": 3.50,
            "phone_lookup": 3.50,  # mismo precio que email
            # Riesgo (en endpoints de customer)
            "blacklist": 0.0,  # incluido en risk
            "judicial": 0.0,
            "risk": 0.0,
            # KYB / servicios adicionales (precios proporcionados por usuario)
            "judicial_persona": 42.00,
            "blacklist_kyb": 10.00,
            "identity_verification": 20.00,
            "judicial_empresa": 42.00,
            "document_signature": 25.00,
        }
        # Paquetes predefinidos
        self.singula_paquetes = {
            "basico": {
                "descripcion": "Identidad + email/phone lookup",
                "endpoints": ["curp_validar", "rfc_validar", "email_lookup", "phone_lookup"],
                "costo_mxn": 13.00,
            },
            "completo": {
                "descripcion": "Identidad + email/phone + blacklist/judicial/risk",
                "endpoints": ["curp_validar", "rfc_validar", "email_lookup", "phone_lookup", "blacklist", "judicial", "risk"],
                "costo_mxn": 13.00,  # risk/judicial/blacklist incluidos sin costo extra
            },
            "premium": {
                "descripcion": "Todo lo anterior + Intel Basic + Intel Premium (dossier narrativo)",
                "endpoints": ["curp_validar", "rfc_validar", "email_lookup", "phone_lookup",
                              "blacklist", "judicial", "risk", "intel_basic", "intel_premium"],
                "costo_mxn": 31.00,
            },
        }

        # === Plataforma ===
        # 2026-08-13: padron_db_path ya está resuelto arriba en __init__
        # (path absoluto). No duplicar aquí.
        self.api_port: int = _get_int("API_PORT", 8765)
        self.log_level: str = _get("LOG_LEVEL", "INFO").upper()

        # === servicios disponibles (calculado) ===
        self.servicios_disponibles = {
            "apify": bool(self.apify_token),
            "tlaloc": bool(self.tlaloc_api_key),
            "singula": bool(self.singula_api_key),
            "checkid": bool(self.checkid_api_key),
            "moffin": bool(self.moffin_api_key),
            "kiban": bool(self.kiban_api_key),
            "consultaunica": bool(self.consultaunica_api_key),
            # === GitHub ===
            "github": bool(self.github_token),
            "ollama": bool(self.ollama_api_key),
            "gemini": bool(self.gemini_api_key),
        }

    def __repr__(self) -> str:
        servicios_activos = [k for k, v in self.servicios_disponibles.items() if v]
        servicios_inactivos = [k for k, v in self.servicios_disponibles.items() if not v]
        return (
            f"Config(\n"
            f"  activos:    {', '.join(servicios_activos) or '(ninguno)'}\n"
            f"  inactivos:  {', '.join(servicios_inactivos) or '(ninguno)'}\n"
            f"  db_path:    {self.padron_db_path}\n"
            f"  api_port:    {self.api_port}\n"
            f")"
        )


# singleton global
config = Config()


if __name__ == "__main__":
    # test: print estado
    print(config)
    print("\nEstado de servicios:")
    for servicio, activo in config.servicios_disponibles.items():
        estado = "✓" if activo else "✗"
        print(f"  {estado} {servicio}")
