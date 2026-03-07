"""Tests for the Query Rewriter (Intent.txt rules).

Covers all six rules plus the conversational follow-up pass-through.

Rule 0  — Conversational follow-ups & commands → unchanged
Rule 1  — Customer IDs (A + digits)             → prepend 'customer'
Rule 2a — rep / representative                  → salesperson
Rule 2b — Salesperson IDs (B-Z + digits)        → prepend 'salesperson'
Rule 3  — Bare 5+ digit item numbers            → prepend 'item'
Rule 4a — turnover / income / revenue           → sales
Rule 4b — No time period on sales query         → append 'for this year'
Rule 5  — top / best items                      → clarify as profit margin
Rule 6  — top customers / best clients          → clarify as annual sales
"""

from agentic_rag.query_rewriter import rewrite_query


# ── Helpers ──────────────────────────────────────────────────────────────────


def _rw(query: str) -> str:
    """Shorthand — return just the rewritten string."""
    return rewrite_query(query)["rewritten"]


def _changed(query: str) -> bool:
    r = rewrite_query(query)
    return r["was_rewritten"]


# ── Rule 0: Conversational / command pass-through ────────────────────────────


class TestConversationalPassthrough:
    def test_show_me_more(self):
        q = "show me more about the second one"
        assert not _changed(q)
        assert _rw(q) == q

    def test_can_you_filter(self):
        q = "can you filter that by date?"
        assert not _changed(q)

    def test_and_for_last_year(self):
        q = "and for last year?"
        assert not _changed(q)

    def test_and_for_last_month(self):
        q = "and for last month"
        assert not _changed(q)

    def test_sort_by(self):
        q = "sort that by date"
        assert not _changed(q)

    def test_filter_that_by(self):
        q = "filter that by status"
        assert not _changed(q)

    def test_empty_query(self):
        r = rewrite_query("")
        assert r["was_rewritten"] is False
        assert r["rewritten"] == ""

    def test_return_keys_present(self):
        r = rewrite_query("what are my sales")
        assert "rewritten" in r
        assert "original" in r
        assert "was_rewritten" in r


# ── Rule 1: Customer Account Number ─────────────────────────────────────────


class TestCustomerIDRule:
    def test_bare_id_gets_customer_label(self):
        result = _rw("what is A024874 sales")
        assert "customer A024874" in result

    def test_no_double_customer(self):
        # 'customer' already present — should NOT add a second one
        query = "show customer A024874 last order"
        result = _rw(query)
        assert result.lower().count("customer") == 1

    def test_customer_after_id_also_skipped(self):
        # 'customer' in a context window 30 chars after the ID
        query = "show A024874 customer summary"
        result = _rw(query)
        assert result.lower().count("customer") == 1

    def test_long_id(self):
        result = _rw("query A1234567")
        assert "customer A1234567" in result

    def test_short_a_number_not_matched(self):
        # A + 3 digits should NOT be treated as a customer ID (too short)
        result = _rw("show A123 results")
        assert "customer A123" not in result

    def test_was_rewritten_true(self):
        assert _changed("show A024874 status")


# ── Rule 2a: rep / representative → salesperson ──────────────────────────────


class TestRepReplacementRule:
    def test_rep_replaced(self):
        result = _rw("show me rep F1010 stats")
        assert "rep " not in result
        assert "salesperson" in result.lower()

    def test_representative_replaced(self):
        result = _rw("show me representative S1133AR stats")
        assert "representative" not in result
        assert "salesperson" in result.lower()

    def test_rep_case_insensitive(self):
        result = _rw("show REP F1010 orders")
        assert "REP " not in result
        assert "salesperson" in result.lower()


# ── Rule 2b: Salesperson ID ───────────────────────────────────────────────────


class TestSalespersonIDRule:
    def test_bare_id_gets_salesperson_label(self):
        result = _rw("performance of F1010AR")
        assert "salesperson F1010AR" in result or "salesperson" in result.lower()

    def test_manager_context_still_gets_salesperson(self):
        # Example from spec: "what about manager F1010" → has "salesperson" near F1010
        result = _rw("what about manager F1010")
        assert "salesperson" in result.lower()
        assert "F1010" in result

    def test_no_double_salesperson_label(self):
        query = "show salesperson F1010 orders"
        result = _rw(query)
        assert result.lower().count("salesperson") == 1

    def test_id_with_suffix_letters(self):
        result = _rw("show S1133AR data")
        assert "salesperson" in result.lower()
        assert "S1133AR" in result

    def test_a_prefix_not_salesperson(self):
        # A + digits is a CUSTOMER id, not a salesperson id
        result = _rw("show A024874 data")
        assert "customer A024874" in result
        # Must NOT also get salesperson label
        assert "salesperson A024874" not in result

    def test_was_rewritten_true(self):
        assert _changed("performance of F1010AR")


# ── Rule 3: Product item numbers ─────────────────────────────────────────────


class TestItemNumberRule:
    def test_bare_5digit_number_gets_item(self):
        result = _rw("what is the price of 11225")
        assert "item 11225" in result

    def test_product_context_already_present(self):
        result = _rw("show product 11225 details")
        # 'product' already in context — should NOT add another label
        assert result.count("11225") == 1
        # Either 'product' or 'item' must be present (it already was)
        assert "product" in result.lower() or "item" in result.lower()

    def test_item_context_already_present(self):
        result = _rw("show item 11225 details")
        assert result.lower().count("item") == 1  # no double 'item item'

    def test_6digit_number(self):
        result = _rw("look up 123456 pricing")
        assert "item 123456" in result

    def test_4digit_number_not_matched(self):
        # 4-digit numbers should NOT be treated as item numbers
        result = _rw("show 1234 results")
        assert "item 1234" not in result

    def test_was_rewritten_true(self):
        assert _changed("price of 11225")


# ── Rule 4a: Sales terminology standardisation ───────────────────────────────


class TestSalesTerminologyRule:
    def test_turnover_replaced(self):
        result = _rw("show me the turnover for last month")
        assert "sales" in result
        assert "turnover" not in result

    def test_revenue_replaced(self):
        result = _rw("total revenue last year")
        assert "sales" in result
        assert "revenue" not in result

    def test_income_replaced(self):
        result = _rw("what is the income for Q3 last year")
        assert "sales" in result
        assert "income" not in result.lower()

    def test_case_insensitive_replace(self):
        result = _rw("TURNOVER this year")
        assert "TURNOVER" not in result
        assert "sales" in result.lower()


# ── Rule 4b: Default to "this year" for sales queries ───────────────────────


class TestDefaultTimeRule:
    def test_adds_this_year_for_sales(self):
        result = _rw("what are my sales")
        assert "this year" in result

    def test_adds_this_year_for_orders(self):
        result = _rw("show my orders")
        assert "this year" in result

    def test_no_duplicate_when_year_present_explicit(self):
        result = _rw("what are my sales this year")
        assert result.count("this year") == 1

    def test_no_duplicate_when_last_year(self):
        result = _rw("sales last year")
        # time period already present — should NOT append "for this year"
        assert result.count("this year") == 0

    def test_no_year_added_for_non_sales_query(self):
        result = _rw("who are the top managers")
        assert "this year" not in result

    def test_specific_month_prevents_addition(self):
        result = _rw("show my sales for January")
        assert result.count("this year") == 0


# ── Rule 5: Top items clarification ──────────────────────────────────────────


class TestTopItemsRule:
    def test_top_items_clarified(self):
        result = _rw("show me the top items")
        assert "profit margin" in result.lower()

    def test_best_items_clarified(self):
        result = _rw("what are the best items this year")
        assert "profit margin" in result.lower()

    def test_best_selling_items_clarified(self):
        result = _rw("show me the best-selling items")
        assert "profit margin" in result.lower()

    def test_best_selling_space(self):
        result = _rw("show best selling items this month")
        assert "profit margin" in result.lower()

    def test_was_rewritten_true(self):
        assert _changed("show top items")


# ── Rule 6: Top customers clarification ──────────────────────────────────────


class TestTopCustomersRule:
    def test_top_customers_clarified(self):
        result = _rw("who are the top customers")
        assert "annual sales performance" in result.lower()

    def test_top_customer_singular(self):
        result = _rw("show me the top customer")
        assert "annual sales performance" in result.lower()

    def test_best_clients_clarified(self):
        result = _rw("show me the best clients")
        assert "annual sales performance" in result.lower()

    def test_best_client_singular(self):
        result = _rw("who is the best client")
        assert "annual sales performance" in result.lower()

    def test_was_rewritten_true(self):
        assert _changed("top customers this year")


# ── Combined rules ────────────────────────────────────────────────────────────


class TestCombinedRules:
    def test_customer_id_and_revenue(self):
        """Rule 1 + Rule 4a: customer ID + revenue → customer label + sales"""
        result = _rw("what is A024874 revenue")
        assert "customer A024874" in result
        assert "sales" in result
        assert "revenue" not in result

    def test_salesperson_id_and_orders_default_year(self):
        """Rule 2 + Rule 4b: salesperson ID + orders (no time) → salesperson label + this year"""
        result = _rw("show F1010 orders")
        assert "salesperson" in result.lower()
        assert "this year" in result

    def test_item_number_and_turnover(self):
        """Rule 3 + Rule 4a: item number + turnover → item label + sales"""
        result = _rw("what is the turnover from item 11225")
        assert "sales" in result
        assert "turnover" not in result

    def test_rep_and_top_customers(self):
        """Rule 2a + Rule 6: rep → salesperson, then top customers → annual sales"""
        result = _rw("show rep F1010 top customers")
        assert "salesperson" in result.lower()
        assert "annual sales performance" in result.lower()
