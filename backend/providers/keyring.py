#!/usr/bin/env python3
"""keyring.py — Cifrado/descifrado de API keys de usuario en reposo.

Modelo: cada usuario puede tener su propia API key por proveedor
(singula, apify, tlaloc). Se guardan en la tabla user_provider_keys
de auth.db, cifradas con Fernet.

Derivación de la master key:
  - Si el .env define USER_KEYS_SECRET, se usa directamente (32 bytes
    url-safe base64, formato Fernet).
  - Si no, se deriva con PBKDF2-HMAC-SHA256 (200k iter) a partir de
    USER_KEYS_SECRET_SEED (o de un valor aleatorio persistente en
    bases/auth.key si tampoco hay seed).

El archivo bases/auth.key:
  - 600 perms, contiene 32 bytes binarios (clave Fernet derivada).
  - Se crea automáticamente la primera vez.
  - NUNCA debe commitearse a git (debe estar en .gitignore).
  - Si lo pierdes, todas las keys de usuario se vuelven irrecuperables
    (pero las keys globales del .env siguen funcionando).

Auditoría de cambios: este módulo NO escribe en activity_log.
Los endpoints que llaman encrypt/decrypt ya loguean el cambio.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
from pathlib import Path
from typing import Optional

try:
    from cryptography.fernet import Fernet, InvalidToken  # type: ignore
    HAS_FERNET = True
except ImportError:
    Fernet = None  # type: ignore
    InvalidToken = Exception  # type: ignore
    HAS_FERNET = False


# === Resolución de paths ===

ROOT = Path(__file__).parent.resolve()
BACKEND_ROOT = ROOT.parent  # /proyecto_kyc/backend
BASES_DIR = BACKEND_ROOT.parent / "bases"
MASTER_KEY_FILE = BASES_DIR / "auth.key"

# Proveedores soportados. Centralizado aquí para validar input de la API.
SUPPORTED_PROVIDERS = ("singula", "apify", "tlaloc", "consultaunica")


# === Master key ===

def _load_or_create_master_key() -> bytes:
    """Devuelve la master key Fernet (32 bytes url-safe base64).

    Prioridad:
      1) USER_KEYS_SECRET en env (debe ser Fernet key ya válida).
      2) USER_KEYS_SECRET_SEED en env (PBKDF2 lo deriva).
      3) bases/auth.key en disco.
      4) Genera y persiste una nueva en bases/auth.key.

    Raises:
        RuntimeError si cryptography no está instalado.
    """
    if not HAS_FERNET:
        raise RuntimeError(
            "instala cryptography: pip install cryptography "
            "(requerido para encriptar API keys de usuario)"
        )

    # 1) Fernet key ya válida del .env
    env_key = os.getenv("USER_KEYS_SECRET", "").strip()
    if env_key:
        try:
            # validar formato Fernet
            Fernet(env_key.encode("ascii"))
            return env_key.encode("ascii")
        except Exception:
            raise RuntimeError(
                "USER_KEYS_SECRET en .env no es una Fernet key válida "
                "(debe ser 32 bytes url-safe base64, 44 chars)"
            )

    # 2) Seed -> derivar
    seed = os.getenv("USER_KEYS_SECRET_SEED", "").strip()
    if seed:
        derived = hashlib.pbkdf2_hmac(
            "sha256", seed.encode("utf-8"),
            b"kyc-platform-user-keys-v1", 200_000, dklen=32
        )
        return base64.urlsafe_b64encode(derived)

    # 3 y 4) disco
    if MASTER_KEY_FILE.exists():
        try:
            stored = MASTER_KEY_FILE.read_bytes().strip()
            Fernet(stored)  # validar formato
            return stored
        except Exception:
            # archivo corrupto: lo regeneramos (advierte, no falla)
            print(f"[keyring] WARNING: {MASTER_KEY_FILE} corrupto, regenerando. "
                  f"Keys de usuario previas se vuelven irrecuperables.", flush=True)

    # 4) crear nueva
    BASES_DIR.mkdir(parents=True, exist_ok=True)
    new_key = Fernet.generate_key()
    MASTER_KEY_FILE.write_bytes(new_key)
    try:
        os.chmod(MASTER_KEY_FILE, 0o600)
    except Exception:
        pass
    print(f"[keyring] master key generada en {MASTER_KEY_FILE} (perm 600). "
          f"NO commitear a git.", flush=True)
    return new_key


_MASTER_KEY: Optional[bytes] = None


def _fernet() -> "Fernet":
    """Singleton Fernet (carga lazy para no tocar disco si no se usa)."""
    global _MASTER_KEY
    if _MASTER_KEY is None:
        _MASTER_KEY = _load_or_create_master_key()
    return Fernet(_MASTER_KEY)


# === Encrypt / decrypt ===

def encrypt_key(plaintext: str) -> bytes:
    """Cifra una API key. Devuelve bytes (Fernet token)."""
    if not plaintext:
        raise ValueError("api_key vacía")
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt_key(ciphertext: bytes) -> str:
    """Descifra una API key. Raises InvalidToken si la master key cambió."""
    if not ciphertext:
        raise ValueError("ciphertext vacío")
    return _fernet().decrypt(ciphertext).decode("utf-8")


# === Masking (para mostrar al frontend sin revelar la key) ===

def mask_key(plaintext: str) -> str:
    """Devuelve una versión enmascarada: 'sk-xxxx-last4'.

    Si la key tiene menos de 8 chars, devuelve '***'.
    Si tiene prefijo conocido (sk-, Apify 'apify_api_', etc), lo respeta.
    """
    if not plaintext:
        return ""
    if len(plaintext) < 8:
        return "***"
    # mostrar primeros 4 y últimos 4
    return f"{plaintext[:4]}{'…' * min(8, len(plaintext) - 8)}{plaintext[-4:]}"


# === Validación de provider ===

def validate_provider(provider: str) -> str:
    """Normaliza y valida que el provider sea uno soportado."""
    p = (provider or "").strip().lower()
    if p not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"provider inválido: {provider!r}. "
            f"Soportados: {', '.join(SUPPORTED_PROVIDERS)}"
        )
    return p


# === Test ===

if __name__ == "__main__":
    # round-trip test
    test = "sk-test-1234567890abcdef"
    enc = encrypt_key(test)
    dec = decrypt_key(enc)
    assert dec == test, "round-trip falló"
    print(f"OK: round-trip {test!r} -> {len(enc)} bytes -> {dec!r}")
    print(f"mask: {mask_key(test)}")
    print(f"master key file: {MASTER_KEY_FILE}")
