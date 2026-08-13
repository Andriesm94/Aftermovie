# aftermovie

Stitch a folder of short video clips into one beat-synced aftermovie. Every
clip is trimmed to exactly `x` milliseconds, where `x` is the beat length of
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

Randomize clip order, span two beats per clip, and set output framerate:

```bash
python -m aftermovie.cli clips_folder output.mp4 --song song.mp3 --order shuffle --seed 42 --beats-per-clip 2 --fps 30
```

Clips shorter than the computed beat length are skipped (with a warning) since
they can't be trimmed up to that length.

## How it works

1. `aftermovie/bpm.py` uses `librosa` to estimate the song's tempo (BPM) and
   converts it to a clip length in milliseconds: `60000 / BPM * beats_per_clip`.
2. `aftermovie/builder.py` loads each clip in the input folder with `moviepy`,
   trims it to `[0, beat_ms]`, concatenates all the trimmed clips, and lays
   the song underneath as the audio track.
3. `aftermovie/cli.py` is the command-line entry point tying it together.
