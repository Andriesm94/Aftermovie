"""Work around a moviepy 2.x parser bug triggered by modern iPhone footage.

`ffmpeg -i` prints an unlabeled "Side data: ... Ambient Viewing Environment,
ambient_illuminance=..." line for HDR/Dolby Vision clips. It has no colon, so
moviepy's line-by-line parser treats it as a continuation of the *previous*
metadata field and tries to string-concatenate it onto that field's value.
When the previous field is `displaymatrix` (parsed as a float rotation
angle), that raises `TypeError: unsupported operand type(s) for +: 'float'
and 'str'` and the clip fails to load entirely.

This patches the parser to only join continuation lines onto fields whose
existing value is actually a string (preserving real multiline metadata like
long comment tags), and to file anything else under its own throwaway field
instead of crashing.
"""
from __future__ import annotations

import itertools

from moviepy.video.io.ffmpeg_reader import FFmpegInfosParser

_original_parse_metadata_field_value = FFmpegInfosParser.parse_metadata_field_value
_unparsed_counter = itertools.count()


def _patched_parse_metadata_field_value(self, line):
    field, value = _original_parse_metadata_field_value(self, line)
    if field == "":
        last_field = getattr(self, "_last_metadata_field_added", None)
        current_value = (self._current_stream or {}).get("metadata", {}).get(last_field)
        if not isinstance(current_value, str):
            return (f"_unparsed_{next(_unparsed_counter)}", line.strip())
    return (field, value)


FFmpegInfosParser.parse_metadata_field_value = _patched_parse_metadata_field_value
