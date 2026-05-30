"""Root router agent: delegates to MSSQL / Odoo / RAG sub-agents."""
from __future__ import annotations

import logging

from google.adk.agents import LlmAgent

from agentic_rag.agents.shared import (
    _no_think,
    _router_config,
    _router_model,
    cache_serve_callback,
)
from agentic_rag.agents.mssql_agent import database_agent
from agentic_rag.agents.odoo_agent import odoo_agent
from agentic_rag.agents.rag_agent import rag_agent

_log = logging.getLogger(__name__)


_BASE_ROUTING_INSTRUCTION = (
    "You are a smart routing agent. Analyse the user's question and "
    "delegate to the right specialist — never answer directly yourself.\n\n"
    "• **database_agent** — for: data, numbers, sales, orders, customers, "
    "products, invoices, shipments, promotions, pricing, counts, totals, "
    "rankings, metrics, SQL, tables, greetings, or anything about Ayra's "
    "capabilities.\n\n"
    "• **odoo_agent** — for: any question explicitly about Odoo, ERP, CRM, "
    "Odoo sales orders, Odoo invoices, Odoo partners, Odoo products, Odoo "
    "shipments/deliveries, Odoo purchase orders, Odoo leads, or when the "
    "user says 'in Odoo' / 'from Odoo' / 'Odoo data'.\n\n"
    "• **rag_agent** — for: policies, contracts, handbooks, guidelines, "
    "procedures, or any question answerable from uploaded documents.\n\n"
)


def _selected_db_type(ctx) -> str:
    """Look up the db_type of the session's selected db_alias (lowercased)."""
    try:
        alias = ctx.state.get("db_alias", "")
    except Exception:  # pragma: no cover - defensive
        alias = ""
    if not alias:
        return ""
    try:
        from agentic_rag.connections import get_connection

        conn = get_connection(alias)
        if conn:
            return str(conn.get("db_type", "")).strip().lower()
    except Exception:  # pragma: no cover - defensive
        pass
    return ""


def _router_instruction(ctx) -> str:
    """Dynamic routing instruction that honors the user's selected data source.

    The UI DB selector injects ``db_alias`` into session state. When that alias
    points at the Odoo connection, data questions must go to ``odoo_agent``;
    when it points at a SQL connection, they must go to ``database_agent``.
    This keeps answers consistent with what the user picked in the UI.
    """
    db_type = _selected_db_type(ctx)
    if db_type == "odoo":
        hint = (
            "IMPORTANT — SELECTED DATA SOURCE: The user has selected the "
            "**Odoo** data source. Route ALL data questions (sales, customers, "
            "orders, invoices, products, counts, totals, rankings, metrics) to "
            "**odoo_agent**. Only use database_agent if the user explicitly "
            "asks for SQL Server / YIS data."
        )
    else:
        hint = (
            "IMPORTANT — SELECTED DATA SOURCE: The user has selected a "
            "**SQL (YIS) Server** data source. Route data questions (sales, "
            "customers, orders, invoices, products, counts, totals, rankings, "
            "metrics) to **database_agent**. Only use odoo_agent when the user "
            "explicitly mentions Odoo."
        )
    return _BASE_ROUTING_INSTRUCTION + hint


root_agent = LlmAgent(
    name="agentic_rag_router",
    model=_router_model,
    planner=_no_think,
    generate_content_config=_router_config,
    description="Multi-agent router for Agentic RAG system",
    instruction=_router_instruction,
    before_agent_callback=cache_serve_callback,
    sub_agents=[database_agent, odoo_agent, rag_agent],
)
