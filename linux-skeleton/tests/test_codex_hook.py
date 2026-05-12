from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from codeisland_linux.client import rpc_call
from codeisland_linux.codex_hook import CodexHookAdapter, codex_permission_response, forward_hook_payload, install_global_hooks, install_project_hooks
from codeisland_linux.server import Phase0Daemon
from codeisland_linux.store import InMemoryDaemonStore


class CodexHookAdapterTests(unittest.TestCase):
    def test_hook_sequence_reaches_completed_store_state(self) -> None:
        adapter = CodexHookAdapter()
        store = InMemoryDaemonStore()
        base = {
            "session_id": "019e1032-bf9d-7651-abf5-521defb1b7b9",
            "turn_id": "turn-1",
            "cwd": "/tmp/codeIsland",
            "model": "gpt-5.5",
            "permission_mode": "never",
            "transcript_path": "/tmp/rollout.jsonl",
        }
        raw_events = [
            {"hook_event_name": "SessionStart", **base},
            {"hook_event_name": "UserPromptSubmit", "prompt": "Wire Codex hooks", **base},
            {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "pwd"}, "tool_use_id": "call-1", **base},
            {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "pwd"}, "tool_response": {"exit_code": 0, "stdout": "/tmp/codeIsland"}, "tool_use_id": "call-1", **base},
            {"hook_event_name": "Stop", "last_assistant_message": "Done.", **base},
        ]

        for raw_event in raw_events:
            for event in adapter.translate_hook(raw_event, terminal_app="Ghostty"):
                store.apply_event(event)

        session_id = "codex-019e1032-bf9d-7651-abf5-521defb1b7b9"
        session = store.sessions[session_id]
        state = store.session_states[session_id]

        self.assertEqual(session.provider, "codex")
        self.assertEqual(session.terminal_app, "Ghostty")
        self.assertEqual(session.status, "completed")
        self.assertEqual(state.last_user_prompt, "Wire Codex hooks")
        self.assertEqual(state.last_assistant_message, "Done.")
        self.assertTrue(state.completion_pending)

    def test_hook_event_override_supports_commands_that_pass_event_flag(self) -> None:
        events = CodexHookAdapter().translate_hook(
            {
                "session_id": "sid",
                "turn_id": "turn",
                "cwd": "/tmp/project",
                "prompt": "hello",
            },
            event_name="UserPromptSubmit",
        )

        self.assertEqual([event.kind for event in events], ["session.started", "task.started", "prompt.submitted"])
        self.assertFalse(events[1].payload["synthetic"])

    def test_permission_request_opens_approval_interaction(self) -> None:
        adapter = CodexHookAdapter()
        store = InMemoryDaemonStore()

        events = adapter.translate_hook(
            {
                "hook_event_name": "PermissionRequest",
                "session_id": "sid",
                "turn_id": "turn",
                "cwd": "/tmp/project",
                "tool_name": "Bash",
                "tool_input": {"command": "rm file"},
                "tool_use_id": "call-1",
            }
        )

        self.assertEqual(
            [event.kind for event in events],
            ["session.started", "task.started", "interaction.approval.requested", "permission.requested"],
        )
        self.assertEqual(events[2].payload["interaction_id"], "codex-sid-permission-call-1")
        self.assertEqual(events[2].payload["options"], ["approve", "deny"])
        self.assertEqual(events[3].payload["tool_name"], "Bash")

        for event in events:
            store.apply_event(event)

        self.assertEqual(store.sessions["codex-sid"].status, "waiting_approval")
        self.assertEqual(store.tasks["codex-sid-turn-turn"].status, "blocked")
        self.assertIn("permission.requested", [event.kind for event in store.activities])

    def test_permission_response_uses_codex_hook_output_shape(self) -> None:
        self.assertEqual(
            codex_permission_response({"answer_payload": {"action": "approve", "answer": None}}),
            {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}},
        )
        self.assertEqual(
            codex_permission_response({"answer_payload": {"action": "deny", "answer": "No"}}),
            {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "deny", "message": "No"}}},
        )


class CodexHookForwardingTests(unittest.IsolatedAsyncioTestCase):
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
        forward_task = asyncio.create_task(
            forward_hook_payload(
                self.socket_path,
                {
                    "hook_event_name": "PermissionRequest",
                    "session_id": "sid",
                    "turn_id": "turn",
                    "cwd": "/tmp/project",
                    "tool_name": "Bash",
                    "tool_input": {"command": "pwd"},
                    "tool_use_id": "call-1",
                },
                wait_for_interaction=True,
                interaction_timeout=2.0,
            )
        )
        interaction_id = "codex-sid-permission-call-1"
        for _ in range(20):
            if interaction_id in self.daemon.store.interactions:
                break
            await asyncio.sleep(0.05)

        response = await rpc_call(
            self.socket_path,
            "interaction_respond",
            {"interaction_id": interaction_id, "action": "approve", "answer": None},
            request_id="approve-codex",
        )
        self.assertTrue(response["ok"])

        responses = await forward_task
        hook_output = codex_permission_response(responses[-1]["result"]["interaction_resolution"])

        self.assertEqual(
            hook_output,
            {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}},
        )

    async def test_permission_request_timeout_returns_no_decision(self) -> None:
        responses = await forward_hook_payload(
            self.socket_path,
            {
                "hook_event_name": "PermissionRequest",
                "session_id": "sid-timeout",
                "turn_id": "turn",
                "cwd": "/tmp/project",
                "tool_name": "Bash",
                "tool_input": {"command": "pwd"},
                "tool_use_id": "call-timeout",
            },
            wait_for_interaction=True,
            interaction_timeout=0.05,
        )

        self.assertNotIn("interaction_resolution", json.dumps(responses))


class CodexHookInstallTests(unittest.TestCase):
    def test_install_merges_with_existing_hooks_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            codex_dir = project / ".codex"
            codex_dir.mkdir()
            hooks_path = codex_dir / "hooks.json"
            hooks_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "UserPromptSubmit": [
                                {
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "python3 .codex/hooks/inject-workflow-state.py",
                                            "timeout": 5,
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )

            install_project_hooks(project, socket_path="/tmp/codeisland.sock", python="python3", module_root=Path("/opt/codeIsland/linux-skeleton"))
            install_project_hooks(project, socket_path="/tmp/codeisland.sock", python="python3", module_root=Path("/opt/codeIsland/linux-skeleton"))

            installed = json.loads(hooks_path.read_text(encoding="utf-8"))

        user_prompt_entries = installed["hooks"]["UserPromptSubmit"]
        codeisland_entries = [
            entry
            for entry in user_prompt_entries
            if "codeisland_linux.codex_hook" in json.dumps(entry)
        ]
        self.assertEqual(len(user_prompt_entries), 2)
        self.assertEqual(len(codeisland_entries), 1)
        self.assertIn("python3 .codex/hooks/inject-workflow-state.py", json.dumps(user_prompt_entries))
        for event_name in ("PreToolUse", "PermissionRequest", "PostToolUse"):
            self.assertEqual(len(installed["hooks"][event_name]), 1)
            self.assertIn("codeisland_linux.codex_hook", json.dumps(installed["hooks"][event_name]))
        self.assertNotIn("SessionStart", installed["hooks"])
        self.assertNotIn("Stop", installed["hooks"])
        self.assertEqual(installed["hooks"]["PermissionRequest"][0]["hooks"][0]["timeout"], 305)
        self.assertIn("--interaction-timeout 300.0", installed["hooks"]["PermissionRequest"][0]["hooks"][0]["command"])

    def test_global_install_writes_codex_home_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / "codex-home"

            hooks_path = install_global_hooks(codex_home, socket_path="/tmp/codeisland.sock", python="python3", module_root=Path("/opt/codeIsland/linux-skeleton"))

            installed = json.loads(hooks_path.read_text(encoding="utf-8"))

        self.assertEqual(hooks_path, codex_home / "hooks.json")
        for event_name in ("UserPromptSubmit", "PreToolUse", "PermissionRequest", "PostToolUse"):
            self.assertEqual(len(installed["hooks"][event_name]), 1)
            self.assertIn("codeisland_linux.codex_hook", json.dumps(installed["hooks"][event_name]))
        self.assertNotIn("SessionStart", installed["hooks"])
        self.assertNotIn("Stop", installed["hooks"])

    def test_install_removes_latency_sensitive_codeisland_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            codex_dir = project / ".codex"
            codex_dir.mkdir()
            hooks_path = codex_dir / "hooks.json"
            hooks_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "SessionStart": [{"hooks": [{"type": "command", "command": "python3 -m codeisland_linux.codex_hook run --event SessionStart", "timeout": 5}]}],
                            "Stop": [{"hooks": [{"type": "command", "command": "python3 -m codeisland_linux.codex_hook run --event Stop", "timeout": 5}]}],
                        }
                    }
                ),
                encoding="utf-8",
            )

            install_project_hooks(project, socket_path="/tmp/codeisland.sock", python="python3", module_root=Path("/opt/codeIsland/linux-skeleton"))

            installed = json.loads(hooks_path.read_text(encoding="utf-8"))

        self.assertNotIn("SessionStart", installed["hooks"])
        self.assertNotIn("Stop", installed["hooks"])

    def test_install_backup_preserves_existing_hooks_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            codex_dir = project / ".codex"
            codex_dir.mkdir()
            hooks_path = codex_dir / "hooks.json"
            original = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "existing", "timeout": 1}]}]}}
            hooks_path.write_text(json.dumps(original), encoding="utf-8")

            install_project_hooks(project, socket_path="/tmp/codeisland.sock", python="python3", module_root=Path("/opt/codeIsland/linux-skeleton"), backup=True)

            backup = hooks_path.with_name("hooks.json.bak")
            self.assertTrue(backup.exists())
            self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), original)


if __name__ == "__main__":
    unittest.main()
