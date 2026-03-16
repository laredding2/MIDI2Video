#!/usr/bin/env python3
"""
midi2video - Convert MIDI files into high-quality piano tutorial videos using FFmpeg.

Renders a falling-note visualization with a piano keyboard, similar to Synthesia.
Notes fall from the top of the screen onto the keyboard and light up the keys
when active. Supports customizable resolution, FPS, colors, and more.

Requirements:
    pip install mido Pillow
    ffmpeg must be installed and on PATH

Usage:
    python midi2video.py input.mid -o output.mp4
    python midi2video.py input.mid -o output.mp4 --fps 60 --resolution 1920x1080
    python midi2video.py input.mid -o output.mp4 --color-scheme neon
"""

from __future__ import annotations

import argparse
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import mido
except ImportError:
    sys.exit("Error: 'mido' is required. Install with: pip install mido")

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("Error: 'Pillow' is required. Install with: pip install Pillow")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Note:
    """A single MIDI note event with timing."""
    pitch: int
    velocity: int
    start_time: float  # seconds
    end_time: float    # seconds
    channel: int = 0
    track: int = 0


@dataclass
class ColorScheme:
    """Visual theme for the video."""
    name: str
    background: tuple[int, int, int]
    white_key: tuple[int, int, int]
    black_key: tuple[int, int, int]
    white_key_border: tuple[int, int, int]
    active_white_key: tuple[int, int, int]
    active_black_key: tuple[int, int, int]
    note_colors: list[tuple[int, int, int]]  # one per channel / track
    falling_note_border: tuple[int, int, int]
    text_color: tuple[int, int, int]
    separator_color: tuple[int, int, int]
    guideline_color: tuple[int, int, int]


COLOR_SCHEMES: dict[str, ColorScheme] = {
    "classic": ColorScheme(
        name="classic",
        background=(18, 18, 24),
        white_key=(240, 240, 240),
        black_key=(30, 30, 30),
        white_key_border=(180, 180, 180),
        active_white_key=(100, 200, 255),
        active_black_key=(60, 160, 220),
        note_colors=[
            (100, 200, 255), (255, 120, 120), (120, 255, 140), (255, 200, 80),
            (200, 130, 255), (255, 160, 200), (80, 220, 220), (255, 150, 60),
            (150, 200, 100), (200, 200, 255), (255, 100, 200), (100, 255, 200),
            (220, 180, 120), (180, 120, 255), (120, 200, 180), (240, 240, 120),
        ],
        falling_note_border=(255, 255, 255),
        text_color=(220, 220, 220),
        separator_color=(60, 60, 80),
        guideline_color=(40, 40, 55),
    ),
    "neon": ColorScheme(
        name="neon",
        background=(8, 8, 16),
        white_key=(220, 220, 230),
        black_key=(20, 20, 30),
        white_key_border=(100, 100, 120),
        active_white_key=(0, 255, 180),
        active_black_key=(0, 200, 140),
        note_colors=[
            (0, 255, 180), (255, 0, 128), (0, 180, 255), (255, 255, 0),
            (180, 0, 255), (255, 100, 0), (0, 255, 80), (255, 0, 255),
            (0, 255, 255), (255, 80, 80), (128, 255, 0), (255, 180, 0),
            (80, 80, 255), (255, 0, 60), (0, 200, 100), (200, 200, 255),
        ],
        falling_note_border=(200, 200, 255),
        text_color=(200, 200, 240),
        separator_color=(40, 40, 70),
        guideline_color=(25, 25, 50),
    ),
    "warm": ColorScheme(
        name="warm",
        background=(30, 20, 15),
        white_key=(250, 245, 235),
        black_key=(40, 30, 25),
        white_key_border=(200, 180, 160),
        active_white_key=(255, 160, 60),
        active_black_key=(220, 130, 40),
        note_colors=[
            (255, 160, 60), (255, 100, 80), (255, 200, 100), (200, 120, 60),
            (255, 140, 140), (220, 180, 80), (180, 100, 50), (255, 180, 120),
            (200, 160, 100), (255, 120, 60), (180, 140, 80), (240, 200, 140),
            (200, 80, 60), (255, 220, 160), (160, 100, 70), (220, 160, 120),
        ],
        falling_note_border=(255, 220, 180),
        text_color=(240, 220, 200),
        separator_color=(70, 50, 40),
        guideline_color=(50, 35, 28),
    ),
}


# ---------------------------------------------------------------------------
# MIDI parsing
# ---------------------------------------------------------------------------

def parse_midi(filepath: str) -> tuple[list[Note], float, int]:
    """
    Parse a MIDI file and return a list of Note objects, the total duration
    in seconds, and the number of distinct tracks with notes.
    """
    mid = mido.MidiFile(filepath)
    notes: list[Note] = []

    # Track active notes: (pitch, channel) -> (start_time, velocity, track_idx)
    active: dict[tuple[int, int], tuple[float, int, int]] = {}

    for track_idx, track in enumerate(mid.tracks):
        abs_time = 0.0
        for msg in track:
            abs_time += mido.tick2second(msg.time, mid.ticks_per_beat, _get_tempo(mid, abs_time))

            if msg.type == "note_on" and msg.velocity > 0:
                key = (msg.note, msg.channel)
                active[key] = (abs_time, msg.velocity, track_idx)
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                key = (msg.note, msg.channel)
                if key in active:
                    start, vel, tidx = active.pop(key)
                    if abs_time > start:
                        notes.append(Note(
                            pitch=msg.note,
                            velocity=vel,
                            start_time=start,
                            end_time=abs_time,
                            channel=msg.channel,
                            track=tidx,
                        ))

    if not notes:
        sys.exit("Error: No notes found in MIDI file.")

    total_duration = max(n.end_time for n in notes)
    track_count = len(set(n.track for n in notes))
    return notes, total_duration, track_count


def _get_tempo(mid: mido.MidiFile, current_time: float) -> int:
    """Get the tempo at a given time (simplified: uses first tempo found)."""
    for track in mid.tracks:
        for msg in track:
            if msg.type == "set_tempo":
                return msg.tempo
    return 500000  # default 120 BPM


# ---------------------------------------------------------------------------
# Piano layout helpers
# ---------------------------------------------------------------------------

# MIDI note range for a standard 88-key piano: 21 (A0) to 108 (C8)
PIANO_MIN = 21
PIANO_MAX = 108
TOTAL_KEYS = PIANO_MAX - PIANO_MIN + 1

# Black key pattern within an octave (0-indexed from C)
BLACK_KEY_OFFSETS = {1, 3, 6, 8, 10}  # C#, D#, F#, G#, A#


def is_black_key(midi_note: int) -> bool:
    return (midi_note % 12) in BLACK_KEY_OFFSETS


def get_white_key_index(midi_note: int) -> Optional[int]:
    """Return the index among white keys only, or None if it's a black key."""
    if is_black_key(midi_note):
        return None
    count = 0
    for n in range(PIANO_MIN, midi_note):
        if not is_black_key(n):
            count += 1
    return count


def count_white_keys() -> int:
    return sum(1 for n in range(PIANO_MIN, PIANO_MAX + 1) if not is_black_key(n))


# Precompute white key positions
NUM_WHITE_KEYS = count_white_keys()


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

@dataclass
class RenderConfig:
    """All parameters controlling the video render."""
    width: int = 1920
    height: int = 1080
    fps: int = 30
    lookahead_seconds: float = 3.0  # how far ahead notes are visible
    keyboard_height_ratio: float = 0.18
    black_key_height_ratio: float = 0.62
    black_key_width_ratio: float = 0.6
    note_corner_radius: int = 4
    note_border_width: int = 1
    margin_bottom: int = 2
    lead_in_seconds: float = 2.0
    lead_out_seconds: float = 2.0
    color_scheme: str = "classic"
    show_note_names: bool = False
    title: str = ""
    color_by: str = "track"  # "track" or "velocity" or "pitch"


class PianoRenderer:
    """Renders individual frames of the piano tutorial video."""

    def __init__(self, config: RenderConfig, notes: list[Note], duration: float):
        self.config = config
        self.notes = notes
        self.duration = duration
        self.scheme = COLOR_SCHEMES[config.color_scheme]

        # Layout calculations
        self.kb_height = int(config.height * config.keyboard_height_ratio)
        self.kb_top = config.height - self.kb_height
        self.waterfall_height = self.kb_top  # area above the keyboard

        self.white_key_width = config.width / NUM_WHITE_KEYS
        self.black_key_width = self.white_key_width * config.black_key_width_ratio
        self.black_key_height = int(self.kb_height * config.black_key_height_ratio)

        # Precompute note x-positions and widths for every MIDI pitch
        self._note_x: dict[int, float] = {}
        self._note_w: dict[int, float] = {}
        self._precompute_key_positions()

        # Sort notes by start_time for efficient window queries
        self.notes.sort(key=lambda n: n.start_time)

        # Try to load a small font for note labels
        self._font = None
        self._small_font = None
        if config.show_note_names:
            try:
                self._font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
            except (IOError, OSError):
                self._font = ImageFont.load_default()
        try:
            self._small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
        except (IOError, OSError):
            self._small_font = ImageFont.load_default()

        # Title font
        self._title_font = None
        if config.title:
            try:
                self._title_font = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28
                )
            except (IOError, OSError):
                self._title_font = ImageFont.load_default()

    def _precompute_key_positions(self):
        """Calculate the x position and width for each MIDI note on the keyboard."""
        white_idx = 0
        for note in range(PIANO_MIN, PIANO_MAX + 1):
            if not is_black_key(note):
                x = white_idx * self.white_key_width
                self._note_x[note] = x
                self._note_w[note] = self.white_key_width
                white_idx += 1

        # Black keys: centered between the surrounding white keys
        for note in range(PIANO_MIN, PIANO_MAX + 1):
            if is_black_key(note):
                # Find neighboring white keys
                left_white = note - 1
                while left_white >= PIANO_MIN and is_black_key(left_white):
                    left_white -= 1
                right_white = note + 1
                while right_white <= PIANO_MAX and is_black_key(right_white):
                    right_white += 1

                if left_white >= PIANO_MIN and right_white <= PIANO_MAX:
                    lx = self._note_x[left_white] + self._note_w[left_white]
                    rx = self._note_x[right_white]
                    cx = (lx + rx) / 2
                elif left_white >= PIANO_MIN:
                    cx = self._note_x[left_white] + self._note_w[left_white]
                else:
                    cx = self._note_x[right_white]

                self._note_x[note] = cx - self.black_key_width / 2
                self._note_w[note] = self.black_key_width

    def _get_note_color(self, note: Note) -> tuple[int, int, int]:
        """Determine the color of a note based on the coloring strategy."""
        colors = self.scheme.note_colors
        if self.config.color_by == "track":
            return colors[note.track % len(colors)]
        elif self.config.color_by == "pitch":
            return colors[note.pitch % 12]
        elif self.config.color_by == "velocity":
            # Map velocity (0-127) to a gradient from dim to bright
            t = note.velocity / 127.0
            base = colors[note.track % len(colors)]
            return tuple(int(c * (0.3 + 0.7 * t)) for c in base)  # type: ignore
        return colors[0]

    def _get_visible_notes(self, current_time: float) -> list[Note]:
        """Return notes that should be visible in the current frame."""
        lookahead = self.config.lookahead_seconds
        visible = []
        for note in self.notes:
            # Note is visible if it hasn't fully passed and starts before lookahead
            if note.end_time < current_time - 0.1:
                continue
            if note.start_time > current_time + lookahead:
                break  # sorted, so no more visible notes
            visible.append(note)
        return visible

    def _get_active_notes(self, current_time: float) -> set[int]:
        """Return set of pitches currently being played."""
        active = set()
        for note in self.notes:
            if note.end_time < current_time:
                continue
            if note.start_time > current_time:
                break
            if note.start_time <= current_time <= note.end_time:
                active.add(note.pitch)
        return active

    def render_frame(self, current_time: float) -> bytes:
        """Render a single frame and return raw RGB bytes."""
        W, H = self.config.width, self.config.height
        img = Image.new("RGB", (W, H), self.scheme.background)
        draw = ImageDraw.Draw(img)

        # Draw horizontal guidelines in the waterfall area
        line_spacing = self.waterfall_height / 8
        for i in range(1, 8):
            y = int(i * line_spacing)
            draw.line([(0, y), (W, y)], fill=self.scheme.guideline_color, width=1)

        # Draw falling notes
        visible_notes = self._get_visible_notes(current_time)
        active_pitches = self._get_active_notes(current_time)
        lookahead = self.config.lookahead_seconds

        # Separate into black-key notes and white-key notes for proper layering
        white_notes_to_draw = []
        black_notes_to_draw = []

        for note in visible_notes:
            if note.pitch < PIANO_MIN or note.pitch > PIANO_MAX:
                continue

            x = self._note_x.get(note.pitch)
            w = self._note_w.get(note.pitch)
            if x is None or w is None:
                continue

            # Calculate vertical position (falling from top)
            # At current_time, the note's start should be at the keyboard line
            pixels_per_second = self.waterfall_height / lookahead
            y_bottom = self.kb_top - (note.start_time - current_time) * pixels_per_second
            y_top = self.kb_top - (note.end_time - current_time) * pixels_per_second

            # Clamp to visible area
            y_top = max(0, y_top)
            y_bottom = min(self.kb_top, y_bottom)

            if y_bottom <= y_top:
                continue

            color = self._get_note_color(note)
            entry = (note, x, y_top, w, y_bottom, color)
            if is_black_key(note.pitch):
                black_notes_to_draw.append(entry)
            else:
                white_notes_to_draw.append(entry)

        # Draw white-key notes first, then black-key notes on top
        for note_obj, x, y_top, w, y_bottom, color in white_notes_to_draw:
            self._draw_note_rect(draw, x, y_top, w, y_bottom, color)

        for note_obj, x, y_top, w, y_bottom, color in black_notes_to_draw:
            self._draw_note_rect(draw, x, y_top, w, y_bottom, color)

        # Draw separator line above keyboard
        draw.line([(0, self.kb_top - 1), (W, self.kb_top - 1)],
                  fill=self.scheme.separator_color, width=2)

        # Draw keyboard - white keys first
        white_idx = 0
        for note_val in range(PIANO_MIN, PIANO_MAX + 1):
            if is_black_key(note_val):
                continue
            x = white_idx * self.white_key_width
            is_active = note_val in active_pitches
            fill = self.scheme.active_white_key if is_active else self.scheme.white_key

            draw.rectangle(
                [x, self.kb_top, x + self.white_key_width - 1, H - self.config.margin_bottom],
                fill=fill,
                outline=self.scheme.white_key_border,
                width=1,
            )

            # Subtle gradient effect at top of active keys
            if is_active:
                glow_color = self._get_active_glow(note_val)
                for g in range(6):
                    alpha = 1.0 - g / 6.0
                    gc = tuple(int(c * alpha + fill[i] * (1 - alpha))
                               for i, c in enumerate(glow_color))
                    draw.line([(x + 1, self.kb_top + g), (x + self.white_key_width - 2, self.kb_top + g)],
                              fill=gc)

            white_idx += 1

        # Draw black keys on top
        for note_val in range(PIANO_MIN, PIANO_MAX + 1):
            if not is_black_key(note_val):
                continue
            x = self._note_x.get(note_val)
            w = self._note_w.get(note_val)
            if x is None or w is None:
                continue

            is_active = note_val in active_pitches
            fill = self.scheme.active_black_key if is_active else self.scheme.black_key

            draw.rectangle(
                [x, self.kb_top, x + w, self.kb_top + self.black_key_height],
                fill=fill,
                outline=(60, 60, 60),
                width=1,
            )

            # Highlight glow for active black keys
            if is_active:
                glow = self._get_active_glow(note_val)
                for g in range(4):
                    alpha = 1.0 - g / 4.0
                    gc = tuple(int(c * alpha + fill[i] * (1 - alpha))
                               for i, c in enumerate(glow))
                    draw.line([(x + 1, self.kb_top + g), (x + w - 1, self.kb_top + g)],
                              fill=gc)

        # Draw title if set
        if self.config.title and self._title_font and current_time < self.config.lead_in_seconds + 5:
            opacity = min(1.0, max(0.0, 1.0 - (current_time - self.config.lead_in_seconds - 3) / 2))
            if opacity > 0:
                tc = tuple(int(c * opacity) for c in self.scheme.text_color)
                bbox = self._title_font.getbbox(self.config.title)
                tw = bbox[2] - bbox[0]
                tx = (W - tw) // 2
                draw.text((tx, 20), self.config.title, fill=tc, font=self._title_font)

        # Progress bar at the very top
        progress = current_time / (self.duration + self.config.lead_in_seconds + self.config.lead_out_seconds)
        bar_width = int(W * max(0, min(1, progress)))
        if bar_width > 0:
            draw.rectangle([0, 0, bar_width, 3], fill=self.scheme.note_colors[0])

        return img.tobytes()

    def _draw_note_rect(self, draw: ImageDraw.ImageDraw,
                        x: float, y_top: float, w: float, y_bottom: float,
                        color: tuple[int, int, int]):
        """Draw a single falling note rectangle with rounded corners and glow."""
        r = self.config.note_corner_radius
        x1, y1, x2, y2 = int(x + 1), int(y_top), int(x + w - 1), int(y_bottom)

        if x2 - x1 < 2 or y2 - y1 < 2:
            draw.rectangle([x1, y1, x2, y2], fill=color)
            return

        # Draw rounded rectangle
        draw.rounded_rectangle([x1, y1, x2, y2], radius=r, fill=color)

        # Subtle highlight on the left edge
        highlight = tuple(min(255, c + 40) for c in color)
        draw.line([(x1 + 1, y1 + r), (x1 + 1, y2 - r)], fill=highlight, width=1)

    def _get_active_glow(self, pitch: int) -> tuple[int, int, int]:
        """Get the glow color for an active key from the notes playing it."""
        for note in self.notes:
            if note.pitch == pitch and note.start_time <= self.duration:
                return self._get_note_color(note)
        return self.scheme.note_colors[0]


# ---------------------------------------------------------------------------
# Video encoding pipeline
# ---------------------------------------------------------------------------

def render_video(midi_path: str, output_path: str, config: RenderConfig):
    """Main entry point: parse MIDI, render frames, pipe to FFmpeg."""
    print(f"Parsing MIDI file: {midi_path}")
    notes, duration, track_count = parse_midi(midi_path)
    print(f"  Found {len(notes)} notes across {track_count} track(s)")
    print(f"  Duration: {duration:.1f}s")

    total_time = config.lead_in_seconds + duration + config.lead_out_seconds
    total_frames = int(total_time * config.fps)
    print(f"\nRender settings:")
    print(f"  Resolution: {config.width}x{config.height}")
    print(f"  FPS: {config.fps}")
    print(f"  Color scheme: {config.color_scheme}")
    print(f"  Total frames: {total_frames}")
    print(f"  Estimated video length: {total_time:.1f}s")

    renderer = PianoRenderer(config, notes, duration)

    # Build FFmpeg command
    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-pix_fmt", "rgb24",
        "-s", f"{config.width}x{config.height}",
        "-r", str(config.fps),
        "-i", "-",  # read from stdin
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        output_path,
    ]

    print(f"\nRendering video to: {output_path}")
    proc = subprocess.Popen(
        ffmpeg_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    start = time.time()
    for frame_idx in range(total_frames):
        current_time = frame_idx / config.fps - config.lead_in_seconds
        frame_bytes = renderer.render_frame(current_time)
        try:
            proc.stdin.write(frame_bytes)
        except BrokenPipeError:
            stderr = proc.stderr.read().decode()
            sys.exit(f"FFmpeg error:\n{stderr}")

        # Progress reporting
        if (frame_idx + 1) % config.fps == 0 or frame_idx == total_frames - 1:
            elapsed = time.time() - start
            pct = (frame_idx + 1) / total_frames * 100
            fps_actual = (frame_idx + 1) / elapsed if elapsed > 0 else 0
            remaining = (total_frames - frame_idx - 1) / fps_actual if fps_actual > 0 else 0
            print(f"\r  Progress: {pct:5.1f}% | "
                  f"Frame {frame_idx + 1}/{total_frames} | "
                  f"{fps_actual:.1f} fps | "
                  f"ETA: {remaining:.0f}s", end="", flush=True)

    proc.stdin.close()
    proc.wait()

    if proc.returncode != 0:
        stderr = proc.stderr.read().decode()
        sys.exit(f"\nFFmpeg failed (exit code {proc.returncode}):\n{stderr}")

    elapsed = time.time() - start
    filesize = os.path.getsize(output_path) / (1024 * 1024)
    print(f"\n\nDone! Rendered in {elapsed:.1f}s")
    print(f"Output: {output_path} ({filesize:.1f} MB)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_resolution(s: str) -> tuple[int, int]:
    """Parse a resolution string like '1920x1080'."""
    parts = s.lower().split("x")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"Invalid resolution: {s}. Use format WIDTHxHEIGHT (e.g., 1920x1080)")
    return int(parts[0]), int(parts[1])


def main():
    parser = argparse.ArgumentParser(
        description="Convert MIDI files into high-quality piano tutorial videos.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s song.mid -o song.mp4
  %(prog)s song.mid -o song.mp4 --fps 60 --resolution 1920x1080
  %(prog)s song.mid -o song.mp4 --color-scheme neon --color-by pitch
  %(prog)s song.mid -o song.mp4 --lookahead 4.0 --title "Moonlight Sonata"
        """,
    )
    parser.add_argument("input", help="Input MIDI file (.mid / .midi)")
    parser.add_argument("-o", "--output", default="output.mp4", help="Output video file (default: output.mp4)")
    parser.add_argument("--fps", type=int, default=30, help="Frames per second (default: 30)")
    parser.add_argument("--resolution", type=str, default="1920x1080",
                        help="Video resolution WIDTHxHEIGHT (default: 1920x1080)")
    parser.add_argument("--color-scheme", choices=list(COLOR_SCHEMES.keys()), default="classic",
                        help="Color scheme (default: classic)")
    parser.add_argument("--color-by", choices=["track", "pitch", "velocity"], default="track",
                        help="How to color notes (default: track)")
    parser.add_argument("--lookahead", type=float, default=3.0,
                        help="Seconds of upcoming notes visible (default: 3.0)")
    parser.add_argument("--lead-in", type=float, default=2.0,
                        help="Seconds of silence before the first note (default: 2.0)")
    parser.add_argument("--lead-out", type=float, default=2.0,
                        help="Seconds of silence after the last note (default: 2.0)")
    parser.add_argument("--title", type=str, default="",
                        help="Title text displayed at the start of the video")
    parser.add_argument("--show-note-names", action="store_true",
                        help="Show note names (C, D, E...) on white keys")
    parser.add_argument("--keyboard-height", type=float, default=0.18,
                        help="Keyboard height as fraction of video height (default: 0.18)")

    args = parser.parse_args()

    if not os.path.isfile(args.input):
        sys.exit(f"Error: File not found: {args.input}")

    width, height = parse_resolution(args.resolution)

    config = RenderConfig(
        width=width,
        height=height,
        fps=args.fps,
        lookahead_seconds=args.lookahead,
        lead_in_seconds=args.lead_in,
        lead_out_seconds=args.lead_out,
        color_scheme=args.color_scheme,
        color_by=args.color_by,
        show_note_names=args.show_note_names,
        title=args.title,
        keyboard_height_ratio=args.keyboard_height,
    )

    render_video(args.input, args.output, config)


if __name__ == "__main__":
    main()
