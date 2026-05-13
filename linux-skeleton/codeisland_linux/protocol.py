from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_socket_path() -> str:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return str(Path(runtime_dir) / "codeislandd.sock")
    fallback_runtime_dir = Path("/tmp") / f"codeisland-{os.getuid()}"
    return str(fallback_runtime_dir / "codeislandd.sock")


SESSION_STATUSES = {
    "idle",
    "running",
    "waiting_approval",
    "waiting_answer",
    "completed",
    "failed",
    "cancelled",
}

TASK_STATUSES = {"queued", "running", "blocked", "done", "error", "cancelled"}
INTERACTION_TYPES = {"approval", "question"}
INTERACTION_STATES = {"open", "answered", "expired", "cancelled"}
ACTIVITY_EVENT_KINDS = {
    "prompt.submitted",
    "tool.use.started",
    "tool.use.completed",
    "tool.use.failed",
    "permission.requested",
    "permission.resolved",
    "question.requested",
    "question.answered",
    "question.rejected",
    "assistant.response.completed",
    "completion.enqueued",
}

SNAPSHOT_ACTIVITY_SESSION_LIMIT = 8
SNAPSHOT_ACTIVITY_TOTAL_LIMIT = 80


@dataclass(slots=True)
class Session:
    session_id: str
    provider: str
    project_root: str | None
    title: str
    status: str
    current_task_id: str | None
    workspace_hint: str | None
    terminal_app: str | None
    terminal_pid: int | None
    terminal_pane: str | None
    terminal_socket: str | None
    cli_pid: int | None
    created_at: str
    updated_at: str
    last_event_at: str
    ended_at: str | None


@dataclass(slots=True)
class Task:
    task_id: str
    session_id: str
    prompt: str | None
    status: str
    started_at: str | None
    ended_at: str | None
    error_code: str | None
    error_message: str | None


@dataclass(slots=True)
class Interaction:
    interaction_id: str
    session_id: str
    task_id: str | None
    type: str
    prompt_text: str
    options: list[str] | None
    state: str
    asked_at: str
    answered_at: str | None
    answer_payload: dict[str, Any] | None


@dataclass(slots=True)
class EventEnvelope:
    event_id: str
    session_id: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=now_iso)
    task_id: str | None = None
    seq: int | None = None


@dataclass(slots=True)
class DerivedSessionState:
    session_id: str
    effective_status: str
    current_task_id: str | None
    pending_interaction_id: str | None
    pending_interaction_kind: str | None
    active_tool_name: str | None
    active_tool_status: str | None
    last_user_prompt: str | None
    last_assistant_message: str | None
    last_activity_kind: str | None
    last_activity_at: str | None
    completion_pending: bool
    completion_enqueued_at: str | None


@dataclass(slots=True)
class IslandSurfaceState:
    surface: str
    active_session_id: str | None
    active_task_id: str | None
    active_interaction_id: str | None
    status: str
    auto_reveal: bool
    approval_queue: list[str]
    question_queue: list[str]
    completion_queue: list[str]
    rotation_queue: list[str]
    updated_at: str | None


def to_json_line(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


def from_json_line(raw: bytes) -> dict[str, Any]:
    return json.loads(raw.decode("utf-8"))


def dataclass_dict(value: Any) -> dict[str, Any]:
    return asdict(value)


def project_recent_activities(
    activities: list[EventEnvelope],
    *,
    per_session_limit: int = SNAPSHOT_ACTIVITY_SESSION_LIMIT,
    total_limit: int = SNAPSHOT_ACTIVITY_TOTAL_LIMIT,
) -> list[EventEnvelope]:
    if per_session_limit <= 0 or total_limit <= 0:
        return []

    kept: list[EventEnvelope] = []
    counts_by_session: dict[str, int] = {}
    for activity in reversed(activities):
        if len(kept) >= total_limit:
            break

        session_count = counts_by_session.get(activity.session_id, 0)
        if session_count >= per_session_limit:
            continue

        counts_by_session[activity.session_id] = session_count + 1
        kept.append(activity)

    kept.reverse()
    return kept


def _stamp_for(value: Any) -> str:
    for field_name in ("updated_at", "last_event_at", "asked_at", "completion_enqueued_at", "started_at", "created_at"):
        stamp = getattr(value, field_name, None)
        if isinstance(stamp, str) and stamp:
            return stamp
    return ""


def _session_for_id(sessions: dict[str, Session], session_id: str | None) -> Session | None:
    if not session_id:
        return None
    session = sessions.get(session_id)
    if session is None or session.ended_at:
        return None
    return session


def _session_status(session: Session, session_states: dict[str, DerivedSessionState]) -> str:
    state = session_states.get(session.session_id)
    return state.effective_status if state and state.effective_status else session.status


def _is_recent_iso(stamp: str | None, *, seconds: int) -> bool:
    if not stamp:
        return False
    try:
        normalized = stamp.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
    except ValueError:
        return False
    return (datetime.now(UTC) - parsed).total_seconds() <= seconds


def derive_island_surface_state(
    *,
    sessions: dict[str, Session],
    interactions: dict[str, Interaction],
    session_states: dict[str, DerivedSessionState],
) -> IslandSurfaceState:
    live_sessions = [session for session in sessions.values() if not session.ended_at]
    open_interactions = [
        interaction
        for interaction in interactions.values()
        if interaction.state == "open" and _session_for_id(sessions, interaction.session_id) is not None
    ]
    open_interactions.sort(key=lambda item: (item.asked_at, item.interaction_id))

    approval_queue = [item.interaction_id for item in open_interactions if item.type == "approval"]
    question_queue = [item.interaction_id for item in open_interactions if item.type == "question"]
    completion_sessions = [
        session
        for session in live_sessions
        if session_states.get(session.session_id) is not None
        and session_states[session.session_id].completion_pending
        and _is_recent_iso(session_states[session.session_id].completion_enqueued_at, seconds=180)
    ]
    completion_sessions.sort(
        key=lambda item: (
            session_states[item.session_id].completion_enqueued_at or item.last_event_at,
            item.session_id,
        ),
        reverse=True,
    )

    def status_priority(session: Session) -> int:
        status = _session_status(session, session_states)
        return {
            "waiting_approval": 0,
            "waiting_answer": 1,
            "running": 2,
            "completed": 3,
            "failed": 4,
            "cancelled": 5,
            "idle": 6,
        }.get(status, 9)

    rotation_sessions = sorted(live_sessions, key=lambda item: item.session_id)
    rotation_sessions.sort(key=_stamp_for, reverse=True)
    rotation_sessions.sort(key=status_priority)
    rotation_queue = [session.session_id for session in rotation_sessions]

    active_interaction: Interaction | None = None
    if approval_queue:
        active_interaction = interactions[approval_queue[0]]
        surface = "approvalCard"
        status = "waiting_approval"
        auto_reveal = True
    elif question_queue:
        active_interaction = interactions[question_queue[0]]
        surface = "questionCard"
        status = "waiting_answer"
        auto_reveal = True
    elif completion_sessions:
        active_interaction = None
        surface = "completionCard"
        status = _session_status(completion_sessions[0], session_states) or "completed"
        auto_reveal = True
    else:
        active_interaction = None
        surface = "collapsed"
        status = _session_status(rotation_sessions[0], session_states) if rotation_sessions else "idle"
        auto_reveal = False

    active_session: Session | None = None
    active_task_id: str | None = None
    updated_at: str | None = None
    if active_interaction is not None:
        active_session = sessions.get(active_interaction.session_id)
        active_task_id = active_interaction.task_id
        updated_at = active_interaction.asked_at
    elif completion_sessions:
        active_session = completion_sessions[0]
        state = session_states.get(active_session.session_id)
        active_task_id = state.current_task_id if state is not None else active_session.current_task_id
        updated_at = state.completion_enqueued_at if state is not None else active_session.last_event_at
    elif rotation_sessions:
        active_session = rotation_sessions[0]
        state = session_states.get(active_session.session_id)
        active_task_id = state.current_task_id if state is not None else active_session.current_task_id
        updated_at = active_session.last_event_at

    return IslandSurfaceState(
        surface=surface,
        active_session_id=active_session.session_id if active_session is not None else None,
        active_task_id=active_task_id,
        active_interaction_id=active_interaction.interaction_id if active_interaction is not None else None,
        status=status,
        auto_reveal=auto_reveal,
        approval_queue=approval_queue,
        question_queue=question_queue,
        completion_queue=[session.session_id for session in completion_sessions],
        rotation_queue=rotation_queue,
        updated_at=updated_at,
    )


def build_snapshot(
    *,
    sessions: dict[str, Session],
    tasks: dict[str, Task],
    interactions: dict[str, Interaction],
    activities: list[EventEnvelope],
    session_states: dict[str, DerivedSessionState],
    next_seq: int,
) -> dict[str, Any]:
    island_state = derive_island_surface_state(
        sessions=sessions,
        interactions=interactions,
        session_states=session_states,
    )
    return {
        "kind": "snapshot.full",
        "payload": {
            "sessions": [dataclass_dict(item) for item in sessions.values()],
            "tasks": [dataclass_dict(item) for item in tasks.values()],
            "interactions": [dataclass_dict(item) for item in interactions.values()],
            "activities": [dataclass_dict(item) for item in project_recent_activities(activities)],
            "session_states": [dataclass_dict(item) for item in session_states.values()],
            "island_state": dataclass_dict(island_state),
            "next_seq": next_seq,
        },
    }
