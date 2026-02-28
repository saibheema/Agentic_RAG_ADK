"""Tests for Text-to-SQL guardrails in agent.py.

These test the SQL validation, LIMIT/TOP injection, and normalization
logic without requiring a database connection.
Covers both PostgreSQL and SQL Server modes.
"""

import os

import pytest

from agentic_rag.agent import (
    _inject_limit_if_missing,
    _is_mssql,
    _normalized_sql,
    _validate_readonly_sql,
)

ALLOWED_TABLES = ["orders", "customers", "products", "order_items"]


# ── _normalized_sql ──────────────────────────────────────────────────────────


class TestNormalizedSql:
    def test_collapses_whitespace(self):
        assert _normalized_sql("SELECT  *\n  FROM   orders") == "SELECT * FROM orders"

    def test_strips_leading_trailing(self):
        assert _normalized_sql("  SELECT 1  ") == "SELECT 1"

    def test_empty_string(self):
        assert _normalized_sql("") == ""


# ── _validate_readonly_sql ───────────────────────────────────────────────────


class TestValidateReadonlySql:
    def test_valid_select(self):
        ok, _ = _validate_readonly_sql("SELECT * FROM orders", ALLOWED_TABLES)
        assert ok

    def test_valid_with_cte(self):
        ok, _ = _validate_readonly_sql(
            "WITH cte AS (SELECT * FROM orders) SELECT * FROM cte",
            ALLOWED_TABLES,
        )
        assert ok

    def test_rejects_empty(self):
        ok, reason = _validate_readonly_sql("", ALLOWED_TABLES)
        assert not ok
        assert "empty" in reason.lower()

    def test_rejects_insert(self):
        ok, reason = _validate_readonly_sql(
            "INSERT INTO orders VALUES (1)", ALLOWED_TABLES
        )
        assert not ok

    def test_rejects_select_with_insert(self):
        ok, reason = _validate_readonly_sql(
            "SELECT 1; INSERT INTO orders VALUES (1)", ALLOWED_TABLES
        )
        assert not ok

    def test_rejects_update(self):
        ok, reason = _validate_readonly_sql(
            "UPDATE orders SET status='x'", ALLOWED_TABLES
        )
        assert not ok

    def test_rejects_delete(self):
        ok, reason = _validate_readonly_sql(
            "DELETE FROM orders", ALLOWED_TABLES
        )
        assert not ok

    def test_rejects_drop(self):
        ok, reason = _validate_readonly_sql(
            "SELECT 1; DROP TABLE orders", ALLOWED_TABLES
        )
        assert not ok

    @pytest.mark.parametrize(
        "keyword",
        ["alter", "create", "truncate", "grant", "revoke", "merge", "copy"],
    )
    def test_rejects_blocked_keywords(self, keyword):
        ok, reason = _validate_readonly_sql(
            f"SELECT 1 FROM orders; {keyword.upper()} TABLE orders",
            ALLOWED_TABLES,
        )
        assert not ok

    def test_rejects_multiple_statements(self):
        ok, reason = _validate_readonly_sql(
            "SELECT 1; SELECT 2", ALLOWED_TABLES
        )
        assert not ok
        assert "Multiple" in reason

    def test_allows_trailing_semicolon(self):
        ok, _ = _validate_readonly_sql("SELECT 1 FROM orders;", ALLOWED_TABLES)
        assert ok

    def test_rejects_information_schema(self):
        ok, reason = _validate_readonly_sql(
            "SELECT * FROM information_schema.tables", ALLOWED_TABLES
        )
        assert not ok
        assert "System" in reason

    def test_rejects_pg_catalog(self):
        ok, reason = _validate_readonly_sql(
            "SELECT * FROM pg_catalog.pg_tables", ALLOWED_TABLES
        )
        assert not ok
        assert "System" in reason

    def test_rejects_sys_schema(self):
        """SQL Server sys schema should be blocked."""
        ok, reason = _validate_readonly_sql(
            "SELECT * FROM sys.tables", ALLOWED_TABLES
        )
        assert not ok
        assert "System" in reason

    def test_rejects_sys_columns(self):
        ok, reason = _validate_readonly_sql(
            "SELECT * FROM sys.columns WHERE object_id = 1", ALLOWED_TABLES
        )
        assert not ok
        assert "sys" in reason.lower()

    def test_allows_date_functions(self):
        """DATE_TRUNC and other functions should not trigger false positives."""
        ok, _ = _validate_readonly_sql(
            "SELECT DATE_TRUNC('month', CURRENT_DATE) FROM orders",
            ALLOWED_TABLES,
        )
        assert ok

    def test_allows_subqueries(self):
        ok, _ = _validate_readonly_sql(
            "SELECT * FROM orders WHERE customer_id IN (SELECT customer_id FROM customers)",
            ALLOWED_TABLES,
        )
        assert ok

    def test_allows_aggregations(self):
        ok, _ = _validate_readonly_sql(
            "SELECT COUNT(*), SUM(total_amount) FROM orders GROUP BY status",
            ALLOWED_TABLES,
        )
        assert ok

    def test_allows_joins(self):
        ok, _ = _validate_readonly_sql(
            "SELECT o.order_id, c.full_name FROM orders o JOIN customers c ON o.customer_id = c.customer_id",
            ALLOWED_TABLES,
        )
        assert ok


# ── _inject_limit_if_missing ─────────────────────────────────────────────────


class TestInjectLimit:
    """Tests for PostgreSQL LIMIT injection (DB_TYPE=postgres)."""

    def test_adds_limit_when_missing(self):
        result = _inject_limit_if_missing("SELECT * FROM orders", 100)
        assert result.endswith("LIMIT 100")

    def test_preserves_existing_limit(self):
        sql = "SELECT * FROM orders LIMIT 10"
        result = _inject_limit_if_missing(sql, 100)
        assert "LIMIT 100" not in result
        assert "LIMIT 10" in result

    def test_strips_trailing_semicolon_before_limit(self):
        result = _inject_limit_if_missing("SELECT * FROM orders;", 50)
        assert result.endswith("LIMIT 50")
        assert ";LIMIT" not in result and "; LIMIT" not in result

    def test_case_insensitive_limit_detection(self):
        sql = "SELECT * FROM orders limit 5"
        result = _inject_limit_if_missing(sql, 200)
        assert "LIMIT 200" not in result


# ── SQL Server TOP injection ─────────────────────────────────────────────────


class TestInjectTop:
    """Tests for SQL Server TOP injection (DB_TYPE=mssql)."""

    def setup_method(self):
        """Switch to MSSQL mode for these tests."""
        self._original = os.environ.get("DB_TYPE", "")
        os.environ["DB_TYPE"] = "mssql"
        # Reload the module-level _DB_TYPE
        import agentic_rag.agent as _agent
        _agent._DB_TYPE = "mssql"

    def teardown_method(self):
        """Restore original DB_TYPE."""
        if self._original:
            os.environ["DB_TYPE"] = self._original
        else:
            os.environ.pop("DB_TYPE", None)
        import agentic_rag.agent as _agent
        _agent._DB_TYPE = self._original or "postgres"

    def test_adds_top_when_missing(self):
        result = _inject_limit_if_missing("SELECT * FROM orders", 100)
        assert "SELECT TOP 100 " in result

    def test_preserves_existing_top(self):
        sql = "SELECT TOP 10 * FROM orders"
        result = _inject_limit_if_missing(sql, 200)
        assert "TOP 200" not in result
        assert "TOP 10" in result

    def test_strips_trailing_semicolon(self):
        result = _inject_limit_if_missing("SELECT * FROM orders;", 50)
        assert "TOP 50" in result
        assert result.startswith("SELECT TOP 50 ")

    def test_case_insensitive_top_detection(self):
        sql = "SELECT top 5 * FROM orders"
        result = _inject_limit_if_missing(sql, 200)
        assert "TOP 200" not in result

    def test_with_cte(self):
        """TOP injection should only apply to the outer SELECT, not CTE."""
        sql = "WITH cte AS (SELECT * FROM orders) SELECT * FROM cte"
        result = _inject_limit_if_missing(sql, 100)
        # CTE queries — regex matches first SELECT after WITH block
        assert "TOP 100" in result


# ── _is_mssql detection ─────────────────────────────────────────────────────


class TestIsMssql:
    def _set_db_type(self, value):
        os.environ["DB_TYPE"] = value
        import agentic_rag.agent as _agent
        _agent._DB_TYPE = value.strip().lower()

    def teardown_method(self):
        os.environ.pop("DB_TYPE", None)
        import agentic_rag.agent as _agent
        _agent._DB_TYPE = "postgres"

    def test_postgres_is_not_mssql(self):
        self._set_db_type("postgres")
        assert not _is_mssql()

    def test_mssql_detected(self):
        self._set_db_type("mssql")
        assert _is_mssql()

    def test_sqlserver_detected(self):
        self._set_db_type("sqlserver")
        assert _is_mssql()

    def test_sql_server_detected(self):
        self._set_db_type("sql_server")
        assert _is_mssql()
