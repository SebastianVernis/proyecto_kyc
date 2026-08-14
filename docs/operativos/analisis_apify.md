# Análisis Estratégico — Plataforma KYC + OSINT con Apify

**Fecha:** 2026-07-23
**Objetivo:** Plataforma integral de inteligencia de personas con KYC, OSINT y Apify como motor de redes sociales.

---

## 1. Panorama Apify relevante

Apify es una plataforma de web scraping con **+5,000 actores** publicados. Muchos de ellos resuelven problemas específicos de OSINT/verificación de identidad.

### 1.1 Actores identificados para OSINT/Redes Sociales

| Actor | Función | Plataformas | Costo | Confianza |
|---|---|---|---|---|
| **`bovi/social-media-finder`** | Buscar perfiles por nombre/email/phone/username | 15+ (LinkedIn, Twitter/X, IG, TikTok, GitHub, Reddit, YouTube, Facebook, Pinterest, Telegram, Medium, Substack, Quora, Twitch, Snapchat) | Pay-per-result | ★★★★ |
| **`burbn/social-links-search`** | Buscar perfiles por nombre/keyword | 9 redes (Facebook, Instagram, Twitter, LinkedIn, GitHub, TikTok, YouTube, Pinterest, Snapchat) | Pay-per-result | ★★★ |
| **`tri_angle/social-media-finder`** | Buscar por nombres/nicknames | 13 redes (AskFM, Discord, Facebook, GitHub, IG, LinkedIn, Medium, Pinterest, Steam, Threads, TikTok, Twitch, YouTube) | Free tier (1500 resultados gratis) | ★★★ |
| **`anshumanatrey/social-analyzer`** | Buscar username | 900+ sitios, confidence scoring, extracción de emails/links | $0.005/record | ★★★★★ |
| **`jungle_synthesizer/social-media-finder`** | Buscar username | 100+ plataformas | $0.001-$0.005/record | ★★★ |
| **`onescales/social-media-profile-finder-pro`** | Buscar username (Sherlock) | 400+ redes | Pay-per-use | ★★★★ |
| **`automation-lab/social-media-profile-finder`** | Encontrar perfiles desde website | 16+ plataformas (extrae de sitios web) | $0.001/website | ★★★★ |

### 1.2 Actores de Email/Phone/Website

| Actor | Función | Costo |
|---|---|---|
| **`automation-lab/website-contact-finder`** | Emails, phones, socials desde URL | $0.001/website + email verify $0.002/email |
| **`tugelbay/contact-finder-pro`** | Emails MX-verified, phones E.164, role labels | Pay-per-value |
| **`haketa/email-extractor`** | Emails, phones, socials desde URL | Pay-per-use |
| **`x_guru/website-email-phone-finder`** | Bulk contact enrichment | $2/1k results |
| **`prodiger/website-contact-finder`** | Email scraper i18n | Pay-per-use |
| **`code-node-tools/website-email-socials-phone-number-scraper-lookup`** | Email obfuscation handling | Pay-per-use |

### 1.3 Pricing de Apify

| Plan | Precio | CUs incluidos | CUs extra | Uso para nosotros |
|---|---|---|---|---|
| **Free** | $0/mes | $5 | Bloqueado al agotar | Testing, desarrollo, demos |
| **Starter** | $29/mes | $29 ($0.20/CU) | $0.20/CU | MVP producción, hasta ~1,000 consultas/mes |
| **Scale** | $199/mes | $199 ($0.16/CU) | $0.16/CU | Producción media, hasta ~10,000 consultas/mes |
| **Business** | $999/mes | $999 ($0.13/CU) | $0.13/CU | Producción alta, hasta ~50,000 consultas/mes |

**Nota:** Muchos actores cobran por evento/resultado ADEMÁS de los CUs. Hay que revisar el pricing de cada actor.

### 1.4 Costos por investigación OSINT con Apify

**Configuración típica por sujeto:**
- 1 social-media-finder (15 redes): ~$0.05-0.30
- 1 social-analyzer (900+ sitios): ~$0.05-0.50
- 1 website-contact-finder (si tiene web): ~$0.005
- Email verify (5 emails): ~$0.01

**Total por investigación:** $0.10 - $1.00 USD ≈ $2-20 MXN

**Margen si cobramos $50-200 MXN por reporte:** excelente.

---

## 2. Arquitectura integrada: Padrón + OSINT + Apify + KYC

```
┌──────────────────────────────────────────────────────────────────┐
│                      Frontend Web                                  │
│  buscar.html — 3 tabs: Padrón / OSINT / KYC                        │
└────────────────────────┬─────────────────────────────────────────┘
                         │ HTTP + JSON
┌────────────────────────┴─────────────────────────────────────────┐
│                  API Gateway: servir.py                            │
│  /api/search   /api/osint   /api/kyc   /api/report                 │
│  /api/total    /api/estados /api/curp  /api/export                │
└──┬─────────┬─────────┬──────────┬──────────┬─────────────────────┘
   │         │         │          │          │
┌──┴──┐  ┌──┴──┐  ┌───┴──┐  ┌────┴───┐  ┌────┴──────────────────┐
│Padrón│ │OSINT│  │ Apify │  │  KYC   │  │   Renderers           │
│DuckDB│ │ pool│  │ broker│  │ broker │  │  (PDF/HTML/JSON)     │
└─────┘  └─────┘  └──┬───┘  └───┬────┘  └───────────────────────┘
                    │          │
            ┌───────┴────┐  ┌───┴────────────────┐
            │ Apify API  │  │  KYC providers      │
            │ + actors   │  │  - Tlaloc           │
            │            │  │  - Singula          │
            │            │  │  - Círculo          │
            │            │  │  - Buró             │
            │            │  │  - RENAPO           │
            └────────────┘  └────────────────────┘
```

### 2.1 Nuevo módulo: `apify_broker.py`

```python
import os
import requests
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")

# Actores clave pre-configurados
ACTORES = {
    "social_finder_full":   ("bovi/social-media-finder", 0.10),       # 15+ redes
    "social_analyzer_900":  ("anshumanatrey/social-analyzer", 0.30), # 900+ sitios
    "sherlock_400":         ("onescales/social-media-profile-finder-pro", 0.20), # 400+ redes
    "website_contact":      ("automation-lab/website-contact-finder", 0.01),  # contact scraper
    "linkedin_profile":     ("valig/linkedin-profile-search", 0.50),  # LinkedIn específico
    "instagram_profile":    ("apify/instagram-profile-scraper", 0.05),
    "facebook_profile":     ("apify/facebook-profile-scraper", 0.05),
    "tiktok_profile":       ("apify/tiktok-profile-scraper", 0.05),
    "twitter_profile":      ("apify/twitter-profile-scraper", 0.05),
}

class ApifyBroker:
    def __init__(self, token: str = None):
        self.token = token or APIFY_TOKEN
        self.base = "https://api.apify.com/v2"
        if not self.token:
            raise ValueError("APIFY_TOKEN no configurado")

    def run_actor(self, actor_id: str, input_data: dict, timeout: int = 120) -> list[dict]:
        """Ejecuta un actor de Apify en modo sync y devuelve el dataset."""
        url = f"{self.base}/acts/{actor_id}/run-sync-get-dataset-items"
        r = requests.post(
            url,
            params={"token": self.token, "timeout": timeout},
            json=input_data,
            timeout=timeout + 30
        )
        r.raise_for_status()
        return r.json()

    def find_social_by_name(self, nombre: str, max_results: int = 20) -> dict:
        """Búsqueda comprehensiva por nombre usando múltiples actores en paralelo."""
        results = {"social_finder": [], "social_analyzer": [], "sherlock": []}

        def run_social_finder():
            return self.run_actor("bovi/social-media-finder", {
                "query": nombre,
                "maxResults": max_results,
                "useSerpFallback": True,
                "proxyConfiguration": {"useApifyProxy": True}
            })

        def run_social_analyzer():
            return self.run_actor("anshumanatrey/social-analyzer", {
                "username": nombre.replace(" ", "").lower(),  # username sin espacios
                "top": 50,
                "mode": "fast"
            })

        def run_sherlock():
            return self.run_actor("onescales/social-media-profile-finder-pro", {
                "usernames": [nombre.replace(" ", "").lower(), nombre.lower()]
            })

        with ThreadPoolExecutor(max_workers=3) as ex:
            futures = {
                "social_finder": ex.submit(run_social_finder),
                "social_analyzer": ex.submit(run_social_analyzer),
                "sherlock": ex.submit(run_sherlock),
            }
            for name, fut in futures.items():
                try:
                    results[name] = fut.result(timeout=180)
                except Exception as e:
                    results[name] = [{"error": str(e)}]

        return results

    def find_by_email(self, email: str) -> dict:
        """Encuentra redes sociales asociadas a un email."""
        return {
            "social_finder": self.run_actor("bovi/social-media-finder", {
                "query": email, "maxResults": 30
            })
        }

    def find_by_username(self, username: str) -> dict:
        """Encuentra todas las cuentas con un username específico."""
        return {
            "social_analyzer": self.run_actor("anshumanatrey/social-analyzer", {
                "username": username, "top": 200
            }),
            "sherlock": self.run_actor("onescales/social-media-profile-finder-pro", {
                "usernames": [username]
            })
        }

    def scrape_website(self, url: str) -> dict:
        """Encuentra emails, phones, socials en una web."""
        return {
            "contact": self.run_actor("automation-lab/website-contact-finder", {
                "urls": [url], "verifyEmails": True, "maxPagesPerSite": 20
            })
        }

    def full_osint(self, nombre: str, paterno: str = None, materno: str = None,
                   email: str = None, telefono: str = None) -> dict:
        """OSINT comprehensivo en paralelo con todos los actores relevantes."""
        full_name = f"{nombre} {paterno or ''} {materno or ''}".strip()
        usernames = [
            nombre.lower(),
            f"{nombre.lower()}{paterno.lower()}" if paterno else None,
            f"{paterno.lower()}{materno.lower()}" if paterno and materno else None,
            f"{nombre.lower()}.{paterno.lower()}" if paterno else None,
        ]
        usernames = [u for u in usernames if u]

        results = {}

        # 1) Búsqueda por nombre
        results["by_name"] = self.find_social_by_name(full_name)

        # 2) Búsqueda por username variants
        results["by_username"] = {}
        for u in usernames:
            results["by_username"][u] = self.find_by_username(u)

        # 3) Búsqueda por email
        if email:
            results["by_email"] = self.find_by_email(email)

        return results
```

### 2.2 Integración con `osint.py`

```python
# En osint.py
try:
    from apify_broker import ApifyBroker
    HAS_APIFY = True
except ImportError:
    HAS_APIFY = False

class OSINTInvestigator:
    def __init__(self, db_path, use_apify=False, apify_token=None, verbose=True):
        # ...
        self.apify = None
        if use_apify and HAS_APIFY and apify_token:
            try:
                self.apify = ApifyBroker(apify_token)
            except Exception as e:
                self.log(f"Apify no disponible: {e}")

    def investigar(self, curp, nombre, paterno, materno, email, telefono, **kwargs):
        # ... existing logic ...

        # Reemplazar GitHub manual con Apify si está disponible
        if self.apify and (nombre or email):
            self.log("[*] Apify: búsqueda comprehensiva en redes sociales...")
            apify_results = self.apify.full_osint(
                nombre=nombre, paterno=paterno, materno=materno,
                email=email, telefono=telefono
            )
            self.results["apify_social"] = apify_results
            self.log(f"    resultados Apify: {sum(len(v) for k,v in apify_results.items() if isinstance(v, list))} perfiles")
```

### 2.3 Integración con `servir.py`

```python
# Nuevo endpoint
def _handle_apify_test(self):
    """Endpoint para probar la conexión con Apify."""
    token = self.headers.get("X-Apify-Token", "")
    if not token:
        self._json(400, {"error": "X-Apify-Token header requerido"})
        return
    try:
        r = requests.get(f"https://api.apify.com/v2/users/me?token={token}")
        if r.status_code == 200:
            self._json(200, {"ok": True, "user": r.json()})
        else:
            self._json(401, {"error": "Token inválido"})
    except Exception as e:
        self._json(500, {"error": str(e)})
```

---

## 3. Roadmap de integración con Apify

### Fase 1 — MVP (1 semana, gratis)
- 🔲 Crear `apify_broker.py`
- 🔲 Configurar 1 actor: `bovi/social-media-finder`
- 🔲 Free tier ($5/mes): suficiente para ~50-100 búsquedas
- 🔲 UI: añadir opción "Búsqueda profunda" en tab OSINT

### Fase 2 — Producción básica (1 mes)
- 🔲 Activar Starter plan ($29/mes)
- 🔲 Agregar 3-4 actores más (`social-analyzer`, `website-contact-finder`, `sherlock`)
- 🔲 Búsquedas en paralelo con ThreadPoolExecutor
- 🔲 Caché de resultados para evitar consultas duplicadas

### Fase 3 — Producción completa (3 meses)
- 🔲 Scale plan ($199/mes)
- 🔲 10+ actores configurados
- 🔲 Búsquedas por email, phone, username, website
- 🔲 Generador PDF con todas las redes sociales encontradas
- 🔲 Métricas de uso y facturación por cliente

### Fase 4 — Avanzado (6 meses)
- 🔲 Machine learning para scoring de match (perfil real vs homónimo)
- 🔲 Verificación cruzada de perfiles (LinkedIn + GitHub + Twitter consistencia)
- 🔲 Detección de suplantación de identidad
- 🔲 Alertas en tiempo real cuando un perfil nuevo del sujeto aparece

---

## 4. Costos combinados con Apify

| Caso | Costo Apify | Costo KYC | Costo total | Precio venta | Margen |
|---|---|---|---|---|---|
| Consulta rápida (padrón + OSINT básico) | $0.50 | $0 | $0.50 | $30 MXN | 98% |
| Consulta estándar (padrón + OSINT + Apify) | $2.00 | $3.00 | $5.00 | $80 MXN | 94% |
| Consulta completa (todo + KYC + Buró) | $5.00 | $15.00 | $20.00 | $200 MXN | 90% |
| Consulta enterprise (todo + biométrico) | $8.00 | $30.00 | $38.00 | $500 MXN | 92% |

---

## 5. Actores recomendados por prioridad

### Tier 1 (imprescindibles)
1. **`bovi/social-media-finder`** — Mejor balance cobertura/costo
2. **`anshumanatrey/social-analyzer`** — 900+ sitios, confidence scoring
3. **`automation-lab/website-contact-finder`** — Para emails y phones

### Tier 2 (recomendables)
4. **`tri_angle/social-media-finder`** — 13 redes principales, free tier
5. **`onescales/social-media-profile-finder-pro`** — 400+ redes (Sherlock)
6. **`tugelbay/contact-finder-pro`** — Email MX-verified, role detection

### Tier 3 (opcionales, casos específicos)
7. **Per-platform scrapers** (LinkedIn, Instagram, Twitter, TikTok) — para datos profundos
8. **`jumbled_falcon/email-phone-social-finder-website`** — Bulk de websites
9. **`code-node-tools/website-email-socials-phone-number-scraper-lookup`** — Email obfuscation

---

## 6. Comparativa Apify vs otras opciones

| Característica | Apify | Moffin | Singula | Kiban | Tlaloc |
|---|---|---|---|---|---|
| **Cobertura redes** | 900+ sitios | 5-10 | 5-10 | 5-10 | 0 (solo RENAPO) |
| **Email finder** | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Phone finder** | ✅ | ✅ | ✅ | ✅ | ❌ |
| **KYC integrado** | ❌ | ✅ | ✅ | ✅ | ✅ |
| **OSINT integrado** | ✅ | parcial | parcial | parcial | ❌ |
| **API unificada** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Pricing modelo** | Pay-per-use | Pay-per-use | Pay-per-use | Pay-per-use | Pay-per-use |
| **Costo consulta** | $0.10-1.00 | $1-5 | $1-3 | $2-5 | $0.50 |
| **Documentación** | Excelente | Buena | Buena | Buena | Media |
| **Sandbox gratis** | ✅ ($5/mes) | ✅ | ✅ | ❌ | ✅ |
| **Onboarding** | 5 min | 30 min | 30 min | 1 hora | 5 min |

**Conclusión:** Apify es el **mejor actor para OSINT puro de redes sociales** pero **complementario** con proveedores KYC para datos oficiales.

---

## 7. Estrategia de precios final

| Plan | Consultas/mes | Precio/mes cliente | Costo | Margen |
|---|---|---|---|---|
| **Free** | 5 | $0 | $0 | 100% (acquisition) |
| **Básico** | 50 | $99 MXN | ~$15 | 85% |
| **Pro** | 200 | $399 MXN | ~$60 | 85% |
| **Enterprise** | 1000 | $1,999 MXN | ~$300 | 85% |
| **Custom** | Ilimitado | $9,999+ | ~$1,500 | 85% |

---

## 8. Próximos pasos concretos

1. **Hoy:** Crear `apify_broker.py` con 3 actores (social-finder, social-analyzer, website-contact)
2. **Esta semana:** Registrar cuenta Apify free, obtener token API, configurar env var
3. **Este mes:** Integrar Apify en `osint.py`, añadir tab en frontend para "redes sociales profundas"
4. **Mes 2:** Combinar con Tlaloc para tener RENAPO + redes sociales
5. **Mes 3:** Generador PDF con todos los datos en formato del HEHA821101

---

## 9. Riesgos

- **Costo de Apify por consulta:** $0.10-1.00 USD = $2-20 MXN. Necesita control de rate limiting y caché.
- **Datos sensibles:** Scraping de redes sociales sin consentimiento puede violar ToS de las plataformas y LFPDPPP.
- **Falsos positivos:** Apify devuelve "match_confidence" pero no garantiza que el perfil sea del sujeto. Necesita scoring manual o ML.
- **Rate limits de plataformas:** Instagram y LinkedIn limitan agresivamente, requieren residential proxies ($$$).

---

## 10. Metadata

```
Fuentes consultadas: 4 búsquedas web, 50+ páginas de Apify Store
Actores identificados: 20+
Costo por consulta: $0.10-1.00 USD
Tier gratis: $5/mes (Free plan Apify)
Tier recomendado: $29-199/mes (Starter/Scale)
Tier enterprise: $999+/mes (Business)
Payback: ~50 consultas/mes
```
