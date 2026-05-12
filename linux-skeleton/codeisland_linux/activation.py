from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .protocol import Session


class SessionActivator:
    def focus(self, session: Session) -> dict[str, Any]:
        pane_result = self._activate_wezterm_pane(session)
        window = self._matching_niri_window(session)
        if window is None:
            return {
                "accepted": False,
                "reason": "window_not_found",
                "session": asdict(session),
                "pane_activated": pane_result,
            }

        window_id = window.get("id")
        if not isinstance(window_id, int):
            return {"accepted": False, "reason": "invalid_window_id", "pane_activated": pane_result}

        focus = subprocess.run(
            ["niri", "msg", "action", "focus-window", "--id", str(window_id)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return {
            "accepted": focus.returncode == 0,
            "window_id": window_id,
            "window_title": window.get("title"),
            "pane_activated": pane_result,
            "stderr": focus.stderr.strip(),
        }

    def _activate_wezterm_pane(self, session: Session) -> dict[str, Any] | None:
        if not session.terminal_socket or not session.terminal_pane:
            return None
        if "wezterm" not in (session.terminal_app or "").lower():
            return None

        env = dict(os.environ)
        env["WEZTERM_UNIX_SOCKET"] = session.terminal_socket
        result = subprocess.run(
            ["wezterm", "cli", "activate-pane", "--pane-id", session.terminal_pane],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        return {"accepted": result.returncode == 0, "stderr": result.stderr.strip()}

    def _matching_niri_window(self, session: Session) -> dict[str, Any] | None:
        windows = self._niri_windows()
        if session.terminal_pid is not None:
            for window in windows:
                if window.get("pid") == session.terminal_pid:
                    return window

        project_root = session.project_root or ""
        project_name = Path(project_root).name if project_root else ""
        if project_name and self._allows_project_title_fallback(project_name):
            project_name_lower = project_name.lower()
            candidates: list[dict[str, Any]] = []
            for window in windows:
                title = str(window.get("title") or "").lower()
                if project_name_lower in title and self._window_matches_terminal(window, session):
                    candidates.append(window)
            if len(candidates) == 1:
                return candidates[0]

        return None

    def _niri_windows(self) -> list[dict[str, Any]]:
        result = subprocess.run(
            ["niri", "msg", "-j", "windows"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            return []
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []
        return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []

    @staticmethod
    def _window_matches_terminal(window: dict[str, Any], session: Session) -> bool:
        app_id = str(window.get("app_id") or "").lower()
        terminal_app = (session.terminal_app or "").lower()
        return not terminal_app or terminal_app in app_id or app_id in terminal_app or "term" in app_id

    @staticmethod
    def _allows_project_title_fallback(project_name: str) -> bool:
        normalized = project_name.strip().lower()
        if len(normalized) < 3:
            return False
        if normalized in {"home", "tmp", "var", "opt", "src", "workspace", "projects", "documents"}:
            return False
        user_name = os.environ.get("USER")
        return not user_name or normalized != user_name.lower()
