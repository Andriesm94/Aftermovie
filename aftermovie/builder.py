"""Trim a folder of short clips to beat-multiple lengths and stitch them together."""
from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from moviepy.editor import AudioFileClip, VideoFileClip, concatenate_audioclips, concatenate_videoclips

from .manifest import ClipSpec, load_manifest

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


@dataclass
class Song:
    path: Optional[Path]  # None means "no soundtrack", a single unbounded silent segment
    unit_ms: int


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
    songs: list[Song],
    default_beats: int = 1,
    manifest_path: Optional[Path] = None,
    order: str = "sequential",
    seed: Optional[int] = None,
    fps: int = 30,
    orientation: Optional[str] = None,
) -> None:
    if not songs:
        raise ValueError("At least one song (or a manual --bpm/--ms) is required.")

    clip_paths = find_clips(clips_dir)
    if order == "shuffle":
        random.Random(seed).shuffle(clip_paths)

    manifest = load_manifest(manifest_path) if manifest_path else {}

    segments = []
    skipped = []
    final = None
    final_audio = None

    song_audio_clips = [AudioFileClip(str(song.path)) if song.path else None for song in songs]
    song_durations = [audio.duration if audio else float("inf") for audio in song_audio_clips]
    song_elapsed_s = [0.0] * len(songs)
    song_idx = 0
    dropped = 0

    try:
        for i, path in enumerate(clip_paths):
            spec = manifest.get(path.name, ClipSpec())
            beats = spec.beats if spec.beats is not None else default_beats

            clip = VideoFileClip(str(path))

            if orientation is not None and clip_orientation(clip) != orientation:
                clip.close()
                continue

            length_s = None
            while song_idx < len(songs):
                candidate_length_s = (songs[song_idx].unit_ms * beats) / 1000
                remaining_s = song_durations[song_idx] - song_elapsed_s[song_idx]
                if candidate_length_s <= remaining_s:
                    length_s = candidate_length_s
                    break
                song_idx += 1

            if length_s is None:
                clip.close()
                dropped = len(clip_paths) - i
                break  # every song's runtime is spoken for; nothing more fits

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
            song_elapsed_s[song_idx] += length_s

        if not segments:
            reason = f"matched orientation={orientation!r} and were long enough" if orientation else "were long enough"
            raise RuntimeError(f"No clips in {clips_dir} {reason} to use.")

        final = concatenate_videoclips(segments, method="compose")

        used_tracks = [
            audio.subclip(0, min(elapsed, audio.duration))
            for audio, elapsed in zip(song_audio_clips, song_elapsed_s)
            if audio is not None and elapsed > 0
        ]
        if used_tracks:
            final_audio = used_tracks[0] if len(used_tracks) == 1 else concatenate_audioclips(used_tracks)
            final = final.set_audio(final_audio)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        final.write_videofile(str(output_path), fps=fps, codec="libx264", audio_codec="aac")
    finally:
        for clip in segments:
            clip.close()
        for audio in song_audio_clips:
            if audio is not None:
                audio.close()
        if final is not None:
            final.close()

    unused_songs = [
        song.path.name for song, elapsed in zip(songs, song_elapsed_s) if song.path and elapsed == 0
    ]
    if unused_songs:
        print(f"Note: never reached {', '.join(unused_songs)} (ran out of clips first)")
    if dropped:
        print(f"Note: dropped {dropped} trailing clip(s) — ran out of song runtime")
    if skipped:
        print(f"Skipped {len(skipped)} clip(s) too short: {', '.join(skipped)}")
