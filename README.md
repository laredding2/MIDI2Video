# MIDI2Video

Convert MIDI files into high-quality Synthesia-style piano tutorial videos using Python and FFmpeg.

![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)
![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)

## Features

- **Falling-note waterfall** visualization with a realistic 88-key piano keyboard
- **3 built-in color schemes**: classic (blue), neon (green/pink), warm (orange)
- **Flexible note coloring**: by track, pitch (chromatic), or velocity
- **High-quality output**: H.264 encoding via FFmpeg with configurable resolution and FPS
- **Customizable**: lookahead distance, keyboard size, lead-in/out timing, title overlay
- **Progress bar** and real-time render stats in the terminal
- **No GPU required** — renders with Pillow and pipes raw frames to FFmpeg

## Requirements

- Python 3.8+
- FFmpeg (must be on your `PATH`)
- Python packages:

```bash
pip install mido Pillow
```

## Usage

Basic usage:

```bash
python midi2video.py song.mid -o song.mp4
```

With options:

```bash
# 60 FPS, 4K, neon theme, color notes by pitch
python midi2video.py song.mid -o song.mp4 \
    --fps 60 \
    --resolution 3840x2160 \
    --color-scheme neon \
    --color-by pitch

# Add a title, longer lookahead
python midi2video.py song.mid -o song.mp4 \
    --title "Moonlight Sonata - Beethoven" \
    --lookahead 4.0 \
    --color-scheme warm
```

## Options

| Flag | Default | Description |
|---|---|---|
| `input` | *(required)* | Input MIDI file (`.mid` / `.midi`) |
| `-o, --output` | `output.mp4` | Output video file path |
| `--fps` | `30` | Frames per second |
| `--resolution` | `1920x1080` | Video resolution (`WIDTHxHEIGHT`) |
| `--color-scheme` | `classic` | Color theme: `classic`, `neon`, `warm` |
| `--color-by` | `track` | Note coloring: `track`, `pitch`, `velocity` |
| `--lookahead` | `3.0` | Seconds of upcoming notes visible |
| `--lead-in` | `2.0` | Seconds of silence before first note |
| `--lead-out` | `2.0` | Seconds of silence after last note |
| `--title` | *(none)* | Title text shown at the start |
| `--show-note-names` | off | Show note names on white keys |
| `--keyboard-height` | `0.18` | Keyboard height as fraction of video |

## Color Schemes

- **classic** — Dark background with soft blue notes
- **neon** — Deep dark background with vivid green/pink/cyan notes
- **warm** — Dark brown background with amber/orange notes

## How It Works

1. **Parse MIDI** — Reads all note-on/note-off events using `mido` and converts ticks to seconds
2. **Render frames** — For each video frame, Pillow draws the piano keyboard and any notes within the lookahead window as falling rectangles
3. **Encode** — Raw RGB frames are piped to FFmpeg's stdin, producing an H.264 MP4

## Tips

- Use `--fps 60` for smoother playback (doubles render time)
- Increase `--lookahead` for songs with fast passages so the viewer can read ahead
- Use `--color-by pitch` to easily distinguish notes by their musical pitch class
- For YouTube uploads, `1920x1080` at 30 fps with CRF 18 is a good balance of quality and file size

## License

MIT
