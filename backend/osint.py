#!/usr/bin/env python3
"""OSINT — búsqueda automatizada de personas en bases públicas.

Recopila información de múltiples fuentes abiertas para una persona a
partir de sus identificadores básicos (CURP, RFC, nombre, fecha de
nacimiento). Guarda los resultados en DuckDB y opcionalmente en JSON.

Fuentes consultadas (todas accesibles sin API key o con clave pública):
  - Padrón INE 2018 local (DuckDB, ya cargado)
  - SEP / latamverify.com — cédulas profesionales
  - Dialnet — producción académica
  - Redalyc — revistas académicas
  - EmailSherlock — email lookup
  - Breach House — filtraciones del dominio del email
  - LinkedIn (vía search) — perfil profesional
  - HaveIBeenRansom — index público de filtraciones
  - GitHub — perfiles de developers
  - Instagram — búsqueda pública
  - Facebook — búsqueda pública
  - Twitter/X — búsqueda pública
  - ScamAdviser — reputación de sitios web

Uso:
  python3 osint.py --curp AISR750901MDFVLS01 --db ine.duckdb --json out.json
  python3 osint.py --nombre "SAMUEL" --paterno "MOLINA" --materno "RAMIREZ"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

# opcional: duckdb
try:
    import duckdb
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False

# opcional: openpyxl
try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

# opcional: Apify
try:
    from apify_broker import ApifyBroker
    HAS_APIFY_BROKER = True
except ImportError:
    HAS_APIFY_BROKER = False

ROOT = Path(__file__).parent.resolve()


# ==== cliente HTTP simple =================================================


class HTTP:
    """Cliente HTTP simple con retries y timeout."""

    def __init__(self, timeout: int = 30, retries: int = 2, user_agent: str | None = None):
        self.timeout = timeout
        self.retries = retries
        self.ua = user_agent or (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

    def get(self, url: str, params: dict | None = None) -> tuple[int, bytes, dict]:
        if params:
            url = url + "?" + urllib.parse.urlencode(params)
        last_err = None
        for i in range(self.retries + 1):
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": self.ua,
                    "Accept": "text/html,application/xhtml+xml,application/json",
                    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
                })
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    body = r.read()
                    return r.status, body, dict(r.headers)
            except Exception as e:
                last_err = e
                time.sleep(0.5 * (i + 1))
        return 0, b"", {"error": str(last_err)}


# ==== entidades ===========================================================


def get_estados() -> dict[int, str]:
    return {
        1: "Aguascalientes", 2: "Baja California", 3: "Baja California Sur",
        4: "Campeche", 5: "Coahuila", 6: "Colima", 7: "Chiapas", 8: "Chihuahua",
        9: "Ciudad de México", 10: "Durango", 11: "Guanajuato", 12: "Guerrero",
        13: "Hidalgo", 14: "Jalisco", 15: "México", 16: "Michoacán", 17: "Morelos",
        18: "Nayarit", 19: "Nuevo León", 20: "Oaxaca", 21: "Puebla", 22: "Querétaro",
        23: "Quintana Roo", 24: "San Luis Potosí", 25: "Sinaloa", 26: "Sonora",
        27: "Tabasco", 28: "Tamaulipas", 29: "Tlaxcala", 30: "Veracruz",
        31: "Yucatán", 32: "Zacatecas",
    }


# ==== busqueda en padrón local ============================================


def query_padron(db_path: str, curp: str | None = None, paterno: str | None = None,
                 materno: str | None = None, nombre: str | None = None,
                 fecnac: str | None = None, limit: int = 10) -> list[dict]:
    """Busca en el padrón local. Devuelve lista de dicts con todos los campos."""
    if not HAS_DUCKDB or not os.path.exists(db_path):
        return []
    try:
        con = duckdb.connect(db_path, read_only=True)
    except Exception as e:
        print(f"  [padron] error abriendo DB: {e}", file=sys.stderr)
        return []
    parts, params = [], []
    if curp:
        parts.append("curp = ?"); params.append(curp.upper())
    if paterno:
        parts.append("paterno ILIKE ?"); params.append(f"%{paterno.upper()}%")
    if materno:
        parts.append("materno ILIKE ?"); params.append(f"%{materno.upper()}%")
    if nombre:
        parts.append("nombre ILIKE ?"); params.append(f"%{nombre.upper()}%")
    if fecnac:
        parts.append("fecnac = ?"); params.append(fecnac)
    where = "WHERE " + " AND ".join(parts) if parts else ""
    sql = f"""
        SELECT id, cve, curp, nombre, paterno, materno, fecnac, sexo,
               calle, "int", "ext", colonia, cp, e, d, m, s, l, mza,
               consec, cred, folio, nac, origen
        FROM padron {where}
        LIMIT {int(limit)}
    """
    try:
        rows = con.execute(sql, params).fetchall()
        cols = [d[0] for d in con.description]
        con.close()
        out = []
        for r in rows:
            d = {}
            for k, v in zip(cols, r):
                if hasattr(v, "isoformat"):
                    v = v.isoformat()
                elif hasattr(v, "__float__") and not isinstance(v, (int, float, bool, str)):
                    try:
                        v = float(v)
                    except Exception:
                        v = str(v)
                d[k] = v
            out.append(d)
        return out
    except Exception as e:
        print(f"  [padron] error query: {e}", file=sys.stderr)
        con.close()
        return []


def exportar_padron_a_csv(rows: list[dict], path: str) -> int:
    if not rows:
        return 0
    import csv
    cols = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return len(rows)


# ==== busqueda en SEP / cédulas profesionales =============================


def search_sep_cedulas(nombre: str, paterno: str, materno: str, http: HTTP) -> list[dict]:
    """Busca cédulas profesionales en latamverify.com."""
    out = []
    base = "https://mx.latamverify.com/perfil/mx/pro-2/"
    # intentar URL slug-based
    slug = f"{nombre.lower()}-{paterno.lower()}-{materno.lower()}"
    # hay IDs variables, intentar directamente
    queries = [
        f"{nombre} {paterno} {materno}",
    ]
    for q in queries:
        # búsqueda con curl: latamverify no expone API, intentar
        # scraping por pattern: "Samuel-Molina-Ramirez-8482313.html"
        # usaremos duckduckgo para localizar
        pass
    return out


def search_google(query: str, http: HTTP, max_results: int = 10) -> list[dict]:
    """Búsqueda ligera vía DuckDuckGo HTML (no requiere API key)."""
    url = "https://html.duckduckgo.com/html/"
    status, body, _ = http.post(url, data={"q": query}) if hasattr(http, "post") else (0, b"", {})
    # fallback: GET con parámetros
    status, body, _ = http.get(url + "?" + urllib.parse.urlencode({"q": query}))
    if status != 200:
        return []
    html = body.decode("utf-8", errors="ignore")
    # extraer resultados: <a class="result__a" href="...">TITULO</a>
    results = []
    for m in re.finditer(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        html, re.DOTALL,
    ):
        url, title, snippet = m.group(1), m.group(2), m.group(3)
        # limpiar HTML
        title = re.sub(r"<[^>]+>", "", title).strip()
        snippet = re.sub(r"<[^>]+>", "", snippet).strip()
        # filtrar URLs propios de DDG
        if "duckduckgo" in url.lower():
            continue
        results.append({"url": url, "title": title, "snippet": snippet})
        if len(results) >= max_results:
            break
    return results


# Añadir método post a HTTP
def _post(self, url: str, data: dict | None = None) -> tuple[int, bytes, dict]:
    body = urllib.parse.urlencode(data or {}).encode("utf-8")
    last_err = None
    for i in range(self.retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers={
                "User-Agent": self.ua,
                "Accept": "text/html,application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            })
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, r.read(), dict(r.headers)
        except Exception as e:
            last_err = e
            time.sleep(0.5 * (i + 1))
    return 0, b"", {"error": str(last_err)}


HTTP.post = _post


# ==== EmailSherlock (email lookup) =========================================


def search_emailsherlock(email: str, http: HTTP) -> dict:
    """Consulta EmailSherlock para un email."""
    url = f"https://www.emailsherlock.com/email-reverse-search/{urllib.parse.quote(email)}/"
    status, body, _ = http.get(url)
    if status != 200:
        return {"fuente": "emailsherlock", "email": email, "status": status}
    html = body.decode("utf-8", errors="ignore")
    # extraer trust score
    trust_m = re.search(r"(\d+\.\d+)/10", html)
    trust = float(trust_m.group(1)) if trust_m else None
    # detectar blacklists: emailsherlock marca "0 listed" cuando está limpio
    blacklist_m = re.search(r"(\d+)\s+listed", html)
    listed_count = int(blacklist_m.group(1)) if blacklist_m else 0
    return {
        "fuente": "emailsherlock",
        "email": email,
        "status": status,
        "trust_score": trust,
        "blacklisted": listed_count > 0,
        "blacklist_count": listed_count,
        "url": url,
    }


# ==== Breach House (filtraciones del dominio) =============================


def search_breach_house(domain: str, http: HTTP) -> list[dict]:
    """Busca filtraciones que incluyan el dominio."""
    url = f"https://breach.house/breach/?q={urllib.parse.quote(domain)}"
    status, body, _ = http.get(url)
    if status != 200:
        return []
    html = body.decode("utf-8", errors="ignore")
    out = []
    for m in re.finditer(
        r'<a[^>]+href="(/breach/[^"]+)"[^>]*>(.*?)</a>.*?'
        r'<small[^>]*>(.*?)</small>',
        html, re.DOTALL,
    ):
        out.append({
            "fuente": "breach_house",
            "url_path": m.group(1),
            "titulo": re.sub(r"<[^>]+>", "", m.group(2)).strip(),
            "fecha": re.sub(r"<[^>]+>", "", m.group(3)).strip(),
            "dominio_buscado": domain,
        })
    return out


# ==== HaveIBeenRansom / HaveIBeenPwned (sin API) =========================


def search_haveibeenransom_pastebin(email: str, http: HTTP) -> list[dict]:
    """Busca el email en pastebin (alternativa pública a HIBP)."""
    url = "https://psbdmp.ws/api/v3/search/" + urllib.parse.quote(email)
    status, body, _ = http.get(url)
    if status != 200:
        return []
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        return []
    out = []
    for item in (data if isinstance(data, list) else data.get("data", [])):
        out.append({
            "fuente": "psbdmp",
            "email": email,
            "id": item.get("id"),
            "tags": item.get("tags", []),
            "emails_count": item.get("email_count"),
        })
    return out


# ==== LinkedIn (público, sin auth) ========================================


def search_linkedin_publico(query: str, http: HTTP, max_results: int = 10) -> list[dict]:
    """Búsqueda en Google site:linkedin.com (puede dar perfiles públicos)."""
    results = search_google(f'site:linkedin.com "{query}"', http, max_results)
    return [{"fuente": "linkedin", **r} for r in results]


# ==== GitHub (público, sin auth) =========================================


def search_github_publico(username: str, http: HTTP) -> dict | None:
    """Consulta la API pública de GitHub para un username."""
    url = f"https://api.github.com/users/{urllib.parse.quote(username)}"
    status, body, _ = http.get(url)
    if status != 200:
        return None
    try:
        d = json.loads(body.decode("utf-8"))
        return {
            "fuente": "github",
            "username": d.get("login"),
            "name": d.get("name"),
            "bio": d.get("bio"),
            "location": d.get("location"),
            "email": d.get("email"),
            "blog": d.get("blog"),
            "company": d.get("company"),
            "public_repos": d.get("public_repos"),
            "followers": d.get("followers"),
            "following": d.get("following"),
            "created_at": d.get("created_at"),
            "url": d.get("html_url"),
        }
    except Exception:
        return None


def search_github_variants(name: str, paterno: str, materno: str, http: HTTP) -> list[dict]:
    """Prueba variantes del username en GitHub."""
    candidates = [
        f"{name.lower()}{paterno.lower()}",
        f"{name.lower()}.{paterno.lower()}",
        f"{name.lower()}{materno.lower()}",
        f"{paterno.lower()}{name.lower()}",
        f"{name.lower()}{paterno.lower()[0]}",
        f"{paterno.lower()}{materno.lower()}",
    ]
    out = []
    for u in candidates:
        r = search_github_publico(u, http)
        if r:
            out.append(r)
    return out


# ==== ScamAdviser (reputación) ============================================


def search_scamadviser_domain(domain: str, http: HTTP) -> dict | None:
    url = f"https://www.scamadviser.com/check-website/{domain}"
    status, body, _ = http.get(url)
    if status != 200:
        return None
    html = body.decode("utf-8", errors="ignore")
    trust_m = re.search(r'trust score[^"]*"[^>]*>([\d.]+)', html, re.IGNORECASE)
    trust = float(trust_m.group(1)) if trust_m else None
    return {
        "fuente": "scamadviser",
        "dominio": domain,
        "trust_score": trust,
        "url": url,
        "status": status,
    }


# ==== Búsqueda en academic profiles ========================================


def search_dialnet(autor: str, http: HTTP) -> list[dict]:
    """Busca autor en Dialnet (repositorio académico hispano)."""
    nombre, _, _ = autor.partition(" ")
    url = f"https://dialnet.unirioja.es/buscar/documentos?querysDismax.DOCUMENTAL_TODO={urllib.parse.quote(autor)}"
    status, body, _ = http.get(url)
    if status != 200:
        return []
    html = body.decode("utf-8", errors="ignore")
    out = []
    # dialnet muestra resultados en <div class="titulo">...</div>
    for m in re.finditer(
        r'<a[^>]+href="(/servlet/articulo\?codigo=\d+)"[^>]*>(.*?)</a>',
        html, re.DOTALL,
    ):
        titulo = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        out.append({
            "fuente": "dialnet",
            "titulo": titulo,
            "url_path": m.group(1),
            "autor_buscado": autor,
        })
        if len(out) >= 10:
            break
    return out


# ==== Orquestador ==========================================================


class OSINTInvestigator:
    def __init__(self, db_path: str | None = None, verbose: bool = True,
                 use_apify: bool = False, apify_token: str | None = None):
        self.http = HTTP(timeout=20, retries=2)
        self.db_path = db_path
        self.verbose = verbose
        self.apify = None
        if use_apify and HAS_APIFY_BROKER:
            try:
                self.apify = ApifyBroker(apify_token or os.getenv("APIFY_TOKEN"))
                self.log("[*] Apify broker inicializado")
            except Exception as e:
                self.log(f"[!] Apify no disponible: {e}")
        self.results: dict[str, Any] = {
            "metadata": {
                "fecha_consulta": datetime.now().isoformat(),
                "user_agent": self.http.ua,
                "apify_enabled": self.apify is not None,
            },
            "sujeto": {},
            "padron": [],
            "cedula_profesional": [],
            "email_lookup": {},
            "brechas_filtradas": [],
            "psbdmp": [],
            "dominio_reputacion": {},
            "github": [],
            "linkedin": [],
            "google_results": [],
            "dialnet": [],
            "apify_results": {},
            "sintesis": {},
        }

    def log(self, msg: str):
        if self.verbose:
            print(msg, file=sys.stderr)

    def investigar(
        self,
        curp: str | None = None,
        nombre: str | None = None,
        paterno: str | None = None,
        materno: str | None = None,
        fecnac: str | None = None,
        email: str | None = None,
        telefono: str | None = None,
        rfc: str | None = None,
        output_json: str | None = None,
    ):
        self.log(f"[*] OSINT iniciando — {datetime.now().isoformat()}")
        self.results["sujeto"] = {
            "curp": curp, "nombre": nombre, "paterno": paterno,
            "materno": materno, "fecnac": fecnac, "email": email,
            "telefono": telefono, "rfc": rfc,
        }

        # 1) Padrón local
        if self.db_path and (curp or paterno or materno or nombre or fecnac):
            self.log("[*] Consultando padrón local...")
            self.results["padron"] = query_padron(
                self.db_path, curp=curp, paterno=paterno, materno=materno,
                nombre=nombre, fecnac=fecnac, limit=20,
            )
            self.log(f"    {len(self.results['padron'])} resultados")

        # 2) Email lookup
        if email:
            self.log(f"[*] Email lookup: {email}")
            self.results["email_lookup"] = search_emailsherlock(email, self.http)
            # extraer dominio y buscarlo en Breach House
            domain = email.split("@", 1)[-1] if "@" in email else ""
            if domain:
                self.log(f"[*] Breach House: dominio {domain}")
                self.results["brechas_filtradas"] = search_breach_house(domain, self.http)
                self.log(f"    {len(self.results['brechas_filtradas'])} entradas")
                self.results["dominio_reputacion"] = (
                    search_scamadviser_domain(domain, self.http) or {}
                )
            # buscar email en pastebin leaks
            self.log("[*] psbdmp (pastebin leaks)...")
            self.results["psbdmp"] = search_haveibeenransom_pastebin(email, self.http)
            self.log(f"    {len(self.results['psbdmp'])} hits")

        # 3) GitHub (con username variants)
        if nombre and paterno:
            self.log("[*] GitHub: probando variantes de username...")
            self.results["github"] = search_github_variants(
                nombre, paterno, materno or "", self.http
            )
            self.log(f"    {len(self.results['github'])} perfiles")

        # 4) LinkedIn (vía Google)
        if nombre and paterno and materno:
            q = f"{nombre} {paterno} {materno}"
            self.log(f"[*] LinkedIn search: {q}")
            self.results["linkedin"] = search_linkedin_publico(q, self.http)
            self.log(f"    {len(self.results['linkedin'])} resultados")

        # 5) Dialnet (perfil académico)
        if nombre and paterno and materno:
            autor = f"{paterno} {materno} {nombre}"
            self.log(f"[*] Dialnet: {autor}")
            self.results["dialnet"] = search_dialnet(autor, self.http)
            self.log(f"    {len(self.results['dialnet'])} publicaciones")

        # 6) Búsqueda general Google
        if nombre and paterno:
            queries = []
            if curp:
                queries.append(curp)
            if nombre and paterno and materno:
                queries.append(f'"{nombre} {paterno} {materno}"')
            if email:
                queries.append(f'"{email}"')
            for q in queries[:3]:
                self.log(f"[*] Google: {q}")
                res = search_google(q, self.http, max_results=5)
                self.results["google_results"].append({
                    "query": q, "resultados": res
                })
                self.log(f"    {len(res)} resultados")
                time.sleep(0.5)

        # 7) Apify: redes sociales comprehensivas (opcional)
        if self.apify and (nombre or email):
            self.log("[*] Apify: búsqueda comprehensiva en redes sociales...")
            try:
                apify_res = self.apify.full_osint(
                    nombre=nombre or "",
                    paterno=paterno or "",
                    materno=materno or "",
                    email=email,
                    telefono=telefono,
                    max_results=15,
                )
                self.results["apify_results"] = apify_res
                sint = apify_res.get("sintesis", {})
                self.log(f"    Apify: {sint.get('total_perfiles_byname', 0)} perfiles por nombre, "
                         f"{sint.get('total_perfiles_byusername', 0)} por username, "
                         f"{len(sint.get('redes_encontradas', []))} redes únicas")
            except Exception as e:
                self.log(f"    [!] Error Apify: {e}")
                self.results["apify_error"] = str(e)

        # 8) Síntesis
        self.results["sintesis"] = self.sintetizar()

        # 9) Guardar
        if output_json:
            def _default(o):
                if hasattr(o, "isoformat"):
                    return o.isoformat()
                return str(o)
            with open(output_json, "w", encoding="utf-8") as f:
                json.dump(self.results, f, ensure_ascii=False, indent=2, default=_default)
            self.log(f"\n[✓] Reporte guardado en {output_json}")
        else:
            def _default(o):
                if hasattr(o, "isoformat"):
                    return o.isoformat()
                return str(o)
            print(json.dumps(self.results, ensure_ascii=False, indent=2, default=_default))

    def sintetizar(self) -> dict:
        s = {
            "total_padron": len(self.results.get("padron") or []),
            "total_brechas": len(self.results.get("brechas_filtradas") or []),
            "total_pastebin_hits": len(self.results.get("psbdmp") or []),
            "total_github_perfiles": len(self.results.get("github") or []),
            "total_linkedin": len(self.results.get("linkedin") or []),
            "total_dialnet": len(self.results.get("dialnet") or []),
            "total_google": sum(len(r.get("resultados", []))
                                for r in (self.results.get("google_results") or [])),
            "alertas": [],
        }
        # Apify stats
        apify = self.results.get("apify_results") or {}
        if apify:
            sint_apify = apify.get("sintesis", {})
            s["total_apify_perfiles_byname"] = sint_apify.get("total_perfiles_byname", 0)
            s["total_apify_perfiles_byusername"] = sint_apify.get("total_perfiles_byusername", 0)
            s["total_redes_unicas_apify"] = len(sint_apify.get("redes_encontradas", []))
        if s["total_pastebin_hits"]:
            s["alertas"].append("Email aparece en pastebin leaks (psbdmp)")
        if s["total_brechas"]:
            s["alertas"].append(f"dominio con {s['total_brechas']} filtraciones conocidas")
        if self.results.get("dominio_reputacion", {}).get("trust_score") and self.results["dominio_reputacion"]["trust_score"] < 50:
            s["alertas"].append("dominio con trust score bajo")
        return s


# ==== CLI ==================================================================


def main():
    ap = argparse.ArgumentParser(description="OSINT automatizado de personas")
    ap.add_argument("--db", default=str(ROOT / "ine.duckdb"), help="Path a DuckDB del padrón")
    ap.add_argument("--curp", help="CURP")
    ap.add_argument("--rfc", help="RFC")
    ap.add_argument("--nombre", help="Nombre(s)")
    ap.add_argument("--paterno", help="Apellido paterno")
    ap.add_argument("--materno", help="Apellido materno")
    ap.add_argument("--fecnac", help="Fecha de nacimiento (YYYY-MM-DD)")
    ap.add_argument("--email", help="Email a investigar")
    ap.add_argument("--telefono", help="Número telefónico")
    ap.add_argument("--json", help="Guardar resultado en archivo JSON")
    ap.add_argument("--quiet", action="store_true", help="Menos logs")
    ap.add_argument("--apify", action="store_true", help="Activar búsqueda con Apify (redes sociales)")
    ap.add_argument("--apify-token", help="Token API de Apify (o env APIFY_TOKEN)")
    args = ap.parse_args()

    if not any([args.curp, args.nombre, args.paterno, args.email, args.rfc]):
        ap.error("Especifica al menos --curp, --nombre/--paterno o --email")

    inv = OSINTInvestigator(
        db_path=args.db,
        verbose=not args.quiet,
        use_apify=args.apify,
        apify_token=args.apify_token,
    )
    inv.investigar(
        curp=args.curp,
        nombre=args.nombre,
        paterno=args.paterno,
        materno=args.materno,
        fecnac=args.fecnac,
        email=args.email,
        telefono=args.telefono,
        rfc=args.rfc,
        output_json=args.json,
    )


if __name__ == "__main__":
    main()
