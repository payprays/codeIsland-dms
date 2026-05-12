from __future__ import annotations

import argparse
import asyncio
from typing import Any

from .client import rpc_call
from .protocol import default_socket_path, now_iso


def build_scenario(name: str) -> list[dict[str, Any]]:
    session_id = "ses_demo"
    task_id = "task_demo"
    common = {"session_id": session_id, "task_id": task_id, "ts": now_iso()}

    if name == "happy-path":
        return [
            {"event_id": "evt_session_started", "kind": "session.started", "payload": {"provider": "opencode", "title": "Demo Session", "project_root": "/tmp/demo"}, "session_id": session_id, "ts": now_iso()},
            {"event_id": "evt_task_started", "kind": "task.started", "payload": {"task_id": task_id, "prompt": "hello"}, **common},
            {"event_id": "evt_task_progress", "kind": "task.progress", "payload": {"message": "working"}, **common},
            {"event_id": "evt_task_completed", "kind": "task.completed", "payload": {"task_id": task_id, "summary": "done"}, **common},
        ]
    if name == "approval":
        return [
            {"event_id": "evt_session_started", "kind": "session.started", "payload": {"provider": "opencode", "title": "Approval Session", "project_root": "/tmp/demo"}, "session_id": session_id, "ts": now_iso()},
            {"event_id": "evt_task_started", "kind": "task.started", "payload": {"task_id": task_id, "prompt": "needs approval"}, **common},
            {"event_id": "evt_approval", "kind": "interaction.approval.requested", "payload": {"interaction_id": "int_approval", "prompt_text": "Allow tool use?", "options": ["approve", "deny"]}, **common},
        ]
    if name == "codex":
        codex_session_id = "codex-demo"
        codex_task_id = "task_codex_demo"
        codex_common = {"session_id": codex_session_id, "task_id": codex_task_id, "ts": now_iso()}
        return [
            {"event_id": "evt_codex_session_started", "kind": "session.started", "payload": {"provider": "codex", "title": "Codex Demo", "project_root": "/tmp/codex-demo", "terminal_app": "Ghostty"}, "session_id": codex_session_id, "ts": now_iso()},
            {"event_id": "evt_codex_task_started", "kind": "task.started", "payload": {"task_id": codex_task_id, "prompt": "Fix the login bug"}, **codex_common},
            {"event_id": "evt_codex_prompt", "kind": "prompt.submitted", "payload": {"task_id": codex_task_id, "prompt": "Fix the login bug"}, **codex_common},
            {"event_id": "evt_codex_tool", "kind": "tool.use.started", "payload": {"tool_name": "pytest"}, **codex_common},
        ]
    if name == "board-demo":
        return [
            {"event_id": "evt_board_opencode_session", "kind": "session.started", "payload": {"provider": "opencode", "title": "codeIsland", "project_root": "/tmp/codeIsland", "terminal_app": "Ghostty"}, "session_id": "opencode-board", "ts": now_iso()},
            {"event_id": "evt_board_opencode_task", "kind": "task.started", "payload": {"task_id": "task_opencode_board", "prompt": "Port the grouped board to DMS"}, "session_id": "opencode-board", "task_id": "task_opencode_board", "ts": now_iso()},
            {"event_id": "evt_board_opencode_prompt", "kind": "prompt.submitted", "payload": {"task_id": "task_opencode_board", "prompt": "Port the grouped board to DMS"}, "session_id": "opencode-board", "task_id": "task_opencode_board", "ts": now_iso()},
            {"event_id": "evt_board_opencode_tool", "kind": "tool.use.started", "payload": {"tool_name": "qmlscene"}, "session_id": "opencode-board", "task_id": "task_opencode_board", "ts": now_iso()},
            {"event_id": "evt_board_codex_session", "kind": "session.started", "payload": {"provider": "codex", "title": "api", "project_root": "/tmp/api", "terminal_app": "Ghostty"}, "session_id": "codex-board", "ts": now_iso()},
            {"event_id": "evt_board_codex_task", "kind": "task.started", "payload": {"task_id": "task_codex_board", "prompt": "Fix the login bug"}, "session_id": "codex-board", "task_id": "task_codex_board", "ts": now_iso()},
            {"event_id": "evt_board_codex_prompt", "kind": "prompt.submitted", "payload": {"task_id": "task_codex_board", "prompt": "Fix the login bug"}, "session_id": "codex-board", "task_id": "task_codex_board", "ts": now_iso()},
            {"event_id": "evt_board_codex_tool", "kind": "tool.use.started", "payload": {"tool_name": "pytest"}, "session_id": "codex-board", "task_id": "task_codex_board", "ts": now_iso()},
            {"event_id": "evt_board_claude_session", "kind": "session.started", "payload": {"provider": "claude", "title": "vibe-notch", "project_root": "/tmp/vibe-notch", "terminal_app": "Ghostty"}, "session_id": "claude-board", "ts": now_iso()},
            {"event_id": "evt_board_claude_task", "kind": "task.started", "payload": {"task_id": "task_claude_board", "prompt": "Update README screenshots"}, "session_id": "claude-board", "task_id": "task_claude_board", "ts": now_iso()},
            {"event_id": "evt_board_claude_prompt", "kind": "prompt.submitted", "payload": {"task_id": "task_claude_board", "prompt": "Update README screenshots"}, "session_id": "claude-board", "task_id": "task_claude_board", "ts": now_iso()},
            {"event_id": "evt_board_claude_done", "kind": "task.completed", "payload": {"task_id": "task_claude_board", "summary": "README screenshots updated"}, "session_id": "claude-board", "task_id": "task_claude_board", "ts": now_iso()},
            {"event_id": "evt_board_gemini_session", "kind": "session.started", "payload": {"provider": "gemini", "title": "web", "project_root": "/tmp/web", "terminal_app": "iTerm2"}, "session_id": "gemini-board", "ts": now_iso()},
            {"event_id": "evt_board_gemini_task", "kind": "task.started", "payload": {"task_id": "task_gemini_board", "prompt": "Review the landing page copy"}, "session_id": "gemini-board", "task_id": "task_gemini_board", "ts": now_iso()},
            {"event_id": "evt_board_gemini_done", "kind": "task.completed", "payload": {"task_id": "task_gemini_board", "summary": "Copy review complete"}, "session_id": "gemini-board", "task_id": "task_gemini_board", "ts": now_iso()},
        ]
    if name == "duplicate-replay":
        event = {"event_id": "evt_duplicate", "kind": "session.started", "payload": {"provider": "opencode", "title": "Duplicate Session", "project_root": "/tmp/demo"}, "session_id": session_id, "ts": now_iso()}
        return [event, event]
    raise ValueError(f"unknown scenario: {name}")


async def run_fixture(socket_path: str, scenario: str) -> None:
    for index, event in enumerate(build_scenario(scenario), start=1):
        result = await rpc_call(socket_path, "ingest_event", event, request_id=f"req-{index}")
        print(result)


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 0 synthetic OpenCode fixture")
    parser.add_argument("--socket-path", default=default_socket_path())
    parser.add_argument("--scenario", choices=["happy-path", "approval", "codex", "board-demo", "duplicate-replay"], default="happy-path")
    args = parser.parse_args()
    asyncio.run(run_fixture(args.socket_path, args.scenario))


if __name__ == "__main__":
    main()
