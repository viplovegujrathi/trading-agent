"""Loads config/strategy.yaml. All limits come from here."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "strategy.yaml"


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_PATH
    with open(p) as f:
        return yaml.safe_load(f)
