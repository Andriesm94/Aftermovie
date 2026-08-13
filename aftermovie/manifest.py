"""Optional per-clip overrides: manual start point and/or beat count."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ClipSpec:
    start_ms: Optional[int] = None
    beats: Optional[int] = None


def load_manifest(path: Path) -> dict[str, ClipSpec]:
    """Load a JSON manifest mapping filename -> {start_ms, beats} (both optional).

    Example:
        {
          "clip1.mp4": {"start_ms": 1200, "beats": 2},
          "clip2.mp4": {"start_ms": 500}
        }

    Clips not listed use the default start (middle of the clip) and the
    global --beats-per-clip value.
    """
    data = json.loads(path.read_text())
    manifest: dict[str, ClipSpec] = {}
    for filename, entry in data.items():
        manifest[filename] = ClipSpec(
            start_ms=entry.get("start_ms"),
            beats=entry.get("beats"),
        )
    return manifest
