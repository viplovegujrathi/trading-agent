"""Kill switch: state/HALT file or TA_HALT=1 blocks new opens. Closes still allowed."""
from __future__ import annotations

import os
from pathlib import Path

from .journal import ROOT, append_event


def is_halted(root: Path = ROOT) -> bool:
    return os.environ.get("TA_HALT") == "1" or (root / "state" / "HALT").exists()


def set_halt(reason: str, root: Path = ROOT) -> None:
    (root / "state").mkdir(parents=True, exist_ok=True)
    (root / "state" / "HALT").write_text(reason)
    append_event("halt_set", None, None, root=root, reason=reason)


def clear_halt(note: str, root: Path = ROOT) -> None:
    """Only the user clears it; a note is required."""
    if not note.strip():
        raise ValueError("a journal note is required to clear the halt")
    (root / "state" / "HALT").unlink(missing_ok=True)
    append_event("halt_cleared", None, None, root=root, note=note)
