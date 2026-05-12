from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from codeisland_linux.activation import SessionActivator
from codeisland_linux.protocol import Session


def make_session(*, project_root: str | None, terminal_pid: int | None = None, terminal_app: str | None = "WezTerm") -> Session:
    return Session(
        session_id="session-1",
        provider="claude",
        project_root=project_root,
        title="session",
        status="running",
        current_task_id=None,
        workspace_hint=None,
        terminal_app=terminal_app,
        terminal_pid=terminal_pid,
        terminal_pane=None,
        terminal_socket=None,
        cli_pid=None,
        created_at="2026-05-12T00:00:00Z",
        updated_at="2026-05-12T00:00:00Z",
        last_event_at="2026-05-12T00:00:00Z",
        ended_at=None,
    )


class FakeSessionActivator(SessionActivator):
    def __init__(self, windows: list[dict]) -> None:
        self.windows = windows

    def _niri_windows(self) -> list[dict]:
        return self.windows


class SessionActivatorTests(unittest.TestCase):
    def test_exact_terminal_pid_wins_for_generic_project_name(self) -> None:
        activator = FakeSessionActivator(
            [
                {"id": 1, "title": "jk", "app_id": "org.wezfurlong.wezterm", "pid": 111},
                {"id": 2, "title": "Free Code", "app_id": "org.wezfurlong.wezterm", "pid": 222},
            ]
        )

        window = activator._matching_niri_window(make_session(project_root="/home/jk", terminal_pid=222))

        self.assertIsNotNone(window)
        self.assertEqual(window["id"], 2)

    def test_generic_home_project_name_does_not_guess_by_title(self) -> None:
        activator = FakeSessionActivator(
            [
                {"id": 1, "title": "jk", "app_id": "org.wezfurlong.wezterm", "pid": 111},
            ]
        )

        with patch.dict(os.environ, {"USER": "jk"}):
            window = activator._matching_niri_window(make_session(project_root="/home/jk"))

        self.assertIsNone(window)

    def test_title_fallback_requires_unique_candidate(self) -> None:
        activator = FakeSessionActivator(
            [
                {"id": 1, "title": "Folo", "app_id": "org.wezfurlong.wezterm", "pid": 111},
                {"id": 2, "title": "Folo logs", "app_id": "org.wezfurlong.wezterm", "pid": 222},
            ]
        )

        window = activator._matching_niri_window(make_session(project_root="/home/jk/Projects/life/Folo"))

        self.assertIsNone(window)


if __name__ == "__main__":
    unittest.main()
