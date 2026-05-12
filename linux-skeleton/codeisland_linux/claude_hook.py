from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import shutil
import shlex
import sys
from pathlib import Path
from typing import Any

from .client import MAX_RESPONSE_BYTES, rpc_call
from .claude_adapter import MAX_TEXT_CHARS, _terminal_app, _terminal_pane, _terminal_pid, _terminal_socket
from .protocol import EventEnvelope, default_socket_path, from_json_line, now_iso, to_json_line


CODEISLAND_HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "StopFailure",
    "SessionEnd",
)
CODEISLAND_HOOK_MARKER = "codeisland_linux.claude_hook"
DEFAULT_INTERACTION_TIMEOUT_SECONDS = 300.0
DEFAULT_HOOK_TIMEOUT_SECONDS = 5


class ClaudeHookError(Exception):
    pass


class ClaudeHookAdapter:
    def translate_hook(self, raw_event: dict[str, Any], *, event_name: str | None = None, terminal_app: str | None = None) -> list[EventEnvelope]:
        if not isinstance(raw_event, dict):
            raise ClaudeHookError("Claude hook payload must be an object")

        hook_event_name = self._string(raw_event.get("hook_event_name")) or self._string(raw_event.get("hookEventName")) or event_name
        if not hook_event_name:
            raise ClaudeHookError("Claude hook payload requires hook_event_name")

        claude_session_id = self._session_id(raw_event)
        if not claude_session_id:
            raise ClaudeHookError("Claude hook payload requires session_id")

        session_id = self._daemon_session_id(claude_session_id)
        timestamp = now_iso()
        terminal = self._terminal_metadata(terminal_app)
        events: list[EventEnvelope] = []

        if hook_event_name == "SessionStart":
            return [self._session_started(raw_event, session_id, claude_session_id, timestamp, terminal)]

        events.append(self._session_started(raw_event, session_id, claude_session_id, timestamp, terminal, ensure=True))

        if hook_event_name == "SessionEnd":
            events.append(
                EventEnvelope(
                    event_id=self._event_id(session_id, hook_event_name, "session.ended", self._string(raw_event.get("reason"))),
                    session_id=session_id,
                    kind="session.ended",
                    payload={"reason": self._string(raw_event.get("reason")) or "session_end"},
                    ts=timestamp,
                )
            )
            return events

        task_id = self._task_id_for_hook(session_id, raw_event)
        if task_id is not None:
            events.append(self._task_started(raw_event, session_id, task_id, timestamp, synthetic=hook_event_name != "UserPromptSubmit"))

        if hook_event_name == "UserPromptSubmit":
            prompt = self._trim(self._prompt(raw_event) or self._latest_user_prompt_from_transcript(raw_event))
            if prompt and task_id:
                events.append(
                    EventEnvelope(
                        event_id=self._event_id(session_id, hook_event_name, "prompt.submitted", task_id, prompt),
                        session_id=session_id,
                        task_id=task_id,
                        kind="prompt.submitted",
                        payload={"task_id": task_id, "prompt": prompt},
                        ts=timestamp,
                    )
                )
            return events

        if hook_event_name == "PreToolUse":
            if task_id is None:
                return events
            tool_use_id = self._tool_use_id(raw_event)
            events.append(
                EventEnvelope(
                    event_id=self._event_id(session_id, hook_event_name, "tool.use.started", task_id, tool_use_id),
                    session_id=session_id,
                    task_id=task_id,
                    kind="tool.use.started",
                    payload={
                        "task_id": task_id,
                        "tool_name": self._tool_name(raw_event),
                        "tool_use_id": tool_use_id,
                        "input": self._tool_input(raw_event),
                    },
                    ts=timestamp,
                )
            )
            return events

        if hook_event_name == "PermissionRequest":
            if task_id is None:
                return events
            interaction_id = self._permission_interaction_id(session_id, task_id, raw_event)
            prompt_text = self._approval_prompt(raw_event)
            events.extend(
                [
                    EventEnvelope(
                        event_id=self._event_id(session_id, hook_event_name, "interaction.approval.requested", interaction_id),
                        session_id=session_id,
                        task_id=task_id,
                        kind="interaction.approval.requested",
                        payload={
                            "interaction_id": interaction_id,
                            "task_id": task_id,
                            "prompt_text": prompt_text,
                            "options": ["approve", "deny"],
                            "tool_name": self._tool_name(raw_event),
                            "tool_input": self._tool_input(raw_event),
                        },
                        ts=timestamp,
                    ),
                    EventEnvelope(
                        event_id=self._event_id(session_id, hook_event_name, "permission.requested", interaction_id),
                        session_id=session_id,
                        task_id=task_id,
                        kind="permission.requested",
                        payload={
                            "interaction_id": interaction_id,
                            "task_id": task_id,
                            "tool_name": self._tool_name(raw_event),
                            "prompt_text": prompt_text,
                            "options": ["approve", "deny"],
                            "tool_input": self._tool_input(raw_event),
                            "permission_mode": raw_event.get("permission_mode") or raw_event.get("permissionMode"),
                        },
                        ts=timestamp,
                    ),
                ]
            )
            return events

        if hook_event_name in {"PostToolUse", "PostToolUseFailure"}:
            if task_id is None:
                return events
            tool_use_id = self._tool_use_id(raw_event)
            failed = hook_event_name == "PostToolUseFailure" or self._tool_response_failed(self._tool_response(raw_event))
            events.append(
                EventEnvelope(
                    event_id=self._event_id(session_id, hook_event_name, "tool.use.finished", task_id, tool_use_id),
                    session_id=session_id,
                    task_id=task_id,
                    kind="tool.use.failed" if failed else "tool.use.completed",
                    payload={
                        "task_id": task_id,
                        "tool_name": self._tool_name(raw_event),
                        "tool_use_id": tool_use_id,
                        "result": self._tool_response(raw_event),
                        "success": not failed,
                        "error_message": self._tool_error_message(self._tool_response(raw_event)) if failed else None,
                    },
                    ts=timestamp,
                )
            )
            return events

        if hook_event_name in {"Stop", "StopFailure"}:
            if task_id is None:
                return events
            summary = self._trim(self._last_assistant_message(raw_event))
            if summary:
                events.append(
                    EventEnvelope(
                        event_id=self._event_id(session_id, hook_event_name, "assistant.response.completed", task_id, summary),
                        session_id=session_id,
                        task_id=task_id,
                        kind="assistant.response.completed",
                        payload={"task_id": task_id, "message": summary, "phase": "final_answer"},
                        ts=timestamp,
                    )
                )
            failed = hook_event_name == "StopFailure"
            events.append(
                EventEnvelope(
                    event_id=self._event_id(session_id, hook_event_name, "task.finished", task_id),
                    session_id=session_id,
                    task_id=task_id,
                    kind="task.failed" if failed else "task.completed",
                    payload={
                        "task_id": task_id,
                        "summary": summary,
                        "error_code": "claude_stop_failure" if failed else None,
                        "error_message": self._trim(self._string(raw_event.get("error")) or self._string(raw_event.get("message"))) if failed else None,
                    },
                    ts=timestamp,
                )
            )
            return events

        return events

    def _session_started(
        self,
        raw_event: dict[str, Any],
        session_id: str,
        claude_session_id: str,
        timestamp: str,
        terminal: dict[str, Any],
        *,
        ensure: bool = False,
    ) -> EventEnvelope:
        project_root = self._string(raw_event.get("cwd")) or self._string(raw_event.get("project_root"))
        title = Path(project_root).name if project_root else claude_session_id
        return EventEnvelope(
            event_id=self._event_id(session_id, "SessionStart", "session.started", "ensure" if ensure else raw_event.get("source")),
            session_id=session_id,
            kind="session.started",
            payload={
                "provider": "claude",
                "source": "claude-code",
                "title": title or claude_session_id,
                "project_root": project_root,
                "terminal_app": terminal.get("terminal_app"),
                "terminal_pid": terminal.get("terminal_pid"),
                "terminal_pane": terminal.get("terminal_pane"),
                "terminal_socket": terminal.get("terminal_socket"),
                "claude_session_id": claude_session_id,
                "model": raw_event.get("model"),
                "permission_mode": raw_event.get("permission_mode") or raw_event.get("permissionMode"),
                "transcript_path": raw_event.get("transcript_path") or raw_event.get("transcriptPath"),
            },
            ts=timestamp,
        )

    @staticmethod
    def _terminal_metadata(terminal_app: str | None) -> dict[str, Any]:
        env = os.environ
        return {
            "terminal_app": terminal_app or _terminal_app(env),
            "terminal_pid": _terminal_pid(env),
            "terminal_pane": _terminal_pane(env),
            "terminal_socket": _terminal_socket(env),
        }

    def _task_started(self, raw_event: dict[str, Any], session_id: str, task_id: str, timestamp: str, *, synthetic: bool) -> EventEnvelope:
        prompt = self._trim(self._prompt(raw_event) or self._latest_user_prompt_from_transcript(raw_event))
        return EventEnvelope(
            event_id=self._event_id(session_id, "task.started", task_id),
            session_id=session_id,
            task_id=task_id,
            kind="task.started",
            payload={"task_id": task_id, "prompt": prompt, "synthetic": synthetic},
            ts=timestamp,
        )

    def _task_id_for_hook(self, session_id: str, raw_event: dict[str, Any]) -> str | None:
        prompt = self._prompt(raw_event) or self._latest_user_prompt_from_transcript(raw_event)
        if prompt:
            seed = {"session_id": session_id, "prompt": prompt}
        else:
            seed_value = self._string(raw_event.get("turn_id")) or self._string(raw_event.get("message_id")) or self._string(raw_event.get("tool_use_id")) or "active"
            seed = {"session_id": session_id, "seed": seed_value}
        digest = hashlib.sha256(json.dumps(seed, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
        return f"{session_id}-turn-{digest}"

    @staticmethod
    def _daemon_session_id(claude_session_id: str) -> str:
        return claude_session_id if claude_session_id.startswith("claude-") else f"claude-{claude_session_id}"

    @staticmethod
    def _session_id(raw_event: dict[str, Any]) -> str | None:
        for key in ("session_id", "sessionId"):
            value = raw_event.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @classmethod
    def _prompt(cls, raw_event: dict[str, Any]) -> str | None:
        for key in ("prompt", "user_prompt", "userPrompt"):
            value = raw_event.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        message = raw_event.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        return None

    @classmethod
    def _last_assistant_message(cls, raw_event: dict[str, Any]) -> str | None:
        for key in ("last_assistant_message", "lastAssistantMessage", "response", "completion"):
            value = raw_event.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return cls._latest_assistant_text_from_transcript(raw_event)

    @classmethod
    def _latest_user_prompt_from_transcript(cls, raw_event: dict[str, Any]) -> str | None:
        record = cls._latest_transcript_record(raw_event, wanted_role="user", skip_tool_results=True)
        if record is None:
            return None
        return cls._message_text(record.get("message"))

    @classmethod
    def _latest_assistant_text_from_transcript(cls, raw_event: dict[str, Any]) -> str | None:
        record = cls._latest_transcript_record(raw_event, wanted_role="assistant", skip_tool_results=False)
        if record is None:
            return None
        return cls._message_text(record.get("message"))

    @classmethod
    def _latest_transcript_record(cls, raw_event: dict[str, Any], *, wanted_role: str, skip_tool_results: bool) -> dict[str, Any] | None:
        transcript_path = cls._string(raw_event.get("transcript_path")) or cls._string(raw_event.get("transcriptPath"))
        if not transcript_path:
            return None
        path = Path(transcript_path).expanduser()
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or record.get("isSidechain") is True or record.get("isMeta") is True:
                continue
            message = record.get("message")
            if not isinstance(message, dict) or message.get("role") != wanted_role:
                continue
            if skip_tool_results and cls._message_has_tool_result(message):
                continue
            text = cls._message_text(message)
            if text:
                return record
        return None

    @classmethod
    def _message_text(cls, message: Any) -> str | None:
        if not isinstance(message, dict):
            return None
        content = message.get("content")
        if isinstance(content, str):
            return content.strip() or None
        if not isinstance(content, list):
            return None
        texts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
        return "\n".join(texts) if texts else None

    @staticmethod
    def _message_has_tool_result(message: dict[str, Any]) -> bool:
        content = message.get("content")
        return isinstance(content, list) and any(isinstance(item, dict) and item.get("type") == "tool_result" for item in content)

    @staticmethod
    def _tool_name(raw_event: dict[str, Any]) -> str:
        for key in ("tool_name", "toolName", "name"):
            value = raw_event.get(key)
            if isinstance(value, str) and value:
                return value
        return "tool"

    @staticmethod
    def _tool_use_id(raw_event: dict[str, Any]) -> str:
        for key in ("tool_use_id", "toolUseId", "tool_id", "toolID"):
            value = raw_event.get(key)
            if isinstance(value, str) and value:
                return value
        digest = hashlib.sha256(json.dumps(raw_event.get("tool_input") or raw_event.get("toolInput") or {}, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
        return f"tool-{digest}"

    @staticmethod
    def _tool_input(raw_event: dict[str, Any]) -> Any:
        if "tool_input" in raw_event:
            return raw_event.get("tool_input")
        return raw_event.get("toolInput")

    @staticmethod
    def _tool_response(raw_event: dict[str, Any]) -> Any:
        for key in ("tool_response", "toolResponse", "response", "result"):
            if key in raw_event:
                return raw_event.get(key)
        return None

    @classmethod
    def _approval_prompt(cls, raw_event: dict[str, Any]) -> str:
        tool_name = cls._tool_name(raw_event)
        tool_input = cls._tool_input(raw_event)
        if tool_input is not None:
            return f"Allow {tool_name}: {cls._trim(json.dumps(tool_input, ensure_ascii=False, sort_keys=True, default=str))}"
        return f"Allow {tool_name}?"

    @classmethod
    def _permission_interaction_id(cls, session_id: str, task_id: str, raw_event: dict[str, Any]) -> str:
        tool_use_id = cls._tool_use_id(raw_event)
        return f"{session_id}-permission-{tool_use_id}" if tool_use_id else f"{task_id}-permission"

    @staticmethod
    def _tool_response_failed(tool_response: Any) -> bool:
        if isinstance(tool_response, str):
            return "error" in tool_response.lower() or "failed" in tool_response.lower()
        if not isinstance(tool_response, dict):
            return False
        success = tool_response.get("success")
        if success is False:
            return True
        for key in ("exit_code", "exitCode", "status"):
            value = tool_response.get(key)
            if isinstance(value, int) and value != 0:
                return True
            if isinstance(value, str) and value.lower() in {"error", "failed"}:
                return True
        return any(isinstance(tool_response.get(key), str) and tool_response.get(key) for key in ("error", "error_message", "stderr"))

    @staticmethod
    def _tool_error_message(tool_response: Any) -> str | None:
        if isinstance(tool_response, str):
            return tool_response
        if not isinstance(tool_response, dict):
            return None
        for key in ("error_message", "error", "stderr"):
            value = tool_response.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _event_id(*parts: Any) -> str:
        digest = hashlib.sha256(
            json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()[:16]
        return f"evt_claude_hook_{digest}"

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


async def forward_hook_payload(
    socket_path: str,
    raw_event: dict[str, Any],
    *,
    event_name: str | None = None,
    terminal_app: str | None = None,
    wait_for_interaction: bool = False,
    interaction_timeout: float = DEFAULT_INTERACTION_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    adapter = ClaudeHookAdapter()
    events = adapter.translate_hook(raw_event, event_name=event_name, terminal_app=terminal_app)
    interaction_id = _pending_interaction_id(events)
    if wait_for_interaction and interaction_id is not None:
        return await _forward_and_wait_for_interaction(socket_path, events, interaction_id, interaction_timeout=interaction_timeout)
    return await _forward_events(socket_path, events)


async def _forward_events(socket_path: str, events: list[EventEnvelope]) -> list[dict[str, Any]]:
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
                request_id=f"claude-hook-{index}",
            )
        )
    return responses


async def _forward_and_wait_for_interaction(
    socket_path: str,
    events: list[EventEnvelope],
    interaction_id: str,
    *,
    interaction_timeout: float,
) -> list[dict[str, Any]]:
    reader, writer = await asyncio.open_unix_connection(socket_path, limit=MAX_RESPONSE_BYTES)
    try:
        writer.write(to_json_line({"id": "claude-hook-subscribe", "method": "subscribe", "params": {"topics": ["interactions"]}}))
        await writer.drain()
        await asyncio.wait_for(reader.readline(), timeout=DEFAULT_HOOK_TIMEOUT_SECONDS)

        responses = await _forward_events(socket_path, events)
        resolution = await _wait_for_interaction_resolution(reader, interaction_id, timeout=interaction_timeout)
        if resolution is not None:
            responses.append({"ok": True, "result": {"interaction_resolution": resolution}})
        return responses
    finally:
        writer.close()
        await writer.wait_closed()


async def _wait_for_interaction_resolution(reader: asyncio.StreamReader, interaction_id: str, *, timeout: float) -> dict[str, Any] | None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return None
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=remaining)
        except TimeoutError:
            return None
        if not line:
            return None
        message = from_json_line(line)
        if _resolved_interaction_id(message) == interaction_id:
            payload = message.get("payload")
            return payload if isinstance(payload, dict) else None
        snapshot_resolution = _snapshot_interaction_resolution(message, interaction_id)
        if snapshot_resolution is not None:
            return snapshot_resolution


def _pending_interaction_id(events: list[EventEnvelope]) -> str | None:
    for event in events:
        if event.kind.startswith("interaction.") and event.kind.endswith(".requested"):
            value = event.payload.get("interaction_id")
            return value if isinstance(value, str) and value else None
    return None


def _resolved_interaction_id(message: dict[str, Any]) -> str | None:
    if message.get("kind") != "interaction.resolved":
        return None
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return None
    value = payload.get("interaction_id")
    return value if isinstance(value, str) and value else None


def _snapshot_interaction_resolution(message: dict[str, Any], interaction_id: str) -> dict[str, Any] | None:
    if message.get("kind") not in {"snapshot.full", "snapshot.patch"}:
        return None
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return None
    interactions = payload.get("interactions")
    if not isinstance(interactions, list):
        return None
    for interaction in interactions:
        if not isinstance(interaction, dict) or interaction.get("interaction_id") != interaction_id:
            continue
        if interaction.get("state") == "answered" and isinstance(interaction.get("answer_payload"), dict):
            return {
                "interaction_id": interaction_id,
                "state": interaction.get("state"),
                "answer_payload": interaction.get("answer_payload"),
            }
    return None


def claude_permission_response(resolution_payload: dict[str, Any]) -> dict[str, Any] | None:
    answer_payload = resolution_payload.get("answer_payload")
    if not isinstance(answer_payload, dict):
        return None
    action = answer_payload.get("action")
    answer = answer_payload.get("answer")
    if action == "approve":
        decision: dict[str, Any] = {"behavior": "allow"}
    elif action == "deny":
        message = answer if isinstance(answer, str) and answer else "Denied by CodeIsland"
        decision = {"behavior": "deny", "message": message}
    else:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": decision,
        }
    }


def default_claude_settings_path() -> Path:
    return Path(os.environ.get("CLAUDE_HOME", "~/.claude")).expanduser() / "settings.json"


def install_project_hooks(
    project_root: Path,
    *,
    socket_path: str,
    python: str | None = None,
    module_root: Path | None = None,
    interaction_timeout: float = DEFAULT_INTERACTION_TIMEOUT_SECONDS,
    backup: bool = False,
) -> Path:
    claude_dir = project_root / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    return install_settings_file(
        claude_dir / "settings.json",
        socket_path=socket_path,
        python=python,
        module_root=module_root,
        interaction_timeout=interaction_timeout,
        backup=backup,
    )


def install_global_hooks(
    settings_path: Path | None = None,
    *,
    socket_path: str,
    python: str | None = None,
    module_root: Path | None = None,
    interaction_timeout: float = DEFAULT_INTERACTION_TIMEOUT_SECONDS,
    backup: bool = False,
) -> Path:
    return install_settings_file(
        (settings_path or default_claude_settings_path()).expanduser(),
        socket_path=socket_path,
        python=python,
        module_root=module_root,
        interaction_timeout=interaction_timeout,
        backup=backup,
    )


def install_settings_file(
    settings_path: Path,
    *,
    socket_path: str,
    python: str | None = None,
    module_root: Path | None = None,
    interaction_timeout: float = DEFAULT_INTERACTION_TIMEOUT_SECONDS,
    backup: bool = False,
) -> Path:
    root = build_settings_file(settings_path, socket_path=socket_path, python=python, module_root=module_root, interaction_timeout=interaction_timeout)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    if backup and settings_path.exists():
        shutil.copy2(settings_path, _backup_path(settings_path))
    settings_path.write_text(json.dumps(root, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return settings_path


def build_settings_file(
    settings_path: Path,
    *,
    socket_path: str,
    python: str | None = None,
    module_root: Path | None = None,
    interaction_timeout: float = DEFAULT_INTERACTION_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    root = _read_settings_file(settings_path)
    hooks = root.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        root["hooks"] = hooks

    command_python = python or sys.executable
    command_module_root = module_root or Path(__file__).resolve().parents[1]
    for event_name in CODEISLAND_HOOK_EVENTS:
        entries = hooks.get(event_name)
        if not isinstance(entries, list):
            entries = []
        entries = [entry for entry in entries if not _contains_codeisland_hook(entry)]
        entries.append(
            {
                "matcher": "*",
                "hooks": [
                    {
                        "type": "command",
                        "command": _hook_command(
                            command_python,
                            command_module_root,
                            socket_path,
                            event_name,
                            interaction_timeout=interaction_timeout,
                        ),
                        "timeout": _hook_timeout(event_name, interaction_timeout),
                    }
                ],
            }
        )
        hooks[event_name] = entries
    return root


def _read_settings_file(settings_path: Path) -> dict[str, Any]:
    try:
        decoded = json.loads(settings_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise ClaudeHookError(f"invalid Claude settings JSON at {settings_path}: {exc.msg}") from exc
    if not isinstance(decoded, dict):
        raise ClaudeHookError(f"Claude settings must decode to an object: {settings_path}")
    return decoded


def _contains_codeisland_hook(entry: Any) -> bool:
    if isinstance(entry, dict):
        return any(_contains_codeisland_hook(value) for value in entry.values())
    if isinstance(entry, list):
        return any(_contains_codeisland_hook(item) for item in entry)
    return isinstance(entry, str) and CODEISLAND_HOOK_MARKER in entry


def _backup_path(settings_path: Path) -> Path:
    candidate = settings_path.with_name(f"{settings_path.name}.bak")
    if not candidate.exists():
        return candidate
    index = 1
    while True:
        candidate = settings_path.with_name(f"{settings_path.name}.bak.{index}")
        if not candidate.exists():
            return candidate
        index += 1


def _hook_timeout(event_name: str, interaction_timeout: float) -> int:
    if event_name == "PermissionRequest":
        return max(DEFAULT_HOOK_TIMEOUT_SECONDS, math.ceil(interaction_timeout) + DEFAULT_HOOK_TIMEOUT_SECONDS)
    return DEFAULT_HOOK_TIMEOUT_SECONDS


def _hook_command(python: str, module_root: Path, socket_path: str, event_name: str, *, interaction_timeout: float) -> str:
    parts = [
        f"PYTHONPATH={shlex.quote(str(module_root))}",
        shlex.quote(python),
        "-m",
        "codeisland_linux.claude_hook",
        "run",
        "--event",
        shlex.quote(event_name),
        "--socket-path",
        shlex.quote(socket_path),
    ]
    if event_name == "PermissionRequest":
        parts.extend(["--interaction-timeout", str(interaction_timeout)])
    return " ".join(parts)


async def _run_hook(args: argparse.Namespace) -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        wait_for_interaction = args.event == "PermissionRequest"
        responses = await forward_hook_payload(
            args.socket_path,
            payload,
            event_name=args.event,
            terminal_app=args.terminal_app,
            wait_for_interaction=wait_for_interaction,
            interaction_timeout=args.interaction_timeout,
        )
    except (OSError, RuntimeError, ValueError, ClaudeHookError) as exc:
        print(f"codeisland claude hook skipped: {exc}", file=sys.stderr)
        return 0
    if args.event == "PermissionRequest":
        response = _permission_response_from_forward_responses(responses)
        if response is not None:
            print(json.dumps(response, separators=(",", ":")))
        return 0
    if args.print_responses:
        for response in responses:
            print(json.dumps(response, sort_keys=True))
    return 0


def _permission_response_from_forward_responses(responses: list[dict[str, Any]]) -> dict[str, Any] | None:
    for response in reversed(responses):
        result = response.get("result")
        if not isinstance(result, dict):
            continue
        resolution = result.get("interaction_resolution")
        if isinstance(resolution, dict):
            return claude_permission_response(resolution)
    return None


def _run_install(args: argparse.Namespace) -> int:
    try:
        target = Path(args.project).expanduser().resolve() / ".claude" / "settings.json" if args.project else Path(args.settings).expanduser().resolve()
        if args.dry_run:
            preview = build_settings_file(target, socket_path=args.socket_path, python=args.python, interaction_timeout=args.interaction_timeout)
            print(json.dumps({"target": str(target), "content": preview}, indent=2, sort_keys=False))
            return 0
        if args.project:
            settings_path = install_project_hooks(
                Path(args.project).expanduser().resolve(),
                socket_path=args.socket_path,
                python=args.python,
                interaction_timeout=args.interaction_timeout,
                backup=args.backup,
            )
        else:
            settings_path = install_global_hooks(
                Path(args.settings).expanduser().resolve(),
                socket_path=args.socket_path,
                python=args.python,
                interaction_timeout=args.interaction_timeout,
                backup=args.backup,
            )
    except (OSError, ClaudeHookError) as exc:
        print(f"failed to install CodeIsland Claude hooks: {exc}", file=sys.stderr)
        return 1
    print(settings_path)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Bridge Claude Code hooks into the CodeIsland daemon")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Read one Claude Code hook payload from stdin and forward it")
    run_parser.add_argument("--socket-path", default=default_socket_path())
    run_parser.add_argument("--event", choices=CODEISLAND_HOOK_EVENTS)
    run_parser.add_argument("--terminal-app")
    run_parser.add_argument("--interaction-timeout", type=float, default=DEFAULT_INTERACTION_TIMEOUT_SECONDS)
    run_parser.add_argument("--print-responses", action="store_true")
    run_parser.set_defaults(func=lambda args: asyncio.run(_run_hook(args)))

    install_parser = subparsers.add_parser("install", help="Merge CodeIsland hook commands into Claude settings.json")
    install_parser.add_argument("--settings", default=str(default_claude_settings_path()), help="Claude settings.json path for global install")
    install_parser.add_argument("--project", help="Install into project .claude/settings.json instead of the global settings")
    install_parser.add_argument("--socket-path", default=default_socket_path())
    install_parser.add_argument("--python", help="Python executable used by generated hook commands")
    install_parser.add_argument("--interaction-timeout", type=float, default=DEFAULT_INTERACTION_TIMEOUT_SECONDS)
    install_parser.add_argument("--dry-run", action="store_true", help="Print the merged settings content without writing it")
    install_parser.add_argument("--backup", action="store_true", help="Copy the existing settings.json before writing")
    install_parser.set_defaults(func=_run_install)

    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
