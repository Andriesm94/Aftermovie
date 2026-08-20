"""Runs one clip's probe or render step as an isolated subprocess.

ffmpeg/moviepy occasionally hard-crashes - not a catchable Python exception,
the whole process just dies - on unusual metadata some cameras/editors embed
(DJI chapter markers, HDR ambient-viewing side data, etc). Doing every
clip's actual ffmpeg work here, in its own process, means such a crash can't
take down a whole batch render: the parent (aftermovie.builder) just sees a
failed subprocess and skips that one clip.

Invoked as `python -m aftermovie._clip_worker <mode> ...`, never imported.
"""
from __future__ import annotations

import sys

from . import _moviepy_compat  # noqa: F401  (must patch moviepy before any clip is opened)
from moviepy import CompositeVideoClip, VideoFileClip


def clip_orientation(clip) -> str:
    if clip.w > clip.h:
        return "landscape"
    if clip.h > clip.w:
        return "portrait"
    return "square"


def _fit_to_canvas(clip, target_size: tuple[int, int]):
    """Scale clip to fit inside target_size preserving aspect ratio, then
    center it on a black canvas of exactly target_size (letterbox/pillarbox)."""
    target_w, target_h = target_size
    if clip.w / clip.h > target_w / target_h:
        resized = clip.resized(width=target_w)
    else:
        resized = clip.resized(height=target_h)
    resized = resized.with_position("center")
    return CompositeVideoClip([resized], size=target_size, bg_color=(0, 0, 0))


def _probe(clip_path: str) -> int:
    clip = VideoFileClip(clip_path)
    print(f"{clip.w} {clip.h} {clip_orientation(clip)} {clip.duration}")
    clip.close()
    return 0


def _render(
    clip_path: str, start_s: float, length_s: float, target_w: int, target_h: int, fps: int, out_path: str
) -> int:
    clip = VideoFileClip(clip_path)
    try:
        sub = clip.subclipped(start_s, start_s + length_s)
        fitted = _fit_to_canvas(sub, (target_w, target_h))
        fitted.write_videofile(out_path, fps=fps, codec="libx264", audio=False, logger=None)
    finally:
        clip.close()
    return 0


def main(argv: list[str]) -> int:
    mode = argv[0]
    if mode == "probe":
        return _probe(argv[1])
    if mode == "render":
        clip_path, start_s, length_s, target_w, target_h, fps, out_path = argv[1:]
        return _render(clip_path, float(start_s), float(length_s), int(target_w), int(target_h), int(fps), out_path)
    raise SystemExit(f"unknown mode {mode!r}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
