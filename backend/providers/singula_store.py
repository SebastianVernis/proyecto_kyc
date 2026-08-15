#!/usr/bin/env python3
"""singula_store.py — Store LOCAL para datos que la API de Singula no persiste.

La API de Singula sólo persiste email/phone en datameta (probablemente por
diseño del sandbox). Este módulo guarda en SQLite local:
  - enrichment (padrón + checkid + tlaloc + sepomex + apify)
  - validations_cache (resultados de check_judicial, email_lookup, etc.)
"""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional


_LOCK = threading.Lock()
_DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "singula_cache.db"
)


def _get_conn(db_path: str = None) -> sqlite3.Connection:
    path = db_path or os.environ.get("SINGULA_STORE_DB") or _DEFAULT_DB
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def init_db(db_path: str = None) -> None:
    """Crea las tablas si no existen."""
    with _LOCK:
        con = _get_conn(db_path)
        try:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS customers (
                    customer_id TEXT PRIMARY KEY,
                    curp TEXT,
                    custom_id TEXT,
                    datameta_enrichment TEXT,
                    validations_cache TEXT,
                    created_at REAL,
                    updated_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_customers_curp ON customers(curp);
            """)
            con.commit()
        finally:
            con.close()


def save_enrichment(customer_id: str, curp: str, enrichment: dict,
                    custom_id: str = None, db_path: str = None) -> None:
    """Guarda el enrichment (padrón + checkid + tlaloc + sepomex + apify)."""
    init_db(db_path)
    with _LOCK:
        con = _get_conn(db_path)
        try:
            now = time.time()
            con.execute("""
                INSERT INTO customers (customer_id, curp, custom_id,
                    datameta_enrichment, validations_cache, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(customer_id) DO UPDATE SET
                    datameta_enrichment = excluded.datameta_enrichment,
                    updated_at = excluded.updated_at
            """, (
                customer_id, curp, custom_id,
                json.dumps(enrichment, default=str),
                None,  # no tocar validations_cache aquí
                now, now,
            ))
            con.commit()
        finally:
            con.close()


def get_enrichment(customer_id: str, db_path: str = None) -> Optional[dict]:
    """Lee el enrichment de un customer. None si no existe."""
    init_db(db_path)
    with _LOCK:
        con = _get_conn(db_path)
        try:
            row = con.execute(
                "SELECT datameta_enrichment FROM customers WHERE customer_id = ?",
                (customer_id,),
            ).fetchone()
            if row and row["datameta_enrichment"]:
                return json.loads(row["datameta_enrichment"])
            return None
        finally:
            con.close()


def save_validation(customer_id: str, validation_key: str,
                    result: dict, db_path: str = None) -> None:
    """Guarda el resultado de UNA validación (judicial/email_lookup/...)."""
    init_db(db_path)
    with _LOCK:
        con = _get_conn(db_path)
        try:
            now = time.time()
            row = con.execute(
                "SELECT validations_cache FROM customers WHERE customer_id = ?",
                (customer_id,),
            ).fetchone()
            cache = {}
            if row and row["validations_cache"]:
                try:
                    cache = json.loads(row["validations_cache"])
                except Exception:
                    cache = {}
            # entry con timestamp
            entry = dict(result) if isinstance(result, dict) else {"value": result}
            entry["cached_at"] = now
            cache[validation_key] = entry
            con.execute("""
                INSERT INTO customers (customer_id, validations_cache, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(customer_id) DO UPDATE SET
                    validations_cache = excluded.validations_cache,
                    updated_at = excluded.updated_at
            """, (customer_id, json.dumps(cache, default=str), now))
            con.commit()
        finally:
            con.close()


def get_validations(customer_id: str, db_path: str = None) -> dict:
    """Lee todas las validaciones cacheadas de un customer."""
    init_db(db_path)
    with _LOCK:
        con = _get_conn(db_path)
        try:
            row = con.execute(
                "SELECT validations_cache FROM customers WHERE customer_id = ?",
                (customer_id,),
            ).fetchone()
            if row and row["validations_cache"]:
                return json.loads(row["validations_cache"])
            return {}
        finally:
            con.close()


def get_all_for_curp(curp: str, db_path: str = None) -> Optional[dict]:
    """Lee enrichment + validations_cache por CURP."""
    init_db(db_path)
    with _LOCK:
        con = _get_conn(db_path)
        try:
            row = con.execute(
                "SELECT * FROM customers WHERE curp = ? ORDER BY updated_at DESC LIMIT 1",
                (curp,),
            ).fetchone()
            if not row:
                return None
            return {
                "customer_id": row["customer_id"],
                "curp": row["curp"],
                "custom_id": row["custom_id"],
                "enrichment": json.loads(row["datameta_enrichment"]) if row["datameta_enrichment"] else None,
                "validations_cache": json.loads(row["validations_cache"]) if row["validations_cache"] else {},
            }
        finally:
            con.close()
