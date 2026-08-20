"""Command-line entry point: build a beat-synced aftermovie from short clips."""
from __future__ import annotations

import argparse
from pathlib import Path

from .bpm import beat_duration_ms, detect_bpm
from .builder import Song, build_aftermovie


def resolution(value: str) -> tuple[int, int]:
    try:
        w, h = value.lower().split("x")
        return int(w), int(h)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected WIDTHxHEIGHT, e.g. 1920x1080, got {value!r}") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Trim clips to beat-length multiples of a song and stitch them into one aftermovie."
    )
    parser.add_argument("clips_dir", type=Path, help="Folder containing the short input video clips")
    parser.add_argument("output", type=Path, help="Path to write the final video to")
    parser.add_argument(
        "--song",
        type=Path,
        action="append",
        help="Song to auto-detect BPM from and use as the soundtrack. Repeat to queue several songs "
        "back to back, one after another, each contributing its own beat length for the clips "
        "that land in its slot, e.g. --song a.mp3 --song b.mp3",
    )
    parser.add_argument(
        "--bpm", type=float, help="Manually specify BPM instead of auto-detecting (only when no --song is given)"
    )
    parser.add_argument(
        "--ms",
        type=int,
        help="Manually specify the length of one beat in milliseconds (only when no --song is given)",
    )
    parser.add_argument(
        "--beats-per-clip",
        type=int,
        default=2,
        help="Default number of beats each clip spans, unless overridden per-clip in --manifest (default: 2)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="JSON file with per-clip {start_ms, beats} overrides, e.g. manifest.example.json",
    )
    parser.add_argument(
        "--order",
        choices=["sequential", "shuffle", "manifest"],
        default="sequential",
        help="'manifest' plays clips in the order they're listed as keys in --manifest (which is then "
        "required); clips not listed in the manifest are appended afterward, alphabetically.",
    )
    parser.add_argument("--seed", type=int, default=None, help="Random seed for --order shuffle")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument(
        "--orientation",
        choices=["landscape", "portrait"],
        action="append",
        help="Only use clips in this orientation. Repeat to play multiple orientation groups back to back "
        "in the order given, each group ordered independently by --order, e.g. --orientation landscape "
        "--orientation portrait plays all landscape clips first, then all portrait clips. "
        "(default: use all clips regardless of orientation)",
    )
    parser.add_argument(
        "--resolution",
        type=resolution,
        help="Output canvas size as WIDTHxHEIGHT, e.g. 1920x1080. Clips are letterboxed/pillarboxed to fit "
        "without cropping. Default: the largest width and largest height found among the clips actually used.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.song:
        if args.bpm is not None or args.ms is not None:
            raise SystemExit("--bpm/--ms aren't supported together with --song; each song's tempo is auto-detected.")
        songs = []
        for path in args.song:
            bpm = detect_bpm(str(path))
            unit_ms = beat_duration_ms(bpm)
            print(f"{path.name}: detected tempo {bpm:.1f} BPM -> {unit_ms} ms/beat")
            songs.append(Song(path=path, unit_ms=unit_ms))
    else:
        if args.ms:
            unit_ms = args.ms
        elif args.bpm:
            unit_ms = beat_duration_ms(args.bpm)
        else:
            raise SystemExit("Provide --song (auto-detect BPM), or pass --bpm / --ms directly.")
        print(f"One beat = {unit_ms} ms (no soundtrack)")
        songs = [Song(path=None, unit_ms=unit_ms)]

    build_aftermovie(
        clips_dir=args.clips_dir,
        output_path=args.output,
        songs=songs,
        default_beats=args.beats_per_clip,
        manifest_path=args.manifest,
        order=args.order,
        seed=args.seed,
        fps=args.fps,
        orientations=args.orientation,
        resolution=args.resolution,
    )
    print(f"Done: {args.output}")


if __name__ == "__main__":
    main()
