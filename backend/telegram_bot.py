#!/usr/bin/env python3
"""
Bot de Telegram para Cuarto de Paz Search.
Consulta el padrón INE vía la API local (http://127.0.0.1:8765).

Comandos:
  /start        — bienvenida
  /curp XXXX    — buscar por CURP (datos completos: padrón + RFC + CheckID + Tlaloc)
  /buscar NOMBRE — buscar por nombre/apellido en el padrón
  /help         — ayuda

Autenticación: whitelist de Telegram user IDs (TG_WHITELIST en .env).
El bot usa un token de sesión interno para autenticarse contra la API.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import httpx
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

# ── Config ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.resolve()
ENV_PATH = ROOT / ".env"

# Cargar .env
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

TG_TOKEN = os.getenv("TG_TOKEN", "").strip()
TG_WHITELIST_RAW = os.getenv("TG_WHITELIST", "").strip()
API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8765").strip().rstrip("/")

# Parsear whitelist: "123456,789012" → {123456, 789012}
TG_WHITELIST: set[int] = set()
if TG_WHITELIST_RAW:
    for uid in TG_WHITELIST_RAW.split(","):
        try:
            TG_WHITELIST.add(int(uid.strip()))
        except ValueError:
            pass

# ── Cliente HTTP compartido ─────────────────────────────────────────────
http = httpx.AsyncClient(timeout=30.0)

# Token de sesión interno (se obtiene al iniciar)
SESSION_TOKEN: Optional[str] = None


async def get_session_token() -> str:
    """Obtiene un token de sesión válido contra la API."""
    global SESSION_TOKEN
    # Intentar login con password (admin / admin123 por defecto)
    try:
        r = await http.post(
            f"{API_BASE}/api/auth/login/password",
            json={"username": "admin", "password": "admin123"},
        )
        if r.status_code == 200:
            data = r.json()
            SESSION_TOKEN = data.get("token", "")
            return SESSION_TOKEN
    except Exception:
        pass

    # Fallback: intentar con token existente
    if SESSION_TOKEN:
        try:
            r = await http.get(
                f"{API_BASE}/api/auth/me",
                headers={"Authorization": f"Bearer {SESSION_TOKEN}"},
            )
            if r.status_code == 200 and r.json().get("ok"):
                return SESSION_TOKEN
        except Exception:
            pass

    return ""


async def api_get(path: str, params: dict = None) -> dict:
    """GET a la API con token de sesión."""
    token = SESSION_TOKEN or await get_session_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = await http.get(f"{API_BASE}{path}", params=params, headers=headers)
    return r.json()


async def api_post(path: str, body: dict) -> dict:
    """POST a la API con token de sesión."""
    token = SESSION_TOKEN or await get_session_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"} if token else {"Content-Type": "application/json"}
    r = await http.post(f"{API_BASE}{path}", json=body, headers=headers)
    return r.json()


# ── Auth middleware ──────────────────────────────────────────────────────

def whitelist_only(func):
    """Decorador: solo responde a usuarios en TG_WHITELIST."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user:
            return
        if TG_WHITELIST and user.id not in TG_WHITELIST:
            await update.message.reply_text(
                "⛔ No estás autorizado para usar este bot.\n"
                f"Tu ID: `{user.id}`\n\n"
                "Pide al administrador que agregue tu ID a TG_WHITELIST.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        return await func(update, context)
    return wrapper


# ── Comandos ─────────────────────────────────────────────────────────────

@whitelist_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mensaje de bienvenida."""
    await update.message.reply_text(
        "🔍 *Cuarto de Paz Search — Bot*\n\n"
        "Consulta el padrón INE desde Telegram.\n\n"
        "*Comandos:*\n"
        "`/curp XXXX` — Datos completos de un CURP\n"
        "`/buscar NOMBRE` — Buscar por nombre/apellido\n"
        "`/help` — Esta ayuda\n\n"
        "_Solo usuarios autorizados._",
        parse_mode=ParseMode.MARKDOWN,
    )


@whitelist_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ayuda."""
    await update.message.reply_text(
        "🔍 *Comandos disponibles*\n\n"
        "`/curp XXXX000000XXXXXX00`\n"
        "Busca un CURP exacto y devuelve:\n"
        "• Datos del padrón (nombre, dirección, RFC)\n"
        "• Validación Tlaloc (RENAPO + SAT)\n"
        "• CheckID (datos fiscales/laborales)\n\n"
        "`/buscar JUAN PEREZ`\n"
        "Busca por nombre/apellido (máx 10 resultados)\n\n"
        "`/start` — Inicio\n"
        "`/help` — Esta ayuda",
        parse_mode=ParseMode.MARKDOWN,
    )


@whitelist_only
async def cmd_curp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Buscar por CURP: /curp XXXX000000XXXXXX00"""
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ Uso: `/curp XXXX000000XXXXXX00`\n"
            "Ejemplo: `/curp AABB000101HDFXXX00`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    curp = args[0].strip().upper()
    if len(curp) != 18:
        await update.message.reply_text(
            f"❌ CURP inválido: `{html.escape(curp)}` ({len(curp)} caracteres, debe tener 18).",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    msg = await update.message.reply_text(f"⏳ Buscando `{html.escape(curp)}`...", parse_mode=ParseMode.MARKDOWN)

    try:
        data = await api_get(f"/api/sujeto", {"curp": curp})
    except Exception as e:
        await msg.edit_text(f"❌ Error de conexión: {html.escape(str(e))}")
        return

    if data.get("error"):
        await msg.edit_text(f"❌ {html.escape(data['error'])}")
        return

    sujeto = data.get("sujeto", {})
    rfc_info = data.get("rfc", {})
    checkid = data.get("checkid", {})
    tlaloc_curp = data.get("tlaloc_curp", {})
    tlaloc_rfc = data.get("tlaloc_rfc", {})

    # Construir respuesta
    nombre = sujeto.get("nombre_completo", "—")
    rfc = sujeto.get("rfc", "—")
    fecnac = sujeto.get("fecnac", "—")
    sexo = sujeto.get("sexo", "—")

    # Dirección
    calle = sujeto.get("calle", "") or ""
    ext = str(sujeto.get("ext", "") or "")
    interior = str(sujeto.get("interior", "") or "")
    colonia = sujeto.get("colonia", "") or ""
    cp = str(sujeto.get("cp", "") or "").strip()
    estado = sujeto.get("estado_nombre", "") or ""
    municipio = sujeto.get("municipio_nombre", "") or ""

    dir_parts = []
    if calle:
        num = ext or "S/N"
        dir_parts.append(f"{calle} {num}")
        if interior:
            dir_parts.append(f"Int. {interior}")
    if colonia:
        dir_parts.append(colonia)
    if municipio:
        dir_parts.append(municipio)
    if estado:
        dir_parts.append(estado)
    if cp and cp != "00000":
        dir_parts.append(f"CP {cp}")
    direccion = ", ".join(dir_parts) if dir_parts else "—"

    # RFC info
    rfc_source = rfc_info.get("source", "—")
    rfc_match = rfc_info.get("match")
    rfc_match_str = "✓ coincide" if rfc_match is True else ("⚠ no coincide" if rfc_match is False else "—")

    # Tlaloc
    tlaloc_ok = "✓ válido" if tlaloc_curp.get("valid") else ("✗ no encontrado" if tlaloc_curp else "—")
    tlaloc_rfc_ok = "✓ válido en SAT" if tlaloc_rfc.get("valid") else ("✗ no encontrado" if tlaloc_rfc else "—")

    # CheckID
    checkid_ok = "✓ éxito" if checkid.get("exitoso") else ("⚠ falló" if checkid else "—")
    checkid_nss = checkid.get("nss", "—") or "—"
    checkid_regimen = checkid.get("regimen_fiscal", "—") or "—"
    checkid_69 = "⚠ Sí" if checkid.get("estado_69_69b", {}).get("con_problema") else ("✓ No" if checkid.get("estado_69_69b") else "—")

    text = (
        f"🪪 *{html.escape(nombre)}*\n"
        f"`{html.escape(curp)}`\n\n"
        f"*RFC:* `{html.escape(rfc)}`\n"
        f"*Fuente RFC:* {html.escape(rfc_source)}\n"
        f"*Match local vs SAT:* {rfc_match_str}\n\n"
        f"*Nacimiento:* {html.escape(fecnac)} · {html.escape(sexo)}\n\n"
        f"📍 *Dirección:*\n{html.escape(direccion)}\n\n"
        f"─── *Validaciones* ───\n"
        f"🛡️ *Tlaloc CURP:* {tlaloc_ok}\n"
        f"🏛️ *Tlaloc RFC (SAT):* {tlaloc_rfc_ok}\n"
        f"💼 *CheckID:* {checkid_ok}\n"
        f"   NSS: `{html.escape(checkid_nss)}`\n"
        f"   Régimen: {html.escape(checkid_regimen)}\n"
        f"   69/69B: {checkid_69}\n\n"
        f"⏱ {data.get('duracion_seg', '?')}s"
    )

    # Truncar si es muy largo (Telegram limita a 4096 chars)
    if len(text) > 4000:
        text = text[:3900] + "\n\n… (truncado)"

    await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)


@whitelist_only
async def cmd_buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Buscar por nombre: /buscar JUAN PEREZ"""
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ Uso: `/buscar NOMBRE APELLIDO`\n"
            "Ejemplo: `/buscar JUAN PEREZ`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    query = " ".join(args).strip().upper()
    if len(query) < 3:
        await update.message.reply_text("❌ La búsqueda debe tener al menos 3 caracteres.")
        return

    msg = await update.message.reply_text(f"⏳ Buscando `{html.escape(query)}`...", parse_mode=ParseMode.MARKDOWN)

    # Intentar buscar como nombre + paterno
    partes = query.split()
    nombre = partes[0] if len(partes) >= 1 else ""
    paterno = partes[1] if len(partes) >= 2 else ""

    try:
        # Buscar por nombre ILIKE
        where_parts = []
        params = []
        if nombre:
            where_parts.append("nombre ILIKE ?")
            params.append(f"%{nombre}%")
        if paterno:
            where_parts.append("paterno ILIKE ?")
            params.append(f"%{paterno}%")
        if not where_parts:
            where_parts.append("nombre ILIKE ?")
            params.append(f"%{query}%")

        where = "WHERE " + " AND ".join(where_parts)
        data = await api_post("/api/search", {
            "where": where,
            "params": params,
            "limit": 10,
            "offset": 0,
            "order_by": "id",
        })
    except Exception as e:
        await msg.edit_text(f"❌ Error de conexión: {html.escape(str(e))}")
        return

    if data.get("error"):
        await msg.edit_text(f"❌ {html.escape(data['error'])}")
        return

    rows = data.get("rows", [])
    total = data.get("total", 0)

    if not rows:
        await msg.edit_text(f"🔍 Sin resultados para `{html.escape(query)}`.\n\nPrueba con menos términos o verifica la ortografía.", parse_mode=ParseMode.MARKDOWN)
        return

    lines = [f"🔍 *{total} resultados* para `{html.escape(query)}`\n"]
    for i, row in enumerate(rows[:10], 1):
        nombre_full = f"{row.get('nombre','')} {row.get('paterno','')} {row.get('materno','')}".strip()
        curp = row.get("curp", "—")
        fecnac = row.get("fecnac", "—")
        estado_num = row.get("e", "")
        # Mapeo rápido de estados
        estados_map = {
            1: "AGS", 2: "BC", 3: "BCS", 4: "CAMP", 5: "COAH", 6: "COL",
            7: "CHIS", 8: "CHIH", 9: "CDMX", 10: "DGO", 11: "GTO", 12: "GRO",
            13: "HGO", 14: "JAL", 15: "MEX", 16: "MICH", 17: "MOR", 18: "NAY",
            19: "NL", 20: "OAX", 21: "PUE", 22: "QRO", 23: "QR", 24: "SLP",
            25: "SIN", 26: "SON", 27: "TAB", 28: "TAMPS", 29: "TLAX",
            30: "VER", 31: "YUC", 32: "ZAC",
        }
        estado = estados_map.get(estado_num, str(estado_num))
        lines.append(
            f"*{i}.* {html.escape(nombre_full)}\n"
            f"   `{html.escape(curp)}` · {html.escape(fecnac)} · {estado}"
        )

    if total > 10:
        lines.append(f"\n_… y {total - 10} más. Afina la búsqueda._")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3900] + "\n\n… (truncado)"

    await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)


# ── Main ─────────────────────────────────────────────────────────────────

async def main():
    if not TG_TOKEN:
        print("❌ TG_TOKEN no configurado en .env", file=sys.stderr)
        sys.exit(1)

    if not TG_WHITELIST:
        print("⚠️  TG_WHITELIST vacío — cualquier usuario podrá usar el bot.", file=sys.stderr)

    print(f"🤖 Iniciando bot de Telegram...")
    print(f"   API: {API_BASE}")
    print(f"   Whitelist: {len(TG_WHITELIST)} usuarios")

    # Obtener token de sesión
    token = await get_session_token()
    if token:
        print(f"   Sesión API: ✓")
    else:
        print(f"   Sesión API: ⚠ no se pudo obtener (el bot usará login en cada request)")

    app = Application.builder().token(TG_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("curp", cmd_curp))
    app.add_handler(CommandHandler("sujeto", cmd_curp))  # alias
    app.add_handler(CommandHandler("buscar", cmd_buscar))

    print("   Polling iniciado...")
    await app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    asyncio.run(main())
