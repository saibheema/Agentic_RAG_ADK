"""agentic_rag root agent (thin assembly module).

Kept so ADK discovery (`agentic_rag.agent.root_agent`) and existing imports keep
working. Each database type now lives in its own module under
`agentic_rag.agents`, so users/tenants can be segregated by data source:

  * agents/mssql_agent.py - YIS / MSSQL text-to-SQL (database_agent)
  * agents/odoo_agent.py  - Odoo ERP (odoo_agent)
  * agents/rag_agent.py   - Vertex AI RAG retrieval (rag_agent)
  * agents/router.py      - root router wiring the sub-agents together
  * agents/shared.py      - response cache + model/generation configs
"""
from __future__ import annotations

from agentic_rag.query_rewriter import rewrite_query  # noqa: F401  (kept intentionally)
from agentic_rag.agents.router import root_agent
from agentic_rag.agents.mssql_agent import database_agent, prewarm_schema_cache
from agentic_rag.agents.odoo_agent import odoo_agent
from agentic_rag.agents.rag_agent import rag_agent

__all__ = [
    "root_agent",
    "database_agent",
    "odoo_agent",
    "rag_agent",
    "prewarm_schema_cache",
    "rewrite_query",
]
