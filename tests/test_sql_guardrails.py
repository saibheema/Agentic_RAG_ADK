"""Tests for Text-to-SQL guardrails in agent.py.

These test the SQL validation, LIMIT/TOP injection, and normalization
logic without requiring a database connection.
Covers both PostgreSQL and SQL Server modes.
"""

import os

import pytest

from agentic_rag.agent import (
    _check_rbac_access,
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

    def test_rejects_string_agg_distinct(self):
        """STRING_AGG(DISTINCT ...) is not supported and must be blocked."""
        ok, reason = _validate_readonly_sql(
            "SELECT STRING_AGG(DISTINCT product_name, ', ') FROM products",
            ALLOWED_TABLES,
        )
        assert not ok
        assert "STRING_AGG" in reason

    def test_rejects_string_agg_distinct_case_insensitive(self):
        """Guardrail must fire regardless of case."""
        ok, reason = _validate_readonly_sql(
            "SELECT string_agg(distinct product_name, ', ') FROM products",
            ALLOWED_TABLES,
        )
        assert not ok
        assert "STRING_AGG" in reason

    def test_allows_string_agg_without_distinct(self):
        """Plain STRING_AGG (no DISTINCT) is permitted."""
        ok, _ = _validate_readonly_sql(
            "SELECT STRING_AGG(product_name, ', ') FROM products",
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

    def test_select_distinct_top_order(self):
        """SELECT DISTINCT must produce SELECT DISTINCT TOP N, not SELECT TOP N DISTINCT."""
        sql = "SELECT DISTINCT customer_account_number FROM vw_orders"
        result = _inject_limit_if_missing(sql, 100)
        # Must be DISTINCT before TOP — SQL Server syntax rule
        assert result.startswith("SELECT DISTINCT TOP 100 ")
        assert "SELECT TOP 100 DISTINCT" not in result

    def test_select_distinct_multiline(self):
        """Multiline SELECT DISTINCT should also be handled correctly."""
        sql = (
            "SELECT DISTINCT\n"
            "    customer_account_number,\n"
            "    billing_customer_name\n"
            "FROM vw_salesperson_orders_summary\n"
            "WHERE salesperson_name = 'ANNIE MILLER'"
        )
        result = _inject_limit_if_missing(sql, 200)
        assert "SELECT DISTINCT TOP 200" in result
        assert "SELECT TOP 200 DISTINCT" not in result

    def test_select_distinct_preserves_existing_top(self):
        """SELECT DISTINCT TOP N should not get double-injected."""
        sql = "SELECT DISTINCT TOP 10 col FROM tbl"
        result = _inject_limit_if_missing(sql, 100)
        assert "TOP 100" not in result
        assert "TOP 10" in result

    def test_multiline_cte_top_injection(self):
        """CTE with multiline formatting — TOP injected in outer SELECT only."""
        sql = (
            "WITH ranked AS (\n"
            "    SELECT salesperson_name, COUNT(*) AS cnt\n"
            "    FROM vw_orders\n"
            "    GROUP BY salesperson_name\n"
            ")\n"
            "SELECT salesperson_name, cnt\n"
            "FROM ranked\n"
            "ORDER BY cnt DESC"
        )
        result = _inject_limit_if_missing(sql, 100)
        # TOP 100 must appear exactly once
        assert result.count("TOP 100") == 1
        # The inner CTE SELECT must NOT have TOP injected
        assert "SELECT TOP 100 salesperson_name, COUNT" not in result
        # The outer SELECT must have TOP injected
        assert "SELECT TOP 100 salesperson_name, cnt" in result


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


# ── _check_rbac_access ───────────────────────────────────────────────────────


class TestCheckRbacAccess:
    """Tests for the RBAC row-level access guardrail (pure function, no DB)."""

    SQL_WITH_ID  = "SELECT TOP 5 OrderID FROM vw_orders WHERE salesperson_id = 'F1010'"
    SQL_LIKE_ID  = "SELECT TOP 5 OrderID FROM vw_orders WHERE salesperson_id LIKE 'F10%'"
    SQL_NO_ID    = "SELECT TOP 5 OrderID FROM vw_orders"

    # ── Level 1 (Internal) — unrestricted ─────────────────────────────────
    def test_level1_no_salesperson_id_passes(self):
        ok, _ = _check_rbac_access(self.SQL_NO_ID, replevel=1, salesperson_id="")
        assert ok

    def test_level1_with_salesperson_id_still_passes(self):
        """Level 1 users are never restricted even if a salesperson_id is present."""
        ok, _ = _check_rbac_access(self.SQL_NO_ID, replevel=1, salesperson_id="F1010")
        assert ok

    # ── Level 5 (Salesperson) — must include exact ID ────────────────────
    def test_level5_sql_contains_id_passes(self):
        ok, _ = _check_rbac_access(self.SQL_WITH_ID, replevel=5, salesperson_id="F1010")
        assert ok

    def test_level5_sql_missing_id_fails(self):
        ok, msg = _check_rbac_access(self.SQL_NO_ID, replevel=5, salesperson_id="F1010")
        assert not ok
        assert "replevel=5" in msg
        assert "F1010" in msg

    def test_level5_case_insensitive_match(self):
        """ID match should be case-insensitive."""
        sql = "SELECT * FROM vw_orders WHERE salesperson_id = 'f1010'"
        ok, _ = _check_rbac_access(sql, replevel=5, salesperson_id="F1010")
        assert ok

    def test_level5_no_salesperson_id_configured_passes(self):
        """If no salesperson_id is stored in session, don't block (agent will handle)."""
        ok, _ = _check_rbac_access(self.SQL_NO_ID, replevel=5, salesperson_id="")
        assert ok

    def test_level5_whitespace_only_id_treated_as_empty(self):
        """Whitespace-only salesperson_id should be treated as not set."""
        ok, _ = _check_rbac_access(self.SQL_NO_ID, replevel=5, salesperson_id="   ")
        assert ok

    # ── Level 3 (Manager) — must include ID prefix ───────────────────────
    def test_level3_sql_contains_id_passes(self):
        sql = "SELECT * FROM vw_orders WHERE salesperson_id LIKE 'F10%'"
        ok, _ = _check_rbac_access(sql, replevel=3, salesperson_id="F10")
        assert ok

    def test_level3_sql_exact_id_also_passes(self):
        """SQL with an exact = filter containing the ID should also pass level 3."""
        ok, _ = _check_rbac_access(self.SQL_WITH_ID, replevel=3, salesperson_id="F1010")
        assert ok

    def test_level3_sql_missing_id_fails(self):
        ok, msg = _check_rbac_access(self.SQL_NO_ID, replevel=3, salesperson_id="F10")
        assert not ok
        assert "replevel=3" in msg
        assert "F10" in msg

    def test_level3_case_insensitive_match(self):
        sql = "SELECT * FROM vw_orders WHERE salesperson_id LIKE 'f10%'"
        ok, _ = _check_rbac_access(sql, replevel=3, salesperson_id="F10")
        assert ok

    def test_level3_no_salesperson_id_configured_passes(self):
        ok, _ = _check_rbac_access(self.SQL_NO_ID, replevel=3, salesperson_id="")
        assert ok

    # ── Error message content ─────────────────────────────────────────────
    def test_level5_error_mentions_equals_filter(self):
        _, msg = _check_rbac_access(self.SQL_NO_ID, replevel=5, salesperson_id="F1010")
        assert "salesperson_id = 'F1010'" in msg

    def test_level3_error_mentions_like_filter(self):
        _, msg = _check_rbac_access(self.SQL_NO_ID, replevel=3, salesperson_id="F10")
        assert "salesperson_id LIKE 'F10%'" in msg
