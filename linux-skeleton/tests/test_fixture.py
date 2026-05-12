from __future__ import annotations

import unittest

from codeisland_linux.fixture import build_scenario


class FixtureTests(unittest.TestCase):
    def test_board_demo_contains_provider_identity(self) -> None:
        events = build_scenario("board-demo")
        session_events = [event for event in events if event["kind"] == "session.started"]

        providers = {event["payload"]["provider"] for event in session_events}
        self.assertIn("opencode", providers)
        self.assertIn("codex", providers)
        self.assertIn("claude", providers)
        self.assertIn("gemini", providers)
        self.assertTrue(all(event["payload"].get("terminal_app") for event in session_events))

    def test_codex_fixture_preserves_terminal_app(self) -> None:
        events = build_scenario("codex")
        session_event = next(event for event in events if event["kind"] == "session.started")

        self.assertEqual(session_event["payload"]["provider"], "codex")
        self.assertEqual(session_event["payload"]["terminal_app"], "Ghostty")


if __name__ == "__main__":
    unittest.main()
