"""engine.py — Motor del Oráculo KYC (tool-use nativo de Ollama Cloud).

Usa el endpoint /api/chat de Ollama con `tools=[...]` en formato OpenAI.
El LLM decide qué tool llamar, nosotros ejecutamos y devolvemos el resultado
como role=tool. Cuando el LLM responde sin tool_call, es la respuesta final.

Loop:
  1. Carga memoria del tenant + catálogo de tools en formato OpenAI
  2. LLM responde con tool_call estructurado
  3. Ejecuta tool, agrega resultado al messages como role=tool
  4. Repite hasta que el LLM devuelva respuesta sin tool_call
  5. Parsea la respuesta final como JSON con veredicto
"""
from __future__ import annotations
import os, json, time, sqlite3, re
from datetime import datetime
from pathlib import Path
from typing import Any

from .tools import TOOL_REGISTRY, ToolContext, get_tool_descriptions_for_prompt, ToolDef
from . import memory as oraculo_memory

ORACULO_AUDIT_DB = Path(__file__).resolve().parents[2] / "bases" / "oraculo_audit.db"
ORACULO_AUDIT_DB.parent.mkdir(parents=True, exist_ok=True)


def _init_audit_db():
    con = sqlite3.connect(str(ORACULO_AUDIT_DB))
    con.executescript("""
        CREATE TABLE IF NOT EXISTS queries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id INTEGER, user_id INTEGER, query TEXT,
            started_at TEXT, finished_at TEXT, status TEXT,
            final_json TEXT
        );
        CREATE TABLE IF NOT EXISTS tool_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_id INTEGER, tool TEXT, args_json TEXT,
            results_count INTEGER, duration_ms REAL, error TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_toolcalls_qid ON tool_calls(query_id);
        CREATE INDEX IF NOT EXISTS idx_queries_tenant ON queries(tenant_id, started_at);
    """)
    con.commit(); con.close()

try: _init_audit_db()
except Exception: pass


def _audit_log(query_id: int, tool: str, args: dict, results: dict, dur_ms: float, error: str = ""):
    try:
        con = sqlite3.connect(str(ORACULO_AUDIT_DB))
        con.execute("INSERT INTO tool_calls (query_id, tool, args_json, results_count, duration_ms, error) VALUES (?,?,?,?,?,?)",
                    (query_id, tool, json.dumps(args, ensure_ascii=False, default=str),
                     results.get("count", 0) if isinstance(results, dict) else 0,
                     dur_ms, error))
        con.commit(); con.close()
    except Exception as e:
        print(f"[audit] warn: {e}", flush=True)


def _audit_query(query_id: int, tenant_id: int, user_id: int, query: str,
                 final: dict, started_at: str, finished_at: str, status: str):
    try:
        con = sqlite3.connect(str(ORACULO_AUDIT_DB))
        con.execute("INSERT INTO queries (id, tenant_id, user_id, query, started_at, finished_at, status, final_json) VALUES (?,?,?,?,?,?,?,?)",
                    (query_id, tenant_id, user_id, query, started_at, finished_at, status,
                     json.dumps(final, ensure_ascii=False, default=str)))
        con.commit(); con.close()
    except Exception as e:
        print(f"[audit] warn: {e}", flush=True)


def _load_memory(tenant_id: int) -> str:
    try:
        return oraculo_memory.render_memory_for_prompt(tenant_id)
    except Exception as e:
        return f"(memoria no disponible: {e})"


def _tool_to_openai(t: ToolDef) -> dict:
    return {
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
        }
    }


SYSTEM_PROMPT = """Eres un agente de investigación KYC (Plataforma Encuentra).
Tu trabajo: localizar un sujeto en las bases de datos mexicanas y devolver evidencia trazable.

## Herramientas — Reconciliación por capas
Tienes 15 tools, agrupadas en 7 capas de razonamiento. Debes INVOCARLAS EN ORDEN LÓGICO,
aunque puedes saltarte una capa si ya tienes suficiente evidencia:

  Capa 1: recon_normalize         — parsear la query, quitar partículas
  Capa 2: recon_padron_exact      — match EXACTO en padrón (88.4M)
  Capa 3: recon_padron_variants   — SOLO si capa 2 devolvió 0 hits
  Capa 4: recon_federadas         — RFC base → 29 bases (Santander×6, Telcel×4, etc.)
  Capa 5: recon_cohabitacion      — padrón.domicilio → CFE cohabitantes
  Capa 6: recon_broker            — RENAPO + Singula (gasta ~$52). SOLO si candidato sólido
  Capa 7: recon_sintetizar        — SIEMPRE al final, calcula veredicto y score compuesto

## Reglas inquebrantables
1. SIEMPRE ejecuta capa 1 (recon_normalize) primero para parsear la query.
2. SIEMPRE ejecuta capa 2 (recon_padron_exact) después.
3. Si capa 2 devuelve 0 hits: ejecuta capa 3 (recon_padron_variants) antes de declarar "no existe".
4. Si capa 2 o 3 encuentran al menos 1 candidato: ejecuta capa 4 (recon_federadas) con el CURP.
5. Si capa 4 tiene hits en CFE (b_cfe): ejecuta capa 5 (recon_cohabitacion) con CP+calle del padrón.
6. Si tienes UN candidato sólido (score capa 2 EXACTO): ejecuta capa 6 (recon_broker).
7. SIEMPRE termina con recon_sintetizar pasando los resultados de las 4-6 capas anteriores.
8. **NO filtres por estado** a menos que el usuario lo pida EXPLÍCITAMENTE.
9. La memoria del tenant son HINTS, no restricciones. Si el usuario dice "busca a X", busca a X en TODO el padrón.
10. **MÁXIMO 8 tool calls** antes de dar el veredicto. No sigas buscando indefinidamente.

## Veredicto final — OBLIGATORIO
Tu última respuesta NUNCA debe ser JSON. SIEMPRE debes terminar invocando
recon_sintetizar con los resultados de las capas anteriores como argumento JSON
(ver schema de la tool). La tool devolverá el veredicto con score compuesto,
dedupe y evidencias. NO redactes el veredicto tú mismo.

## Contexto del tenant (HINTS, no restricciones)
__MEMORY__
"""


def _get_llm():
    try:
        from providers.ollama_cloud import OllamaCloudClient
        from config import config
        if not config.ollama_api_key:
            return None
        return OllamaCloudClient(config.ollama_api_key, model=config.ollama_model)
    except Exception:
        return None


def _chat_with_tools(llm, messages: list, tools: list, temperature: float = 0.2, max_tokens: int = 4096) -> dict:
    """Llama /api/chat con tools nativos. Devuelve dict normalizado."""
    import requests
    payload = {
        "model": llm.model,
        "messages": messages,
        "tools": tools,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    r = requests.post(
        "https://ollama.com/api/chat",
        json=payload,
        headers={"Authorization": f"Bearer {llm.api_key}"},
        timeout=180,
    )
    if r.status_code != 200:
        # Debug: qué iteración + qué tool_call rompió
        import sys
        print(f"[ORACULO DEBUG] HTTP {r.status_code}: {r.text[:500]}", file=sys.stderr, flush=True)
        # Buscar el último tool_call que enviamos
        for m in reversed(messages):
            if m.get("role") == "assistant" and m.get("tool_calls"):
                print(f"[ORACULO DEBUG] last tool_calls: {json.dumps(m['tool_calls'], ensure_ascii=False)[:800]}", file=sys.stderr, flush=True)
                break
        return {"error": f"HTTP {r.status_code}: {r.text[:300]}", "raw_status": r.status_code}
    data = r.json()
    msg = data.get("message", {})
    out = {
        "role": "assistant",
        "content": msg.get("content", ""),
        "tool_calls": [],
        "tokens_in": data.get("prompt_eval_count", 0),
        "tokens_out": data.get("eval_count", 0),
        "raw": data,
    }
    for tc in msg.get("tool_calls", []) or []:
        fn = tc.get("function", {})
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try: args = json.loads(args)
            except Exception: args = {"_raw": args}
        out["tool_calls"].append({
            "id": tc.get("id", f"call_{int(time.time()*1000)}"),
            "name": fn.get("name", ""),
            "arguments": args,
        })
    return out


def run(query: str, tenant_id: int = 1, user_id: int = 1, query_id: int | None = None,
        max_iterations: int = 8, llm_model_override: str | None = None) -> dict:
    """Loop principal: tool-use nativo de Ollama Cloud."""
    if query_id is None:
        query_id = int(time.time() * 1000)
    started_at = datetime.utcnow().isoformat() + "Z"
    t0 = time.time()

    llm = _get_llm()
    if llm is None:
        return {"error": "Ollama Cloud no configurado", "query_id": query_id}
    if llm_model_override:
        llm.model = llm_model_override

    ctx = ToolContext(tenant_id=tenant_id, user_id=user_id, query_id=query_id)
    memory = _load_memory(tenant_id)
    system = SYSTEM_PROMPT.replace("__MEMORY__", memory)

    # Solo pasamos al LLM las 7 tools de reconciliación (las core las invoca el sistema
    # internamente; el LLM se confunde si le pasamos las 15 juntas)
    openai_tools = [_tool_to_openai(t) for t in TOOL_REGISTRY.values() if t.name.startswith("recon_")]

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": query},
    ]

    tools_usados: list[dict] = []
    tokens_in = 0
    tokens_out = 0
    final_content = None
    sintetizar_result = None  # si recon_sintetizar corrió, aquí queda su output
    iterations_used = 0

    for i in range(max_iterations):
        iterations_used = i + 1
        r = _chat_with_tools(llm, messages, openai_tools, temperature=0.2, max_tokens=4096)
        if "error" in r and "tool_calls" not in r:
            _audit_query(query_id, tenant_id, user_id, query,
                         {"error": r.get("error")}, started_at,
                         datetime.utcnow().isoformat()+"Z", "llm_error")
            return {"error": r.get("error"), "query_id": query_id}

        tokens_in += r.get("tokens_in", 0)
        tokens_out += r.get("tokens_out", 0)

        asst_msg = {"role": "assistant", "content": r.get("content", "")}
        if r.get("tool_calls"):
            asst_msg["tool_calls"] = [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                for tc in r["tool_calls"]
            ]
        messages.append(asst_msg)

        if not r.get("tool_calls"):
            final_content = r.get("content", "")
            break

        for tc in r["tool_calls"]:
            name = tc["name"]
            args = tc["arguments"] or {}
            call_id = tc["id"]
            tool_t0 = time.time()
            if name not in TOOL_REGISTRY:
                err_result = {"error": f"tool '{name}' no existe", "rows": []}
            else:
                try:
                    err_result = TOOL_REGISTRY[name].fn(args, ctx)
                except Exception as e:
                    err_result = {"error": f"excepción: {e}", "rows": []}
            tool_dur = (time.time()-tool_t0)*1000
            results_count = (err_result.get("count") if isinstance(err_result, dict) else 0) or 0
            tools_usados.append({"tool": name, "args": args, "count": results_count, "duration_ms": round(tool_dur,1)})
            _audit_log(query_id, name, args, err_result, tool_dur,
                       error=err_result.get("error","") if isinstance(err_result, dict) else "")

            # Si esta tool es recon_sintetizar, su output es el veredicto final
            if name == "recon_sintetizar" and isinstance(err_result, dict):
                sintetizar_result = err_result

            result_str = json.dumps(err_result, ensure_ascii=False, default=str)
            if len(result_str) > 6000:
                if isinstance(err_result.get("rows"), list) and len(err_result["rows"]) > 5:
                    err_result["rows"] = err_result["rows"][:5]
                    err_result["_truncated"] = True
                result_str = json.dumps(err_result, ensure_ascii=False, default=str)
            messages.append({"role": "tool", "content": result_str, "name": name})

        # Nudge: si llevamos 4+ iteraciones y aún no cerramos,催促 al LLM
        if iterations_used == 4:
            messages.append({
                "role": "user",
                "content": "Ya tienes evidencia suficiente. NO llames más tools. Responde DIRECTO con el JSON de veredicto final (veredicto_global, candidatos, descartados, confianza, resumen)."
            })

    # Si recon_sintetizar corrió, su output es el veredicto final autoritativo
    if sintetizar_result and isinstance(sintetizar_result, dict) and sintetizar_result.get("veredicto_global"):
        final = {
            "veredicto_global": sintetizar_result.get("veredicto_global", "INCIERTO"),
            "candidatos": sintetizar_result.get("candidatos", []),
            "descartados": sintetizar_result.get("descartados", []),
            "confianza": sintetizar_result.get("confianza", 0.0),
            "resumen": sintetizar_result.get("resumen", ""),
            "scoring_detalle": sintetizar_result.get("scoring_detalle", {}),
        }
    elif final_content is None:
        final = {"veredicto_global": "INCIERTO", "candidatos": [], "descartados": [],
                 "confianza": 0.0, "resumen": f"motor terminó tras {iterations_used} iteraciones sin veredicto JSON",
                 "tools_summary": tools_usados}
    else:
        final = _try_parse_veredicto(final_content)
        if final is None:
            # LLM respondió pero sin JSON estructurado — el content es la respuesta
            # Lo tratamos como veredicto INCIERTO con el raw
            tools_summary = [{"tool": t["tool"], "count": t["count"]} for t in tools_usados]
            final = {
                "veredicto_global": "INCIERTO",
                "candidatos": [],
                "descartados": [],
                "confianza": 0.0,
                "resumen": (final_content or "")[:500],
                "tools_summary": tools_summary,
                "raw_content": final_content[:2000],
            }

    duration_ms = (time.time()-t0)*1000
    finished_at = datetime.utcnow().isoformat() + "Z"

    # Aprender: registrar sujetos candidatos en memoria
    try:
        for c in final.get("candidatos", []):
            cid = c.get("id") or c.get("curp") or c.get("rfc")
            if cid:
                oraculo_memory.remember_sujeto(
                    tenant_id, cid, c.get("nombre_completo", "?"),
                    resumen=f"score={c.get('score')} edo={c.get('estado_probable')}",
                    fuentes=",".join(set(e.get("fuente","?") for e in c.get("evidencia",[]))),
                )
    except Exception:
        pass

    out = {**final,
           "tools_usados": tools_usados,
           "iteraciones": iterations_used,
           "tokens_in": tokens_in,
           "tokens_out": tokens_out,
           "duration_ms": round(duration_ms, 1),
           "query_id": query_id,
           "model": llm.model,
           }
    _audit_query(query_id, tenant_id, user_id, query, out, started_at, finished_at, "ok")
    return out


def _try_parse_veredicto(s: str) -> dict | None:
    if not s: return None
    s = s.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.DOTALL)
    if m: s = m.group(1)
    s = re.sub(r"<thinking>.*?</thinking>", "", s, flags=re.DOTALL).strip()
    try:
        parsed = json.loads(s)
        if isinstance(parsed, dict) and "veredicto_global" in parsed:
            return parsed
    except Exception: pass
    start = s.find("{")
    if start < 0: return None
    depth = 0
    for j in range(start, len(s)):
        if s[j] == "{": depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(s[start:j+1])
                    if isinstance(parsed, dict) and "veredicto_global" in parsed:
                        return parsed
                except Exception:
                    return None
    return None
