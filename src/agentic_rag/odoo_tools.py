"""
ADK FunctionTool wrappers for Odoo XML-RPC queries.

All tools are read-only. The Odoo client caches the auth UID in-process
so repeated calls within the same Cloud Run instance pay no auth overhead.

Common Odoo models:
  res.partner          — Customers / suppliers / contacts
  sale.order           — Sales orders (header)
  sale.order.line      — Sales order lines
  account.move         — Invoices / bills (type = 'out_invoice' / 'in_invoice')
  account.move.line    — Invoice lines
  product.template     — Product catalogue (master)
  product.product      — Product variants
  purchase.order       — Purchase orders
  purchase.order.line  — Purchase order lines
  stock.picking        — Transfers / shipments
  stock.move           — Stock moves
  res.users            — Users
  hr.employee          — Employees
  crm.lead             — CRM leads / opportunities
"""

from __future__ import annotations

import logging
from typing import Any

from google.adk.tools import ToolContext

from agentic_rag.odoo_client import get_odoo_client

_log = logging.getLogger(__name__)

# ── Key models exposed to the agent ─────────────────────────────────────────

ODOO_MODEL_HINTS = {
    "res.partner": "customers, suppliers, contacts, companies",
    "sale.order": "sales orders, SO, customer orders",
    "sale.order.line": "sales order lines, SO items",
    "account.move": "invoices, bills, credit notes",
    "account.move.line": "invoice lines",
    "product.template": "products, items, SKUs, catalogue",
    "product.product": "product variants",
    "purchase.order": "purchase orders, PO, vendor orders",
    "purchase.order.line": "purchase order line items",
    "stock.picking": "deliveries, shipments, transfers, receipts",
    "stock.move": "stock movements",
    "crm.lead": "CRM leads, opportunities, pipeline",
    "hr.employee": "employees, staff",
    "res.users": "Odoo users",
}


# ── Structured-result helpers (enable client-side charts) ───────────────────

# Field-name fragments that indicate a monetary value (rendered as currency).
_CURRENCY_HINTS = (
    "amount",
    "price",
    "total",
    "subtotal",
    "revenue",
    "sales",
    "cost",
    "balance",
    "debit",
    "credit",
    "untaxed",
)


def _odoo_header(field: str) -> str:
    """Human-readable column header from an Odoo field name."""
    name = field[:-3] if field.endswith("_id") else field
    name = name.lstrip("_")
    return name.replace("_", " ").strip().title() or field


def _odoo_value_type(field: str) -> str:
    """Classify an aggregated field as 'currency' or 'numeric' for the UI."""
    low = field.lower()
    if any(h in low for h in _CURRENCY_HINTS):
        return "currency"
    return "numeric"


def _odoo_flatten_groups(
    cleaned: list[dict],
    groupby: list,
    aggregate_fields: list,
) -> tuple[list[dict], list[dict]]:
    """Flatten Odoo read_group output into flat rows + columns_meta.

    Returns (rows, columns_meta) where columns_meta entries are
    {key, header, type} — the exact shape the UI uses to render
    type-aware tables and zero-latency inline charts.
    """
    group_fields = [str(g) for g in groupby]
    # Value columns from aggregate_fields (minus group-by fields). Each entry is
    # (source_key, output_key, header, type). Count aggregates (e.g. 'id:count')
    # read from Odoo's '__count' rather than the field itself.
    value_specs: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()
    for af in aggregate_fields:
        parts = str(af).split(":")
        base = parts[0]
        func = parts[1].lower() if len(parts) > 1 else ""
        if base in group_fields:
            continue
        is_count = func == "count" or base == "id"
        out_key = "__count" if is_count else base
        if out_key in seen:
            continue
        seen.add(out_key)
        if is_count:
            value_specs.append(("__count", "__count", "Count", "numeric"))
        else:
            value_specs.append((base, base, _odoo_header(base), _odoo_value_type(base)))

    # Default to record counts when no explicit aggregate was requested.
    use_default_count = not value_specs
    if use_default_count:
        value_specs.append(("__count", "__count", "Count", "numeric"))

    rows: list[dict] = []
    for g in cleaned:
        row: dict[str, Any] = {}
        for f in group_fields:
            v = g.get(f)
            if isinstance(v, (list, tuple)):
                row[f] = v[1] if len(v) >= 2 else (v[0] if v else "—")
            elif v is False or v is None:
                row[f] = "—"
            else:
                row[f] = v
        for src_key, out_key, _h, _t in value_specs:
            row[out_key] = g.get(src_key)
        rows.append(row)

    columns_meta: list[dict] = []
    for f in group_fields:
        columns_meta.append({"key": f, "header": _odoo_header(f), "type": "text"})
    for _src, out_key, header, vtype in value_specs:
        columns_meta.append({"key": out_key, "header": header, "type": vtype})

    return rows, columns_meta


# ── Tools ────────────────────────────────────────────────────────────────────


def query_odoo(
    model: str,
    domain: list,
    fields: list[str],
    limit: int = 80,
    offset: int = 0,
    order: str = "",
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Search and read records from an Odoo model.

    Args:
        model:   Odoo model technical name (e.g. 'sale.order', 'res.partner').
        domain:  Odoo domain filter list. Use [] to return all records.
                 Examples:
                   [['state','=','sale']]                    — confirmed SOs
                   [['partner_id.name','ilike','Acme']]      — by partner name
                   [['date_order','>=','2024-01-01']]         — by date
                   [['invoice_ids','!=',False]]               — has invoices
        fields:  List of field names to return (keeps payload small).
                 Pass [] to get all fields (slower — avoid for large models).
        limit:   Maximum records to return (default 80, max 200).
        offset:  Pagination offset (default 0).
        order:   Sort expression, e.g. 'date_order desc'.

    Returns:
        dict with keys:
          'records': list of record dicts
          'count':   number of records returned (not total matching)
          'model':   model name echoed back
    """
    limit = min(limit, 200)
    try:
        client = get_odoo_client()
        records = client.search_read(
            model=model,
            domain=domain or [],
            fields=fields or None,
            limit=limit,
            offset=offset,
            order=order,
        )
        return {"records": records, "count": len(records), "model": model}
    except Exception as exc:
        _log.error("query_odoo failed model=%s: %s", model, exc)
        return {"error": str(exc), "model": model, "records": []}


def count_odoo(
    model: str,
    domain: list,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Count records matching a domain without fetching data (very fast).

    Args:
        model:  Odoo model technical name.
        domain: Odoo domain filter. Use [] for total count.

    Returns:
        dict with 'count' (int) and 'model'.
    """
    try:
        client = get_odoo_client()
        count = client.search_count(model=model, domain=domain or [])
        return {"count": count, "model": model}
    except Exception as exc:
        _log.error("count_odoo failed model=%s: %s", model, exc)
        return {"error": str(exc), "model": model, "count": 0}


def get_odoo_fields(
    model: str,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Return field definitions for an Odoo model (schema introspection).

    Call this BEFORE building a query whenever you are unsure which fields or
    relationships exist — it makes the query deterministic instead of guessed.

    Returns field name, label, type, and (for relational fields) the target
    model in `relation`. A compact `relations` map (field -> target model) is
    included so you can build dotted-path domains that traverse links, e.g.
    on 'sale.order.line' the field 'order_id' relates to 'sale.order', so
    'order_id.partner_id.name' is a valid domain path.

    Args:
        model: Odoo model technical name (e.g. 'sale.order').

    Returns:
        dict with:
          'fields'     — dict keyed by field name (string/type/required/relation)
          'relations'  — {field: target_model} for many2one/one2many/many2many
          'selections' — {field: [allowed values]} for selection fields
          'model'      — the model name
    """
    try:
        client = get_odoo_client()
        fields = client.fields_get(
            model=model,
            attributes=["string", "type", "required", "readonly", "relation", "selection"],
        )
        relations: dict[str, str] = {}
        selections: dict[str, list] = {}
        for name, meta in fields.items():
            rel = meta.get("relation")
            if rel:
                relations[name] = rel
            sel = meta.get("selection")
            if isinstance(sel, (list, tuple)) and sel:
                # selection is a list of [value, label] pairs — keep the values.
                selections[name] = [s[0] for s in sel if isinstance(s, (list, tuple)) and s]
        return {
            "fields": fields,
            "relations": relations,
            "selections": selections,
            "model": model,
        }
    except Exception as exc:
        _log.error("get_odoo_fields failed model=%s: %s", model, exc)
        return {"error": str(exc), "model": model, "fields": {}}


def aggregate_odoo(
    model: str,
    groupby: list,
    aggregate_fields: list,
    domain: list,
    limit: int = 80,
    orderby: str = "",
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Group and aggregate records from an Odoo model using read_group.

    Use this for analytical queries like "top customers by revenue",
    "total sales per product", "count of orders per state", etc.
    This is much more efficient than fetching all records and summing manually.

    Args:
        model:             Odoo model (e.g. 'sale.order', 'account.move').
        groupby:           Fields to group by (e.g. ['partner_id'] or ['partner_id', 'state']).
        aggregate_fields:  Fields to aggregate with function suffix:
                             'amount_total:sum'  — sum of amount_total
                             'amount_total:max'  — max value
                             'amount_total:avg'  — average
                             'id:count'          — count of records
                           Also include the groupby field names here (without suffix)
                           so they appear in the output.
        domain:            Filter criteria. Use [] for all records.
                           Examples:
                             [['state','in',['sale','done']]]  — confirmed orders only
                             [['date_order','>=','2024-01-01']] — since 2024
        limit:             Max number of groups (default 80, max 200).
        orderby:           Sort by aggregated field, e.g. 'amount_total desc'.
                           Note: use the raw field name (not 'amount_total:sum').

    Returns:
        dict with keys:
          'groups': list of group dicts, each containing:
              - group-by field value(s)
              - aggregated values
              - '__count': number of records in this group
          'count': number of groups returned
          'model': model name

    Example — top 5 customers by total order value:
        aggregate_odoo(
            model='sale.order',
            groupby=['partner_id'],
            aggregate_fields=['partner_id', 'amount_total:sum'],
            domain=[['state','in',['sale','done']]],
            limit=5,
            orderby='amount_total desc',
        )
    """
    limit = min(limit, 200)
    try:
        client = get_odoo_client()
        # Build fields list: groupby names + aggregate_fields (deduplicated)
        all_fields = list(dict.fromkeys(groupby + aggregate_fields))
        groups = client.read_group(
            model=model,
            domain=domain or [],
            fields=all_fields,
            groupby=groupby,
            limit=limit,
            orderby=orderby,
        )
        # Clean up internal Odoo keys that clutter the response
        cleaned = []
        for g in groups:
            clean = {
                k: v
                for k, v in g.items()
                if not k.startswith("__") or k == "__count"
            }
            cleaned.append(clean)
        rows, columns_meta = _odoo_flatten_groups(cleaned, groupby, aggregate_fields)
        query_desc = (
            f"read_group(model={model!r}, groupby={groupby}, "
            f"aggregate={aggregate_fields}, domain={domain or []}, "
            f"orderby={orderby or 'default'}, limit={limit})"
        )
        return {
            "groups": cleaned,
            "count": len(cleaned),
            "model": model,
            # Structured shape consumed by the UI for type-aware tables + charts.
            "ok": True,
            "rows": rows,
            "columns_meta": columns_meta,
            "row_count": len(rows),
            "sql_executed": query_desc,
            "source": "Odoo",
        }
    except Exception as exc:
        _log.error("aggregate_odoo failed model=%s: %s", model, exc)
        return {"error": str(exc), "model": model, "groups": [], "ok": False}


def list_odoo_models(
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Return the list of commonly-used Odoo models with descriptions.

    Call this first when you are unsure which model to query.
    """
    return {
        "models": [
            {"model": k, "description": v} for k, v in ODOO_MODEL_HINTS.items()
        ]
    }
