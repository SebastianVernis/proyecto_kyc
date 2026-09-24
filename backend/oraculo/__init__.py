"""oraculo — Agente de IA multi-tenant para investigación KYC.

Stack:
  - LLM: Ollama Cloud (gpt-oss:20b por defecto, configurable)
  - Tools: wrappers tipados sobre DuckDB extendido + providers externos
  - Memoria: SQLite por-tenant (oraculo_mem_<tenant_id>.db)
  - Auditoría: SQLite compartido (bases/oraculo_audit.db)
  - Wiki: /home/sebastianvernis/proyectos/kyc/wiki/ (formato llm-wiki/gitnexus)

Diseño de bucle:
  1. Carga memoria del tenant + wiki recall
  2. System prompt + catálogo de tools + contexto de la query
  3. LLM itera (ReAct) hasta `final_answer` o 8 iteraciones
  4. Dedupe + ranking con criterios del skill KYC
  5. Persiste auditoría + actualiza memoria
"""
__version__ = "0.1.0"
