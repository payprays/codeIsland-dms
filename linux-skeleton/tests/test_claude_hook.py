from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeisland_linux.claude_hook import ClaudeHookAdapter, claude_permission_response, forward_hook_payload, install_global_hooks, install_project_hooks
from codeisland_linux.client import rpc_call
from codeisland_linux.server import Phase0Daemon
from codeisland_linux.store import InMemoryDaemonStore


def write_claude_transcript(path: Path, *, session_id: str = "ses_claude_hook", prompt: str = "Wire Claude hooks", assistant: str = "Done.") -> None:
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "timestamp": "2026-05-11T02:00:00.000Z",
                        "cwd": "/tmp/claude-hook",
                        "sessionId": session_id,
                        "message": {"role": "user", "content": prompt},
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": "2026-05-11T02:00:02.000Z",
                        "cwd": "/tmp/claude-hook",
                        "sessionId": session_id,
                        "message": {"role": "assistant", "content": [{"type": "text", "text": assistant}], "stop_reason": "end_turn"},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )


class ClaudeHookAdapterTests(unittest.TestCase):
    def test_hook_sequence_reaches_completed_store_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript_path = Path(temp_dir) / "session.jsonl"
            write_claude_transcript(transcript_path)
            adapter = ClaudeHookAdapter()
            store = InMemoryDaemonStore()
            base = {
                "session_id": "ses_claude_hook",
                "cwd": "/tmp/claude-hook",
                "model": "claude-sonnet",
                "permission_mode": "default",
                "transcript_path": str(transcript_path),
            }
            raw_events = [
                {"hook_event_name": "SessionStart", **base},
                {"hook_event_name": "UserPromptSubmit", "prompt": "Wire Claude hooks", **base},
                {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "pwd"}, "tool_use_id": "call-1", **base},
                {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_response": {"exit_code": 0, "stdout": "/tmp/claude-hook"}, "tool_use_id": "call-1", **base},
                {"hook_event_name": "Stop", **base},
            ]

            for raw_event in raw_events:
                for event in adapter.translate_hook(raw_event, terminal_app="WezTerm"):
                    store.apply_event(event)

        session_id = "claude-ses_claude_hook"
        session = store.sessions[session_id]
        state = store.session_states[session_id]

        self.assertEqual(session.provider, "claude")
        self.assertEqual(session.terminal_app, "WezTerm")
        self.assertEqual(session.status, "completed")
        self.assertEqual(state.last_user_prompt, "Wire Claude hooks")
        self.assertEqual(state.last_assistant_message, "Done.")
        self.assertTrue(state.completion_pending)

    def test_permission_request_opens_approval_interaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript_path = Path(temp_dir) / "session.jsonl"
            write_claude_transcript(transcript_path)
            events = ClaudeHookAdapter().translate_hook(
                {
                    "hook_event_name": "PermissionRequest",
                    "session_id": "ses_claude_hook",
                    "cwd": "/tmp/claude-hook",
                    "transcript_path": str(transcript_path),
                    "tool_name": "Bash",
                    "tool_input": {"command": "rm file"},
                    "tool_use_id": "call-1",
                }
            )

        self.assertEqual(
            [event.kind for event in events],
            ["session.started", "task.started", "interaction.approval.requested", "permission.requested"],
        )
        self.assertEqual(events[2].payload["interaction_id"], "claude-ses_claude_hook-permission-call-1")
        self.assertEqual(events[3].payload["tool_name"], "Bash")

    def test_session_started_includes_terminal_identity_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TERM_PROGRAM": "WezTerm",
                "WEZTERM_UNIX_SOCKET": "/run/user/1000/wezterm/gui-sock-5678",
                "WEZTERM_PANE": "9",
            },
        ):
            events = ClaudeHookAdapter().translate_hook(
                {
                    "hook_event_name": "SessionStart",
                    "session_id": "ses_claude_hook",
                    "cwd": "/tmp/claude-hook",
                }
            )

        payload = events[0].payload
        self.assertEqual(payload["terminal_app"], "WezTerm")
        self.assertEqual(payload["terminal_pid"], 5678)
        self.assertEqual(payload["terminal_pane"], "9")
        self.assertEqual(payload["terminal_socket"], "/run/user/1000/wezterm/gui-sock-5678")

    def test_permission_response_uses_hook_output_shape(self) -> None:
        self.assertEqual(
            claude_permission_response({"answer_payload": {"action": "approve", "answer": None}}),
            {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}},
        )
        self.assertEqual(
            claude_permission_response({"answer_payload": {"action": "deny", "answer": "No"}}),
            {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "deny", "message": "No"}}},
        )


class ClaudeHookForwardingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.socket_path = str(Path(self.temp_dir.name) / "codeislandd.sock")
        self.daemon = Phase0Daemon(self.socket_path)
        await self.daemon.start()
        self.serve_task = asyncio.create_task(self.daemon.serve_forever())
        await asyncio.sleep(0.05)

    async def asyncTearDown(self) -> None:
        self.serve_task.cancel()
        try:
            await self.serve_task
        except asyncio.CancelledError:
            pass
        await self.daemon.shutdown()
        self.temp_dir.cleanup()

    async def test_permission_request_waits_for_daemon_resolution(self) -> None:
        transcript_path = Path(self.temp_dir.name) / "session.jsonl"
        write_claude_transcript(transcript_path)
        forward_task = asyncio.create_task(
            forward_hook_payload(
                self.socket_path,
                {
                    "hook_event_name": "PermissionRequest",
                    "session_id": "ses_wait",
                    "cwd": "/tmp/claude-hook",
                    "transcript_path": str(transcript_path),
                    "tool_name": "Bash",
                    "tool_input": {"command": "pwd"},
                    "tool_use_id": "call-1",
                },
                wait_for_interaction=True,
                interaction_timeout=2.0,
            )
        )
        interaction_id = "claude-ses_wait-permission-call-1"
        for _ in range(20):
            if interaction_id in self.daemon.store.interactions:
                break
            await asyncio.sleep(0.05)

        response = await rpc_call(
            self.socket_path,
            "interaction_respond",
            {"interaction_id": interaction_id, "action": "approve", "answer": None},
            request_id="approve-claude",
        )
        self.assertTrue(response["ok"])

        responses = await forward_task
        self.assertEqual(claude_permission_response(responses[-1]["result"]["interaction_resolution"]), {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}})


class ClaudeHookInstallTests(unittest.TestCase):
    def test_install_merges_with_existing_settings_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            claude_dir = project / ".claude"
            claude_dir.mkdir()
            settings_path = claude_dir / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "model": "gpt-5.4",
                        "hooks": {
                            "UserPromptSubmit": [
                                {
                                    "matcher": "*",
                                    "hooks": [{"type": "command", "command": "existing hook", "timeout": 1}],
                                }
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )

            install_project_hooks(project, socket_path="/tmp/codeisland.sock", python="python3", module_root=Path("/opt/codeIsland/linux-skeleton"))
            install_project_hooks(project, socket_path="/tmp/codeisland.sock", python="python3", module_root=Path("/opt/codeIsland/linux-skeleton"))
            installed = json.loads(settings_path.read_text(encoding="utf-8"))

        self.assertEqual(installed["model"], "gpt-5.4")
        self.assertEqual(len(installed["hooks"]["UserPromptSubmit"]), 2)
        self.assertIn("existing hook", json.dumps(installed["hooks"]["UserPromptSubmit"]))
        for event_name in ("SessionStart", "PreToolUse", "PermissionRequest", "PostToolUse", "Stop", "SessionEnd"):
            self.assertEqual(len(installed["hooks"][event_name]), 1)
            self.assertIn("codeisland_linux.claude_hook", json.dumps(installed["hooks"][event_name]))
        self.assertEqual(installed["hooks"]["PermissionRequest"][0]["hooks"][0]["timeout"], 305)

    def test_global_install_writes_settings_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"

            installed_path = install_global_hooks(settings_path, socket_path="/tmp/codeisland.sock", python="python3", module_root=Path("/opt/codeIsland/linux-skeleton"))
            installed = json.loads(installed_path.read_text(encoding="utf-8"))

        self.assertEqual(installed_path, settings_path)
        for event_name in ("SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest", "PostToolUse", "Stop", "SessionEnd"):
            self.assertEqual(len(installed["hooks"][event_name]), 1)
            self.assertIn("codeisland_linux.claude_hook", json.dumps(installed["hooks"][event_name]))

    def test_install_backup_preserves_existing_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            original = {"model": "gpt-5.4", "hooks": {}}
            settings_path.write_text(json.dumps(original), encoding="utf-8")

            install_global_hooks(settings_path, socket_path="/tmp/codeisland.sock", python="python3", module_root=Path("/opt/codeIsland/linux-skeleton"), backup=True)

            backup = settings_path.with_name("settings.json.bak")
            self.assertTrue(backup.exists())
            self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), original)


if __name__ == "__main__":
    unittest.main()
