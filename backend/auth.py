import os
import sqlite3
import secrets
import hashlib
import time
from pathlib import Path

from webauthn import generate_registration_options, verify_registration_response
from webauthn import generate_authentication_options, verify_authentication_response

from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    ResidentKeyRequirement,
    UserVerificationRequirement,
    PublicKeyCredentialDescriptor,
    PublicKeyCredentialType,
)

ROOT = Path(__file__).parent.resolve()
DB_PATH = ROOT / "auth.db"

# Configuración de RP (Relying Party)
RP_NAME = "Cuarto de Paz Search"
RP_ID = None  # se setea desde servir.py basado en el hostname
RP_ORIGIN = None  # se setea desde servir.py basado en el origin


def set_rp(hostname: str, origin: str):
    global RP_ID, RP_ORIGIN
    RP_ID = hostname
    RP_ORIGIN = origin


def _db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                display_name TEXT,
                password_hash TEXT,
                is_admin INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                created_at REAL DEFAULT (strftime('%s','now'))
            );
            CREATE TABLE IF NOT EXISTS credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                credential_id TEXT UNIQUE NOT NULL,
                public_key BLOB NOT NULL,
                sign_count INTEGER DEFAULT 0,
                transports TEXT,
                is_backup_eligible INTEGER DEFAULT 0,
                created_at REAL DEFAULT (strftime('%s','now')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS challenges (
                challenge TEXT PRIMARY KEY,
                purpose TEXT NOT NULL,
                user_id INTEGER,
                created_at REAL DEFAULT (strftime('%s','now'))
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at REAL DEFAULT (strftime('%s','now')),
                last_seen REAL DEFAULT (strftime('%s','now'))
            );
        """)
        # Migración: agregar columnas nuevas si la tabla ya existía sin ellas
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "is_admin" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
        if "is_active" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1")
        # el primer usuario creado es admin por default
        conn.execute("UPDATE users SET is_admin = 1 WHERE username = 'admin'")
        conn.commit()


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}${h}"


def _verify_password(password: str, stored: str) -> bool:
    if "$" not in stored:
        return False
    salt, h = stored.split("$", 1)
    return hashlib.sha256((salt + password).encode()).hexdigest() == h


def set_user_password(username: str, password: str) -> dict:
    username = username.strip().lower()
    with _db() as conn:
        row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if row:
            conn.execute(
                "UPDATE users SET password_hash=? WHERE username=?",
                (_hash_password(password), username),
            )
        else:
            conn.execute(
                "INSERT INTO users (username, display_name, password_hash) VALUES (?, ?, ?)",
                (username, username, _hash_password(password)),
            )
        conn.commit()
        user = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    return {"user_id": user["id"], "username": username}


def verify_password(username: str, password: str) -> dict:
    username = username.strip().lower()
    with _db() as conn:
        user = conn.execute(
            "SELECT id, password_hash, is_active FROM users WHERE username=?", (username,)
        ).fetchone()
    if not user or not user["password_hash"]:
        raise ValueError("Credenciales inválidas")
    if not user["is_active"]:
        raise PermissionError("Cuenta deshabilitada. Contacta al administrador.")
    if not _verify_password(password, user["password_hash"]):
        raise ValueError("Credenciales inválidas")
    return create_session(user["id"], username)


def create_user_with_password(current_user: dict, username: str, password: str) -> dict:
    """Solo admin puede crear usuarios."""
    if current_user.get("username") != "admin":
        raise PermissionError("Solo admin puede crear usuarios")
    return set_user_password(username, password)


def list_users() -> list:
    """Lista mínima (compatibilidad con el modal viejo)."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, username, display_name, created_at FROM users ORDER BY username"
        ).fetchall()
    return [dict(r) for r in rows]


def list_users_admin() -> list:
    """Lista completa para la pantalla admin: incluye is_admin, is_active,
    last_seen, total_logins_30d."""
    from audit import _conn as _audit_conn
    with _db() as conn:
        rows = conn.execute("""
            SELECT u.id, u.username, u.display_name, u.is_admin, u.is_active,
                   u.created_at,
                   (SELECT MAX(s.last_seen) FROM sessions s WHERE s.user_id = u.id) AS last_seen
            FROM users u
            ORDER BY u.is_active DESC, u.username
        """).fetchall()
    user_ids = [r["id"] for r in rows]
    logins_30d = {}
    if user_ids:
        cutoff = _now() - 30 * 86400
        with _audit_conn() as ac:
            placeholders = ",".join("?" * len(user_ids))
            logins = ac.execute(
                f"SELECT user_id, COUNT(*) as c FROM activity_log "
                f"WHERE action='login' AND created_at >= ? AND user_id IN ({placeholders}) "
                f"GROUP BY user_id",
                [cutoff] + user_ids,
            ).fetchall()
        logins_30d = {r["user_id"]: r["c"] for r in logins}
    return [
        {**dict(r), "logins_30d": logins_30d.get(r["id"], 0)}
        for r in rows
    ]


def set_user_active(username: str, is_active: bool) -> dict:
    with _db() as conn:
        conn.execute("UPDATE users SET is_active = ? WHERE username = ?",
                     (1 if is_active else 0, username.strip().lower()))
        conn.commit()
        # si lo desactivamos, matar todas sus sesiones
        if not is_active:
            conn.execute("""DELETE FROM sessions WHERE user_id IN
                           (SELECT id FROM users WHERE username = ?)""",
                         (username.strip().lower(),))
            conn.commit()
    return {"username": username.strip().lower(), "is_active": bool(is_active)}


def set_user_admin(username: str, is_admin: bool) -> dict:
    with _db() as conn:
        conn.execute("UPDATE users SET is_admin = ? WHERE username = ?",
                     (1 if is_admin else 0, username.strip().lower()))
        conn.commit()
    return {"username": username.strip().lower(), "is_admin": bool(is_admin)}


def delete_user(username: str) -> dict:
    """Borra un usuario y todas sus sesiones / credenciales."""
    username = username.strip().lower()
    with _db() as conn:
        conn.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()
    return {"deleted": username}


def ensure_default_admin() -> dict:
    return set_user_password("admin", "admin123")


def _now() -> float:
    return time.time()


def _b64(data: bytes) -> str:
    return bytes_to_base64url(data)


def _b64d(s: str) -> bytes:
    return base64url_to_bytes(s)


def _create_challenge(user_id: int = None, purpose: str = "register") -> str:
    chal = secrets.token_urlsafe(32)
    with _db() as conn:
        conn.execute(
            "INSERT INTO challenges (challenge, purpose, user_id) VALUES (?, ?, ?)",
            (chal, purpose, user_id),
        )
        conn.commit()
    return chal


def _consume_challenge(challenge: str, purpose: str, user_id: int = None) -> bool:
    with _db() as conn:
        c = conn.execute(
            "SELECT * FROM challenges WHERE challenge=? AND purpose=? AND (user_id IS ? OR user_id=? OR user_id IS NULL)",
            (challenge, purpose, user_id, user_id),
        ).fetchone()
        if not c:
            return False
        if _now() - c["created_at"] > 120:
            conn.execute("DELETE FROM challenges WHERE challenge=?", (challenge,))
            conn.commit()
            return False
        conn.execute("DELETE FROM challenges WHERE challenge=?", (challenge,))
        conn.commit()
        return True


def get_registration_options(username: str):
    if not RP_ID or not RP_ORIGIN:
        raise RuntimeError("RP no configurado")
    # Reutilizar usuario si ya existe
    with _db() as conn:
        row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if row:
            user_id = row["id"]
        else:
            cur = conn.execute(
                "INSERT INTO users (username, display_name) VALUES (?, ?)",
                (username, username),
            )
            user_id = cur.lastrowid
            conn.commit()
    challenge = _create_challenge(user_id=user_id, purpose="register")
    user_id_bytes = user_id.to_bytes(4, "big", signed=True)
    try:
        options = generate_registration_options(
            rp_id=RP_ID,
            rp_name=RP_NAME,
            user_id=user_id_bytes,
            user_name=username,
            user_display_name=username,
            challenge=_b64d(challenge),
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            supported_pub_key_algs=[-7, -257],  # ES256, RS256
        )
    except Exception:
        # Fallback mínimo si la librería cambia API
        options = generate_registration_options(
            rp_id=RP_ID,
            rp_name=RP_NAME,
            user_id=user_id_bytes,
            user_name=username,
            user_display_name=username,
            challenge=_b64d(challenge),
        )
    # Guardamos user_id en session para el siguiente paso
    return {
        "options": options,
        "username": username,
        "user_id_b64": _b64(user_id_bytes),
    }


def verify_registration(username: str, credential: dict):
    if not RP_ID or not RP_ORIGIN:
        raise RuntimeError("RP no configurado")
    client_data_json_b64 = credential.get("response", {}).get("clientDataJSON")
    if not client_data_json_b64:
        raise ValueError("Falta clientDataJSON")
    import json
    client_data = json.loads(_b64d(client_data_json_b64).decode("utf-8"))
    challenge = client_data.get("challenge")
    if not challenge:
        raise ValueError("Falta challenge")

    with _db() as conn:
        user = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if not user:
            raise ValueError("Usuario no encontrado")
    if not _consume_challenge(challenge, "register", user["id"]):
        raise ValueError("Challenge inválido o expirado")

    result = verify_registration_response(
        credential=credential,
        expected_challenge=_b64d(challenge),
        expected_rp_id=RP_ID,
        expected_origin=RP_ORIGIN,
    )
    with _db() as conn:
        conn.execute(
            """INSERT INTO credentials
               (user_id, credential_id, public_key, sign_count, transports, is_backup_eligible)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                user["id"],
                bytes_to_base64url(result.credential_id),
                result.credential_public_key,
                result.sign_count,
                ",".join(credential.get("response", {}).get("transports") or []) or "",
                int(result.credential_device_type == "multi_device"),
            ),
        )
        conn.commit()
    return {"ok": True, "user_id": user["id"], "username": username}


def get_authentication_options(username: str = None):
    if not RP_ID or not RP_ORIGIN:
        raise RuntimeError("RP no configurado")
    allow_credentials = None
    user_id = None
    if username:
        with _db() as conn:
            user = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            if user:
                user_id = user["id"]
                creds = conn.execute(
                    "SELECT credential_id FROM credentials WHERE user_id=?", (user_id,)
                ).fetchall()
                allow_credentials = [PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["credential_id"]), type=PublicKeyCredentialType.PUBLIC_KEY) for c in creds]
    challenge = _create_challenge(user_id=user_id, purpose="login")
    try:
        options = generate_authentication_options(
            rp_id=RP_ID,
            challenge=_b64d(challenge),
            allow_credentials=allow_credentials,
            user_verification=UserVerificationRequirement.REQUIRED,
        )
    except Exception:
        options = generate_authentication_options(
            rp_id=RP_ID,
            challenge=_b64d(challenge),
            allow_credentials=allow_credentials,
        )
    return {"options": options}


def verify_authentication(credential: dict):
    if not RP_ID or not RP_ORIGIN:
        raise RuntimeError("RP no configurado")
    import json
    client_data = json.loads(_b64d(credential["response"]["clientDataJSON"]).decode("utf-8"))
    challenge = client_data.get("challenge")
    if not challenge:
        raise ValueError("Falta challenge")
    cred_id = credential.get("id")
    if not cred_id:
        raise ValueError("Falta id de credencial")
    with _db() as conn:
        cred = conn.execute(
            "SELECT * FROM credentials WHERE credential_id=?", (cred_id,)
        ).fetchone()
        if not cred:
            raise ValueError("Credencial no registrada")
        user = conn.execute("SELECT * FROM users WHERE id=?", (cred["user_id"],)).fetchone()
        if not user:
            raise ValueError("Usuario no encontrado")
    if not _consume_challenge(challenge, "login", cred["user_id"]):
        raise ValueError("Challenge inválido o expirado")

    result = verify_authentication_response(
        credential=credential,
        expected_challenge=_b64d(challenge),
        expected_rp_id=RP_ID,
        expected_origin=RP_ORIGIN,
        credential_public_key=cred["public_key"],
        credential_current_sign_count=cred["sign_count"],
    )
    with _db() as conn:
        conn.execute(
            "UPDATE credentials SET sign_count=? WHERE credential_id=?",
            (result.new_sign_count, cred_id),
        )
        conn.commit()
    return create_session(cred["user_id"], user["username"])


def create_session(user_id: int, username: str) -> dict:
    token = secrets.token_urlsafe(32)
    with _db() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id) VALUES (?, ?)",
            (token, user_id),
        )
        conn.commit()
    return {"token": token, "username": username, "user_id": user_id}


def validate_session(token: str) -> dict:
    if not token:
        return None
    with _db() as conn:
        s = conn.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
        if not s:
            return None
        if _now() - s["created_at"] > 86400 * 7:  # 7 días
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            conn.commit()
            return None
        conn.execute(
            "UPDATE sessions SET last_seen=? WHERE token=?",
            (_now(), token),
        )
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE id=?", (s["user_id"],)).fetchone()
    if not user:
        return None
    return {"user_id": user["id"], "username": user["username"]}


def logout(token: str):
    with _db() as conn:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        conn.commit()


def has_users() -> bool:
    with _db() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0


def cleanup_challenges():
    cutoff = _now() - 300
    with _db() as conn:
        conn.execute("DELETE FROM challenges WHERE created_at < ?", (cutoff,))
        conn.commit()


def require_session(handler_func):
    """Decorador para proteger endpoints. El handler recibe (self, session) en vez de (self)."""
    def wrapper(self, *args, **kwargs):
        token = self._get_session_token()
        session = validate_session(token)
        if not session:
            self._json(401, {"error": "no autenticado"})
            return
        return handler_func(self, session, *args, **kwargs)
    return wrapper


if __name__ == "__main__":
    init_db()
    print("auth.db inicializado en", DB_PATH)


# Inicializar la DB al importar — crea tablas y aplica migraciones.
# Idempotente y barato (sólo corre CREATE TABLE IF NOT EXISTS + ALTER).
init_db()
