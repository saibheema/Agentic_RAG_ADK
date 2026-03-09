"""Tests for server-side error classification helpers.

Covers the _is_overflow() helper used in both run_local.py and server.py
to distinguish context-window-full errors from other Gemini API failures.
"""

import pytest

from agentic_rag.server import _is_overflow


# ── _is_overflow ─────────────────────────────────────────────────────────────


class TestIsOverflow:
    """_is_overflow returns True only for context-window-related error strings."""

    # ── should return True ────────────────────────────────────────────────────

    @pytest.mark.parametrize(
        "msg",
        [
            "Request payload size exceeds the limit",
            "request payload size exceeds the limit",  # case-insensitive
            "400 Request payload size exceeds the limit: 1638400 bytes, max allowed: 1048576 bytes",
            "The context window is full",
            "context window exceeded",
            "Too many tokens in the prompt",
            "too many tokens",
            "token limit reached",
            "exceeds the limit",
            "Maximum context length exceeded",
            "Input too large for this model",
            "Prompt is too long for the given model",
        ],
    )
    def test_detects_overflow(self, msg: str) -> None:
        assert _is_overflow(msg) is True, f"Expected overflow for: {msg!r}"

    # ── should return False ───────────────────────────────────────────────────

    @pytest.mark.parametrize(
        "msg",
        [
            "429 RESOURCE_EXHAUSTED",
            "Internal server error",
            "Database connection refused",
            "Invalid SQL syntax near 'SELECT'",
            "Permission denied",
            "",
            "AI model error. Please try again.",
            "502 Bad Gateway",
        ],
    )
    def test_ignores_other_errors(self, msg: str) -> None:
        assert _is_overflow(msg) is False, f"Expected non-overflow for: {msg!r}"

    def test_empty_string_is_not_overflow(self) -> None:
        assert _is_overflow("") is False

    def test_partial_match_in_long_message(self) -> None:
        long = (
            "Error calling Gemini API: status 400 "
            "Request payload size exceeds the limit: 1638400 bytes. "
            "Please reduce the number of messages in your conversation."
        )
        assert _is_overflow(long) is True
