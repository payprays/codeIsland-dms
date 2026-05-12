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
from .codex_adapter import MAX_TEXT_CHARS, _terminal_app
from .protocol import EventEnvelope, default_socket_path, from_json_line, now_iso, to_json_line


CODEISLAND_HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "Stop",
)
DEFAULT_INSTALLED_HOOK_EVENTS = (
    "UserPromptSubmit",
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
)
LATENCY_SENSITIVE_HOOK_EVENTS = (
    "SessionStart",
    "Stop",
)
CODEISLAND_HOOK_MARKER = "codeisland_linux.codex_hook"
DEFAULT_INTERACTION_TIMEOUT_SECONDS = 300.0
DEFAULT_HOOK_TIMEOUT_SECONDS = 5


class CodexHookError(Exception):
    pass


class CodexHookAdapter:
    def translate_hook(self, raw_event: dict[str, Any], *, event_name: str | None = None, terminal_app: str | None = None) -> list[EventEnvelope]:
        if not isinstance(raw_event, dict):
            raise CodexHookError("Codex hook payload must be an object")

        hook_event_name = self._string(raw_event.get("hook_event_name")) or event_name
        if not hook_event_name:
            raise CodexHookError("Codex hook payload requires hook_event_name")

        codex_session_id = self._string(raw_event.get("session_id"))
        if not codex_session_id:
            raise CodexHookError("Codex hook payload requires session_id")

        session_id = self._daemon_session_id(codex_session_id)
        timestamp = now_iso()
        terminal_label = terminal_app or _terminal_app(os.environ)
        events: list[EventEnvelope] = []

        if hook_event_name == "SessionStart":
            return [self._session_started(raw_event, session_id, codex_session_id, timestamp, terminal_label)]

        events.append(self._session_started(raw_event, session_id, codex_session_id, timestamp, terminal_label, ensure=True))

        turn_id = self._string(raw_event.get("turn_id"))
        task_id = self._task_id(session_id, turn_id) if turn_id else None
        if task_id is not None:
            events.append(self._task_started(raw_event, session_id, task_id, timestamp, synthetic=hook_event_name != "UserPromptSubmit"))

        if hook_event_name == "UserPromptSubmit":
            prompt = self._trim(self._string(raw_event.get("prompt")))
            if prompt and task_id:
                events.append(
                    EventEnvelope(
                        event_id=self._event_id(session_id, hook_event_name, "prompt.submitted", turn_id, prompt),
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
            tool_use_id = self._string(raw_event.get("tool_use_id")) or "unknown-tool"
            events.append(
                EventEnvelope(
                    event_id=self._event_id(session_id, hook_event_name, "tool.use.started", turn_id, tool_use_id),
                    session_id=session_id,
                    task_id=task_id,
                    kind="tool.use.started",
                    payload={
                        "task_id": task_id,
                        "tool_name": self._tool_name(raw_event),
                        "tool_use_id": tool_use_id,
                        "input": raw_event.get("tool_input"),
                    },
                    ts=timestamp,
                )
            )
            return events

        if hook_event_name == "PermissionRequest":
            if task_id is None:
                return events
            interaction_id = self._permission_interaction_id(session_id, turn_id, raw_event)
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
                            "tool_input": raw_event.get("tool_input"),
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
                            "tool_input": raw_event.get("tool_input"),
                            "permission_mode": raw_event.get("permission_mode"),
                        },
                        ts=timestamp,
                    ),
                ]
            )
            return events

        if hook_event_name == "PostToolUse":
            if task_id is None:
                return events
            tool_use_id = self._string(raw_event.get("tool_use_id")) or "unknown-tool"
            failed = self._tool_response_failed(raw_event.get("tool_response"))
            events.append(
                EventEnvelope(
                    event_id=self._event_id(session_id, hook_event_name, "tool.use.finished", turn_id, tool_use_id),
                    session_id=session_id,
                    task_id=task_id,
                    kind="tool.use.failed" if failed else "tool.use.completed",
                    payload={
                        "task_id": task_id,
                        "tool_name": self._tool_name(raw_event),
                        "tool_use_id": tool_use_id,
                        "result": raw_event.get("tool_response"),
                        "success": not failed,
                    },
                    ts=timestamp,
                )
            )
            return events

        if hook_event_name == "Stop":
            if task_id is None:
                return events
            summary = self._trim(self._string(raw_event.get("last_assistant_message")))
            if summary:
                events.append(
                    EventEnvelope(
                        event_id=self._event_id(session_id, hook_event_name, "assistant.response.completed", turn_id, summary),
                        session_id=session_id,
                        task_id=task_id,
                        kind="assistant.response.completed",
                        payload={"task_id": task_id, "message": summary, "phase": "final_answer"},
                        ts=timestamp,
                    )
                )
            events.append(
                EventEnvelope(
                    event_id=self._event_id(session_id, hook_event_name, "task.completed", turn_id),
                    session_id=session_id,
                    task_id=task_id,
                    kind="task.completed",
                    payload={"task_id": task_id, "summary": summary},
                    ts=timestamp,
                )
            )
            return events

        return events

    def _session_started(
        self,
        raw_event: dict[str, Any],
        session_id: str,
        codex_session_id: str,
        timestamp: str,
        terminal_app: str | None,
        *,
        ensure: bool = False,
    ) -> EventEnvelope:
        project_root = self._string(raw_event.get("cwd"))
        title = Path(project_root).name if project_root else codex_session_id
        return EventEnvelope(
            event_id=self._event_id(session_id, "SessionStart", "session.started", "ensure" if ensure else raw_event.get("source")),
            session_id=session_id,
            kind="session.started",
            payload={
                "provider": "codex",
                "source": "codex",
                "title": title or codex_session_id,
                "project_root": project_root,
                "terminal_app": terminal_app,
                "codex_session_id": codex_session_id,
                "model": raw_event.get("model"),
                "permission_mode": raw_event.get("permission_mode"),
                "transcript_path": raw_event.get("transcript_path"),
            },
            ts=timestamp,
        )

    def _task_started(self, raw_event: dict[str, Any], session_id: str, task_id: str, timestamp: str, *, synthetic: bool) -> EventEnvelope:
        prompt = self._trim(self._string(raw_event.get("prompt")))
        return EventEnvelope(
            event_id=self._event_id(session_id, "task.started", task_id),
            session_id=session_id,
            task_id=task_id,
            kind="task.started",
            payload={
                "task_id": task_id,
                "prompt": prompt,
                "turn_id": raw_event.get("turn_id"),
                "synthetic": synthetic,
            },
            ts=timestamp,
        )

    @staticmethod
    def _daemon_session_id(codex_session_id: str) -> str:
        return codex_session_id if codex_session_id.startswith("codex-") else f"codex-{codex_session_id}"

    @staticmethod
    def _task_id(session_id: str, turn_id: str) -> str:
        return f"{session_id}-turn-{turn_id}"

    @staticmethod
    def _tool_name(raw_event: dict[str, Any]) -> str:
        value = raw_event.get("tool_name")
        return value if isinstance(value, str) and value else "tool"

    @classmethod
    def _approval_prompt(cls, raw_event: dict[str, Any]) -> str:
        tool_name = cls._tool_name(raw_event)
        tool_input = raw_event.get("tool_input")
        if tool_input is not None:
            return f"Allow {tool_name}: {cls._trim(json.dumps(tool_input, ensure_ascii=False, sort_keys=True, default=str))}"
        return f"Allow {tool_name}?"

    @classmethod
    def _permission_interaction_id(cls, session_id: str, turn_id: str | None, raw_event: dict[str, Any]) -> str:
        tool_use_id = cls._string(raw_event.get("tool_use_id"))
        if tool_use_id:
            return f"{session_id}-permission-{tool_use_id}"
        digest = hashlib.sha256(
            json.dumps(
                {
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "tool_name": raw_event.get("tool_name"),
                    "tool_input": raw_event.get("tool_input"),
                },
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:16]
        return f"{session_id}-permission-{digest}"

    @staticmethod
    def _tool_response_failed(tool_response: Any) -> bool:
        if not isinstance(tool_response, dict):
            return False
        success = tool_response.get("success")
        if success is False:
            return True
        for key in ("exit_code", "exitCode", "status"):
            value = tool_response.get(key)
            if isinstance(value, int) and value != 0:
                return True
        metadata = tool_response.get("metadata")
        if isinstance(metadata, dict):
            exit_code = metadata.get("exit_code")
            if isinstance(exit_code, int) and exit_code != 0:
                return True
        return any(isinstance(tool_response.get(key), str) and tool_response.get(key) for key in ("error", "error_message", "stderr"))

    @staticmethod
    def _event_id(*parts: Any) -> str:
        digest = hashlib.sha256(
            json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()[:16]
        return f"evt_codex_hook_{digest}"

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
    adapter = CodexHookAdapter()
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
                request_id=f"codex-hook-{index}",
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
        writer.write(to_json_line({"id": "codex-hook-subscribe", "method": "subscribe", "params": {"topics": ["interactions"]}}))
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


async def _wait_for_interaction_resolution(
    reader: asyncio.StreamReader,
    interaction_id: str,
    *,
    timeout: float,
) -> dict[str, Any] | None:
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


def codex_permission_response(resolution_payload: dict[str, Any]) -> dict[str, Any] | None:
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


def install_project_hooks(
    project_root: Path,
    *,
    socket_path: str,
    python: str | None = None,
    module_root: Path | None = None,
    interaction_timeout: float = DEFAULT_INTERACTION_TIMEOUT_SECONDS,
    backup: bool = False,
) -> Path:
    codex_dir = project_root / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    return install_hooks_file(
        codex_dir / "hooks.json",
        socket_path=socket_path,
        python=python,
        module_root=module_root,
        interaction_timeout=interaction_timeout,
        backup=backup,
    )


def install_global_hooks(
    codex_home: Path,
    *,
    socket_path: str,
    python: str | None = None,
    module_root: Path | None = None,
    interaction_timeout: float = DEFAULT_INTERACTION_TIMEOUT_SECONDS,
    backup: bool = False,
) -> Path:
    codex_home.mkdir(parents=True, exist_ok=True)
    return install_hooks_file(
        codex_home / "hooks.json",
        socket_path=socket_path,
        python=python,
        module_root=module_root,
        interaction_timeout=interaction_timeout,
        backup=backup,
    )


def install_hooks_file(
    hooks_path: Path,
    *,
    socket_path: str,
    python: str | None = None,
    module_root: Path | None = None,
    interaction_timeout: float = DEFAULT_INTERACTION_TIMEOUT_SECONDS,
    backup: bool = False,
) -> Path:
    root = build_hooks_file(hooks_path, socket_path=socket_path, python=python, module_root=module_root, interaction_timeout=interaction_timeout)
    if backup and hooks_path.exists():
        shutil.copy2(hooks_path, _backup_path(hooks_path))
    hooks_path.write_text(json.dumps(root, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return hooks_path


def build_hooks_file(
    hooks_path: Path,
    *,
    socket_path: str,
    python: str | None = None,
    module_root: Path | None = None,
    interaction_timeout: float = DEFAULT_INTERACTION_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    root = _read_hooks_file(hooks_path)
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
        if event_name in DEFAULT_INSTALLED_HOOK_EVENTS:
            entries.append(
                {
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
                    ]
                }
            )
        if entries:
            hooks[event_name] = entries
        else:
            hooks.pop(event_name, None)

    return root


def _read_hooks_file(hooks_path: Path) -> dict[str, Any]:
    try:
        decoded = json.loads(hooks_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"hooks": {}}
    except json.JSONDecodeError as exc:
        raise CodexHookError(f"invalid hooks.json at {hooks_path}: {exc.msg}") from exc
    if not isinstance(decoded, dict):
        raise CodexHookError(f"hooks.json must decode to an object: {hooks_path}")
    return decoded


def _contains_codeisland_hook(entry: Any) -> bool:
    if isinstance(entry, dict):
        for value in entry.values():
            if _contains_codeisland_hook(value):
                return True
        return False
    if isinstance(entry, list):
        return any(_contains_codeisland_hook(item) for item in entry)
    return isinstance(entry, str) and CODEISLAND_HOOK_MARKER in entry


def _backup_path(hooks_path: Path) -> Path:
    candidate = hooks_path.with_name(f"{hooks_path.name}.bak")
    if not candidate.exists():
        return candidate
    index = 1
    while True:
        candidate = hooks_path.with_name(f"{hooks_path.name}.bak.{index}")
        if not candidate.exists():
            return candidate
        index += 1


def _hook_timeout(event_name: str, interaction_timeout: float) -> int:
    if event_name == "PermissionRequest":
        return max(DEFAULT_HOOK_TIMEOUT_SECONDS, math.ceil(interaction_timeout) + DEFAULT_HOOK_TIMEOUT_SECONDS)
    return DEFAULT_HOOK_TIMEOUT_SECONDS


def _hook_command(
    python: str,
    module_root: Path,
    socket_path: str,
    event_name: str,
    *,
    interaction_timeout: float = DEFAULT_INTERACTION_TIMEOUT_SECONDS,
) -> str:
    env_assignment = f"PYTHONPATH={shlex.quote(str(module_root))}"
    parts = [
        env_assignment,
        shlex.quote(python),
        "-m",
        "codeisland_linux.codex_hook",
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
    except (OSError, RuntimeError, ValueError, CodexHookError) as exc:
        print(f"codeisland codex hook skipped: {exc}", file=sys.stderr)
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
            return codex_permission_response(resolution)
    return None


def _run_install(args: argparse.Namespace) -> int:
    try:
        target = Path(args.codex_home).expanduser().resolve() / "hooks.json" if args.global_hooks else Path(args.project).expanduser().resolve() / ".codex" / "hooks.json"
        if args.dry_run:
            preview = build_hooks_file(target, socket_path=args.socket_path, python=args.python, interaction_timeout=args.interaction_timeout)
            print(json.dumps({"target": str(target), "content": preview}, indent=2, sort_keys=False))
            return 0
        if args.global_hooks:
            hooks_path = install_global_hooks(
                Path(args.codex_home).expanduser().resolve(),
                socket_path=args.socket_path,
                python=args.python,
                interaction_timeout=args.interaction_timeout,
                backup=args.backup,
            )
        else:
            hooks_path = install_project_hooks(
                Path(args.project).expanduser().resolve(),
                socket_path=args.socket_path,
                python=args.python,
                interaction_timeout=args.interaction_timeout,
                backup=args.backup,
            )
    except (OSError, CodexHookError) as exc:
        print(f"failed to install CodeIsland Codex hooks: {exc}", file=sys.stderr)
        return 1
    print(hooks_path)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Bridge Codex hooks into the CodeIsland daemon")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Read one Codex hook payload from stdin and forward it")
    run_parser.add_argument("--socket-path", default=default_socket_path())
    run_parser.add_argument("--event", choices=CODEISLAND_HOOK_EVENTS)
    run_parser.add_argument("--terminal-app")
    run_parser.add_argument("--interaction-timeout", type=float, default=DEFAULT_INTERACTION_TIMEOUT_SECONDS)
    run_parser.add_argument("--print-responses", action="store_true")
    run_parser.set_defaults(func=lambda args: asyncio.run(_run_hook(args)))

    install_parser = subparsers.add_parser("install", help="Merge CodeIsland hook commands into Codex hooks.json")
    install_parser.add_argument("--global", dest="global_hooks", action="store_true", help="Install into CODEX_HOME/hooks.json instead of a project .codex/hooks.json")
    install_parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", "~/.codex"), help="Codex home used with --global")
    install_parser.add_argument("--project", default=os.getcwd())
    install_parser.add_argument("--socket-path", default=default_socket_path())
    install_parser.add_argument("--python", help="Python executable used by generated hook commands")
    install_parser.add_argument("--interaction-timeout", type=float, default=DEFAULT_INTERACTION_TIMEOUT_SECONDS)
    install_parser.add_argument("--dry-run", action="store_true", help="Print the merged hooks.json content without writing it")
    install_parser.add_argument("--backup", action="store_true", help="Copy the existing hooks.json to hooks.json.bak before writing")
    install_parser.set_defaults(func=_run_install)

    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
