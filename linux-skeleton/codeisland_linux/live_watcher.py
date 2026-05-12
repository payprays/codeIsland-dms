from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .client import rpc_call
from .jsonl_tail import read_jsonl_chunk


DEFAULT_STALE_SCAN_INTERVAL_SECONDS = 15.0
DEFAULT_DAEMON_REFRESH_INTERVAL_SECONDS = 3.0


@dataclass(slots=True, frozen=True)
class LiveWatcherConfig:
    interval: float
    history_lines: int
    stale_scan_interval: float
    daemon_refresh_interval: float


@dataclass(slots=True, frozen=True)
class InitialReplayCursor:
    session_id: str
    next_line: int
    file_offset: int
    pending_text: str = ""


@dataclass(slots=True, frozen=True)
class IncrementalReplayCursor:
    session_id: str
    next_line: int


class JsonlLiveAdapter(Protocol):
    provider: str

    def discover_running_sessions(self, home: Path) -> list[Any]:
        ...

    def running_session_key(self, session: Any) -> str:
        ...

    def running_session_path(self, session: Any) -> Path | None:
        ...

    def path_from_running_key(self, session_key: str) -> Path | None:
        ...

    def daemon_session_id_for_running(self, session: Any) -> str:
        ...

    def reset_path_context(self, path: Path) -> None:
        ...

    async def send_running_session_started(self, socket_path: str, session: Any) -> list[dict[str, Any]]:
        ...

    async def send_session_ended(self, socket_path: str, session_id: str) -> list[dict[str, Any]]:
        ...

    async def replay_initial_file(
        self,
        socket_path: str,
        session: Any,
        *,
        history_lines: int,
    ) -> InitialReplayCursor:
        ...

    async def replay_incremental_file(
        self,
        socket_path: str,
        session: Any,
        *,
        start_line: int,
        lines: list[str],
    ) -> IncrementalReplayCursor:
        ...


async def daemon_session_ids(socket_path: str, provider: str, *, request_id: str) -> set[str]:
    response = await rpc_call(socket_path, "list_sessions", {"provider": provider}, request_id=request_id)
    sessions = response.get("result", {}).get("sessions", []) if response.get("ok") else []
    ids: set[str] = set()
    for session in sessions:
        if not isinstance(session, dict) or session.get("ended_at"):
            continue
        session_id = session.get("session_id")
        if isinstance(session_id, str) and session_id:
            ids.add(session_id)
    return ids


async def end_daemon_sessions_not_running(
    socket_path: str,
    provider: str,
    running_ids: set[str],
    *,
    send_session_ended: Any,
) -> set[str]:
    daemon_ids = await daemon_session_ids(socket_path, provider, request_id=f"{provider}-stale-scan")
    for session_id in daemon_ids:
        if session_id in running_ids:
            continue
        await send_session_ended(socket_path, session_id)
    return daemon_ids


def running_keys_missing_from_daemon(adapter: JsonlLiveAdapter, running_sessions: list[Any], daemon_ids: set[str]) -> set[str]:
    missing: set[str] = set()
    for session in running_sessions:
        if adapter.daemon_session_id_for_running(session) not in daemon_ids:
            missing.add(adapter.running_session_key(session))
    return missing


async def watch_jsonl_sessions(
    adapter: JsonlLiveAdapter,
    socket_path: str,
    home: Path,
    config: LiveWatcherConfig,
) -> None:
    next_line_by_key: dict[str, int] = {}
    file_offset_by_key: dict[str, int] = {}
    pending_text_by_key: dict[str, str] = {}
    session_id_by_key: dict[str, str] = {}
    next_stale_scan_at = 0.0
    next_daemon_refresh_at = 0.0
    while True:
        running_sessions = adapter.discover_running_sessions(home)
        running_keys = {adapter.running_session_key(session) for session in running_sessions}
        loop_time = asyncio.get_running_loop().time()
        missing_daemon_keys: set[str] = set()
        if loop_time >= next_stale_scan_at:
            daemon_ids = await end_daemon_sessions_not_running(
                socket_path,
                adapter.provider,
                {adapter.daemon_session_id_for_running(session) for session in running_sessions},
                send_session_ended=adapter.send_session_ended,
            )
            missing_daemon_keys = running_keys_missing_from_daemon(adapter, running_sessions, daemon_ids)
            next_stale_scan_at = loop_time + config.stale_scan_interval if config.stale_scan_interval > 0 else loop_time
            next_daemon_refresh_at = loop_time + config.daemon_refresh_interval if config.daemon_refresh_interval > 0 else loop_time
        elif loop_time >= next_daemon_refresh_at:
            daemon_ids = await daemon_session_ids(socket_path, adapter.provider, request_id=f"{adapter.provider}-session-refresh")
            missing_daemon_keys = running_keys_missing_from_daemon(adapter, running_sessions, daemon_ids)
            next_daemon_refresh_at = loop_time + config.daemon_refresh_interval if config.daemon_refresh_interval > 0 else loop_time

        for missing_key in missing_daemon_keys:
            context_path = adapter.path_from_running_key(missing_key)
            if context_path is not None:
                adapter.reset_path_context(context_path)
            next_line_by_key[missing_key] = 1
            file_offset_by_key.pop(missing_key, None)
            pending_text_by_key.pop(missing_key, None)

        for closed_key in sorted(set(next_line_by_key) - running_keys):
            session_id = session_id_by_key.pop(closed_key, None)
            next_line_by_key.pop(closed_key, None)
            file_offset_by_key.pop(closed_key, None)
            pending_text_by_key.pop(closed_key, None)
            context_path = adapter.path_from_running_key(closed_key)
            if context_path is not None:
                adapter.reset_path_context(context_path)
            if session_id:
                await adapter.send_session_ended(socket_path, session_id)

        for session in running_sessions:
            session_key = adapter.running_session_key(session)
            path = adapter.running_session_path(session)
            if path is None:
                if session_key not in next_line_by_key or session_key in missing_daemon_keys:
                    await adapter.send_running_session_started(socket_path, session)
                    session_id_by_key[session_key] = adapter.daemon_session_id_for_running(session)
                    next_line_by_key[session_key] = 1
                continue

            next_line = next_line_by_key.get(session_key, 1)
            if next_line == 1:
                cursor = await adapter.replay_initial_file(socket_path, session, history_lines=config.history_lines)
                file_offset_by_key[session_key] = cursor.file_offset
                pending_text_by_key[session_key] = cursor.pending_text
                next_line_by_key[session_key] = cursor.next_line
                session_id_by_key[session_key] = cursor.session_id
                continue

            lines, file_offset, pending_text, reset = read_jsonl_chunk(
                path,
                offset=file_offset_by_key.get(session_key, 0),
                pending=pending_text_by_key.get(session_key, ""),
            )
            file_offset_by_key[session_key] = file_offset
            pending_text_by_key[session_key] = pending_text
            if reset:
                next_line_by_key[session_key] = 1
                adapter.reset_path_context(path)
                continue
            if not lines:
                continue

            cursor = await adapter.replay_incremental_file(socket_path, session, start_line=next_line, lines=lines)
            session_id_by_key[session_key] = cursor.session_id
            next_line_by_key[session_key] = cursor.next_line
        await asyncio.sleep(config.interval)
