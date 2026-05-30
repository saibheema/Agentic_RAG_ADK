"""Shared infrastructure for agentic_rag sub-agents.

Access-scoped response cache + model/planner/generation configs reused by the
MSSQL, Odoo, and RAG agents. Pure infrastructure: no agent definitions here.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from typing import Any

from google.adk.planners import BuiltInPlanner
from google.genai import types

_log = logging.getLogger(__name__)


# ── Response cache (access-scoped) ───────────────────────────────────────────
# Caches the final answer text for an exact
#   (db_alias, replevel, salesperson_id, normalized_query)
# tuple so repeat questions return in <100ms with ZERO LLM calls.
#
# SECURITY: access context (replevel + salesperson_id + db_alias) is part of the
# key, so a salesperson can NEVER receive another user's row-filtered data — two
# users asking the same question with different access levels get different keys.
# TTL bounds staleness for date-relative queries ('this year', 'today').
_RESPONSE_CACHE_TTL = int(os.environ.get("RESPONSE_CACHE_TTL_SECONDS", "300"))
_RESPONSE_CACHE_MAX = int(os.environ.get("RESPONSE_CACHE_MAX", "512"))
_response_cache: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
_response_cache_lock = threading.Lock()


def _norm_query(text: str) -> str:
    """Lowercase + collapse whitespace so trivially different phrasings share a key."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _response_cache_key(state: Any, query: str) -> str:
    raw = "|".join(
        [
            str(state.get("db_alias", "")),
            str(state.get("replevel", 1)),
            str(state.get("salesperson_id", "")),
            _norm_query(query),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _user_query_from_ctx(callback_context: Any) -> str:
    """Extract the user's latest message text from a callback context."""
    content = getattr(callback_context, "user_content", None)
    parts = getattr(content, "parts", None) if content else None
    if not parts:
        return ""
    return " ".join(p.text for p in parts if getattr(p, "text", None)).strip()


def cache_serve_callback(callback_context):
    """Root-agent before_agent_callback: serve a cached answer with zero LLM calls.

    On a hit, returns the recorded final-answer Content directly — the entire
    invocation (router + every sub-agent) is skipped. The UI renders the last
    text event regardless of author, so the response is identical to a live run.
    """
    if _RESPONSE_CACHE_TTL <= 0:
        return None
    query = _user_query_from_ctx(callback_context)
    if not query:
        return None
    key = _response_cache_key(callback_context.state, query)
    with _response_cache_lock:
        entry = _response_cache.get(key)
        if entry and (time.time() - entry["at"]) < _RESPONSE_CACHE_TTL:
            _response_cache.move_to_end(key)
            text = entry["text"]
        else:
            if entry:
                _response_cache.pop(key, None)
            text = None
    if text is None:
        return None
    _log.info("Response cache HIT (key=%s…)", key[:12])
    return types.Content(role="model", parts=[types.Part(text=text)])


def cache_store_callback(callback_context, llm_response):
    """Sub-agent after_model_callback: store the final answer text in the cache.

    Only the final natural-language / JSON answer is cached — tool-call turns
    (function_call parts), partial chunks, empty content, and error envelopes
    are skipped. Returns None so the live response is never altered.
    """
    if _RESPONSE_CACHE_TTL <= 0:
        return None
    if getattr(llm_response, "partial", False):
        return None
    content = getattr(llm_response, "content", None)
    parts = getattr(content, "parts", None) if content else None
    if not parts:
        return None
    # Skip tool-call turns — only the final answer turn has text and no calls.
    if any(getattr(p, "function_call", None) for p in parts):
        return None
    text = " ".join(p.text for p in parts if getattr(p, "text", None)).strip()
    if not text:
        return None
    # Never cache an error envelope — DB/connection errors are transient.
    if re.search(r'"error"\s*:\s*"', text):
        return None
    query = _user_query_from_ctx(callback_context)
    if not query:
        return None
    key = _response_cache_key(callback_context.state, query)
    with _response_cache_lock:
        _response_cache[key] = {"text": text, "at": time.time()}
        _response_cache.move_to_end(key)
        while len(_response_cache) > _RESPONSE_CACHE_MAX:
            _response_cache.popitem(last=False)
    _log.info("Response cached (key=%s…, %d chars)", key[:12], len(text))
    return None


_model = os.environ.get("AGENT_MODEL", "gemini-2.5-flash")
# Lightweight model for the router — it only picks between 2 sub-agents
# NOTE: gemini-2.5-flash-lite has hidden rate limits (no explicit quota in Vertex AI);
#       gemini-2.5-flash maps to the gemini-2.5-flash-ga quota bucket (10B tokens/day, no RPM cap)
_router_model = os.environ.get("ROUTER_MODEL", "gemini-2.5-flash")

# ── Token / thinking budget settings (tunable via env vars or Secret Manager) ─
# Update these in Cloud Run env vars (or Secret Manager) without redeploying.
_db_thinking_budget = int(os.environ.get("DB_AGENT_THINKING_BUDGET", "8192"))
_db_max_output_tokens = int(os.environ.get("DB_AGENT_MAX_OUTPUT_TOKENS", "8192"))
_rag_max_output_tokens = int(os.environ.get("RAG_AGENT_MAX_OUTPUT_TOKENS", "2048"))
_router_max_output_tokens = int(os.environ.get("ROUTER_MAX_OUTPUT_TOKENS", "256"))

# Disable extended thinking on all agents: saves 5-15s per LLM call.
# Cap output tokens to reduce generation time (SQL answers rarely exceed 1k).
_no_think = BuiltInPlanner(
    thinking_config=types.ThinkingConfig(thinking_budget=0)
)
_light_think = BuiltInPlanner(
    thinking_config=types.ThinkingConfig(thinking_budget=_db_thinking_budget)
)
_fast_config = types.GenerateContentConfig(
    max_output_tokens=_rag_max_output_tokens,
)
# Database agent needs more headroom: thinking tokens + SQL + result table.
# temperature=0 + fixed seed make SQL generation DETERMINISTIC: the same
# question always produces the same query (no more run-to-run view/column drift).
_db_agent_config = types.GenerateContentConfig(
    max_output_tokens=_db_max_output_tokens,
    temperature=0.0,
    top_p=1.0,
    seed=42,
)
# Router must pick the SAME sub-agent for the same question every time.
_router_config = types.GenerateContentConfig(
    max_output_tokens=_router_max_output_tokens,
    temperature=0.0,
    top_p=1.0,
    seed=42,
)
