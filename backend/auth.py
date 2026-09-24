import os
import sqlite3
import secrets
import hashlib
import time
from pathlib import Path
from typing import Any, Optional

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
DB_PATH = ROOT.parent / "bases" / "auth.db"

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
            CREATE TABLE IF NOT EXISTS user_provider_keys (
                user_id    INTEGER NOT NULL,
                provider   TEXT    NOT NULL,   -- 'singula' | 'apify' | 'tlaloc' | ...
                api_key_enc BLOB   NOT NULL,   -- Fernet-encrypted
                created_at REAL    DEFAULT (strftime('%s','now')),
                updated_at REAL    DEFAULT (strftime('%s','now')),
                last_used  REAL,               -- último request
                last_ok    REAL,               -- último health check OK
                last_error TEXT,               -- mensaje si último health falló
                PRIMARY KEY (user_id, provider),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_upk_user ON user_provider_keys(user_id);

            -- 2026-08-26: multi-tenant
            CREATE TABLE IF NOT EXISTS tenants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,        -- 'plataforma-encuentra', 'cuartodepaz', etc.
                nombre TEXT NOT NULL,
                created_by INTEGER NOT NULL,
                created_at REAL DEFAULT (strftime('%s','now')),
                FOREIGN KEY (created_by) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS tenants_users (
                tenant_id INTEGER NOT NULL,
                user_id   INTEGER NOT NULL,
                rol       TEXT DEFAULT 'member',  -- 'owner' | 'admin' | 'member' | 'viewer'
                joined_at REAL DEFAULT (strftime('%s','now')),
                PRIMARY KEY (tenant_id, user_id),
                FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id)   REFERENCES users(id)   ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_tu_user ON tenants_users(user_id);
        """)
        # Migración: agregar columnas nuevas si la tabla ya existía sin ellas
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "is_admin" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
        if "is_active" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1")
        # el primer usuario creado es admin por default
        conn.execute("UPDATE users SET is_admin = 1 WHERE username = 'admin'")
        # 2026-08-26: bootstrap del tenant default "plataforma-encuentra" si no existe
        admin_row = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()
        if admin_row:
            admin_id = admin_row["id"]
            t = conn.execute("SELECT id FROM tenants WHERE slug='plataforma-encuentra'").fetchone()
            if not t:
                conn.execute(
                    "INSERT INTO tenants (slug, nombre, created_by) VALUES (?, ?, ?)",
                    ("plataforma-encuentra", "Plataforma Encuentra", admin_id)
                )
                t = conn.execute("SELECT id FROM tenants WHERE slug='plataforma-encuentra'").fetchone()
            # todos los users existentes quedan miembros del tenant default
            for u in conn.execute("SELECT id FROM users").fetchall():
                conn.execute(
                    "INSERT OR IGNORE INTO tenants_users (tenant_id, user_id, rol) VALUES (?, ?, ?)",
                    (t["id"], u["id"], "owner" if u["id"] == admin_id else "member")
                )
        conn.commit()

    # 2026-09-03: subscriptions & transactions tables
    with _db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                plan TEXT NOT NULL CHECK(plan IN ('basico','profesional','empresarial','corporativo')),
                status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','expired','cancelled','pending')),
                started_at REAL DEFAULT (strftime('%s','now')),
                expires_at REAL NOT NULL,
                renewed_at REAL,
                cancelled_at REAL,
                created_at REAL DEFAULT (strftime('%s','now')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_sub_user ON subscriptions(user_id);
            CREATE INDEX IF NOT EXISTS idx_sub_status ON subscriptions(status);
            CREATE INDEX IF NOT EXISTS idx_sub_expires ON subscriptions(expires_at);

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                subscription_id INTEGER,
                plan TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'USD',
                provider TEXT NOT NULL DEFAULT 'clip',
                provider_tx_id TEXT,
                tx_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','declined','refunded')),
                clip_url TEXT,
                ip_address TEXT,
                user_agent TEXT,
                created_at REAL DEFAULT (strftime('%s','now')),
                approved_at REAL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
            );
            CREATE INDEX IF NOT EXISTS idx_tx_user ON transactions(user_id);
            CREATE INDEX IF NOT EXISTS idx_tx_hash ON transactions(tx_hash);
            CREATE INDEX IF NOT EXISTS idx_tx_status ON transactions(status);

            CREATE TABLE IF NOT EXISTS onboarding (
                user_id INTEGER PRIMARY KEY,
                tour_completed INTEGER NOT NULL DEFAULT 0,
                tour_started_at REAL,
                tour_completed_at REAL,
                created_at REAL DEFAULT (strftime('%s','now')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)

    # 2026-09-03: usage counters table
    with _db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS usage_counters (
                user_id INTEGER NOT NULL,
                counter_type TEXT NOT NULL CHECK(counter_type IN ('busqueda','rastreo','osint')),
                period TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, counter_type, period),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_uc_user ON usage_counters(user_id);

            CREATE TABLE IF NOT EXISTS payment_tokens (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                plan TEXT NOT NULL,
                clip_url TEXT,
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','used','expired')),
                created_at REAL DEFAULT (strftime('%s','now')),
                expires_at REAL NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_pt_user ON payment_tokens(user_id);
            CREATE INDEX IF NOT EXISTS idx_pt_status ON payment_tokens(status);
        """)

    # 2026-09-03: search history, saved reports, KYC verification
    with _db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS search_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                query TEXT NOT NULL,
                query_type TEXT DEFAULT 'global',
                results_count INTEGER DEFAULT 0,
                duration_ms INTEGER,
                ip_address TEXT,
                created_at REAL DEFAULT (strftime('%s','now')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_sh_user ON search_history(user_id);
            CREATE INDEX IF NOT EXISTS idx_sh_created ON search_history(created_at);

            CREATE TABLE IF NOT EXISTS saved_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                subject_name TEXT,
                subject_curp TEXT,
                subject_rfc TEXT,
                report_type TEXT DEFAULT 'kyc',
                report_html TEXT,
                report_json TEXT,
                file_path TEXT,
                status TEXT DEFAULT 'active' CHECK(status IN ('active','archived','deleted')),
                created_at REAL DEFAULT (strftime('%s','now')),
                updated_at REAL DEFAULT (strftime('%s','now')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_sr_user ON saved_reports(user_id);
            CREATE INDEX IF NOT EXISTS idx_sr_status ON saved_reports(status);

            CREATE TABLE IF NOT EXISTS kyc_verifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                curp TEXT,
                rfc TEXT,
                nombre TEXT,
                apellido_paterno TEXT,
                apellido_materno TEXT,
                verification_type TEXT DEFAULT 'ine_scan',
                provider TEXT DEFAULT 'singula',
                provider_response TEXT,
                status TEXT DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','error')),
                verified_by INTEGER,
                verified_at REAL,
                rejection_reason TEXT,
                created_at REAL DEFAULT (strftime('%s','now')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (verified_by) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_kv_user ON kyc_verifications(user_id);
            CREATE INDEX IF NOT EXISTS idx_kv_status ON kyc_verifications(status);

            CREATE TABLE IF NOT EXISTS actas_submissions (
                uuid TEXT PRIMARY KEY,
                curp TEXT NOT NULL,
                acta_type TEXT NOT NULL,
                con_folio INTEGER DEFAULT 0,
                user_id INTEGER,
                created_at REAL DEFAULT (strftime('%s','now')),
                status TEXT DEFAULT 'pending' CHECK(status IN ('pending','completed','failed')),
                received_at REAL,
                error_message TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_actas_curp ON actas_submissions(curp);
            CREATE INDEX IF NOT EXISTS idx_actas_status ON actas_submissions(status);

            CREATE TABLE IF NOT EXISTS user_approvals (
                user_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
                approved_by INTEGER,
                approved_at REAL,
                rejection_reason TEXT,
                created_at REAL DEFAULT (strftime('%s','now')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (approved_by) REFERENCES users(id)
            );
        """)


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


def list_transactions(limit: int = 100, offset: int = 0) -> list:
    """Lista transacciones con username del usuario."""
    with _db() as conn:
        rows = conn.execute("""
            SELECT t.id, t.user_id, u.username, t.plan, t.amount, t.currency,
                   t.provider, t.tx_hash, t.status, t.created_at, t.approved_at
            FROM transactions t
            LEFT JOIN users u ON t.user_id = u.id
            ORDER BY t.created_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()
    return [dict(r) for r in rows]


def list_subscriptions_admin() -> list:
    """Lista TODOS los usuarios con su suscripción (si la tiene) y transacciones."""
    with _db() as conn:
        rows = conn.execute("""
            SELECT u.id AS user_id, u.username, u.display_name,
                   s.id AS sub_id, s.plan, s.status, s.started_at, s.expires_at,
                   s.renewed_at, s.cancelled_at,
                   (SELECT COUNT(*) FROM transactions t WHERE t.user_id=u.id AND t.status='approved') AS tx_count,
                   (SELECT SUM(t.amount) FROM transactions t WHERE t.user_id=u.id AND t.status='approved') AS total_paid
            FROM users u
            LEFT JOIN subscriptions s ON s.user_id = u.id AND s.id = (
                SELECT id FROM subscriptions WHERE user_id=u.id ORDER BY created_at DESC LIMIT 1
            )
            ORDER BY u.is_active DESC, u.username
        """).fetchall()
    return [dict(r) for r in rows]


def update_subscription_admin(subscription_id: int, plan: str = None, status: str = None, expires_at: float = None) -> dict:
    """Actualizar una suscripción desde el admin."""
    with _db() as conn:
        sub = conn.execute("SELECT * FROM subscriptions WHERE id=?", (subscription_id,)).fetchone()
        if not sub:
            return None
        updates = []
        params = []
        if plan and plan in PLAN_PRICES:
            updates.append("plan=?")
            params.append(plan)
        if status and status in ("active", "expired", "cancelled", "pending"):
            updates.append("status=?")
            params.append(status)
            if status == "cancelled":
                updates.append("cancelled_at=?")
                params.append(time.time())
        if expires_at is not None:
            updates.append("expires_at=?")
            params.append(expires_at)
        if not updates:
            return dict(sub)
        params.append(subscription_id)
        conn.execute(f"UPDATE subscriptions SET {', '.join(updates)} WHERE id=?", params)
        conn.commit()
        updated = conn.execute("SELECT * FROM subscriptions WHERE id=?", (subscription_id,)).fetchone()
        return dict(updated)


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
    return {"user_id": user["id"], "username": user["username"], "is_admin": bool(user["is_admin"])}


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


# ============================================================================
# Provider keys (singula, apify, tlaloc) — per-usuario
# ============================================================================

def _get_user_id(username: str) -> Optional[int]:
    """Helper: devuelve user_id o None."""
    with _db() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username.strip().lower(),)
        ).fetchone()
    return row["id"] if row else None


def get_provider_key(username: str, provider: str) -> Optional[str]:
    """Devuelve la API key en claro del usuario para `provider`, o None si
    no tiene una configurada. NO hace fallback a la key global."""
    from providers.keyring import decrypt_key, validate_provider
    provider = validate_provider(provider)
    uid = _get_user_id(username)
    if uid is None:
        return None
    with _db() as conn:
        row = conn.execute(
            "SELECT api_key_enc FROM user_provider_keys WHERE user_id=? AND provider=?",
            (uid, provider),
        ).fetchone()
    if not row:
        return None
    try:
        return decrypt_key(row["api_key_enc"])
    except Exception:
        # master key cambió o datos corruptos: tratar como no-configurado
        return None


def set_provider_key(username: str, provider: str, api_key: str) -> dict:
    """Guarda (o reemplaza) la API key del usuario para `provider`."""
    from providers.keyring import encrypt_key, validate_provider
    provider = validate_provider(provider)
    api_key = (api_key or "").strip()
    if not api_key:
        raise ValueError("api_key vacía")
    if len(api_key) < 8:
        raise ValueError("api_key demasiado corta (mínimo 8 chars)")
    uid = _get_user_id(username)
    if uid is None:
        raise ValueError(f"usuario no existe: {username}")
    enc = encrypt_key(api_key)
    now = _now()
    with _db() as conn:
        conn.execute("""
            INSERT INTO user_provider_keys (user_id, provider, api_key_enc, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, provider) DO UPDATE SET
                api_key_enc = excluded.api_key_enc,
                updated_at  = excluded.updated_at,
                last_error  = NULL
        """, (uid, provider, enc, now, now))
        conn.commit()
    return {"username": username, "provider": provider, "updated_at": now}


def delete_provider_key(username: str, provider: str) -> dict:
    """Borra la API key del usuario (vuelve al fallback global).

    Tolerante: si el usuario no existe, devuelve deleted=False (no raise).
    """
    from providers.keyring import validate_provider
    provider = validate_provider(provider)
    uid = _get_user_id(username)
    if uid is None:
        return {"username": username, "provider": provider, "deleted": False}
    with _db() as conn:
        cur = conn.execute(
            "DELETE FROM user_provider_keys WHERE user_id=? AND provider=?",
            (uid, provider),
        )
        conn.commit()
        deleted = cur.rowcount
    return {"username": username, "provider": provider, "deleted": deleted > 0}


def list_provider_keys(username: str) -> list:
    """Lista las keys configuradas por el usuario (MÁSCARA, no en claro)."""
    from providers.keyring import decrypt_key, mask_key
    uid = _get_user_id(username)
    if uid is None:
        return []
    with _db() as conn:
        rows = conn.execute("""
            SELECT provider, api_key_enc, created_at, updated_at,
                   last_used, last_ok, last_error
            FROM user_provider_keys
            WHERE user_id = ?
            ORDER BY provider
        """, (uid,)).fetchall()
    out = []
    for r in rows:
        try:
            plain = decrypt_key(r["api_key_enc"])
            masked = mask_key(plain)
        except Exception:
            masked = "*** (error de cifrado)"
        out.append({
            "provider": r["provider"],
            "masked": masked,
            "has_key": True,
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "last_used":  r["last_used"],
            "last_ok":    r["last_ok"],
            "last_error": r["last_error"],
        })
    return out


def touch_provider_key_usage(username: str, provider: str, ok: bool,
                              error_msg: Optional[str] = None) -> None:
    """Actualiza last_used/last_ok/last_error después de un request al provider.
    Fire-and-forget; no propaga excepciones.
    """
    try:
        from providers.keyring import validate_provider
        provider = validate_provider(provider)
        uid = _get_user_id(username)
        if uid is None:
            return
        now = _now()
        with _db() as conn:
            if ok:
                conn.execute("""
                    UPDATE user_provider_keys
                    SET last_used = ?, last_ok = ?, last_error = NULL
                    WHERE user_id = ? AND provider = ?
                """, (now, now, uid, provider))
            else:
                conn.execute("""
                    UPDATE user_provider_keys
                    SET last_used = ?, last_error = ?
                    WHERE user_id = ? AND provider = ?
                """, (now, (error_msg or "")[:500], uid, provider))
            conn.commit()
    except Exception:
        pass


# ============================================================
# SUBSCRIPTION MANAGEMENT
# ============================================================

PLAN_PRICES = {
    "basico": 29,
    "profesional": 99,
    "empresarial": 299,
    "corporativo": 500,
}

PLAN_LIMITS = {
    "basico":        {"busquedas_mes": 30,   "rastreos_mes": 10,   "osint": False, "api": False, "usuarios": 1},
    "profesional":   {"busquedas_mes": 500,  "rastreos_mes": 100,  "osint": True,  "api": False, "usuarios": 3},
    "empresarial":   {"busquedas_mes": 1000,  "rastreos_mes": 500,  "osint": True,  "api": True,  "usuarios": 10},
    "corporativo":   {"busquedas_mes": -1,    "rastreos_mes": -1,   "osint": True,  "api": True,  "usuarios": -1},
}


def get_subscription(user_id: int) -> Optional[dict]:
    """Return active subscription or None."""
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM subscriptions WHERE user_id=? AND status='active' ORDER BY expires_at DESC LIMIT 1",
            (user_id,)
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] < time.time():
            conn.execute("UPDATE subscriptions SET status='expired' WHERE id=?", (row["id"],))
            conn.commit()
            return None
        return dict(row)


def create_subscription(user_id: int, plan: str, tx_id: str = None, ip: str = None, ua: str = None) -> dict:
    """Create subscription + transaction record. Returns {subscription, transaction, tx_hash}."""
    if plan not in PLAN_PRICES:
        raise ValueError(f"Plan inválido: {plan}")

    now = time.time()
    expires = now + 30 * 86400  # 30 days

    # Generate transaction hash
    tx_data = f"{user_id}:{plan}:{tx_id}:{now}"
    tx_hash = hashlib.sha256(tx_data.encode()).hexdigest()

    with _db() as conn:
        # Check duplicate
        existing = conn.execute("SELECT id FROM transactions WHERE tx_hash=?", (tx_hash,)).fetchone()
        if existing:
            return {"duplicate": True, "tx_hash": tx_hash}

        # Create subscription
        conn.execute(
            "INSERT OR REPLACE INTO subscriptions (user_id, plan, status, started_at, expires_at) VALUES (?,?,?,?,?)",
            (user_id, plan, "active", now, expires)
        )
        sub_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Create transaction
        conn.execute(
            "INSERT INTO transactions (user_id, subscription_id, plan, amount, provider, provider_tx_id, tx_hash, status, ip_address, user_agent, approved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, sub_id, plan, PLAN_PRICES[plan], "clip", tx_id, tx_hash, "approved", ip, ua, now)
        )
        tx_db_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Onboarding
        conn.execute("INSERT OR IGNORE INTO onboarding (user_id, tour_started_at) VALUES (?,?)", (user_id, now))

        conn.commit()

    return {
        "subscription_id": sub_id,
        "transaction_id": tx_db_id,
        "tx_hash": tx_hash,
        "expires_at": expires,
        "plan": plan,
        "amount": PLAN_PRICES[plan],
    }


def register_declined(user_id: int, plan: str, code: str = None, reason: str = None, ip: str = None, ua: str = None) -> dict:
    """Register a declined transaction."""
    now = time.time()
    tx_data = f"{user_id}:{plan}:declined:{now}"
    tx_hash = hashlib.sha256(tx_data.encode()).hexdigest()
    with _db() as conn:
        conn.execute(
            "INSERT INTO transactions (user_id, plan, amount, provider, tx_hash, status, ip_address, user_agent) VALUES (?,?,?,?,?,?,?,?)",
            (user_id, plan or "unknown", 0, "clip", tx_hash, "declined", ip, ua)
        )
        conn.commit()
    return {"tx_hash": tx_hash}


def check_subscription_access(user_id: int) -> dict:
    """Check if user has active subscription. Returns {has_subscription, plan, limits, expires_at}."""
    sub = get_subscription(user_id)
    if not sub:
        return {"has_subscription": False, "plan": None, "limits": None, "expires_at": None}
    return {
        "has_subscription": True,
        "plan": sub["plan"],
        "limits": PLAN_LIMITS.get(sub["plan"], {}),
        "expires_at": sub["expires_at"],
        "started_at": sub["started_at"],
    }


def complete_onboarding(user_id: int):
    with _db() as conn:
        conn.execute("UPDATE onboarding SET tour_completed=1, tour_completed_at=? WHERE user_id=?", (time.time(), user_id))
        conn.commit()


def is_onboarding_complete(user_id: int) -> bool:
    with _db() as conn:
        row = conn.execute("SELECT tour_completed FROM onboarding WHERE user_id=?", (user_id,)).fetchone()
        return row["tour_completed"] == 1 if row else False


# ============================================================
# USAGE COUNTERS
# ============================================================

def _current_period() -> str:
    """Return current billing period as 'YYYY-MM'."""
    import datetime
    return datetime.datetime.now().strftime("%Y-%m")


def get_usage(user_id: int, counter_type: str = "busqueda") -> dict:
    """Get current usage for a counter type in the current period.
    Returns {used, limit, remaining, unlimited}."""
    period = _current_period()
    sub = get_subscription(user_id)
    if not sub:
        return {"used": 0, "limit": 0, "remaining": 0, "unlimited": False, "no_subscription": True}

    limits = PLAN_LIMITS.get(sub["plan"], {})
    if counter_type == "osint":
        # osint is a boolean access flag, not a numeric quota
        has_osint = limits.get("osint", False)
        return {"used": 0, "limit": -1 if has_osint else 0, "remaining": -1 if has_osint else 0, "unlimited": has_osint}
    limit_key = {"busqueda": "busquedas_mes", "rastreo": "rastreos_mes"}.get(counter_type, f"{counter_type}s_mes")
    limit = limits.get(limit_key, 0)

    with _db() as conn:
        row = conn.execute(
            "SELECT count FROM usage_counters WHERE user_id=? AND counter_type=? AND period=?",
            (user_id, counter_type, period)
        ).fetchone()
        used = row["count"] if row else 0

    unlimited = limit == -1
    remaining = -1 if unlimited else max(0, limit - used)

    return {"used": used, "limit": limit, "remaining": remaining, "unlimited": unlimited}


def check_and_increment(user_id: int, counter_type: str = "busqueda") -> dict:
    """Check if user has remaining quota and increment counter.
    Returns {allowed, ...usage} or {allowed: False, error}."""
    usage = get_usage(user_id, counter_type)
    if usage.get("no_subscription"):
        return {"allowed": False, "error": "Suscripción requerida", **usage}
    if not usage["unlimited"] and usage["remaining"] <= 0:
        return {"allowed": False, "error": f"Límite de {counter_type}s alcanzado ({usage['limit']}/mes)", **usage}

    period = _current_period()
    with _db() as conn:
        conn.execute("""
            INSERT INTO usage_counters (user_id, counter_type, period, count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(user_id, counter_type, period)
            DO UPDATE SET count = count + 1
        """, (user_id, counter_type, period))
        conn.commit()

    usage["used"] += 1
    if not usage["unlimited"]:
        usage["remaining"] -= 1
    usage["allowed"] = True
    return usage


def get_all_usage(user_id: int) -> dict:
    """Get usage for all counter types."""
    return {
        "busquedas": get_usage(user_id, "busqueda"),
        "rastreos": get_usage(user_id, "rastreo"),
        "osint": get_usage(user_id, "osint"),
    }


# ============================================================
# SEARCH HISTORY
# ============================================================

def save_search(user_id: int, query: str, query_type: str = "global", results_count: int = 0, duration_ms: int = 0, ip: str = None):
    with _db() as conn:
        conn.execute(
            "INSERT INTO search_history (user_id, query, query_type, results_count, duration_ms, ip_address) VALUES (?,?,?,?,?,?)",
            (user_id, query, query_type, results_count, duration_ms, ip)
        )
        conn.commit()

def get_search_history(user_id: int, limit: int = 50, offset: int = 0) -> list:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM search_history WHERE user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset)
        ).fetchall()
        return [dict(r) for r in rows]

def get_search_history_count(user_id: int) -> int:
    with _db() as conn:
        row = conn.execute("SELECT COUNT(*) as c FROM search_history WHERE user_id=?", (user_id,)).fetchone()
        return row["c"] if row else 0


# ============================================================
# SAVED REPORTS
# ============================================================

def save_report(user_id: int, title: str, subject_name: str = None, subject_curp: str = None, subject_rfc: str = None, report_type: str = "kyc", report_html: str = None, report_json: str = None, file_path: str = None) -> int:
    with _db() as conn:
        conn.execute(
            "INSERT INTO saved_reports (user_id, title, subject_name, subject_curp, subject_rfc, report_type, report_html, report_json, file_path) VALUES (?,?,?,?,?,?,?,?,?)",
            (user_id, title, subject_name, subject_curp, subject_rfc, report_type, report_html, report_json, file_path)
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

def get_saved_reports(user_id: int, status: str = "active", limit: int = 50, offset: int = 0) -> list:
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, user_id, title, subject_name, subject_curp, subject_rfc, report_type, status, created_at, updated_at FROM saved_reports WHERE user_id=? AND status=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (user_id, status, limit, offset)
        ).fetchall()
        return [dict(r) for r in rows]

def get_report_detail(user_id: int, report_id: int) -> dict:
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM saved_reports WHERE id=? AND user_id=?",
            (report_id, user_id)
        ).fetchone()
        return dict(row) if row else None

def archive_report(user_id: int, report_id: int):
    with _db() as conn:
        conn.execute("UPDATE saved_reports SET status='archived', updated_at=? WHERE id=? AND user_id=?", (time.time(), report_id, user_id))
        conn.commit()

def delete_report(user_id: int, report_id: int):
    with _db() as conn:
        conn.execute("UPDATE saved_reports SET status='deleted', updated_at=? WHERE id=? AND user_id=?", (time.time(), report_id, user_id))
        conn.commit()


# ============================================================
# KYC VERIFICATION (INE scan via Singula)
# ============================================================

def create_kyc_verification(user_id: int, curp: str = None, rfc: str = None, nombre: str = None, apellido_paterno: str = None, apellido_materno: str = None, verification_type: str = "ine_scan", provider: str = "singula", provider_response: str = None) -> int:
    with _db() as conn:
        conn.execute(
            "INSERT INTO kyc_verifications (user_id, curp, rfc, nombre, apellido_paterno, apellido_materno, verification_type, provider, provider_response, status) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (user_id, curp, rfc, nombre, apellido_paterno, apellido_materno, verification_type, provider, provider_response, "pending")
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

def get_kyc_verifications(user_id: int = None, status: str = None) -> list:
    with _db() as conn:
        q = "SELECT kv.*, u.username, u.display_name FROM kyc_verifications kv JOIN users u ON kv.user_id = u.id WHERE 1=1"
        params = []
        if user_id:
            q += " AND kv.user_id=?"
            params.append(user_id)
        if status:
            q += " AND kv.status=?"
            params.append(status)
        q += " ORDER BY kv.created_at DESC"
        rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]

def approve_kyc_verification(verification_id: int, verified_by: int, status: str = "approved", rejection_reason: str = None):
    with _db() as conn:
        conn.execute(
            "UPDATE kyc_verifications SET status=?, verified_by=?, verified_at=?, rejection_reason=? WHERE id=?",
            (status, verified_by, time.time(), rejection_reason, verification_id)
        )
        conn.commit()


# ============================================================
# USER APPROVALS (admin must approve new registrations)
# ============================================================

def create_user_approval(user_id: int):
    with _db() as conn:
        conn.execute("INSERT OR IGNORE INTO user_approvals (user_id, status) VALUES (?, 'pending')", (user_id,))
        conn.commit()

def get_user_approval(user_id: int) -> dict:
    with _db() as conn:
        row = conn.execute("SELECT * FROM user_approvals WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None

def approve_user(user_id: int, approved_by: int, status: str = "approved", rejection_reason: str = None):
    with _db() as conn:
        conn.execute(
            "UPDATE user_approvals SET status=?, approved_by=?, approved_at=?, rejection_reason=? WHERE user_id=?",
            (status, approved_by, time.time(), rejection_reason, user_id)
        )
        conn.execute("UPDATE users SET is_active=? WHERE id=?", (1 if status == "approved" else 0, user_id))
        conn.commit()

def get_pending_users() -> list:
    with _db() as conn:
        rows = conn.execute("""
            SELECT u.id, u.username, u.display_name, u.created_at, ua.status, ua.created_at as requested_at
            FROM users u
            JOIN user_approvals ua ON u.id = ua.user_id
            WHERE ua.status = 'pending'
            ORDER BY ua.created_at ASC
        """).fetchall()
        return [dict(r) for r in rows]


# ============================================================
# PAYMENT TOKENS (Clip callback flow)
# ============================================================

def create_payment_token(user_id: int, plan: str, clip_url: str = None) -> str:
    """Create a one-time payment token for Clip callback."""
    token = secrets.token_urlsafe(32)
    expires = time.time() + 3600  # 1 hour
    with _db() as conn:
        conn.execute(
            "INSERT INTO payment_tokens (token, user_id, plan, clip_url, status, expires_at) VALUES (?,?,?,?,?,?)",
                (token, user_id, plan, clip_url, "pending", expires)
        )
        conn.commit()
    return token

def validate_payment_token(token: str) -> dict:
    """Validate and consume a payment token. Returns {user_id, plan} or None."""
    with _db() as conn:
        row = conn.execute("SELECT * FROM payment_tokens WHERE token=? AND status='pending'", (token,)).fetchone()
        if not row:
            return None
        if row["expires_at"] < time.time():
            conn.execute("UPDATE payment_tokens SET status='expired' WHERE token=?", (token,))
            conn.commit()
            return None
        conn.execute("UPDATE payment_tokens SET status='used' WHERE token=?", (token,))
        conn.commit()
        return {"user_id": row["user_id"], "plan": row["plan"]}

def cleanup_expired_payment_tokens():
    """Remove expired payment tokens."""
    with _db() as conn:
        conn.execute("UPDATE payment_tokens SET status='expired' WHERE status='pending' AND expires_at<?", (time.time(),))
        conn.commit()


# Inicializar la DB al importar — crea tablas y aplica migraciones.
# Idempotente y barato (sólo corre CREATE TABLE IF NOT EXISTS + ALTER).
init_db()


# 2026-08-26: helpers multi-tenant
def user_tenants(user_id: int) -> list:
    """Devuelve la lista de tenants a los que pertenece el user."""
    with _db() as conn:
        rows = conn.execute("""
            SELECT t.id, t.slug, t.nombre, tu.rol
            FROM tenants t
            JOIN tenants_users tu ON tu.tenant_id = t.id
            WHERE tu.user_id = ?
            ORDER BY t.id
        """, (user_id,)).fetchall()
    return [dict(r) for r in rows]


def user_belongs_to_tenant(user_id: int, tenant_id: int) -> bool:
    with _db() as conn:
        row = conn.execute(
            "SELECT 1 FROM tenants_users WHERE user_id=? AND tenant_id=?",
            (user_id, tenant_id)
        ).fetchone()
    return bool(row)


def create_tenant(slug: str, nombre: str, created_by: int) -> dict:
    with _db() as conn:
        conn.execute(
            "INSERT INTO tenants (slug, nombre, created_by) VALUES (?,?,?)",
            (slug, nombre, created_by)
        )
        t = conn.execute("SELECT id FROM tenants WHERE slug=?", (slug,)).fetchone()
        conn.execute(
            "INSERT INTO tenants_users (tenant_id, user_id, rol) VALUES (?,?, 'owner')",
            (t["id"], created_by)
        )
        conn.commit()
    return {"id": t["id"], "slug": slug, "nombre": nombre}


def add_user_to_tenant(tenant_id: int, user_id: int, rol: str = "member"):
    with _db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tenants_users (tenant_id, user_id, rol) VALUES (?,?,?)",
            (tenant_id, user_id, rol)
        )
        conn.commit()
