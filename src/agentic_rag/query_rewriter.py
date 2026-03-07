"""Query Rewriter — Intent.txt rules implementation.

Rewrites user queries to be explicit for downstream database/SQL search:

  Rule 0 — Conversational follow-ups & commands pass through unchanged.
  Rule 1 — Customer account IDs  (A + digits)             → prepend "customer"
  Rule 2 — Salesperson IDs       (non-A capital + digits)  → prepend "salesperson";
            replace "rep" / "representative" with "salesperson"
  Rule 3 — Product item numbers  (5+ bare digits)          → prepend "item"
  Rule 4 — Sales terminology     (turnover/income/revenue  → sales);
            default to "this year" when no time period mentioned on sales queries
  Rule 5 — "top/best[-selling] items" → clarify as ranked by profit margin
  Rule 6 — "top customers" / "best clients" → clarify as annual sales performance

Note on Rule 5 vs Intent.txt §5:
  Intent.txt says "number of customers purchased"; Instructions And Rules.txt §13
  says "profit margin". The Instructions doc is the authoritative source and takes
  precedence — profit margin is used here.
"""

from __future__ import annotations

import re

# ── Conversational follow-up detection ──────────────────────────────────────
# If ANY of these patterns match at the START (or as the full) query, return unchanged.

_CONVERSATIONAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"^show\s+me\s+more",
        r"^can\s+you\s+filter",
        r"^(and\s+)?for\s+last\s+(year|month|week|quarter|day)",
        r"^and\s+for\s+",
        r"^sort\s+(that\s+)?by",
        r"^filter\s+that\s+by",
        r"^also\s+show",
        r"^also\s+include",
        r"^now\s+show",
        r"^(the\s+)?(second|third|fourth|fifth|first|last)\s+(one|customer|order|product|salesperson)",
        r"^(that|those|the\s+same)\s+(customer|salesperson|product|order)",
        r"^include\s+only",
        r"^exclude\s+",
        r"^add\s+a\s+filter",
        r"^what\s+about\s+the\s+(previous|next|other)",
    ]
]

# ── Regex patterns ────────────────────────────────────────────────────────────

# Rule 1: Customer ID — uppercase A followed by 4+ digits (e.g. A024874, A123456)
_CUSTOMER_ID_RE = re.compile(r"\b(A\d{4,})\b")

# Rule 2: Salesperson ID — uppercase letter B–Z followed by 3+ digits,
#          optionally followed by more uppercase letters (e.g. F1010, S1133AR, F1010AR)
_SALESPERSON_ID_RE = re.compile(r"\b([B-Z]\d{3,}[A-Z]*)\b")

# Rule 2: rep / representative → salesperson
_REP_WORD_RE = re.compile(r"\b(rep|representative)\b", re.IGNORECASE)

# Rule 3: Bare 5+ digit item number not already next to a product/item/SKU word
_ITEM_NUMBER_RE = re.compile(r"\b(\d{5,})\b")

# Rule 4: Replace turnover / income / revenue with "sales"
_TURNOVER_RE = re.compile(r"\b(turnover|income|revenue)\b", re.IGNORECASE)

# Rule 4: Time period already present — skip adding "this year"
_HAS_TIME_RE = re.compile(
    r"\b(year|month|week|day|today|yesterday|last|this|next|quarter|annual|ytd|"
    r"january|february|march|april|may|june|july|august|september|october|november|december|"
    r"\d{4})\b",
    re.IGNORECASE,
)

# Rule 4: Trigger "this year" default only on sales-context queries
_SALES_CONTEXT_RE = re.compile(
    r"\b(sales|orders?|invoices?|shipments?|purchases?)\b",
    re.IGNORECASE,
)

# Rule 5: top / best / best-selling items
_TOP_ITEMS_RE = re.compile(
    r"\b(top|best)(\s+selling|\-selling)?\s+items?\b",
    re.IGNORECASE,
)

# Rule 6: top customers / best clients
_TOP_CUSTOMERS_RE = re.compile(
    r"\b(top\s+customers?|best\s+clients?)\b",
    re.IGNORECASE,
)


def _is_conversational(query: str) -> bool:
    """Return True if the query is a conversational follow-up or command."""
    stripped = query.strip()
    for pat in _CONVERSATIONAL_PATTERNS:
        if pat.match(stripped):
            return True
    return False


def rewrite_query(query: str) -> dict:
    """Rewrite the user query to be explicit for downstream database search.

    Apply normalization rules from Intent.txt:
      - Detect customer IDs (A + digits) and ensure 'customer' appears near them.
      - Detect salesperson IDs (capital B-Z + digits) and ensure 'salesperson' appears.
      - Replace 'rep' / 'representative' with 'salesperson'.
      - Detect bare product item numbers (5+ digits) and prepend 'item'.
      - Replace 'turnover' / 'income' / 'revenue' with 'sales'.
      - Default to 'this year' when no time period is mentioned on sales queries.
      - Clarify 'top/best items' as profit-margin ranking.
      - Clarify 'top customers' / 'best clients' as annual sales performance.
      - Conversational follow-ups and commands pass through unchanged.

    Args:
        query: The raw user question.

    Returns:
        dict with keys:
          rewritten    — The (possibly rewritten) query string.
          original     — The original unchanged query.
          was_rewritten — True if any rule was applied.
    """
    if not query or not query.strip():
        return {"rewritten": query, "original": query, "was_rewritten": False}

    # ── Rule 0: Conversational / command follow-ups ──────────────────────────
    if _is_conversational(query):
        return {"rewritten": query, "original": query, "was_rewritten": False}

    q = query

    # ── Rule 2a: rep / representative → salesperson (before ID detection) ───
    q = _REP_WORD_RE.sub("salesperson", q)

    # ── Rule 1: Customer Account Number ──────────────────────────────────────
    def _add_customer(m: re.Match) -> str:  # type: ignore[type-arg]
        id_val = m.group(1)
        # Peek at the ±30-char context in the current string
        ctx_start = max(0, m.start() - 30)
        ctx_end = min(len(q), m.end() + 30)
        ctx = q[ctx_start:ctx_end].lower()
        if "customer" in ctx:
            return id_val  # already labelled
        return f"customer {id_val}"

    q = _CUSTOMER_ID_RE.sub(_add_customer, q)

    # ── Rule 2b: Salesperson ID ───────────────────────────────────────────────
    def _add_salesperson(m: re.Match) -> str:  # type: ignore[type-arg]
        id_val = m.group(1)
        ctx_start = max(0, m.start() - 35)
        ctx_end = min(len(q), m.end() + 25)
        ctx = q[ctx_start:ctx_end].lower()
        if "salesperson" in ctx:
            return id_val  # already labelled
        return f"salesperson {id_val}"

    q = _SALESPERSON_ID_RE.sub(_add_salesperson, q)

    # ── Rule 3: Product item numbers (5+ bare digits) ────────────────────────
    def _add_item(m: re.Match) -> str:  # type: ignore[type-arg]
        num = m.group(1)
        ctx_start = max(0, m.start() - 25)
        ctx_end = min(len(q), m.end() + 25)
        ctx = q[ctx_start:ctx_end].lower()
        if any(w in ctx for w in ("product", "item", "sku")):
            return num  # already labelled
        return f"item {num}"

    q = _ITEM_NUMBER_RE.sub(_add_item, q)

    # ── Rule 4a: Terminology — turnover / income / revenue → sales ───────────
    q = _TURNOVER_RE.sub("sales", q)

    # ── Rule 4b: Default to "this year" when no time period mentioned ─────────
    if _SALES_CONTEXT_RE.search(q) and not _HAS_TIME_RE.search(q):
        q = q.rstrip("?!. ") + " for this year"

    # ── Rule 5: Top / best-selling items → profit margin ranking ─────────────
    q = _TOP_ITEMS_RE.sub("top items (ranked by profit margin)", q)

    # ── Rule 6: Top customers / best clients → annual sales performance ───────
    q = _TOP_CUSTOMERS_RE.sub("top customers (by annual sales performance)", q)

    was_rewritten = q != query
    return {"rewritten": q, "original": query, "was_rewritten": was_rewritten}
