"""Trim a folder of short clips to beat-multiple lengths and stitch them together."""
from __future__ import annotations

import random
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import imageio_ffmpeg

from . import _moviepy_compat  # noqa: F401  (must patch moviepy before any clip is opened)
from moviepy import AudioFileClip, CompositeVideoClip, VideoFileClip, concatenate_audioclips

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


def _even(n: int) -> int:
    return max(2, n - (n % 2))


def _probe_clips(clip_paths: list[Path], skipped: list[str]) -> list[tuple[Path, str, int, int]]:
    """Open every clip once (metadata only) to learn its orientation and size.
    Clips that fail to open (corrupted, or the source folder syncing live via
    OneDrive and briefly inconsistent) are logged to `skipped` and left out,
    rather than crashing the whole build."""
    probed = []
    for path in clip_paths:
        try:
            clip = VideoFileClip(str(path))
        except Exception as exc:
            skipped.append(f"{path.name} (failed to open while probing: {exc})")
            continue
        probed.append((path, clip_orientation(clip), clip.w, clip.h))
        clip.close()
    return probed


def _order_clips(
    probed: list[tuple[Path, str, int, int]],
    orientations: Optional[list[str]],
    order: str,
    seed: Optional[int],
    manifest_order: Optional[dict[str, int]] = None,
) -> list[Path]:
    """Either a flat (optionally shuffled/manifest-ordered) list of every
    probed clip, or, if orientations is given, only clips matching those
    orientations, ordered group by group in the order the orientations were
    given (each group independently arranged per `order`)."""
    rng = random.Random(seed)

    def arrange(paths: list[Path]) -> list[Path]:
        if order == "shuffle":
            paths = paths[:]
            rng.shuffle(paths)
            return paths
        if order == "manifest":
            return sorted(paths, key=lambda p: manifest_order.get(p.name, len(manifest_order)))
        return paths  # sequential: already alphabetical, courtesy of find_clips()

    if not orientations:
        return arrange([path for path, _, _, _ in probed])

    groups: dict[str, list[Path]] = {o: [] for o in orientations}
    for path, o, _, _ in probed:
        if o in groups:
            groups[o].append(path)

    ordered: list[Path] = []
    for o in orientations:
        ordered.extend(arrange(groups[o]))
    return ordered


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


def build_aftermovie(
    clips_dir: Path,
    output_path: Path,
    songs: list[Song],
    default_beats: int = 1,
    manifest_path: Optional[Path] = None,
    order: str = "sequential",
    seed: Optional[int] = None,
    fps: int = 30,
    orientations: Optional[list[str]] = None,
    resolution: Optional[tuple[int, int]] = None,
) -> None:
    if not songs:
        raise ValueError("At least one song (or a manual --bpm/--ms) is required.")
    if order == "manifest" and not manifest_path:
        raise ValueError("order='manifest' requires a manifest_path.")

    manifest = load_manifest(manifest_path) if manifest_path else {}
    manifest_order = {name: idx for idx, name in enumerate(manifest.keys())}

    skipped: list[str] = []
    probed = _probe_clips(find_clips(clips_dir), skipped)
    clip_paths = _order_clips(probed, orientations, order, seed, manifest_order)

    if resolution:
        target_size = (_even(resolution[0]), _even(resolution[1]))
    else:
        max_w = max((w for _, _, w, _ in probed), default=0)
        max_h = max((h for _, _, _, h in probed), default=0)
        target_size = (_even(max_w), _even(max_h))

    song_audio_clips = [AudioFileClip(str(song.path)) if song.path else None for song in songs]
    song_durations = [audio.duration if audio else float("inf") for audio in song_audio_clips]
    song_elapsed_s = [0.0] * len(songs)
    song_idx = 0
    dropped = 0

    try:
        with tempfile.TemporaryDirectory(prefix="aftermovie_") as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            segment_paths: list[Path] = []

            # Render each clip's trimmed segment to its own small temp file and
            # close the source clip right away, instead of keeping every clip's
            # ffmpeg reader open at once for the whole build (that stops scaling
            # once there are more than a couple dozen clips).
            for i, path in enumerate(clip_paths):
                spec = manifest.get(path.name, ClipSpec())
                beats = spec.beats if spec.beats is not None else default_beats

                try:
                    clip = VideoFileClip(str(path))
                except Exception as exc:
                    skipped.append(f"{path.name} (failed to open: {exc})")
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

                seg_path = tmp_dir / f"seg_{len(segment_paths):05d}.mp4"
                rendered = False
                try:
                    sub = clip.subclipped(start_s, start_s + length_s)
                    fitted = _fit_to_canvas(sub, target_size)
                    fitted.write_videofile(str(seg_path), fps=fps, codec="libx264", audio=False, logger=None)
                    rendered = True
                except Exception as exc:
                    skipped.append(f"{path.name} (failed to render at {start_s:.2f}s: {exc})")
                finally:
                    clip.close()

                if not rendered:
                    continue

                segment_paths.append(seg_path)
                song_elapsed_s[song_idx] += length_s

            if not segment_paths:
                reason = (
                    f"matched orientation(s) {orientations} and were long enough" if orientations else "were long enough"
                )
                raise RuntimeError(f"No clips in {clips_dir} {reason} to use.")

            list_path = tmp_dir / "concat_list.txt"
            with open(list_path, "w", encoding="utf-8") as f:
                for seg_path in segment_paths:
                    escaped = str(seg_path).replace("\\", "/").replace("'", "'\\''")
                    f.write(f"file '{escaped}'\n")

            used_tracks = [
                audio.subclipped(0, min(elapsed, audio.duration))
                for audio, elapsed in zip(song_audio_clips, song_elapsed_s)
                if audio is not None and elapsed > 0
            ]
            audio_path = None
            if used_tracks:
                final_audio = used_tracks[0] if len(used_tracks) == 1 else concatenate_audioclips(used_tracks)
                audio_path = tmp_dir / "audio.wav"  # uncompressed: no encoder-availability surprises; re-encoded to aac below
                final_audio.write_audiofile(str(audio_path), logger=None)

            output_path.parent.mkdir(parents=True, exist_ok=True)
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            cmd = [ffmpeg_exe, "-y", "-f", "concat", "-safe", "0", "-i", str(list_path)]
            if audio_path is not None:
                cmd += [
                    "-i", str(audio_path),
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", "copy", "-c:a", "aac", "-shortest",
                ]
            else:
                cmd += ["-map", "0:v:0", "-c:v", "copy"]
            cmd += [str(output_path)]

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg failed to join segments:\n{result.stderr[-4000:]}")
    finally:
        for audio in song_audio_clips:
            if audio is not None:
                audio.close()

    unused_songs = [
        song.path.name for song, elapsed in zip(songs, song_elapsed_s) if song.path and elapsed == 0
    ]
    if unused_songs:
        print(f"Note: never reached {', '.join(unused_songs)} (ran out of clips first)")
    if dropped:
        print(f"Note: dropped {dropped} trailing clip(s) — ran out of song runtime")
    if skipped:
        print(f"Skipped {len(skipped)} clip(s): {', '.join(skipped)}")
