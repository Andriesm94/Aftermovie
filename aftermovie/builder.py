"""Trim a folder of short clips to beat-multiple lengths and stitch them together."""
from __future__ import annotations

import hashlib
import json
import random
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import imageio_ffmpeg

from . import _moviepy_compat  # noqa: F401  (must patch moviepy before any clip is opened)
from moviepy import AudioFileClip, concatenate_audioclips

from .manifest import ClipSpec, load_manifest

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
PROBE_TIMEOUT_S = 60
RENDER_TIMEOUT_S = 900  # long/HQ segments (e.g. many beats at a large auto-detected canvas) can legitimately need this
CONCAT_TIMEOUT_S = 600


def _file_fingerprint(path: Path) -> str:
    """Cheap content-change signal so a clip replaced with a new file (same
    name, different bytes - e.g. a higher-quality re-export) doesn't reuse a
    stale cached probe/segment."""
    st = path.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


@dataclass
class Song:
    path: Optional[Path]  # None means "no soundtrack", a single unbounded silent segment
    unit_ms: int


def find_clips(clips_dir: Path) -> list[Path]:
    clips = sorted(p for p in clips_dir.iterdir() if p.suffix.lower() in VIDEO_EXTENSIONS)
    if not clips:
        raise FileNotFoundError(f"No video files found in {clips_dir}")
    return clips


def _run_worker(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "aftermovie._clip_worker", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _failure_reason(result: subprocess.CompletedProcess) -> str:
    if result.stderr and result.stderr.strip():
        return result.stderr.strip().splitlines()[-1]
    return f"exit code {result.returncode}"


def _even(n: int) -> int:
    return max(2, n - (n % 2))


def _probe_clips(
    clip_paths: list[Path], skipped: list[str], cache_dir: Optional[Path] = None
) -> list[tuple[Path, str, int, int, float]]:
    """Probe every clip's orientation/size/duration, each in its own isolated
    subprocess. Some clips carry metadata that crashes moviepy's ffmpeg-output
    parser outright (not a catchable exception) - isolating each probe keeps
    one bad clip from taking down the whole run. Results are cached to disk
    (keyed by file size+mtime) so a build that gets interrupted partway
    through - by anything, including something outside our control - doesn't
    need to re-probe clips it already succeeded on when simply re-run."""
    cache_path = cache_dir / "probe_cache.json" if cache_dir else None
    cache: dict[str, list] = {}
    if cache_path and cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
        except (OSError, json.JSONDecodeError):
            cache = {}

    probed = []
    for path in clip_paths:
        cache_key = f"{path.name}:{_file_fingerprint(path)}"
        cached = cache.get(cache_key)
        if cached is not None:
            w, h, orientation, duration = cached
            probed.append((path, orientation, w, h, duration))
            continue

        try:
            result = _run_worker(["probe", str(path)], timeout=PROBE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            skipped.append(f"{path.name} (timed out while probing)")
            continue
        if result.returncode != 0:
            skipped.append(f"{path.name} (failed to open while probing: {_failure_reason(result)})")
            continue

        w_s, h_s, orientation, duration_s = result.stdout.split()
        w, h, duration = int(w_s), int(h_s), float(duration_s)
        probed.append((path, orientation, w, h, duration))

        if cache_path:
            # Written after every single probe, not batched at the end - the
            # entire point is surviving an interruption partway through.
            cache[cache_key] = [w, h, orientation, duration]
            cache_path.write_text(json.dumps(cache))

    return probed


def _order_clips(
    probed: list[tuple[Path, str, int, int, float]],
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
            return sorted(paths, key=lambda p: manifest_order.get(p.name.lower(), len(manifest_order)))
        return paths  # sequential: already alphabetical, courtesy of find_clips()

    if not orientations:
        return arrange([path for path, _, _, _, _ in probed])

    groups: dict[str, list[Path]] = {o: [] for o in orientations}
    for path, o, _, _, _ in probed:
        if o in groups:
            groups[o].append(path)

    ordered: list[Path] = []
    for o in orientations:
        ordered.extend(arrange(groups[o]))
    return ordered


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
    continue_without_audio: bool = False,
) -> None:
    if not songs:
        raise ValueError("At least one song (or a manual --bpm/--ms) is required.")
    if order == "manifest" and not manifest_path:
        raise ValueError("order='manifest' requires a manifest_path.")

    manifest = load_manifest(manifest_path) if manifest_path else {}
    manifest_order = {name: idx for idx, name in enumerate(manifest.keys())}

    # Persistent (not auto-deleted) cache tied to the output path, so that if
    # the whole process gets killed partway through a long render - by
    # anything, we've seen everything from a moviepy parsing crash to what
    # looks like external interference on this machine - simply re-running
    # the exact same command resumes from where it left off instead of
    # redoing already-finished work from scratch.
    cache_dir = output_path.parent / f".cache_{output_path.stem}"
    cache_dir.mkdir(parents=True, exist_ok=True)

    skipped: list[str] = []
    probed = _probe_clips(find_clips(clips_dir), skipped, cache_dir)
    clip_paths = _order_clips(probed, orientations, order, seed, manifest_order)

    if resolution:
        target_size = (_even(resolution[0]), _even(resolution[1]))
    else:
        # Size the canvas from only the clips actually selected (post
        # orientation-filtering) rather than every clip in the folder -
        # otherwise e.g. an all-portrait build gets sized against landscape
        # clips it never uses and everything ends up pillarboxed tiny.
        dims = {path: (w, h) for path, _, w, h, _ in probed}
        used_dims = [dims[p] for p in clip_paths if p in dims]
        max_w = max((w for w, _ in used_dims), default=0)
        max_h = max((h for _, h in used_dims), default=0)
        target_size = (_even(max_w), _even(max_h))

    durations = {path: dur for path, _, _, _, dur in probed}

    song_audio_clips = [AudioFileClip(str(song.path)) if song.path else None for song in songs]
    song_durations = [audio.duration if audio else float("inf") for audio in song_audio_clips]
    song_elapsed_s = [0.0] * len(songs)
    song_idx = 0
    dropped = 0

    try:
        segment_paths: list[Path] = []
        total_video_s = 0.0

        # Render each clip's trimmed segment in its own subprocess (see
        # _clip_worker.py) and only keep the small file - never hold a
        # clip's ffmpeg reader open in this process, and never let one
        # clip's crash take the whole build down. Segments are written into
        # the persistent cache_dir under a name keyed by exactly what
        # produced them, so a segment already rendered by an earlier,
        # interrupted attempt at this same build is reused instead of redone.
        for i, path in enumerate(clip_paths):
            spec = manifest.get(path.name.lower(), ClipSpec())
            beats = spec.beats if spec.beats is not None else default_beats
            duration = durations[path]

            if beats == -1:
                # Take the whole clip regardless of remaining song runtime -
                # if that runs the audio out early, the tail plays silent
                # (see the apad step where the final track is built).
                if song_idx >= len(songs) and not continue_without_audio:
                    dropped = len(clip_paths) - i
                    break  # no song left to attribute this segment's audio to
                length_s = duration
            else:
                length_s = None
                while song_idx < len(songs):
                    candidate_length_s = (songs[song_idx].unit_ms * beats) / 1000
                    remaining_s = song_durations[song_idx] - song_elapsed_s[song_idx]
                    if candidate_length_s <= remaining_s:
                        length_s = candidate_length_s
                        break
                    song_idx += 1

                if length_s is None:
                    if continue_without_audio:
                        # Every song is spoken for - keep going at the last
                        # song's pace, just without any audio backing it.
                        length_s = (songs[-1].unit_ms * beats) / 1000
                    else:
                        dropped = len(clip_paths) - i
                        break  # every song's runtime is spoken for; nothing more fits

            if duration < length_s:
                skipped.append(
                    f"{path.name} (needs {length_s * 1000:.0f}ms, has {duration * 1000:.0f}ms)"
                )
                continue

            if spec.start_ms is not None:
                start_s = spec.start_ms / 1000
                if start_s + length_s > duration:
                    start_s = max(0.0, duration - length_s)
            else:
                start_s = (duration - length_s) / 2  # avoid shaky clip starts/ends

            seg_key = hashlib.sha1(
                f"{path.name}|{_file_fingerprint(path)}|{start_s:.3f}|{length_s:.3f}|"
                f"{target_size[0]}x{target_size[1]}|{fps}".encode()
            ).hexdigest()[:20]
            seg_path = cache_dir / f"seg_{seg_key}.mp4"

            if seg_path.exists():
                rendered = True
            else:
                rendered = False
                try:
                    result = _run_worker(
                        [
                            "render",
                            str(path),
                            str(start_s),
                            str(length_s),
                            str(target_size[0]),
                            str(target_size[1]),
                            str(fps),
                            str(seg_path),
                        ],
                        timeout=RENDER_TIMEOUT_S,
                    )
                    rendered = result.returncode == 0 and seg_path.exists()
                    if not rendered:
                        skipped.append(
                            f"{path.name} (failed to render at {start_s:.2f}s: {_failure_reason(result)})"
                        )
                except subprocess.TimeoutExpired:
                    skipped.append(f"{path.name} (timed out while rendering)")

            if not rendered:
                continue

            segment_paths.append(seg_path)
            if song_idx < len(songs):
                song_elapsed_s[song_idx] += length_s
            total_video_s += length_s

        if not segment_paths:
            reason = (
                f"matched orientation(s) {orientations} and were long enough" if orientations else "were long enough"
            )
            raise RuntimeError(f"No clips in {clips_dir} {reason} to use.")

        # concat_list.txt and audio.wav live in cache_dir (inside the project,
        # not the OS temp dir) - deep OS temp dirs turned out to be an
        # unreliable place to stage files right before ffmpeg reads them on
        # this machine, for whatever external reason.
        # Absolute paths: ffmpeg's concat demuxer resolves relative entries
        # relative to the list file's own directory, not the cwd - a
        # relative cache_dir (the common case, e.g. "output/...") made every
        # entry double up on itself (".../output/.../output/...").
        list_path = cache_dir / "concat_list.txt"
        with open(list_path, "w", encoding="utf-8") as f:
            for seg_path in segment_paths:
                escaped = str(seg_path.resolve()).replace("\\", "/").replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")

        used_tracks = [
            audio.subclipped(0, min(elapsed, audio.duration))
            for audio, elapsed in zip(song_audio_clips, song_elapsed_s)
            if audio is not None and elapsed > 0
        ]
        audio_path = None
        if used_tracks:
            final_audio = used_tracks[0] if len(used_tracks) == 1 else concatenate_audioclips(used_tracks)
            audio_path = cache_dir / "audio.wav"  # uncompressed: no encoder-availability surprises; re-encoded to aac below
            final_audio.write_audiofile(str(audio_path), logger=None)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        cmd = [ffmpeg_exe, "-y", "-f", "concat", "-safe", "0", "-i", str(list_path)]
        if audio_path is not None:
            # apad=whole_dur pads audio with silence up to an exact target
            # duration (a no-op if it's already longer); -t then hard-trims
            # the output to that same duration. Both bounds are known
            # up-front from total_video_s, so ffmpeg never has to work out
            # dynamically which stream is "shortest" - an open-ended `apad`
            # (no whole_dur) combined with `-shortest` against a stream-copied
            # concat-demuxer video measured in the hours, not seconds.
            cmd += [
                "-i", str(audio_path),
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy",
                "-af", f"apad=whole_dur={total_video_s:.3f}",
                "-c:a", "aac",
                "-t", f"{total_video_s:.3f}",
            ]
        else:
            cmd += ["-map", "0:v:0", "-c:v", "copy"]
        cmd += [str(output_path)]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=CONCAT_TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"ffmpeg timed out joining segments after {CONCAT_TIMEOUT_S}s") from exc
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
