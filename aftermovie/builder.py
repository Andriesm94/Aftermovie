"""Trim a folder of short clips to beat-multiple lengths and stitch them together."""
from __future__ import annotations

import random
from pathlib import Path
from typing import Optional

from moviepy.editor import AudioFileClip, VideoFileClip, concatenate_videoclips

from .manifest import ClipSpec, load_manifest

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


def find_clips(clips_dir: Path) -> list[Path]:
    clips = sorted(p for p in clips_dir.iterdir() if p.suffix.lower() in VIDEO_EXTENSIONS)
    if not clips:
        raise FileNotFoundError(f"No video files found in {clips_dir}")
    return clips


def clip_orientation(clip) -> str:
    if clip.w > clip.h:
        return "landscape"
    if clip.h > clip.w:
        return "portrait"
    return "square"


def build_aftermovie(
    clips_dir: Path,
    output_path: Path,
    unit_ms: int,
    default_beats: int = 1,
    manifest_path: Optional[Path] = None,
    song_path: Optional[Path] = None,
    order: str = "sequential",
    seed: Optional[int] = None,
    fps: int = 30,
    orientation: Optional[str] = None,
) -> None:
    clip_paths = find_clips(clips_dir)
    if order == "shuffle":
        random.Random(seed).shuffle(clip_paths)

    manifest = load_manifest(manifest_path) if manifest_path else {}

    segments = []
    skipped = []
    final = None
    audio = None

    try:
        for path in clip_paths:
            spec = manifest.get(path.name, ClipSpec())
            beats = spec.beats if spec.beats is not None else default_beats
            length_s = (unit_ms * beats) / 1000

            clip = VideoFileClip(str(path))

            if orientation is not None and clip_orientation(clip) != orientation:
                clip.close()
                continue

            if clip.duration < length_s:
                skipped.append(
                    f"{path.name} (needs {length_s * 1000:.0f}ms, has {clip.duration * 1000:.0f}ms)"
                )
                clip.close()
                continue

            if spec.start_ms is not None:
                start_s = spec.start_ms / 1000
                if start_s + length_s > clip.duration:
                    start_s = max(0.0, clip.duration - length_s)
            else:
                start_s = (clip.duration - length_s) / 2  # avoid shaky clip starts/ends

            segments.append(clip.subclip(start_s, start_s + length_s))

        if not segments:
            reason = f"matched orientation={orientation!r} and were long enough" if orientation else "were long enough"
            raise RuntimeError(f"No clips in {clips_dir} {reason} to use.")

        final = concatenate_videoclips(segments, method="compose")

        if song_path is not None:
            audio = AudioFileClip(str(song_path))
            audio = audio.subclip(0, min(final.duration, audio.duration))
            final = final.set_audio(audio)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        final.write_videofile(str(output_path), fps=fps, codec="libx264", audio_codec="aac")
    finally:
        for clip in segments:
            clip.close()
        if audio is not None:
            audio.close()
        if final is not None:
            final.close()

    if skipped:
        print(f"Skipped {len(skipped)} clip(s) too short: {', '.join(skipped)}")
