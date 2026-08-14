"""Command-line entry point: build a beat-synced aftermovie from short clips."""
from __future__ import annotations

import argparse
from pathlib import Path

from .bpm import beat_duration_ms, detect_bpm
from .builder import build_aftermovie


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Trim clips to beat-length multiples of a song and stitch them into one aftermovie."
    )
    parser.add_argument("clips_dir", type=Path, help="Folder containing the short input video clips")
    parser.add_argument("output", type=Path, help="Path to write the final video to")
    parser.add_argument("--song", type=Path, help="Song to auto-detect BPM from and use as the soundtrack")
    parser.add_argument("--bpm", type=float, help="Manually specify BPM instead of auto-detecting")
    parser.add_argument(
        "--ms", type=int, help="Manually specify the length of one beat in milliseconds (overrides BPM/--song)"
    )
    parser.add_argument(
        "--beats-per-clip",
        type=int,
        default=1,
        help="Default number of beats each clip spans, unless overridden per-clip in --manifest (default: 1)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="JSON file with per-clip {start_ms, beats} overrides, e.g. manifest.example.json",
    )
    parser.add_argument("--order", choices=["sequential", "shuffle"], default="sequential")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for --order shuffle")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument(
        "--orientation",
        choices=["landscape", "portrait"],
        help="Only use clips in this orientation (default: use all clips regardless of orientation)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.ms:
        unit_ms = args.ms
    else:
        bpm = args.bpm
        if bpm is None:
            if args.song is None:
                raise SystemExit("Provide --song to auto-detect BPM, or pass --bpm / --ms directly.")
            bpm = detect_bpm(str(args.song))
            print(f"Detected tempo: {bpm:.1f} BPM")
        unit_ms = beat_duration_ms(bpm)

    print(f"One beat = {unit_ms} ms")

    build_aftermovie(
        clips_dir=args.clips_dir,
        output_path=args.output,
        unit_ms=unit_ms,
        default_beats=args.beats_per_clip,
        manifest_path=args.manifest,
        song_path=args.song,
        order=args.order,
        seed=args.seed,
        fps=args.fps,
        orientation=args.orientation,
    )
    print(f"Done: {args.output}")


if __name__ == "__main__":
    main()
