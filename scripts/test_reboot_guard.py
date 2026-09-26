"""Regression checks for commit-triggered fresh-process reboot guard."""

from __future__ import annotations

import builtins
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.reboot_guard as guard


def main() -> None:
    marker = guard._BOOT_MARKER
    previous = getattr(builtins, marker, None)
    had_previous = hasattr(builtins, marker)
    try:
        if had_previous:
            delattr(builtins, marker)

        exits: list[int] = []
        assert guard.ensure_fresh_process(
            current_commit="commit-a",
            exit_fn=lambda code: exits.append(code),
        ) is False
        assert getattr(builtins, marker) == "commit-a"
        assert exits == []

        assert guard.ensure_fresh_process(
            current_commit="commit-a",
            exit_fn=lambda code: exits.append(code),
        ) is False
        assert exits == []

        assert guard.ensure_fresh_process(
            current_commit="commit-b",
            exit_fn=lambda code: exits.append(code),
        ) is True
        assert exits == [75]

        root = Path(__file__).resolve().parents[1]
        app_source = (root / "streamlit_app.py").read_text(encoding="utf-8")
        agents = (root / "AGENTS.md").read_text(encoding="utf-8")
        assert "from src.reboot_guard import ensure_fresh_process" in app_source
        assert "ensure_fresh_process()" in app_source
        assert "POST-COMMIT APP REBOOT" in agents
    finally:
        if had_previous:
            setattr(builtins, marker, previous)
        elif hasattr(builtins, marker):
            delattr(builtins, marker)

    print("commit-triggered fresh-process reboot guard: PASS")


if __name__ == "__main__":
    main()
