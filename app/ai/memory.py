"""Development conversation memory for the local JARVIS test harness.

The default backend is intentionally process-local. It is not a production
conversation store, but sessions are ownership-checked and bounded so the local
console cannot freely read or delete another user's thread.
"""

from __future__ import annotations

import copy
import hashlib
import logging
import re
from collections import defaultdict
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from app.core.config import settings

logger = logging.getLogger(__name__)

_CHECKPOINT_SAVER: MemorySaver | None = None
_SESSION_OWNERS: dict[str, tuple[int, int, int, str]] = {}
_FALLBACK_HISTORY: dict[str, list[dict[str, Any]]] = defaultdict(list)
_MAX_HISTORY_ENTRIES = 100
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def get_checkpoint_saver() -> MemorySaver:
    global _CHECKPOINT_SAVER
    if _CHECKPOINT_SAVER is None:
        _CHECKPOINT_SAVER = MemorySaver()
        logger.info("JARVIS: development MemorySaver initialised")
    return _CHECKPOINT_SAVER


def _owner_tuple(actor: Any) -> tuple[int, int, int, str]:
    return (int(actor.user_id), int(actor.org_id), int(actor.branch_id), str(actor.role))


def build_session_id(
    user_id: str | int | None,
    branch_id: int,
    role: str,
    explicit_session_id: str | None = None,
    *,
    org_id: int = 1,
) -> str:
    """Return a stable bounded ID for an actor's local conversation."""
    if explicit_session_id:
        if not _SESSION_ID_RE.fullmatch(explicit_session_id):
            raise ValueError("session_id must contain only letters, numbers, '.', '_', ':', or '-'")
        return explicit_session_id
    raw = f"{org_id}:{user_id or 0}:{branch_id}:{role.lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def claim_session(session_id: str, actor: Any) -> None:
    """Bind a new/explicit session to the authenticated actor."""
    owner = _owner_tuple(actor)
    existing = _SESSION_OWNERS.get(session_id)
    if existing is not None and existing != owner:
        raise PermissionError("Conversation session belongs to another actor")
    _SESSION_OWNERS[session_id] = owner


def session_belongs_to(session_id: str, actor: Any) -> bool:
    owner = _SESSION_OWNERS.get(session_id)
    return owner is not None and owner == _owner_tuple(actor)


def assert_session_access(session_id: str, actor: Any) -> None:
    if not session_belongs_to(session_id, actor):
        raise PermissionError("Conversation session is not owned by this actor")


def build_langgraph_config(session_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": session_id}}


def record_fallback_turn(
    session_id: str,
    *,
    user_query: str,
    summary: str,
    role: str,
    branch_id: int,
    recommendations: list[dict[str, Any]] | None = None,
) -> None:
    """Keep a bounded local history when Groq is disabled or unavailable."""
    entries = _FALLBACK_HISTORY[session_id]
    entries.append(
        {
            "user_query": user_query[:settings.ai_max_query_length],
            "summary": summary[:4000],
            "role": role,
            "branch_id": branch_id,
            "recommendations": copy.deepcopy(recommendations or [])[:20],
        }
    )
    if len(entries) > _MAX_HISTORY_ENTRIES:
        del entries[:-_MAX_HISTORY_ENTRIES]


def get_conversation_history(session_id: str) -> list[dict[str, Any]]:
    """Return bounded, de-duplicated local history for a known thread."""
    saver = get_checkpoint_saver()
    history: list[dict[str, Any]] = []

    # Do not index the defaultdict for unknown IDs; doing so creates memory
    # entries for arbitrary requests.
    if session_id in getattr(saver, "storage", {}):
        try:
            checkpoints = list(
                saver.list(config={"configurable": {"thread_id": session_id}})
            )
            # MemorySaver currently returns newest first. The public helper
            # promises oldest first.
            for checkpoint_tuple in reversed(checkpoints):
                channel_values = checkpoint_tuple.checkpoint.get("channel_values", {})
                user_query = str(channel_values.get("user_query", "") or "")
                summary = str(channel_values.get("summary", "") or "")
                if not user_query or not summary:
                    continue
                entry = {
                    "user_query": user_query[:settings.ai_max_query_length],
                    "summary": summary[:4000],
                    "role": str(channel_values.get("role", "")),
                    "branch_id": channel_values.get("branch_id"),
                    "recommendations": copy.deepcopy(channel_values.get("recommendations", []))[:20],
                }
                if history and history[-1]["user_query"] == entry["user_query"] and history[-1]["summary"] == entry["summary"]:
                    continue
                history.append(entry)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read local conversation history: %s", exc.__class__.__name__)

    for entry in _FALLBACK_HISTORY.get(session_id, []):
        if history and history[-1]["user_query"] == entry["user_query"]:
            continue
        history.append(copy.deepcopy(entry))

    return history[-_MAX_HISTORY_ENTRIES:]


def clear_conversation_history(session_id: str) -> int:
    """Delete all local state associated with a session."""
    saver = get_checkpoint_saver()
    count = 0
    try:
        if session_id in getattr(saver, "storage", {}):
            count = sum(
                1
                for namespace in saver.storage[session_id].values()
                for _ in namespace
            )
            delete_thread = getattr(saver, "delete_thread", None)
            if callable(delete_thread):
                delete_thread(session_id)
            else:
                saver.storage.pop(session_id, None)
                for key in list(getattr(saver, "writes", {})):
                    if key[0] == session_id:
                        saver.writes.pop(key, None)
                for key in list(getattr(saver, "blobs", {})):
                    if key[0] == session_id:
                        saver.blobs.pop(key, None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not clear local conversation history: %s", exc.__class__.__name__)
    _FALLBACK_HISTORY.pop(session_id, None)
    _SESSION_OWNERS.pop(session_id, None)
    return count
