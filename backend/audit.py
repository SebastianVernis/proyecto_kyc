#!/usr/bin/env python3
"""audit.py — Registro de actividad de usuarios (admin only).

Tabla `activity_log` en SQLite (mismo archivo `auth.db` para que un solo
backup cubra todo lo del admin). Cada request autenticada queda
registrada con:
  - user_id, username
  - action ("login", "search", "view_sujeto", "logout", "user_create", ...)
  - endpoint (path del API)
  - method (GET, POST)
  - status_code
  - duration_ms
  - query_summary (JSON sanitizado — NUNCA passwords, NUNCA tokens)
  - results_count (si aplica)
  - ip (X-Forwarded-For con fallback a la conexión)
  - user_agent (truncado a 200 chars)
  - created_at (epoch seconds)

Retention policy: configurable, default 90 días. La función
`purge_old_activity(days)` se puede llamar desde un cron.
"""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

# Mismo DB que auth — un solo archivo = un solo backup.
ROOT = Path(__file__).parent.resolve()
DB_PATH = ROOT.parent / "bases" / "auth.db"

# Defaults — pueden ser sobreescritos en env
DEFAULT_RETENTION_DAYS = int(os.getenv("AUDIT_RETENTION_DAYS", "90"))
DEFAULT_MAX_ROWS = int(os.getenv("AUDIT_MAX_ROWS", "200000"))

# Campos que SIEMPRE se eliminan del query_summary (defensa en profundidad)
SENSITIVE_KEYS = {
    "password", "passwd", "pwd", "secret", "token", "api_key", "apikey",
    "session", "session_token", "cookie", "authorization", "credential",
    "private_key", "sign_count",
}

# Acciones conocidas — usadas por la UI para filtrar y colorear
ACTIONS = {
    "login":        "Inicio de sesión",
    "logout":       "Cierre de sesión",
    "login_fail":   "Intento fallido",
    "search":       "Búsqueda padrón",
    "view_sujeto":  "Ver expediente",
    "view_curp":    "Ver CURP",
    "user_create":  "Crear usuario",
    "user_disable": "Deshabilitar usuario",
    "user_enable":  "Habilitar usuario",
    "user_delete":  "Eliminar usuario",
    "user_reset":   "Resetear contraseña",
    "admin_view":   "Vista admin",
    "export_csv":   "Exportar CSV",
}

_LOCK = threading.Lock()


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    """Crea la tabla activity_log si no existe. Idempotente."""
    with _LOCK, _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                action TEXT NOT NULL,
                endpoint TEXT,
                method TEXT,
                status_code INTEGER,
                duration_ms INTEGER,
                query_summary TEXT,
                results_count INTEGER,
                ip TEXT,
                user_agent TEXT,
                created_at REAL DEFAULT (strftime('%s','now'))
            );
            CREATE INDEX IF NOT EXISTS idx_activity_user
                ON activity_log(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_activity_action
                ON activity_log(action, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_activity_created
                ON activity_log(created_at DESC);
        """)
        c.commit()


def _sanitize(value: Any) -> Any:
    """Elimina recursivamente campos sensibles de un dict. Trunca strings."""
    if isinstance(value, dict):
        return {
            k: _sanitize(v) for k, v in value.items()
            if k.lower() not in SENSITIVE_KEYS
        }
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    if isinstance(value, str):
        return value[:200] if len(value) <= 200 else value[:200] + "…"
    return value


def _summarize_query(endpoint: str, params: dict, body: dict) -> dict:
    """Construye un query_summary mínimo útil, sanitizado.

    - Para /api/search: guarda {where_prefix, params_keys, params_count}
      — NO los valores de params (pueden ser nombres).
    - Para /api/sujeto: guarda {curp_prefix} (4 chars).
    - Para /api/curp: guarda {curp_prefix}.
    - Para /api/auth/users: guarda {action}.
    - Default: guarda sólo las keys presentes.
    """
    summary: dict = {"endpoint": endpoint}

    if endpoint == "/api/search":
        where = (body.get("where") or "").strip()
        # sólo el prefix del WHERE — suficiente para entender QUÉ buscó
        summary["where_prefix"] = where[:120] if where else ""
        params = body.get("params") or []
        summary["params_count"] = len(params)
        summary["params_types"] = [type(p).__name__ for p in params[:5]]
        summary["limit"] = body.get("limit")
        summary["offset"] = body.get("offset")
    elif endpoint == "/api/sujeto":
        curp = (params.get("curp") or [""])[0]
        summary["curp_prefix"] = curp[:4] if curp else ""
    elif endpoint.startswith("/api/curp/"):
        # path-param
        curp = endpoint.split("/")[-1]
        summary["curp_prefix"] = curp[:4] if curp else ""
    elif endpoint == "/api/auth/users":
        summary["action"] = body.get("action")
        if body.get("action") == "create":
            summary["target_user"] = (body.get("username") or "")[:80]
    elif endpoint == "/api/auth/login":
        username = (body.get("username") or "")[:60]
        summary["username_attempt"] = username

    return summary


def log_activity(
    *,
    session: Optional[dict],
    action: str,
    endpoint: str = None,
    method: str = None,
    status_code: int = None,
    duration_ms: int = None,
    query_summary: dict = None,
    results_count: int = None,
    ip: str = None,
    user_agent: str = None,
) -> int:
    """Registra una acción. Devuelve el id del row insertado (0 si falla).

    Esta función es fire-and-forget — no lanza excepciones al caller.
    Si la DB está bloqueada, loguea el error a stderr pero no rompe la
    request principal.
    """
    try:
        init_db()
        user_id = session.get("user_id") if session else None
        username = session.get("username") if session else None
        qjson = json.dumps(_sanitize(query_summary or {}), ensure_ascii=False)
        ua = (user_agent or "")[:200]
        with _LOCK, _conn() as c:
            cur = c.execute(
                """INSERT INTO activity_log
                   (user_id, username, action, endpoint, method, status_code,
                    duration_ms, query_summary, results_count, ip, user_agent)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, username, action, endpoint, method, status_code,
                 duration_ms, qjson, results_count, ip, ua),
            )
            c.commit()
            return cur.lastrowid or 0
    except Exception as e:
        import sys
        print(f"[audit] ERROR logging {action} for {username}: {e}", file=sys.stderr)
        return 0


def get_activity(
    *,
    user_id: int = None,
    username: str = None,
    action: str = None,
    endpoint_like: str = None,
    since: float = None,  # epoch seconds
    until: float = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Lee el activity log con filtros. Devuelve {rows, total, limit, offset}.

    `total` es el conteo total SIN limit/offset (útil para paginación).
    """
    init_db()
    where_parts = []
    params: list = []
    if user_id is not None:
        where_parts.append("user_id = ?")
        params.append(user_id)
    if username:
        where_parts.append("username = ?")
        params.append(username)
    if action:
        where_parts.append("action = ?")
        params.append(action)
    if endpoint_like:
        where_parts.append("endpoint LIKE ?")
        params.append(f"%{endpoint_like}%")
    if since is not None:
        where_parts.append("created_at >= ?")
        params.append(since)
    if until is not None:
        where_parts.append("created_at <= ?")
        params.append(until)
    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    with _LOCK, _conn() as c:
        total = c.execute(
            f"SELECT COUNT(*) FROM activity_log {where_sql}", params
        ).fetchone()[0]
        cur = c.execute(
            f"""SELECT * FROM activity_log {where_sql}
                ORDER BY id DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        )
        rows = [dict(r) for r in cur.fetchall()]

    # parse query_summary JSON
    for r in rows:
        if r.get("query_summary"):
            try:
                r["query_summary"] = json.loads(r["query_summary"])
            except Exception:
                pass

    return {"rows": rows, "total": total, "limit": limit, "offset": offset}


def get_user_stats(days: int = 30) -> list:
    """Estadísticas de uso por usuario en los últimos N días.

    Returns: [{username, total, last_seen, by_action: {...}}, ...]
    """
    init_db()
    since = time.time() - days * 86400
    with _LOCK, _conn() as c:
        # total + last_seen por usuario
        rows = c.execute("""
            SELECT username,
                   COUNT(*) as total,
                   MAX(created_at) as last_seen,
                   user_id
            FROM activity_log
            WHERE created_at >= ? AND username IS NOT NULL
            GROUP BY username
            ORDER BY total DESC
        """, (since,)).fetchall()
        # desglose por acción
        by_action_rows = c.execute("""
            SELECT username, action, COUNT(*) as cnt
            FROM activity_log
            WHERE created_at >= ? AND username IS NOT NULL
            GROUP BY username, action
        """, (since,)).fetchall()

    by_action_map: dict = {}
    for r in by_action_rows:
        by_action_map.setdefault(r["username"], {})[r["action"]] = r["cnt"]

    result = []
    for r in rows:
        result.append({
            "username": r["username"],
            "user_id": r["user_id"],
            "total": r["total"],
            "last_seen": r["last_seen"],
            "by_action": by_action_map.get(r["username"], {}),
        })
    return result


def purge_old_activity(days: int = None) -> int:
    """Borra logs con más de N días. Devuelve el número de rows borrados."""
    days = days or DEFAULT_RETENTION_DAYS
    cutoff = time.time() - days * 86400
    init_db()
    with _LOCK, _conn() as c:
        cur = c.execute("DELETE FROM activity_log WHERE created_at < ?", (cutoff,))
        c.commit()
        return cur.rowcount


def purge_to_max_rows(max_rows: int = None) -> int:
    """Si la tabla tiene más de max_rows, borra las más antiguas."""
    max_rows = max_rows or DEFAULT_MAX_ROWS
    init_db()
    with _LOCK, _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0]
        if total <= max_rows:
            return 0
        # borra las (total - max_rows) más antiguas
        to_delete = total - max_rows
        cur = c.execute("""
            DELETE FROM activity_log WHERE id IN (
                SELECT id FROM activity_log ORDER BY id ASC LIMIT ?
            )
        """, (to_delete,))
        c.commit()
        return cur.rowcount


# === init al importar ===
init_db()
