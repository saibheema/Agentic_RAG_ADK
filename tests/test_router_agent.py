"""Tests for the multi-agent structure in agent.py.

Verifies the agent hierarchy (router -> database_agent + rag_agent),
tool assignments, and sub-agent configuration.
"""

from agentic_rag.agent import database_agent, rag_agent, root_agent


def test_root_agent_has_two_sub_agents() -> None:
    assert len(root_agent.sub_agents) == 2
    names = {a.name for a in root_agent.sub_agents}
    assert names == {"database_agent", "rag_agent"}


def test_root_agent_has_no_direct_tools() -> None:
    assert root_agent.tools is None or len(root_agent.tools) == 0


def test_database_agent_has_sql_tools() -> None:
    tool_names = {t.name for t in database_agent.tools}
    assert "get_schema_metadata" in tool_names
    assert "run_readonly_sql" in tool_names


def test_rag_agent_has_retrieve_tool() -> None:
    tool_names = {t.name for t in rag_agent.tools}
    assert "retrieve_documents" in tool_names


def test_agents_use_same_model() -> None:
    # database_agent and rag_agent use the same heavy model.
    # root_agent (router) intentionally uses a lighter model to reduce latency.
    assert database_agent.model == rag_agent.model
    assert root_agent.model != database_agent.model


def test_database_agent_description_mentions_sql() -> None:
    assert "sql" in database_agent.description.lower()


def test_rag_agent_description_mentions_documents() -> None:
    assert "document" in rag_agent.description.lower()
