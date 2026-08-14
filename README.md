# Aftermovie

This repo is for concatenating small videos to an aftermovie. You provide a song file as well, and the video lengths are fixed to the beat of the song.

Every clip is trimmed to exactly `x` milliseconds, where `x` is the beat length of
a song (auto-detected via BPM, or set manually), then concatenated in order.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Usage

Auto-detect BPM from a song and use it as the soundtrack:

```bash
python -m aftermovie.cli clips_folder output.mp4 --song song.mp3
```

Specify BPM or exact clip length manually (no soundtrack detection needed):

```bash
python -m aftermovie.cli clips_folder output.mp4 --bpm 128
python -m aftermovie.cli clips_folder output.mp4 --ms 469
```

Randomize clip order, span two beats per clip by default, and set output framerate:

```bash
python -m aftermovie.cli clips_folder output.mp4 --song song.mp3 --order shuffle --seed 42 --beats-per-clip 2 --fps 30
```

Clips shorter than their computed length are skipped (with a warning) since
they can't be trimmed up to that length.

Queue up multiple songs — once one song's runtime is used up, the next one
kicks in with its own auto-detected tempo, so clips after the switch are cut
to the new song's beat length:

```bash
python -m aftermovie.cli clips_folder output.mp4 --song song1.mp3 --song song2.mp3
```

If every song runs out before the clips do, the remaining trailing clips are
dropped rather than left with silence (you'll see a note printed telling you
how many were dropped).

Mixing landscape and portrait clips? Restrict to one orientation with
`--orientation landscape` or `--orientation portrait` (default: use both):

```bash
python -m aftermovie.cli clips_folder output.mp4 --song song.mp3 --orientation portrait
```

## Choosing which part of each clip to use

By default each clip is trimmed to the *middle* `beats-per-clip` beats, since
phone clips are often shaky right at the start/end. If you want control over
exactly which moment a clip starts at, or want specific clips to last longer
(more beats) because the content deserves it, pass `--manifest` pointing at a
JSON file like [`manifest.example.json`](manifest.example.json):

```json
{
  "clip1.mp4": {"start_ms": 1200, "beats": 2},
  "clip2.mp4": {"start_ms": 500},
  "clip3.mp4": {"beats": 4}
}
```

- `start_ms`: where in the source clip to start (find this by scrubbing the
  clip in any video player). Omit it to keep the middle-of-clip default.
- `beats`: how many beats this specific clip should span. Omit it to fall
  back to `--beats-per-clip`.

Clips not listed in the manifest just use the defaults. Nothing gets
re-encoded or overwritten — the manifest only tells the tool where to cut.

```bash
python -m aftermovie.cli clips_folder output.mp4 --song song.mp3 --manifest manifest.json
```

## How it works

1. `aftermovie/bpm.py` uses `librosa` to estimate each song's tempo (BPM) and
   converts it to the length of a single beat, in milliseconds.
2. `aftermovie/manifest.py` optionally loads per-clip `start_ms`/`beats`
   overrides from a JSON file.
3. `aftermovie/builder.py` loads each clip in the input folder with
   `moviepy`, trims it to `[start, start + beats * unit_ms]` using whichever
   song currently has runtime left (defaulting start to the clip's middle and
   beats to `--beats-per-clip`), concatenates all the trimmed clips, and lays
   the matching stretch of each song underneath as the audio track.
4. `aftermovie/cli.py` is the command-line entry point tying it together.
