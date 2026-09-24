"""Tests for JARVIS persistent memory (MemorySaver checkpoint backend)."""

from __future__ import annotations

import hashlib

import pytest

from app.ai.memory import (
    build_langgraph_config,
    build_session_id,
    clear_conversation_history,
    get_checkpoint_saver,
    get_conversation_history,
)


# ---------------------------------------------------------------------------
# build_session_id
# ---------------------------------------------------------------------------

def test_build_session_id_auto_derives_from_user_branch_role():
    """Same inputs always produce the same 24-char hex session_id."""
    sid1 = build_session_id(user_id=42, branch_id=1, role="manager")
    sid2 = build_session_id(user_id=42, branch_id=1, role="manager")
    assert sid1 == sid2
    assert len(sid1) == 24
    assert all(c in "0123456789abcdef" for c in sid1)


def test_build_session_id_differs_across_roles():
    """Different roles produce different session_ids."""
    sid_mgr = build_session_id(user_id=1, branch_id=1, role="manager")
    sid_wait = build_session_id(user_id=1, branch_id=1, role="waiter")
    assert sid_mgr != sid_wait


def test_build_session_id_differs_across_branches():
    """Different branches produce different session_ids for the same user."""
    sid_b1 = build_session_id(user_id=5, branch_id=1, role="kitchen")
    sid_b2 = build_session_id(user_id=5, branch_id=2, role="kitchen")
    assert sid_b1 != sid_b2


def test_build_session_id_explicit_passthrough():
    """An explicit session_id is returned verbatim (client-controlled thread)."""
    explicit = "my-custom-session-abc"
    sid = build_session_id(user_id=1, branch_id=1, role="waiter", explicit_session_id=explicit)
    assert sid == explicit


def test_build_session_id_none_user_id_is_stable():
    """None user_id is handled gracefully and produces a stable id."""
    sid1 = build_session_id(user_id=None, branch_id=3, role="customer")
    sid2 = build_session_id(user_id=None, branch_id=3, role="customer")
    assert sid1 == sid2


# ---------------------------------------------------------------------------
# build_langgraph_config
# ---------------------------------------------------------------------------

def test_build_langgraph_config_shape():
    """LangGraph config must have configurable.thread_id."""
    cfg = build_langgraph_config("abc123")
    assert cfg == {"configurable": {"thread_id": "abc123"}}


# ---------------------------------------------------------------------------
# get_checkpoint_saver singleton
# ---------------------------------------------------------------------------

def test_get_checkpoint_saver_returns_singleton():
    """MemorySaver is a singleton; repeated calls return the same instance."""
    saver1 = get_checkpoint_saver()
    saver2 = get_checkpoint_saver()
    assert saver1 is saver2


# ---------------------------------------------------------------------------
# get_conversation_history – empty for unknown session
# ---------------------------------------------------------------------------

def test_get_conversation_history_empty_for_unknown_session():
    """History is empty for a session_id that has never been used."""
    history = get_conversation_history("nonexistent-session-xyz-9999")
    assert history == []


# ---------------------------------------------------------------------------
# clear_conversation_history – safe on unknown session
# ---------------------------------------------------------------------------

def test_clear_conversation_history_safe_on_unknown_session():
    """Clearing an unknown session returns 0 deleted and does not raise."""
    deleted = clear_conversation_history("nonexistent-session-xyz-9998")
    assert deleted == 0

