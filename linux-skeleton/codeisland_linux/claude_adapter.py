from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .client import rpc_call
from .jsonl_tail import read_initial_line_entries as read_initial_jsonl_entries
from .jsonl_tail import read_jsonl
from .live_watcher import (
    DEFAULT_DAEMON_REFRESH_INTERVAL_SECONDS,
    DEFAULT_STALE_SCAN_INTERVAL_SECONDS,
    IncrementalReplayCursor,
    InitialReplayCursor,
    LiveWatcherConfig,
    daemon_session_ids,
    end_daemon_sessions_not_running,
    running_keys_missing_from_daemon,
    watch_jsonl_sessions,
)
from .protocol import EventEnvelope, default_socket_path, now_iso


MAX_TEXT_CHARS = 360
DEFAULT_HISTORY_LINES = 120


@dataclass(slots=True)
class ClaudeFileContext:
    claude_session_id: str
    daemon_session_id: str
    project_root: str | None
    title: str
    terminal_app: str | None
    current_task_id: str | None = None
    call_names: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class RunningClaudeSession:
    path: Path | None
    pid: int
    cwd: str | None
    terminal_app: str | None
    terminal_pid: int | None = None
    terminal_pane: str | None = None
    terminal_socket: str | None = None
    synthetic_id: str | None = None


class ClaudeAdapterError(Exception):
    pass


class ClaudeSessionAdapter:
    provider = "claude"

    def __init__(self) -> None:
        self.contexts: dict[Path, ClaudeFileContext] = {}

    def translate_record(
        self,
        record: dict[str, Any],
        *,
        path: Path,
        line_number: int,
        terminal_app: str | None = None,
        running_session: RunningClaudeSession | None = None,
    ) -> list[EventEnvelope]:
        if not isinstance(record, dict):
            raise ClaudeAdapterError("Claude JSONL record must be an object")

        record_type = record.get("type")
        if record_type not in {"user", "assistant", "system"}:
            return []
        if record.get("isSidechain") is True:
            return []

        claude_session_id = self._record_session_id(record)
        if not claude_session_id:
            return []

        context, created = self._upsert_context(
            path,
            record,
            claude_session_id=claude_session_id,
            terminal_app=terminal_app,
            running_session=running_session,
        )
        events: list[EventEnvelope] = []
        if created:
            events.append(self._session_started_event(context, record, line_number, running_session=running_session))

        timestamp = self._timestamp(record)
        if record_type == "user":
            events.extend(self._translate_user_record(record, context=context, line_number=line_number, timestamp=timestamp))
            return events

        if record_type == "assistant":
            events.extend(self._translate_assistant_record(record, context=context, line_number=line_number, timestamp=timestamp))
            return events

        return events

    def translate_lines(
        self,
        lines: list[str],
        *,
        path: Path,
        start_line: int = 1,
        terminal_app: str | None = None,
        running_session: RunningClaudeSession | None = None,
    ) -> list[EventEnvelope]:
        return self.translate_line_entries(
            list(enumerate(lines, start=start_line)),
            path=path,
            terminal_app=terminal_app,
            running_session=running_session,
        )

    def translate_line_entries(
        self,
        entries: list[tuple[int, str]],
        *,
        path: Path,
        terminal_app: str | None = None,
        running_session: RunningClaudeSession | None = None,
    ) -> list[EventEnvelope]:
        events: list[EventEnvelope] = []
        for line_number, line in entries:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ClaudeAdapterError(f"invalid Claude JSONL at line {line_number}: {exc.msg}") from exc
            events.extend(
                self.translate_record(
                    record,
                    path=path,
                    line_number=line_number,
                    terminal_app=terminal_app,
                    running_session=running_session,
                )
            )
        return events

    def read_jsonl(self, path: Path) -> list[str]:
        return read_jsonl(path)

    def read_initial_line_entries(self, path: Path, *, history_lines: int = DEFAULT_HISTORY_LINES) -> tuple[list[tuple[int, str]], int, int]:
        return read_initial_jsonl_entries(
            path,
            history_lines=history_lines,
            anchor_entries=self._anchor_line_entries,
        )

    def translate_initial_lines(
        self,
        lines: list[str],
        *,
        path: Path,
        terminal_app: str | None = None,
        running_session: RunningClaudeSession | None = None,
        history_lines: int = DEFAULT_HISTORY_LINES,
    ) -> list[EventEnvelope]:
        if history_lines <= 0 or len(lines) <= history_lines:
            return self.translate_lines(lines, path=path, terminal_app=terminal_app, running_session=running_session)

        tail_start = len(lines) - history_lines + 1
        events: list[EventEnvelope] = []
        for line_number in self._anchor_line_numbers(lines, tail_start=tail_start):
            events.extend(
                self.translate_lines(
                    [lines[line_number - 1]],
                    path=path,
                    start_line=line_number,
                    terminal_app=terminal_app,
                    running_session=running_session,
                )
            )
        events.extend(
            self.translate_lines(
                lines[tail_start - 1:],
                path=path,
                start_line=tail_start,
                terminal_app=terminal_app,
                running_session=running_session,
            )
        )
        return events

    async def replay_jsonl(
        self,
        socket_path: str,
        input_path: Path,
        *,
        terminal_app: str | None = None,
        history_lines: int = DEFAULT_HISTORY_LINES,
    ) -> list[dict[str, Any]]:
        lines = self.read_jsonl(input_path)
        return await self._send_events(
            socket_path,
            self.translate_initial_lines(lines, path=input_path, terminal_app=terminal_app, history_lines=history_lines),
        )

    async def replay_running_once(self, socket_path: str, claude_home: Path, *, history_lines: int = DEFAULT_HISTORY_LINES) -> list[dict[str, Any]]:
        responses: list[dict[str, Any]] = []
        running_sessions = discover_running_claude_sessions(claude_home=claude_home)
        await self._end_daemon_sessions_not_running(socket_path, running_sessions)
        for session in running_sessions:
            if session.path is not None:
                responses.extend(await self._replay_running_session(socket_path, session, history_lines=history_lines))
            responses.extend(await self._send_running_session_started(socket_path, session))
        return responses

    async def watch_running(
        self,
        socket_path: str,
        claude_home: Path,
        *,
        interval: float,
        history_lines: int = DEFAULT_HISTORY_LINES,
        stale_scan_interval: float = DEFAULT_STALE_SCAN_INTERVAL_SECONDS,
        daemon_refresh_interval: float = DEFAULT_DAEMON_REFRESH_INTERVAL_SECONDS,
    ) -> None:
        await watch_jsonl_sessions(
            self,
            socket_path,
            claude_home,
            LiveWatcherConfig(
                interval=interval,
                history_lines=history_lines,
                stale_scan_interval=stale_scan_interval,
                daemon_refresh_interval=daemon_refresh_interval,
            ),
        )

    def discover_running_sessions(self, home: Path) -> list[RunningClaudeSession]:
        return discover_running_claude_sessions(claude_home=home)

    def running_session_key(self, session: RunningClaudeSession) -> str:
        return self._running_session_key(session)

    @staticmethod
    def running_session_path(session: RunningClaudeSession) -> Path | None:
        return session.path

    def path_from_running_key(self, session_key: str) -> Path | None:
        return self._path_from_running_key(session_key)

    def daemon_session_id_for_running(self, session: RunningClaudeSession) -> str:
        return self._daemon_session_id_for_running(session)

    def reset_path_context(self, path: Path) -> None:
        self.contexts.pop(path, None)

    async def send_running_session_started(self, socket_path: str, session: RunningClaudeSession) -> list[dict[str, Any]]:
        return await self._send_running_session_started(socket_path, session)

    async def send_session_ended(self, socket_path: str, session_id: str) -> list[dict[str, Any]]:
        return await self._send_session_ended(socket_path, session_id)

    async def replay_initial_file(
        self,
        socket_path: str,
        session: RunningClaudeSession,
        *,
        history_lines: int,
    ) -> InitialReplayCursor:
        if session.path is None:
            await self._send_running_session_started(socket_path, session)
            return InitialReplayCursor(
                session_id=self._daemon_session_id_for_running(session),
                next_line=1,
                file_offset=0,
            )
        entries, file_offset, total_lines = self.read_initial_line_entries(session.path, history_lines=history_lines)
        events = self.translate_line_entries(
            entries,
            path=session.path,
            terminal_app=session.terminal_app,
            running_session=session,
        )
        await self._send_events(socket_path, events)
        context = self.contexts.get(session.path)
        session_id = context.daemon_session_id if context is not None else self._daemon_session_id_for_running(session)
        await self._send_running_session_started(socket_path, session)
        return InitialReplayCursor(
            session_id=session_id,
            next_line=total_lines + 1,
            file_offset=file_offset,
        )

    async def replay_incremental_file(
        self,
        socket_path: str,
        session: RunningClaudeSession,
        *,
        start_line: int,
        lines: list[str],
    ) -> IncrementalReplayCursor:
        if session.path is None:
            await self._send_running_session_started(socket_path, session)
            return IncrementalReplayCursor(
                session_id=self._daemon_session_id_for_running(session),
                next_line=start_line,
            )
        events = self.translate_lines(
            lines,
            path=session.path,
            start_line=start_line,
            terminal_app=session.terminal_app,
        )
        await self._send_events(socket_path, events)
        context = self.contexts.get(session.path)
        session_id = context.daemon_session_id if context is not None else self._daemon_session_id_for_running(session)
        return IncrementalReplayCursor(session_id=session_id, next_line=start_line + len(lines))

    async def _replay_running_session(self, socket_path: str, session: RunningClaudeSession, *, history_lines: int) -> list[dict[str, Any]]:
        if session.path is None:
            return await self._send_running_session_started(socket_path, session)
        lines = self.read_jsonl(session.path)
        return await self._send_events(
            socket_path,
            self.translate_initial_lines(
                lines,
                path=session.path,
                terminal_app=session.terminal_app,
                running_session=session,
                history_lines=history_lines,
            ),
        )

    async def _daemon_session_ids(self, socket_path: str) -> set[str]:
        return await daemon_session_ids(socket_path, self.provider, request_id="claude-session-refresh")

    async def _end_daemon_sessions_not_running(self, socket_path: str, running_sessions: list[RunningClaudeSession]) -> set[str]:
        return await end_daemon_sessions_not_running(
            socket_path,
            self.provider,
            {self._daemon_session_id_for_running(session) for session in running_sessions},
            send_session_ended=self.send_session_ended,
        )

    def _running_keys_missing_from_daemon(self, running_sessions: list[RunningClaudeSession], daemon_session_ids: set[str]) -> set[str]:
        return running_keys_missing_from_daemon(self, running_sessions, daemon_session_ids)

    async def _send_running_session_started(self, socket_path: str, session: RunningClaudeSession) -> list[dict[str, Any]]:
        session_id = self._daemon_session_id_for_running(session)
        claude_session_id = self._claude_session_id_for_running(session)
        title = Path(session.cwd).name if session.cwd else session_id
        digest = hashlib.sha256(
            json.dumps(
                {"session_id": session_id, "pid": session.pid, "path": str(session.path) if session.path is not None else ""},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        return await self._send_events(socket_path, [
            EventEnvelope(
                event_id=f"evt_claude_alive_{digest}",
                session_id=session_id,
                kind="session.started",
                payload={
                    "provider": "claude",
                    "source": "claude-code",
                    "title": title,
                    "project_root": session.cwd,
                    "terminal_app": session.terminal_app,
                    "claude_session_id": claude_session_id,
                    "cli_pid": session.pid,
                    "terminal_pid": session.terminal_pid,
                    "terminal_pane": session.terminal_pane,
                    "terminal_socket": session.terminal_socket,
                    "synthetic": session.path is None,
                },
                ts=now_iso(),
            )
        ])

    async def _send_session_ended(self, socket_path: str, session_id: str) -> list[dict[str, Any]]:
        timestamp = now_iso()
        digest = hashlib.sha256(f"{session_id}:{timestamp}".encode("utf-8")).hexdigest()[:16]
        return await self._send_events(socket_path, [
            EventEnvelope(
                event_id=f"evt_claude_end_{digest}",
                session_id=session_id,
                kind="session.ended",
                payload={"reason": "process_closed"},
                ts=timestamp,
            )
        ])

    async def _send_events(self, socket_path: str, events: list[EventEnvelope]) -> list[dict[str, Any]]:
        responses: list[dict[str, Any]] = []
        for index, event in enumerate(events, start=1):
            responses.append(
                await rpc_call(
                    socket_path,
                    "ingest_event",
                    {
                        "event_id": event.event_id,
                        "session_id": event.session_id,
                        "task_id": event.task_id,
                        "kind": event.kind,
                        "payload": event.payload,
                        "ts": event.ts,
                    },
                    request_id=f"claude-{index}",
                )
            )
        return responses

    def _translate_user_record(
        self,
        record: dict[str, Any],
        *,
        context: ClaudeFileContext,
        line_number: int,
        timestamp: str,
    ) -> list[EventEnvelope]:
        if self._is_tool_result_record(record):
            return self._tool_result_events(record, context=context, line_number=line_number, timestamp=timestamp)
        if not self._is_user_prompt_record(record):
            return []

        prompt = self._trim(self._user_message_text(record))
        if not prompt:
            return []

        turn_id = self._string(record.get("promptId")) or self._string(record.get("uuid")) or f"line-{line_number}"
        task_id = self._task_id(context, turn_id)
        context.current_task_id = task_id
        return [
            EventEnvelope(
                event_id=self._event_id(context, line_number, "task.started"),
                session_id=context.daemon_session_id,
                task_id=task_id,
                kind="task.started",
                payload={"task_id": task_id, "prompt": prompt, "claude_message_uuid": record.get("uuid")},
                ts=timestamp,
            ),
            EventEnvelope(
                event_id=self._event_id(context, line_number, "prompt.submitted"),
                session_id=context.daemon_session_id,
                task_id=task_id,
                kind="prompt.submitted",
                payload={"task_id": task_id, "prompt": prompt, "claude_message_uuid": record.get("uuid")},
                ts=timestamp,
            ),
        ]

    def _translate_assistant_record(
        self,
        record: dict[str, Any],
        *,
        context: ClaudeFileContext,
        line_number: int,
        timestamp: str,
    ) -> list[EventEnvelope]:
        message = record.get("message")
        if not isinstance(message, dict):
            return []
        blocks = self._content_blocks(message.get("content"))
        if not blocks:
            return []

        task_id = context.current_task_id
        events: list[EventEnvelope] = []
        text = self._trim("\n".join(item for item in (self._block_text(block) for block in blocks) if item))

        if task_id is None and (text or any(block.get("type") == "tool_use" for block in blocks)):
            task_id = self._fallback_task(context, line_number)
            context.current_task_id = task_id
            events.append(
                EventEnvelope(
                    event_id=self._event_id(context, line_number, "task.started", "synthetic"),
                    session_id=context.daemon_session_id,
                    task_id=task_id,
                    kind="task.started",
                    payload={"task_id": task_id, "synthetic": True, "claude_message_uuid": record.get("uuid")},
                    ts=timestamp,
                )
            )

        for index, block in enumerate(blocks):
            if block.get("type") != "tool_use" or task_id is None:
                continue
            call_id = self._string(block.get("id")) or f"line-{line_number}-{index}"
            tool_name = self._string(block.get("name")) or "tool"
            context.call_names[call_id] = tool_name
            events.append(
                EventEnvelope(
                    event_id=self._event_id(context, line_number, "tool.use.started", call_id),
                    session_id=context.daemon_session_id,
                    task_id=task_id,
                    kind="tool.use.started",
                    payload={
                        "task_id": task_id,
                        "tool_name": tool_name,
                        "call_id": call_id,
                        "input": block.get("input"),
                    },
                    ts=timestamp,
                )
            )

        if text and task_id is not None:
            events.append(
                EventEnvelope(
                    event_id=self._event_id(context, line_number, "assistant.response.completed"),
                    session_id=context.daemon_session_id,
                    task_id=task_id,
                    kind="assistant.response.completed",
                    payload={"task_id": task_id, "message": text, "claude_message_uuid": record.get("uuid")},
                    ts=timestamp,
                )
            )

        if message.get("stop_reason") == "end_turn" and task_id is not None:
            events.append(
                EventEnvelope(
                    event_id=self._event_id(context, line_number, "task.completed"),
                    session_id=context.daemon_session_id,
                    task_id=task_id,
                    kind="task.completed",
                    payload={"task_id": task_id, "summary": text, "claude_message_uuid": record.get("uuid")},
                    ts=timestamp,
                )
            )
            context.current_task_id = None

        return events

    def _tool_result_events(
        self,
        record: dict[str, Any],
        *,
        context: ClaudeFileContext,
        line_number: int,
        timestamp: str,
    ) -> list[EventEnvelope]:
        task_id = context.current_task_id
        if task_id is None:
            return []
        message = record.get("message")
        if not isinstance(message, dict):
            return []

        events: list[EventEnvelope] = []
        for index, block in enumerate(self._content_blocks(message.get("content"))):
            if block.get("type") != "tool_result":
                continue
            call_id = self._string(block.get("tool_use_id")) or f"line-{line_number}-{index}"
            tool_name = context.call_names.get(call_id) or self._tool_name_from_result(block, record) or "tool"
            failed = bool(block.get("is_error")) or self._tool_result_failed(record.get("toolUseResult"))
            result = self._trim(self._tool_result_text(block, record))
            events.append(
                EventEnvelope(
                    event_id=self._event_id(context, line_number, "tool.use.failed" if failed else "tool.use.completed", call_id),
                    session_id=context.daemon_session_id,
                    task_id=task_id,
                    kind="tool.use.failed" if failed else "tool.use.completed",
                    payload={
                        "task_id": task_id,
                        "tool_name": tool_name,
                        "call_id": call_id,
                        "result": result,
                        "success": not failed,
                        "error_message": result if failed else None,
                    },
                    ts=timestamp,
                )
            )
        return events

    def _upsert_context(
        self,
        path: Path,
        record: dict[str, Any],
        *,
        claude_session_id: str,
        terminal_app: str | None,
        running_session: RunningClaudeSession | None,
    ) -> tuple[ClaudeFileContext, bool]:
        existing = self.contexts.get(path)
        if existing is not None and existing.claude_session_id == claude_session_id:
            if terminal_app and not existing.terminal_app:
                existing.terminal_app = terminal_app
            return existing, False

        project_root = running_session.cwd if running_session is not None and running_session.cwd else self._string(record.get("cwd")) or self._project_root_from_path(path)
        title = Path(project_root).name if project_root else claude_session_id
        context = ClaudeFileContext(
            claude_session_id=claude_session_id,
            daemon_session_id=self._daemon_session_id(claude_session_id),
            project_root=project_root,
            title=title or claude_session_id,
            terminal_app=terminal_app,
        )
        self.contexts[path] = context
        return context, True

    def _session_started_event(
        self,
        context: ClaudeFileContext,
        record: dict[str, Any],
        line_number: int,
        *,
        running_session: RunningClaudeSession | None,
    ) -> EventEnvelope:
        payload = {
            "provider": "claude",
            "source": "claude-code",
            "title": context.title,
            "project_root": context.project_root,
            "terminal_app": context.terminal_app,
            "claude_session_id": context.claude_session_id,
            "cli_version": record.get("version"),
        }
        if running_session is not None:
            payload.update({
                "cli_pid": running_session.pid,
                "terminal_pid": running_session.terminal_pid,
                "terminal_pane": running_session.terminal_pane,
                "terminal_socket": running_session.terminal_socket,
            })
        return EventEnvelope(
            event_id=self._event_id(context, line_number, "session.started"),
            session_id=context.daemon_session_id,
            kind="session.started",
            payload=payload,
            ts=self._timestamp(record),
        )

    @classmethod
    def _anchor_line_numbers(cls, lines: list[str], *, tail_start: int) -> list[int]:
        latest_user_line: int | None = None
        for line_number in range(tail_start - 1, 0, -1):
            try:
                record = json.loads(lines[line_number - 1])
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and cls._is_user_prompt_record(record):
                latest_user_line = line_number
                break
        return [latest_user_line] if latest_user_line is not None else []

    @classmethod
    def _anchor_line_entries(cls, entries: list[tuple[int, str]], *, tail_start: int) -> list[tuple[int, str]]:
        for line_number, line in reversed(entries):
            if line_number >= tail_start:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and cls._is_user_prompt_record(record):
                return [(line_number, line)]
        return []

    @staticmethod
    def _record_session_id(record: dict[str, Any]) -> str | None:
        return ClaudeSessionAdapter._string(record.get("sessionId")) or ClaudeSessionAdapter._string(record.get("session_id"))

    @classmethod
    def _session_id_from_jsonl(cls, path: Path) -> str | None:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        return None
                    if not isinstance(record, dict):
                        continue
                    session_id = cls._record_session_id(record)
                    if session_id:
                        return session_id
        except OSError:
            return None
        return None

    @staticmethod
    def _running_session_key(session: RunningClaudeSession) -> str:
        if session.path is not None:
            return f"file:{session.path}"
        return f"proc:{session.pid}"

    @staticmethod
    def _path_from_running_key(session_key: str) -> Path | None:
        if not session_key.startswith("file:"):
            return None
        return Path(session_key[5:])

    def _claude_session_id_for_running(self, session: RunningClaudeSession) -> str:
        if session.path is not None:
            claude_session_id = self._session_id_from_jsonl(session.path)
            if claude_session_id:
                return claude_session_id
        return session.synthetic_id or f"proc-{session.pid}"

    def _daemon_session_id_for_running(self, session: RunningClaudeSession) -> str:
        return self._daemon_session_id(self._claude_session_id_for_running(session))

    @staticmethod
    def _content_blocks(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, str):
            return [{"type": "text", "text": value}]
        return []

    @staticmethod
    def _block_text(block: dict[str, Any]) -> str | None:
        if block.get("type") != "text":
            return None
        text = block.get("text")
        return text if isinstance(text, str) and text.strip() else None

    @classmethod
    def _user_message_text(cls, record: dict[str, Any]) -> str | None:
        message = record.get("message")
        if not isinstance(message, dict):
            return None
        content = message.get("content")
        if isinstance(content, str):
            return content
        texts = [cls._block_text(block) for block in cls._content_blocks(content)]
        joined = "\n".join(item for item in texts if item)
        return joined if joined else None

    @classmethod
    def _is_user_prompt_record(cls, record: dict[str, Any]) -> bool:
        if record.get("type") != "user" or record.get("isMeta") is True:
            return False
        if cls._is_tool_result_record(record):
            return False
        text = cls._user_message_text(record)
        if not text:
            return False
        stripped = text.strip()
        return not (
            stripped.startswith("<local-command-")
            or stripped.startswith("<command-name>")
            or stripped.startswith("<task-notification>")
        )

    @classmethod
    def _is_tool_result_record(cls, record: dict[str, Any]) -> bool:
        message = record.get("message")
        if not isinstance(message, dict):
            return False
        return any(block.get("type") == "tool_result" for block in cls._content_blocks(message.get("content")))

    @staticmethod
    def _tool_name_from_result(block: dict[str, Any], record: dict[str, Any]) -> str | None:
        content = block.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    tool_name = item.get("tool_name")
                    if isinstance(tool_name, str) and tool_name:
                        return tool_name
        tool_use_result = record.get("toolUseResult")
        if isinstance(tool_use_result, dict):
            tool_name = tool_use_result.get("tool_name") or tool_use_result.get("name")
            if isinstance(tool_name, str) and tool_name:
                return tool_name
        return None

    @staticmethod
    def _tool_result_text(block: dict[str, Any], record: dict[str, Any]) -> str | None:
        content = block.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content") or item.get("tool_name")
                    if isinstance(text, str) and text:
                        texts.append(text)
                elif isinstance(item, str) and item:
                    texts.append(item)
            if texts:
                return "\n".join(texts)
        tool_use_result = record.get("toolUseResult")
        if isinstance(tool_use_result, dict):
            for key in ("output", "result", "content", "summary"):
                value = tool_use_result.get(key)
                if isinstance(value, str) and value:
                    return value
            return json.dumps(tool_use_result, ensure_ascii=False, sort_keys=True)
        return None

    @staticmethod
    def _tool_result_failed(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        status = value.get("status")
        if isinstance(status, str) and status.lower() in {"failed", "error"}:
            return True
        exit_code = value.get("exit_code")
        if isinstance(exit_code, int):
            return exit_code != 0
        code = value.get("code")
        return isinstance(code, int) and code >= 400

    @staticmethod
    def _project_root_from_path(path: Path) -> str | None:
        parts = path.parts
        if "projects" not in parts:
            return None
        try:
            encoded = parts[parts.index("projects") + 1]
        except IndexError:
            return None
        if not encoded.startswith("-"):
            return None
        return "/" + encoded[1:].replace("-", "/")

    @staticmethod
    def _timestamp(record: dict[str, Any]) -> str:
        value = record.get("timestamp")
        return value if isinstance(value, str) and value else now_iso()

    @staticmethod
    def _string(value: Any) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _trim(value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if len(text) <= MAX_TEXT_CHARS:
            return text
        return f"{text[:MAX_TEXT_CHARS - 3]}..."

    @staticmethod
    def _daemon_session_id(claude_session_id: str) -> str:
        return claude_session_id if claude_session_id.startswith("claude-") else f"claude-{claude_session_id}"

    @staticmethod
    def _task_id(context: ClaudeFileContext, turn_id: str) -> str:
        return f"{context.daemon_session_id}-turn-{turn_id}"

    def _fallback_task(self, context: ClaudeFileContext, line_number: int) -> str:
        return self._task_id(context, f"line-{line_number}")

    @staticmethod
    def _event_id(context: ClaudeFileContext, line_number: int, normalized_kind: str, *parts: str) -> str:
        digest = hashlib.sha256(
            json.dumps(
                {
                    "session_id": context.daemon_session_id,
                    "line_number": line_number,
                    "kind": normalized_kind,
                    "parts": parts,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        return f"evt_claude_{digest}"


def discover_running_claude_sessions(*, claude_home: Path) -> list[RunningClaudeSession]:
    sessions: dict[str, RunningClaudeSession] = {}
    for proc_dir in Path("/proc").iterdir():
        if not proc_dir.name.isdigit() or not _is_claude_process(proc_dir):
            continue
        pid = int(proc_dir.name)
        env = _read_environ(proc_dir)
        terminal_app = _terminal_app(env)
        terminal_socket = _terminal_socket(env)
        terminal_pid = _terminal_pid(env)
        terminal_pane = _terminal_pane(env)
        cwd = _readlink_text(proc_dir / "cwd")
        session_paths = _open_claude_jsonl_paths(proc_dir, claude_home)
        if not session_paths and cwd:
            session_paths = _latest_project_jsonl_for_cwd(claude_home, cwd)
        if session_paths:
            for session_path in session_paths:
                sessions.setdefault(
                    f"file:{session_path}",
                    RunningClaudeSession(
                        path=session_path,
                        pid=pid,
                        cwd=cwd,
                        terminal_app=terminal_app,
                        terminal_pid=terminal_pid,
                        terminal_pane=terminal_pane,
                        terminal_socket=terminal_socket,
                    ),
                )
            continue
        sessions.setdefault(
            f"proc:{pid}",
            RunningClaudeSession(
                path=None,
                pid=pid,
                cwd=cwd,
                terminal_app=terminal_app,
                terminal_pid=terminal_pid,
                terminal_pane=terminal_pane,
                terminal_socket=terminal_socket,
                synthetic_id=f"proc-{pid}",
            ),
        )
    return sorted(sessions.values(), key=lambda item: str(item.path) if item.path is not None else f"proc:{item.pid}")


def _open_claude_jsonl_paths(proc_dir: Path, claude_home: Path) -> list[Path]:
    fd_dir = proc_dir / "fd"
    try:
        fd_paths = list(fd_dir.iterdir())
    except OSError:
        return []
    session_paths: list[Path] = []
    seen: set[Path] = set()
    for fd_path in fd_paths:
        target = _readlink_text(fd_path)
        if not target:
            continue
        session_path = Path(target.removesuffix(" (deleted)"))
        if session_path in seen or not _is_claude_jsonl_path(session_path, claude_home):
            continue
        seen.add(session_path)
        session_paths.append(session_path)
    return sorted(session_paths)


def _latest_project_jsonl_for_cwd(claude_home: Path, cwd: str) -> list[Path]:
    directory = claude_home.expanduser() / "projects" / _encode_claude_project_path(cwd)
    try:
        files = [item for item in directory.iterdir() if item.is_file() and item.suffix == ".jsonl"]
    except OSError:
        return []
    if not files:
        return []
    return [max(files, key=lambda item: item.stat().st_mtime)]


def _encode_claude_project_path(value: str) -> str:
    return value.replace("/", "-") or "-"


def _is_claude_jsonl_path(path: Path, claude_home: Path) -> bool:
    if path.suffix != ".jsonl" or not path.exists():
        return False
    root = claude_home.expanduser()
    try:
        path.relative_to(root / "projects")
        return True
    except ValueError:
        pass
    try:
        path.relative_to(root / "transcripts")
        return True
    except ValueError:
        return False


def _is_claude_process(proc_dir: Path) -> bool:
    comm = _read_text(proc_dir / "comm")
    if comm and comm.strip() in {"claude", "claude-code"}:
        return True
    cmdline = _read_text(proc_dir / "cmdline")
    if not cmdline:
        return False
    args = [item for item in cmdline.split("\0") if item]
    if any(Path(item).name in {"claude", "claude-code"} for item in args[:4]):
        return True
    return any("claude-code" in item or "@anthropic-ai/claude" in item for item in args[:6])


def _read_environ(proc_dir: Path) -> dict[str, str]:
    raw = _read_text(proc_dir / "environ")
    values: dict[str, str] = {}
    if not raw:
        return values
    for item in raw.split("\0"):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        values[key] = value
    return values


def _terminal_app(env: dict[str, str]) -> str | None:
    if env.get("GHOSTTY_RESOURCES_DIR") or env.get("GHOSTTY_BIN_DIR"):
        return "Ghostty"
    if env.get("WEZTERM_PANE") or env.get("TERM_PROGRAM") == "WezTerm":
        return "WezTerm"
    if env.get("KITTY_WINDOW_ID"):
        return "kitty"
    if env.get("ALACRITTY_WINDOW_ID"):
        return "Alacritty"
    value = env.get("TERM_PROGRAM")
    return value if value else None


def _terminal_socket(env: dict[str, str]) -> str | None:
    value = env.get("WEZTERM_UNIX_SOCKET")
    return value if value else None


def _terminal_pane(env: dict[str, str]) -> str | None:
    value = env.get("WEZTERM_PANE")
    return value if value else None


def _terminal_pid(env: dict[str, str]) -> int | None:
    socket_path = _terminal_socket(env)
    if socket_path:
        match = re.search(r"gui-sock-(\d+)(?:$|/)", socket_path)
        if match:
            return int(match.group(1))
    return None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _readlink_text(path: Path) -> str | None:
    try:
        return os.readlink(path)
    except OSError:
        return None


async def _main_async(args: argparse.Namespace) -> None:
    adapter = ClaudeSessionAdapter()
    claude_home = Path(args.claude_home).expanduser()
    if args.input:
        responses: list[dict[str, Any]] = []
        for input_path in args.input:
            responses.extend(
                await adapter.replay_jsonl(
                    args.socket_path,
                    Path(input_path).expanduser(),
                    terminal_app=args.terminal_app,
                    history_lines=args.history_lines,
                )
            )
        for response in responses:
            print(response)
        return

    if args.watch:
        await adapter.watch_running(
            args.socket_path,
            claude_home,
            interval=args.interval,
            history_lines=args.history_lines,
            stale_scan_interval=args.stale_scan_interval,
            daemon_refresh_interval=args.daemon_refresh_interval,
        )
        return

    responses = await adapter.replay_running_once(args.socket_path, claude_home, history_lines=args.history_lines)
    for response in responses:
        print(response)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay live Claude Code JSONL sessions into the CodeIsland daemon")
    parser.add_argument("--socket-path", default=default_socket_path())
    parser.add_argument("--claude-home", default=os.environ.get("CLAUDE_HOME", "~/.claude"))
    parser.add_argument("--input", action="append", help="Replay an explicit Claude Code project/transcript JSONL file")
    parser.add_argument("--terminal-app", help="Terminal label to attach when replaying --input")
    parser.add_argument("--watch", action="store_true", help="Continue polling currently running Claude Code session files")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--history-lines", type=int, default=DEFAULT_HISTORY_LINES, help="Initial JSONL tail window per session; use 0 to replay all history")
    parser.add_argument(
        "--stale-scan-interval",
        type=float,
        default=DEFAULT_STALE_SCAN_INTERVAL_SECONDS,
        help="Seconds between daemon stale-session cleanup passes while watching; use 0 to scan every poll",
    )
    parser.add_argument(
        "--daemon-refresh-interval",
        type=float,
        default=DEFAULT_DAEMON_REFRESH_INTERVAL_SECONDS,
        help="Seconds between checks that running sessions still exist in the daemon after daemon restarts",
    )
    args = parser.parse_args()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
