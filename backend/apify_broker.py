#!/usr/bin/env python3
"""apify_broker.py — broker para Apify con 14 actores especializados en OSINT.

Organizados por dominio de búsqueda:
  - EMAIL        → buscar redes por email + extraer email desde website
  - PHONE        → buscar email/phone por nombre (hunter-style)
  - NAME         → buscar username/profiles por nombre
  - USERNAME     → buscar perfiles dado un username
  - PROFILE      → enriquecimiento de perfil individual
  - WEBSITE      → extraer emails/phones/socials de website
  - SEARCH       → discovery por keyword (Instagram, Facebook, LinkedIn)
  - COMPANY      → enricher de empresa + empleados

Para correrlos todos en paralelo: ver método `full_dossier()`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

APIFY_BASE = "https://api.apify.com/v2"
DEFAULT_TIMEOUT = 180

# === catálogo completo de actores ===
ACTORES = {
    # ============== EMAIL ==============
    "instagram-search-users": {
        "id": "data-slayer~instagram-search-users",
        "description": "Instagram: keyword → matching accounts, con emails/phones/websites en modo enriched",
        "categoria": "email",
        "input_builder": lambda q, **k: {
            "query": q if isinstance(q, str) else q[0],
            "mode": k.get("mode", "enriched"),
            "maxItems": k.get("max_items", 20),
            "proxyConfiguration": {"useApifyProxy": True},
        },
    },
    "contact-info-scraper": {
        "id": "vdrmota~contact-info-scraper",
        "description": "De website → emails, phones, LinkedIn, Twitter, IG, FB, TikTok, etc.",
        "categoria": "email",
        "input_builder": lambda q, **k: {
            "startUrls": [{"url": u} for u in ([q] if isinstance(q, str) else q)],
            "maxDepth": k.get("max_depth", 2),
            "maxRequestsPerStartUrl": k.get("max_pages", 15),
            "onlyEmails": k.get("only_emails", False),
            "proxyConfiguration": {"useApifyProxy": True},
        },
    },
    "instagram-search-scraper": {
        "id": "apify~instagram-search-scraper",
        "description": "Instagram: keyword → profile URLs + emails (con enhanceUserSearchWithFacebookPage)",
        "categoria": "email",
        "input_builder": lambda q, **k: {
            "search": q if isinstance(q, str) else q[0],
            "searchType": k.get("search_type", "user"),
            "searchLimit": k.get("limit", 10),
            "enhanceUserSearchWithFacebookPage": True,
        },
    },
    # ============== PHONE ==============
    "facebook-search": {
        "id": "apify~facebook-search-scraper",
        "description": "Facebook: keyword → perfiles con datos públicos",
        "categoria": "phone",
        "input_builder": lambda q, **k: {
            "search": q if isinstance(q, str) else q[0],
            "maxItems": k.get("limit", 10),
        },
    },
    "facebook-profile-url-finder": {
        "id": "unlimitedleadtestinbox~facebook-profile-url-finder",
        "description": "FB: nombre+empresa → URL del perfil",
        "categoria": "phone",
        "input_builder": lambda q, **k: {
            "firstName": k.get("first_name", q if isinstance(q, str) else q[0]),
            "lastName": k.get("last_name", k.get("lastname", "")),
            "company": k.get("company", ""),
        },
    },
    "facebook-profile-scraper": {
        "id": "apivault_labs~facebook-profile-scraper",
        "description": "FB profile: emails, phones, websites, follower count",
        "categoria": "phone",
        "input_builder": lambda q, **k: {
            "profileUrls": [{"url": u} for u in ([q] if isinstance(q, str) else q)],
            "maxConcurrency": 3,
            "timeout": 45,
            "useResidentialProxy": True,
        },
    },
    "phone-lookup-singula": {
        "id": None,  # se maneja internamente con SingulaClient
        "description": "Phone lookup: carrier, line_type, region, valid",
        "categoria": "phone",
        "internal": True,
    },
    # ============== NAME ==============
    "instagram-user-search": {
        "id": "maximedupre~instagram-user-search-scraper",
        "description": "Instagram: keyword → matching accounts con publicEmail + publicPhone",
        "categoria": "name",
        "input_builder": lambda q, **k: {
            "keywords": [q] if isinstance(q, str) else q,
            "maxItems": k.get("max_items", 25),
        },
    },
    "facebook-user-search": {
        "id": "lexis-solutions~facebook-user-search-scraper",
        "description": "FB: query word → profiles con name/profileImage/work/education",
        "categoria": "name",
        "input_builder": lambda q, **k: {
            "query": q if isinstance(q, str) else q[0],
            "maxItems": k.get("max_items", 10),
        },
    },
    "linkedin-search-by-name": {
        "id": "harvestapi~linkedin-profile-search-by-name",
        "description": "LinkedIn: name + location + company → perfiles con email",
        "categoria": "name",
        "input_builder": lambda q, **k: {
            "firstName": k.get("first_name", q if isinstance(q, str) else q[0]),
            "lastName": k.get("last_name", k.get("lastname", "")),
            "maxItems": k.get("max_items", 10),
        },
    },
    "linkedin-real-time-contact": {
        "id": "enrich-crm~linkedin-real-time-contact-scraper",
        "description": "LinkedIn: enrich 1 contacto con 250+ campos (email/phone/job/company)",
        "categoria": "name",
        "input_builder": lambda q, **k: {
            "firstName": k.get("first_name", q if isinstance(q, str) else q[0]),
            "lastName": k.get("last_name", ""),
            "companyName": k.get("company", ""),
        },
    },
    # ============== USERNAME ==============
    "tiktok-profile": {
        "id": "clockworks~tiktok-profile-scraper",
        "description": "TikTok: username → perfil completo + videos + métricas",
        "categoria": "username",
        "input_builder": lambda q, **k: {
            "profiles": [q] if isinstance(q, str) else q,
        },
    },
    "instagram-profile": {
        "id": "apify~instagram-profile-scraper",
        "description": "Instagram: username → perfil completo + posts + métricas",
        "categoria": "username",
        "input_builder": lambda q, **k: {
            "usernames": [q] if isinstance(q, str) else q,
        },
    },
    "facebook-profile-by-url": {
        "id": "vdrmota~contact-info-scraper",
        "description": "FB: username URL → emails/phones (alternativa)",
        "categoria": "username",
        "input_builder": lambda q, **k: {
            "startUrls": [{"url": u} for u in ([q] if isinstance(q, str) else q)],
        },
    },
    # ============== WEBSITE ==============
    "website-emails": {
        "id": "code-node-tools~website-email-socials-phone-number-scraper-lookup",
        "description": "Website: emails, phones, socials con obfuscation handling",
        "categoria": "website",
        "input_builder": lambda q, **k: {
            "startUrls": [{"url": u} for u in ([q] if isinstance(q, str) else q)],
            "maxRequestsPerCrawl": k.get("max_pages", 50),
            "handleObfuscation": True,
            "extractEmails": True,
            "extractPhones": True,
            "extractSocials": True,
        },
    },
    "google-search": {
        "id": "apify~google-search-scraper",
        "description": "Google: keyword → resultados con email/phone/linkedin si aparecen",
        "categoria": "name",
        "input_builder": lambda q, **k: {
            "queries": [q] if isinstance(q, str) else q,
            "maxPagesPerQuery": 1,
            "resultsPerPage": k.get("limit", 10),
        },
    },
    "youtube-channel": {
        "id": "streamers~youtube-channel-scraper",
        "description": "YouTube: @username → info del canal + videos",
        "categoria": "username",
        "input_builder": lambda q, **k: {
            "startUrls": [{"url": f"https://www.youtube.com/@{q}"}],
        },
    },
    # ============== SOCIAL MEDIA FINDER ==============
    "social-media-finder": {
        "id": "tri_angle~social-media-finder",
        "description": "Busca perfiles en 13 redes sociales por nombre/nickname (TikTok, Twitch, LinkedIn, Medium, etc.)",
        "categoria": "name",
        "input_builder": lambda q, **k: {
            "profileNames": [q] if isinstance(q, str) else q,
            "socials": k.get("socials", ["instagram", "facebook", "linkedin", "tiktok", "twitter", "youtube", "twitch", "medium", "pinterest", "snapchat", "reddit", "github", "spotify"]),
        },
    },
    # ============== COMPANY ==============
    "linkedin-company": {
        "id": "harvestapi~linkedin-profile-search",
        "description": "LinkedIn: company → empleados con email (búsqueda masiva)",
        "categoria": "company",
        "input_builder": lambda q, **k: {
            "currentCompanies": [q] if isinstance(q, str) else q,
            "maxItems": k.get("max_items", 50),
        },
    },
}


# Categorías de los actores
CATEGORIAS = {}
for name, info in ACTORES.items():
    cat = info.get("categoria", "otro")
    CATEGORIAS.setdefault(cat, []).append(name)


class ApifyBroker:
    def __init__(self, token: str | None = None):
        self.token = token or os.getenv("APIFY_TOKEN", "")
        if not self.token:
            raise ValueError("APIFY_TOKEN no configurado")
        if not HAS_REQUESTS:
            raise ImportError("instala requests: pip install requests")
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "apify-broker/2.0"

    def _run(self, actor_id: str, input_data: dict,
             timeout: int = DEFAULT_TIMEOUT, memory_mb: int = 512) -> list[dict]:
        """Ejecuta actor en modo sync. Devuelve dataset items."""
        url = f"{APIFY_BASE}/acts/{actor_id}/run-sync-get-dataset-items"
        params = {"token": self.token, "timeout": timeout, "memory": memory_mb}
        try:
            r = self.session.post(url, params=params, json=input_data, timeout=timeout + 60)
            if r.status_code not in (200, 201):
                return [{"error": f"HTTP {r.status_code}", "body": r.text[:500]}]
            # el response puede ser: lista directa, {data: [...]}, o string JSON en body
            try:
                data = r.json()
            except json.JSONDecodeError:
                return [{"error": "json_decode", "body": r.text[:500]}]
            # si es dict con "data" key
            if isinstance(data, dict):
                if "error" in data and len(data) == 2:
                    # actor devolvió estructura de error
                    body_str = data.get("body", "")
                    try:
                        parsed = json.loads(body_str) if isinstance(body_str, str) else data.get("body")
                        if isinstance(parsed, list):
                            return parsed
                    except (json.JSONDecodeError, TypeError):
                        pass
                    return [data]  # devolver como item único
                if "data" in data and isinstance(data["data"], list):
                    return data["data"]
                # si es dict de item único, envolverlo
                return [data]
            # si es lista directa
            if not data:
                return []
            return data
        except requests.exceptions.Timeout:
            return [{"error": f"timeout después de {timeout}s"}]
        except Exception as e:
            return [{"error": str(e)}]

    def run_named(self, name: str, query, **kwargs) -> list[dict]:
        if name not in ACTORES:
            raise KeyError(f"actor '{name}' no existe. Disponibles: {list(ACTORES)}")
        actor = ACTORES[name]
        if actor.get("internal"):
            return [{"error": f"actor '{name}' es interno, no se puede ejecutar vía Apify API"}]
        # extraer timeout/memory si vienen en kwargs
        timeout = kwargs.pop("timeout", DEFAULT_TIMEOUT)
        memory_mb = kwargs.pop("memory_mb", 512)
        return self._run(
            actor["id"],
            actor["input_builder"](query, **kwargs),
            timeout=timeout, memory_mb=memory_mb,
        )

    def list_actors(self) -> dict:
        return {
            name: {
                "id": info["id"],
                "description": info["description"],
                "categoria": info.get("categoria", "otro"),
            }
            for name, info in ACTORES.items()
            if not info.get("internal")
        }

    def list_by_categoria(self, categoria: str) -> list[str]:
        return CATEGORIAS.get(categoria, [])

    # ============== helpers de alto nivel ==============

    def find_by_email(self, email: str) -> dict:
        """Usa instagram-search-users con email → busca perfiles donde ese email está registrado."""
        username = email.split("@")[0] if "@" in email else email
        return {
            "metodo": "email",
            "email_buscado": email,
            "username_probado": username,
            "resultados": self.run_named("instagram-search-users", username, mode="enriched", max_items=20),
        }

    def find_by_phone(self, phone: str) -> dict:
        """Phone → no hay actor directo, retornamos el número para usar en otros métodos."""
        return {
            "metodo": "phone",
            "telefono": phone,
            "resultados": [],  # se usa en combinación con name/email
        }

    def find_by_username(self, username: str, **kwargs) -> dict:
        """Búsqueda paralela en Instagram + TikTok + YouTube por username."""
        results = {}
        with ThreadPoolExecutor(max_workers=3) as ex:
            tasks = {
                "instagram": ("instagram-profile", username),
                "tiktok": ("tiktok-profile", username),
                "youtube": ("youtube-channel", username),
            }
            futures = {plat: ex.submit(self.run_named, actor, username, **kwargs)
                       for plat, (actor, _) in tasks.items()}
            for plat, fut in futures.items():
                try:
                    results[plat] = fut.result(timeout=120)
                except Exception as e:
                    results[plat] = [{"error": str(e)}]
        return results

    def find_by_name(self, nombre: str, paterno: str = "",
                    materno: str = "", company: str = "",
                    max_results: int = 15) -> dict:
        """Búsqueda por nombre en múltiples plataformas en paralelo."""
        results = {}
        # construir queries
        full_name = f"{nombre} {paterno}".strip()
        keywords_instagram = [full_name]
        queries_fb = full_name
        # LinkedIn necesita firstName/lastName
        # Instagram: keyword search
        with ThreadPoolExecutor(max_workers=4) as ex:
            tasks = {}
            if full_name:
                tasks["instagram"] = ("instagram-user-search", full_name, {"max_items": max_results})
                tasks["facebook"] = ("facebook-user-search", full_name, {"max_items": 10})
                if paterno:
                    tasks["linkedin"] = ("linkedin-search-by-name", nombre, {
                        "first_name": nombre, "last_name": paterno, "max_items": 10
                    })
            futures = {name: ex.submit(self.run_named, actor, q, **kw)
                       for name, (actor, q, kw) in tasks.items()}
            for name, fut in futures.items():
                try:
                    results[name] = fut.result(timeout=180)
                except Exception as e:
                    results[name] = [{"error": str(e)}]
        return results

    def find_social_media(self, profile_names: list[str], socials: list[str] | None = None) -> dict:
        """Busca perfiles sociales con tri_angle/social-media-finder.

        Args:
            profile_names: lista de nombres/nicknames a buscar
            socials: lista de redes sociales (default: todas las 13 disponibles)
        Returns:
            dict con resultados por red social
        """
        if socials is None:
            socials = ["instagram", "facebook", "linkedin", "tiktok", "twitter",
                       "youtube", "twitch", "medium", "pinterest", "snapchat",
                       "reddit", "github", "spotify"]
        results = {}
        with ThreadPoolExecutor(max_workers=min(len(profile_names), 5)) as ex:
            futures = {}
            for name in profile_names:
                futures[name] = ex.submit(
                    self.run_named, "social-media-finder", name,
                    socials=socials, timeout=120, memory_mb=1024
                )
            for name, fut in futures.items():
                try:
                    results[name] = fut.result(timeout=180)
                except Exception as e:
                    results[name] = [{"error": str(e)}]
        return results

    def scrape_website(self, url: str, max_pages: int = 15) -> dict:
        """Extrae emails, phones, socials de un website."""
        return {
            "metodo": "website",
            "url": url,
            "contact_info": self.run_named("contact-info-scraper", url,
                                          max_depth=2, max_pages=max_pages),
        }

    def find_by_curp(self, curp: str) -> dict:
        """Stub — para mantener compatibilidad con interfaz previa."""
        return self.find_by_name(curp)

    # ============== pipeline full_dossier ==============

    def full_dossier(self, *, email: str = "", phone: str = "",
                     nombre: str = "", paterno: str = "", materno: str = "",
                     website: str = "", company: str = "",
                     max_results: int = 10) -> dict:
        """Pipeline OSINT completo:
          - si email: instagram-search-users por username + website contact
          - si phone: + name search (porque phone no es buscable directo)
          - si name: instagram + facebook + linkedin en paralelo
          - si website: contact-info-scraper
        Returns dict consolidado con resultados por vía.
        """
        t0 = time.time()
        result = {
            "metadata": {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "email": email,
                "phone": phone,
                "nombre": f"{nombre} {paterno} {materno}".strip(),
                "company": company,
            },
            "resultados": {},
            "errores": [],
        }

        def task_email():
            if not email:
                return ("email", None)
            r = {"via": "email", "email": email, "plataformas": []}
            username = email.split("@")[0] if "@" in email else email
            # 1) Instagram search por username
            ig = self.run_named("instagram-user-search", username, max_items=max_results)
            for item in ig:
                if isinstance(item, dict) and not item.get("error"):
                    r["plataformas"].append({
                        "plataforma": "instagram",
                        "username": item.get("username"),
                        "fullName": item.get("fullName"),
                        "url": item.get("profileUrl"),
                        "publicEmail": item.get("publicEmail"),
                        "publicPhone": item.get("publicPhone"),
                        "matchedKeywords": item.get("matchedKeywords"),
                    })
            # 2) Instagram user search
            ig2 = self.run_named("instagram-search-users", username, mode="enriched", max_items=max_results)
            for item in ig2:
                if isinstance(item, dict) and not item.get("error"):
                    r["plataformas"].append({
                        "plataforma": "instagram",
                        "username": item.get("username"),
                        "fullName": item.get("full_name"),
                        "url": item.get("inputUrl") or f"https://instagram.com/{item.get('username')}",
                        "publicEmail": item.get("public_email"),
                        "publicPhone": item.get("public_phone"),
                    })
            return ("email", r)

        def task_phone():
            if not phone:
                return ("phone", None)
            # phone no es buscable directo; lo asociamos al nombre
            return ("phone", {"via": "phone", "phone": phone, "plataformas": [],
                              "nota": "phone solo se usa como discriminador, no se puede buscar OSINT directo"})

        def task_name():
            if not nombre and not paterno:
                return ("name", None)
            r = {"via": "name", "nombre": f"{nombre} {paterno} {materno}".strip(),
                 "plataformas": []}
            # Instagram por nombre
            full = f"{nombre} {paterno}".strip()
            if full:
                ig = self.run_named("instagram-user-search", full, max_results=max_results)
                for item in ig:
                    if isinstance(item, dict) and not item.get("error"):
                        r["plataformas"].append({
                            "plataforma": "instagram",
                            "username": item.get("username"),
                            "fullName": item.get("fullName"),
                            "url": item.get("profileUrl"),
                            "publicEmail": item.get("publicEmail"),
                            "publicPhone": item.get("publicPhone"),
                            "followerCount": item.get("followerCount"),
                            "isBusinessAccount": item.get("isBusinessAccount"),
                        })
                # Facebook
                fb = self.run_named("facebook-user-search", full, max_items=10)
                for item in fb:
                    if isinstance(item, dict) and not item.get("error"):
                        r["plataformas"].append({
                            "plataforma": "facebook",
                            "name": item.get("name"),
                            "profileUrl": item.get("profileUrl"),
                            "profileImage": item.get("profileImage"),
                            "userData": item.get("userData"),
                        })
                # LinkedIn
                if paterno:
                    li = self.run_named("linkedin-search-by-name", nombre,
                                          first_name=nombre, last_name=paterno, max_items=10)
                    for item in li:
                        if isinstance(item, dict) and not item.get("error"):
                            r["plataformas"].append({
                                "plataforma": "linkedin",
                                "name": item.get("name") or item.get("fullName"),
                                "headline": item.get("headline") or item.get("position"),
                                "profileUrl": item.get("url") or item.get("profileUrl"),
                                "location": item.get("location"),
                            })
            return ("name", r)

        def task_website():
            if not website:
                return ("website", None)
            r = self.scrape_website(website, max_pages=10)
            # consolidar contactos
            contacts = []
            for item in r.get("contact_info", []):
                if isinstance(item, dict) and not item.get("error"):
                    contacts.append({
                        "url_origen": item.get("startUrl") or item.get("url"),
                        "emails": item.get("emails", []),
                        "phones": item.get("phones", []),
                        "linkedins": item.get("linkedins", item.get("linkedin", [])),
                        "twitters": item.get("twitters", item.get("twitter", [])),
                        "instagrams": item.get("instagrams", item.get("instagram", [])),
                        "facebooks": item.get("facebooks", item.get("facebook", [])),
                        "tiktoks": item.get("tiktoks", item.get("tiktok", [])),
                        "youtubes": item.get("youtubes", item.get("youtube", [])),
                    })
            return ("website", {
                "via": "website", "url": website,
                "contactos": contacts,
            })

        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(t) for t in [task_email, task_phone, task_name, task_website]]
            for fut in as_completed(futures, timeout=300):
                try:
                    name, data = fut.result()
                    if data is not None:
                        result["resultados"][name] = data
                except Exception as e:
                    result["errores"].append(str(e))

        result["duracion_seg"] = round(time.time() - t0, 2)
        # consolidar todos los perfiles únicos
        all_profiles = []
        for via, data in result["resultados"].items():
            for plat in data.get("plataformas", []) + (data.get("contactos", []) or []):
                if isinstance(plat, dict):
                    plat["via"] = via
                    all_profiles.append(plat)
        result["perfiles_consolidados"] = all_profiles
        result["total_perfiles"] = len(all_profiles)

        # ====== Validación con IA (Ollama Cloud) ======
        # Envía TODOS los perfiles a la IA para que decida cuáles son correctos.
        # Devuelve: scores, conflicts, high_confidence, low_confidence.
        result["validacion_ia"] = self._validate_with_ai(all_profiles, result["metadata"])

        return result

    def _validate_with_ai(self, profiles: list[dict], metadata: dict) -> dict:
        """Usa Ollama Cloud (deepseek-v4-pro) para validar los perfiles.

        La IA recibe TODOS los perfiles y devuelve:
          - scores: confianza por perfil (0-100)
          - conflicts: perfiles con datos contradictorios (mismo nombre, distinta empresa)
          - high_confidence: lista de los que la IA considera correctos
          - low_confidence: lista de los que NO confía
          - rationale: explicación textual
          - summary_ia: narrativa sobre el sujeto
        """
        result = {
            "scores": [],         # [{perfil, score, razon}]
            "conflicts": [],      # [{campo, valores: [...]}]
            "high_confidence": [],  # [perfiles] con score >= 70
            "low_confidence": [],   # [perfiles] con score < 50
            "rationale": "",
            "summary_ia": "",
            "status": "skipped",
        }
        if not profiles:
            result["status"] = "no_profiles"
            return result
        try:
            # leer API key y modelo de la config
            try:
                from config import config
                api_key = config.ollama_api_key
                model = config.ollama_model
            except Exception:
                api_key = os.getenv("OLLAMA_API_KEY")
                model = os.getenv("OLLAMA_MODEL", "deepseek-v4-pro")
            if not api_key:
                result["status"] = "no_api_key"
                return result

            # preparar resumen de perfiles para el prompt
            profiles_text = json.dumps(profiles, ensure_ascii=False, default=str)[:8000]
            metadata_text = json.dumps(metadata, ensure_ascii=False, default=str)

            prompt = (
                "Eres un analista OSINT experto en verificar identidad digital. "
                "Recibirás una lista de perfiles de redes sociales encontrados sobre una persona. "
                "Tu trabajo es:\n"
                "1. Asignar un score de confianza (0-100) a cada perfil basado en:\n"
                "   - Coincidencia de nombre completo\n"
                "   - Coincidencia de email/phone (si está disponible)\n"
                "   - Calidad del perfil (verificado, biografía, empresas mencionadas)\n"
                "2. Identificar CONFLICTOS: perfiles con datos contradictorios (ej: mismo nombre, distinta empresa)\n"
                "3. Listar los high_confidence (score >= 70) y low_confidence (score < 50)\n"
                "4. Generar un summary_ia: narrativa sobre la presencia digital de la persona\n\n"
                f"Metadata del sujeto: {metadata_text}\n\n"
                f"Perfiles encontrados ({len(profiles)} total):\n{profiles_text}\n\n"
                "Responde SOLO con un JSON válido con esta estructura:\n"
                "{\n"
                '  "scores": [{"index": 0, "plataforma": "instagram", "username": "x", "score": 85, '
                '"razon": "coincide nombre y empresa"}],\n'
                '  "conflicts": [{"campo": "empresa", "valores": ["Google", "Microsoft"]}],\n'
                '  "high_confidence": [0, 2],\n'
                '  "low_confidence": [3],\n'
                '  "rationale": "explicación de las decisiones",\n'
                '  "summary_ia": "narrativa sobre la huella digital de la persona, '
                'qué se confirmó, qué es ambiguo, qué redes sociales son confiables"\n'
                "}"
            )

            r = requests.post(
                "https://ollama.com/api/chat",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "user", "content": prompt}
                    ],
                    "stream": False,
                    "format": "json",
                },
                timeout=60,
            )
            if r.status_code != 200:
                result["status"] = f"error_http_{r.status_code}"
                result["error"] = r.text[:500]
                return result
            data = r.json()
            content = data.get("message", {}).get("content", "{}")
            try:
                ai_resp = json.loads(content)
            except json.JSONDecodeError:
                # intentar extraer JSON del texto
                import re as re_mod
                m = re_mod.search(r"\{.*\}", content, re.DOTALL)
                if m:
                    try:
                        ai_resp = json.loads(m.group())
                    except json.JSONDecodeError:
                        result["status"] = "json_parse_error"
                        result["raw"] = content[:500]
                        return result
                else:
                    result["status"] = "no_json"
                    result["raw"] = content[:500]
                    return result
            # mapear resultados
            result.update(ai_resp)
            result["status"] = "ok"
            result["model"] = model
            # adjuntar info adicional por cada score
            for s in result.get("scores", []):
                if isinstance(s, dict) and "index" in s and 0 <= s["index"] < len(profiles):
                    s["perfil_resumen"] = {
                        "plataforma": profiles[s["index"]].get("plataforma"),
                        "username": profiles[s["index"]].get("username") or profiles[s["index"]].get("name"),
                        "url": profiles[s["index"]].get("url") or profiles[s["index"]].get("profileUrl"),
                    }
        except Exception as e:
            result["status"] = f"exception: {e}"
        return result


# ============== CLI ==============

def main():
    ap = argparse.ArgumentParser(description="Apify broker — OSINT completo")
    ap.add_argument("--token", help="Apify API token (o env APIFY_TOKEN)")
    ap.add_argument("--actor", help="nombre del actor (ver --list)")
    ap.add_argument("--query", help="query (nombre, email, username, URL)")
    ap.add_argument("--max-results", type=int, default=10)
    ap.add_argument("--list", action="store_true", help="listar todos los actores")
    ap.add_argument("--json", help="guardar resultado en archivo JSON")
    ap.add_argument("--dossier", action="store_true",
                    help="ejecutar pipeline full_dossier con --email/--phone/--nombre")
    ap.add_argument("--email", help="email (para dossier)")
    ap.add_argument("--phone", help="phone (para dossier)")
    ap.add_argument("--nombre", help="nombre (para dossier)")
    ap.add_argument("--paterno", help="apellido paterno (para dossier)")
    ap.add_argument("--materno", help="apellido materno (para dossier)")
    ap.add_argument("--company", help="empresa (para dossier)")
    ap.add_argument("--website", help="URL (para dossier)")
    args = ap.parse_args()

    try:
        broker = ApifyBroker(args.token)
    except (ValueError, ImportError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if args.list:
        for cat, acts in CATEGORIAS.items():
            print(f"\n=== {cat.upper()} ===")
            for a in acts:
                info = ACTORES[a]
                print(f"  {a:30s} {info['id']:50s} {info['description'][:60]}")
        return

    if args.dossier:
        result = broker.full_dossier(
            email=args.email or "", phone=args.phone or "",
            nombre=args.nombre or "", paterno=args.paterno or "",
            materno=args.materno or "", website=args.website or "",
            company=args.company or "", max_results=args.max_results,
        )
    elif args.actor and args.query:
        items = broker.run_named(args.actor, args.query, max_results=args.max_results)
        result = {"items": items}
    else:
        ap.print_help()
        return

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        print(f"Guardado en {args.json}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str)[:3000])


if __name__ == "__main__":
    main()
