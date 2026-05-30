"""RAG agent: Vertex AI RAG Engine document retrieval."""
from __future__ import annotations

import logging
import os
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from agentic_rag.agents.shared import (
    _fast_config,
    _model,
    _no_think,
    cache_serve_callback,
    cache_store_callback,
)

_log = logging.getLogger(__name__)


def retrieve_documents(query: str) -> dict[str, Any]:
    """Search the document knowledge base for relevant information.

    Uses Vertex AI RAG Engine when VERTEX_RAG_CORPUS is configured.
    Returns matching document chunks with source attribution and relevance scores.
    """
    corpus_name = os.environ.get("VERTEX_RAG_CORPUS", "").strip()

    if not corpus_name:
        return {
            "ok": False,
            "error": (
                "Document knowledge base is not configured. "
                "Set VERTEX_RAG_CORPUS=projects/PROJECT/locations/REGION"
                "/ragCorpora/CORPUS_ID to enable document search."
            ),
        }

    try:
        from vertexai.preview import rag

        response = rag.retrieval_query(
            rag_resources=[rag.RagResource(rag_corpus=corpus_name)],
            text=query,
            similarity_top_k=5,
        )
        contexts = []
        if response.contexts and response.contexts.contexts:
            for chunk in response.contexts.contexts:
                ctx = {
                    "text": chunk.text,
                    "source": getattr(chunk, "source_uri", ""),
                }
                score = getattr(chunk, "distance", None) or getattr(
                    chunk, "score", None
                )
                if score is not None:
                    ctx["score"] = round(float(score), 4)
                contexts.append(ctx)

        return {
            "ok": True,
            "query": query,
            "result_count": len(contexts),
            "results": contexts,
        }
    except ImportError:
        return {
            "ok": False,
            "error": (
                "vertexai package not installed. "
                "Add google-cloud-aiplatform to requirements.txt."
            ),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "query": query}


rag_agent = LlmAgent(
    name="rag_agent",
    model=_model,
    planner=_no_think,
    generate_content_config=_fast_config,
    description=(
        "Specialist for document and policy questions. Handles anything about "
        "policies, contracts, handbooks, guidelines, procedures, or any "
        "question answerable from uploaded documents."
    ),
    instruction=(
        "You are a document knowledge assistant.\n"
        "1. Call retrieve_documents with the user's question to search the "
        "knowledge base.\n"
        "2. Synthesize retrieved contexts into a clear, accurate answer.\n"
        "3. Cite which document or source the information came from.\n"
        "If no relevant documents are found or the corpus is not configured, "
        "say so explicitly."
    ),
    before_agent_callback=cache_serve_callback,
    after_model_callback=cache_store_callback,
    tools=[
        FunctionTool(retrieve_documents),
    ],
)
