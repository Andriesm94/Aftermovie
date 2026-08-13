"""Trim a folder of short clips to a fixed beat length and stitch them together."""
from __future__ import annotations

import random
from pathlib import Path
from typing import Optional

from moviepy.editor import AudioFileClip, VideoFileClip, concatenate_videoclips

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


def find_clips(clips_dir: Path) -> list[Path]:
    clips = sorted(p for p in clips_dir.iterdir() if p.suffix.lower() in VIDEO_EXTENSIONS)
    if not clips:
        raise FileNotFoundError(f"No video files found in {clips_dir}")
    return clips


def build_aftermovie(
    clips_dir: Path,
    output_path: Path,
    beat_ms: int,
    song_path: Optional[Path] = None,
    order: str = "sequential",
    seed: Optional[int] = None,
    fps: int = 30,
) -> None:
    clip_paths = find_clips(clips_dir)
    if order == "shuffle":
        random.Random(seed).shuffle(clip_paths)

    beat_s = beat_ms / 1000
    segments = []
    skipped = []
    final = None
    audio = None

    try:
        for path in clip_paths:
            clip = VideoFileClip(str(path))
            if clip.duration < beat_s:
                skipped.append(path.name)
                clip.close()
                continue
            segments.append(clip.subclip(0, beat_s))

        if not segments:
            raise RuntimeError(f"No clips in {clips_dir} are at least {beat_ms}ms long.")

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
        print(f"Skipped {len(skipped)} clip(s) shorter than {beat_ms}ms: {', '.join(skipped)}")
