/**
 * KYC Worker — Cloudflare Worker con Hono.
 *
 * Funciones:
 * 1. Sirve frontend estático (Static Assets binding)
 * 2. Proxy /api/* → backend (con Cache API para endpoints read-only)
 * 3. Rate limiting con KV token bucket
 */
import { Hono } from "hono";

type Bindings = {
  ASSETS: Fetcher;
  RATE_LIMIT: KVNamespace;
  ACTAS_BUCKET: R2Bucket;
  API_ORIGIN: string;
  ENVIRONMENT: string;
};

const app = new Hono<{ Bindings: Bindings }>();

// ── Rate Limiting ────────────────────────────────────────────────────────────

const RATE_LIMIT_RPM = 60; // requests per minute (anónimo)
const RATE_LIMIT_RPM_AUTH = 600; // requests per minute (autenticado)
const RATE_LIMIT_WINDOW = 60; // seconds

async function checkRateLimit(
  kv: KVNamespace,
  key: string,
  limit: number
): Promise<{ allowed: boolean; remaining: number; retryAfter: number }> {
  const now = Math.floor(Date.now() / 1000);
  const windowKey = `rl:${key}:${Math.floor(now / RATE_LIMIT_WINDOW)}`;

  const raw = await kv.get(windowKey);
  const count = raw ? parseInt(raw, 10) : 0;

  if (count >= limit) {
    const retryAfter = RATE_LIMIT_WINDOW - (now % RATE_LIMIT_WINDOW);
    return { allowed: false, remaining: 0, retryAfter };
  }

  await kv.put(windowKey, String(count + 1), {
    expirationTtl: RATE_LIMIT_WINDOW * 2,
  });

  return { allowed: true, remaining: limit - count - 1, retryAfter: 0 };
}

// ── Cache Config ─────────────────────────────────────────────────────────────

const CACHE_TTL: Record<string, number> = {
  "/api/v1/sepomex": 3600, // 1h — catálogo CP, cambia poco
  "/api/v1/geo": 3600, // 1h — geocodificación, cacheable
  "/health": 300, // 5min — healthcheck
};

function getCacheTTL(path: string): number | null {
  for (const [prefix, ttl] of Object.entries(CACHE_TTL)) {
    if (path.startsWith(prefix)) return ttl;
  }
  return null;
}

// ── Middleware: Rate Limiting ────────────────────────────────────────────────

app.use("/api/*", async (c, next) => {
  const ip =
    c.req.header("cf-connecting-ip") ||
    c.req.header("x-forwarded-for") ||
    "unknown";

  // Detectar si hay token de sesión (cookie auth)
  const cookie = c.req.header("cookie") || "";
  const hasSession = /session=/.test(cookie) || /token=/.test(cookie);
  const limit = hasSession ? RATE_LIMIT_RPM_AUTH : RATE_LIMIT_RPM;

  const { allowed, remaining, retryAfter } = await checkRateLimit(
    c.env.RATE_LIMIT,
    ip,
    limit
  );

  if (!allowed) {
    return c.json(
      { error: "Rate limit exceeded", retryAfter },
      429,
      {
        "Retry-After": String(retryAfter),
        "X-RateLimit-Limit": String(limit),
        "X-RateLimit-Remaining": "0",
      }
    );
  }

  await next();

  c.header("X-RateLimit-Limit", String(limit));
  c.header("X-RateLimit-Remaining", String(remaining));
});

// ── Proxy: /api/* → Backend ─────────────────────────────────────────────────

app.use("/api/*", async (c) => {
  const apiOrigin = c.env.API_ORIGIN;
  if (!apiOrigin) {
    return c.json({ error: "API_ORIGIN not configured" }, 500);
  }

  const url = new URL(c.req.url);
  const targetUrl = `${apiOrigin}${url.pathname}${url.search}`;

  // Construir headers para el backend
  const headers = new Headers();
  headers.set("Host", new URL(apiOrigin).host);
  headers.set("X-Forwarded-For", c.req.header("cf-connecting-ip") || "");
  headers.set("X-Real-IP", c.req.header("cf-connecting-ip") || "");

  // Copiar headers relevantes del request original
  for (const key of ["authorization", "content-type", "cookie"]) {
    const val = c.req.header(key);
    if (val) headers.set(key, val);
  }

  // Intentar cache para endpoints read-only
  const cacheTTL = getCacheTTL(url.pathname);
  if (cacheTTL && c.req.method === "GET") {
    const cache = caches.default;
    const cacheKey = new Request(c.req.url, { method: "GET" });
    const cached = await cache.match(cacheKey);
    if (cached) {
      const resp = new Response(cached.body, cached);
      resp.headers.set("cf-cache-status", "HIT");
      return resp;
    }

    const resp = await fetch(targetUrl, {
      method: c.req.method,
      headers,
      body: c.req.method !== "GET" ? c.req.raw.body : undefined,
    });

    // Solo cachear respuestas exitosas
    if (resp.ok) {
      const respToCache = new Response(resp.body, resp);
      respToCache.headers.set("Cache-Control", `public, max-age=${cacheTTL}`);
      c.executionCtx.waitUntil(cache.put(cacheKey, respToCache.clone()));
      return respToCache;
    }

    return resp;
  }

  // Sin cache — proxy directo
  const resp = await fetch(targetUrl, {
    method: c.req.method,
    headers,
    body: c.req.method !== "GET" ? c.req.raw.body : undefined,
  });

  return resp;
});

// ── Healthcheck del Worker ───────────────────────────────────────────────────

app.get("/worker/health", (c) => {
  return c.json({
    service: "kyc-worker",
    environment: c.env.ENVIRONMENT || "unknown",
    status: "ok",
  });
});

// ── Webhook: Consulta Única (Actas Registro Civil) ───────────────────────────
// Ruta PÚBLICA (sin cookie ni rate limit de usuario). El handler hace dos
// pasos:
//   1) Proxealo al backend: valida HMAC X-CU-Signature (auth real),
//      actualiza actas_submissions en SQLite, y devuelve la metadata que
//      necesitamos para construir la R2 key (uuid/curp/acta_type).
//   2) Si el backend respondió ok y viene pdfBase64, subir a R2 con key
//      actas/<curp>/<acta_type>/<uuid>.pdf (el backend nunca ve el PDF).
// Esto mantiene la firma fuera del worker (validada solo con la api key)
// y aprovecha el CDN/edge para archivos.
app.post("/webhook/consultaunica/actas", async (c) => {
  const apiOrigin = c.env.API_ORIGIN;
  if (!apiOrigin) {
    return c.json({ error: "API_ORIGIN not configured" }, 500);
  }
  const bucket = c.env.ACTAS_BUCKET;
  if (!bucket) {
    return c.json({ error: "ACTAS_BUCKET not configured" }, 500);
  }
  const targetUrl = `${apiOrigin}/webhook/consultaunica/actas`;

  // Forwardear headers X-CU-* intactos para que la firma valide igual
  // en el backend.
  const headers = new Headers();
  for (const [key, value] of c.req.raw.headers.entries()) {
    const k = key.toLowerCase();
    if (k === "content-type" || k.startsWith("x-cu-") || k === "user-agent") {
      headers.set(key, value);
    }
  }

  const resp = await fetch(targetUrl, {
    method: "POST",
    headers,
    body: c.req.raw.body,
  });

  // Sólo si el backend firmó OK extraemos la metadata y subimos el PDF
  // a R2. Si falla la firma (401), no tocamos nada.
  if (!resp.ok) {
    const body = await resp.text();
    return new Response(body, {
      status: resp.status,
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  }

  const payload = await resp.json() as {
    ok?: boolean;
    uuid?: string;
    status?: string;
    curp?: string;
    actaType?: string;
    conFolio?: boolean;
    pdfBase64?: string | null;
    dedup?: boolean;
  };

  // Dedup o estado no completado: el backend ya lo registró, nada más que hacer
  if (payload.dedup || payload.status !== "completed" || !payload.pdfBase64) {
    return c.json(payload, 200);
  }

  // Construir R2 key estable. El PDF siempre es pdfBase64 en el body.
  const r2Key = `actas/${payload.curp}/${payload.actaType}${payload.conFolio ? ".foliada" : ""}/${payload.uuid}.pdf`;
  try {
    const pdfBytes = Uint8Array.from(atob(payload.pdfBase64), (ch) => ch.charCodeAt(0));
    await bucket.put(r2Key, pdfBytes, {
      httpMetadata: {
        contentType: "application/pdf",
        cacheControl: "private, max-age=86400",
      },
      customMetadata: {
        uuid: payload.uuid || "",
        curp: payload.curp || "",
        actaType: payload.actaType || "",
        conFolio: String(!!payload.conFolio),
        source: "consultaunica",
      },
    });
  } catch (err) {
    return c.json(
      {
        ok: false,
        error: "backend ok pero R2 upload falló",
        detail: String(err),
        uuid: payload.uuid,
      },
      502
    );
  }

  return c.json(
    {
      ok: true,
      uuid: payload.uuid,
      status: payload.status,
      r2_key: r2Key,
    },
    200
  );
});

// ── Fallback: Static Assets ──────────────────────────────────────────────────
// Debe ir DESPUÉS de todas las rutas específicas (incluido el webhook) para
// que app.all("*") no capture las peticiones antes de que lleguen a su handler.
app.all("*", async (c) => {
  const resp = await c.env.ASSETS.fetch(c.req.raw);
  // No cachear HTML — siempre fresco
  const url = new URL(c.req.url);
  if (url.pathname.endsWith(".html") || url.pathname === "/" || !url.pathname.includes(".")) {
    const newResp = new Response(resp.body, resp);
    newResp.headers.set("Cache-Control", "no-store");
    return newResp;
  }
  return resp;
});

export default app;