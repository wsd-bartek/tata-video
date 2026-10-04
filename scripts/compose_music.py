"""Compose the score: low strings + light piano, timed to the voice-over.

Writes build/score.mid and renders build/music.wav with FluidSynth
(copies kept in assets/audio/ as score.mid and score.flac)
(FluidR3 General MIDI soundfont), then adds a hall reverb.

Harmony follows the story: D minor in the dark 1981 opening, a warm turn to
F major at "W 2008 roku", a bright lift with high strings at "Dziś ... 2026",
and a long final F major chord under the end card.

Usage: python3 scripts/compose_music.py
"""
import os
import shutil
import subprocess
import sys

import mido
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from timeline import VO_OFFSET, total_duration  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
SF2 = "/usr/share/sounds/sf2/FluidR3_GM.sf2"
TPB = 480          # ticks per beat; tempo is 60 BPM, so one beat = one second
SR = 48000

NOTE = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5,
        "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}


def n(name):
    """'Bb3' -> MIDI note number."""
    pitch, octave = name[:-1], int(name[-1])
    return 12 * (octave + 1) + NOTE[pitch]


def f(t_vo):
    return t_vo + VO_OFFSET


# Chord plan in film seconds: (time, bass, pad voicing, high strings or None)
CHORDS = [
    (0.0,  "D2",  ["D3", "F3", "A3"], None),                      # 1981, darkness
    (3.6,  "Bb1", ["D3", "F3", "Bb3"], None),
    (f(4.42), "F2", ["C3", "F3", "A3"], None),                    # first light
    (7.4,  "C2",  ["C3", "E3", "G3"], None),
    (f(6.80), "D2", ["D3", "F3", "A3"], None),                    # 45 lat ... radości
    (9.5,  "Bb1", ["D3", "F3", "Bb3"], None),
    (f(9.20), "G1", ["D3", "G3", "Bb3"], None),                   # wspomnień
    (11.9, "A1",  ["D3", "E3", "A3"], None),                      # A sus4
    (12.35, "A1", ["C#3", "E3", "A3"], None),                     # A
    (f(11.30), "F2", ["C3", "F3", "A3", "C4"], None),             # W 2008 roku - warm
    (f(13.20), "Bb1", ["D3", "F3", "Bb3", "D4"], None),           # ktoś wyjątkowy
    (f(15.20), "A1", ["C3", "F3", "A3", "C4"], None),             # jego syn, Bartek
    (f(16.10), "Bb1", ["D3", "F3", "Bb3", "D4"], None),
    (f(17.05), "F2", ["C3", "F3", "A3"], None),                   # wspólna historia
    (f(18.30), "E2", ["C3", "E3", "G3"], None),
    (f(19.20), "D2", ["D3", "F3", "A3"], None),
    (f(19.60), "C2", ["C3", "E3", "G3"], None),
    (f(20.05), "F2", ["F3", "A3", "C4"], ["F4", "A4", "C5"]),     # Dziś - golden
    (f(21.70), "E2", ["E3", "G3", "C4"], ["E4", "G4", "C5"]),
    (f(23.00), "D2", ["D3", "F3", "A3"], ["F4", "A4", "D5"]),     # dates title
    (f(24.20), "Bb1", ["D3", "F3", "Bb3"], ["F4", "Bb4", "D5"]),  # 45 lat życia
    (f(25.50), "C2", ["C3", "F3", "G3"], ["F4", "G4", "C5"]),     # C sus4
    (f(26.30), "C2", ["C3", "E3", "G3"], ["E4", "G4", "C5"]),
    (f(27.00), "F2", ["C3", "F3", "A3"], None),                   # Tato - tender
    (f(28.20), "F2", ["D3", "F3", "Bb3"], None),
    (f(29.35), "D2", ["D3", "F3", "A3"], ["F4", "A4", "D5"]),     # road ahead
    (f(30.40), "Bb1", ["D3", "F3", "Bb3"], ["F4", "Bb4", "D5"]),
    (f(31.50), "C2", ["C3", "F3", "G3"], ["F4", "G4", "C5"]),
    (f(32.60), "C2", ["C3", "E3", "G3"], ["E4", "G4", "C5"]),
    (f(33.60), "F2", ["C3", "F3", "A3", "C4"], ["F4", "A4", "C5"]),  # black + end card
]

# Piano: sparse single notes in the opening, gentle broken chords later.
PIANO_OPENING = [(0.9, "A4", 1.6), (2.3, "F4", 1.4), (3.6, "D4", 2.0),
                 (f(4.42), "C5", 1.3), (f(5.50), "A4", 1.2), (7.4, "G4", 1.0)]

# Strings expression curve (film seconds, 0-127) - swells with the story.
EXPRESSION = [(0.0, 20), (2.5, 62), (f(4.42), 70), (f(6.8), 66), (f(11.0), 64),
              (f(11.3), 82), (f(15.2), 88), (f(17.05), 78), (f(19.9), 80),
              (f(20.05), 96), (f(24.2), 104), (f(26.8), 100), (f(27.0), 74),
              (f(29.2), 76), (f(29.35), 92), (f(32.9), 104), (f(33.6), 96),
              (f(38.0), 84), (total_duration() - 0.3, 0)]
HIGH_EXPRESSION = [(f(20.05), 0), (f(20.9), 70), (f(26.5), 80), (f(27.0), 0),
                   (f(29.35), 0), (f(30.5), 72), (f(33.6), 84), (f(38.0), 70),
                   (total_duration() - 0.3, 0)]


def ticks(sec):
    return int(round(sec * TPB))


def build_midi(path):
    end = total_duration()
    events = []  # (tick, order, channel, message)
    sounding = {}  # (channel, pitch) -> index of its pending note_off

    def note(ch, t0, t1, pitch, vel):
        prev = sounding.get((ch, pitch))
        if prev is not None and events[prev][0] > ticks(t0):
            # a legato overlap of the same pitch must not cut the new note short
            events[prev] = (ticks(t0),) + events[prev][1:]
        events.append((ticks(t0), 1, ch, mido.Message("note_on", channel=ch, note=pitch, velocity=vel)))
        events.append((ticks(t1), 0, ch, mido.Message("note_off", channel=ch, note=pitch, velocity=0)))
        sounding[(ch, pitch)] = len(events) - 1

    def cc(ch, t, control, value):
        events.append((ticks(t), 0, ch, mido.Message("control_change", channel=ch,
                                                      control=control, value=int(value))))

    def ramp(ch, points, control=11, step=0.05):
        for (t0, v0), (t1, v1) in zip(points, points[1:]):
            t = t0
            while t < t1:
                cc(ch, t, control, v0 + (v1 - v0) * (t - t0) / (t1 - t0))
                t += step
        cc(ch, points[-1][0], control, points[-1][1])

    # channel setup: 0 piano, 1 slow strings, 2 cellos, 3 basses, 4 high strings
    programs = {0: 0, 1: 49, 2: 42, 3: 43, 4: 49}
    volumes = {0: 78, 1: 92, 2: 84, 3: 80, 4: 70}
    pans = {0: 60, 1: 70, 2: 50, 3: 58, 4: 80}
    for ch, prog in programs.items():
        events.append((0, 0, ch, mido.Message("program_change", channel=ch, program=prog)))
        cc(ch, 0, 7, volumes[ch])
        cc(ch, 0, 10, pans[ch])
        cc(ch, 0, 91, 96)   # reverb send
        cc(ch, 0, 93, 0)    # no chorus

    ramp(1, EXPRESSION)
    ramp(2, EXPRESSION)
    ramp(3, [(t, min(127, v + 10)) for t, v in EXPRESSION])
    ramp(4, HIGH_EXPRESSION)
    cc(0, 0, 11, 100)
    cc(0, 0, 64, 0)

    # strings
    for i, (t, bass, pad, high) in enumerate(CHORDS):
        t_next = CHORDS[i + 1][0] if i + 1 < len(CHORDS) else end - 0.2
        overlap = 0.12  # legato
        note(3, t, t_next + overlap, n(bass), 70)
        note(2, t, t_next + overlap, n(bass) + 12, 66)
        for p in pad:
            note(1, t, t_next + overlap, n(p), 72)
        if high:
            for p in high:
                note(4, t, t_next + overlap, n(p), 64)

    # piano - opening single notes
    for t, p, dur in PIANO_OPENING:
        note(0, t, t + dur, n(p), 44)

    # piano - broken chords from the Lebensweg on, sustain pedal per chord
    rng = np.random.default_rng(7)
    for i, (t, bass, pad, high) in enumerate(CHORDS):
        if t < f(6.80) - 0.01:
            continue
        t_next = CHORDS[i + 1][0] if i + 1 < len(CHORDS) else end
        tender = f(27.0) <= t < f(29.35)          # "Tato" - slower, more intimate
        bright = high is not None
        step = 0.75 if tender else 0.5
        tones = [n(p) + 12 for p in pad]           # piano one octave above the pad
        if bright:
            tones = [x + 12 for x in tones[:3]] + [tones[0] + 24]
        pattern = [tones[0], tones[min(2, len(tones) - 1)], tones[1], tones[min(2, len(tones) - 1)]]
        cc(0, t, 64, 0)
        cc(0, t + 0.03, 64, 127)
        last = min(t_next, f(35.1)) if t < f(33.6) else t + 0.1
        k, tt = 0, t
        while tt < last - 0.05:
            vel = int(36 + 8 * (k % 4 == 0) + rng.integers(-3, 4))
            note(0, tt, tt + step * 1.8, pattern[k % 4], vel)
            k += 1
            tt += step

    # final: rolled F major chord on the black, then three falling notes
    t0 = f(33.6) + 0.3
    for j, p in enumerate(["F3", "C4", "F4", "A4", "C5"]):
        note(0, t0 + j * 0.09, end - 0.5, n(p), 46 - j * 2)
    for t, p in [(f(35.1) + 1.0, "C5"), (f(35.1) + 2.6, "A4"), (f(35.1) + 4.4, "F4")]:
        note(0, t, end - 0.4, n(p), 40)
    cc(0, end - 0.3, 64, 0)

    mid = mido.MidiFile(ticks_per_beat=TPB)
    tracks = {}
    for ch in programs:
        tr = mido.MidiTrack()
        tr.append(mido.MetaMessage("set_tempo", tempo=1_000_000, time=0))
        mid.tracks.append(tr)
        tracks[ch] = tr
    events.sort(key=lambda e: (e[0], e[1]))
    last_tick = {ch: 0 for ch in programs}
    for tick, _, ch, msg in events:
        tracks[ch].append(msg.copy(time=tick - last_tick[ch]))
        last_tick[ch] = tick
    mid.save(path)


def hall_reverb(x, sr, seconds=3.2, wet=0.28, seed=3):
    """Convolve with a synthetic, slightly dark hall impulse response."""
    rng = np.random.default_rng(seed)
    length = int(seconds * sr)
    t = np.arange(length) / sr
    ir = np.empty((length, 2), dtype=np.float32)
    for c in range(2):
        noise = rng.standard_normal(length).astype(np.float32)
        # darken: one-pole low-pass that gets darker over time
        out = np.empty_like(noise)
        acc = 0.0
        for i in range(0, length, 256):
            a = 0.35 + 0.55 * min(1.0, i / length * 2.0)
            seg = noise[i:i + 256]
            for j in range(len(seg)):
                acc = a * acc + (1 - a) * seg[j]
                out[i + j] = acc
        ir[:, c] = out * np.exp(-t * 6.9 / seconds)
    ir[: int(0.02 * sr)] *= np.linspace(0, 1, int(0.02 * sr))[:, None]  # pre-delay softening
    ir /= np.sqrt((ir ** 2).sum(axis=0, keepdims=True))
    nfft = 1 << int(np.ceil(np.log2(len(x) + length)))
    y = np.empty((len(x) + length - 1, 2), dtype=np.float32)
    for c in range(2):
        y[:, c] = np.fft.irfft(np.fft.rfft(x[:, c], nfft) * np.fft.rfft(ir[:, c], nfft), nfft)[: len(y)]
    y = y[: len(x)]
    return (1 - wet) * x + wet * y * (np.abs(x).max() / (np.abs(y).max() + 1e-9))


def main():
    os.makedirs(BUILD, exist_ok=True)
    mid_path = os.path.join(BUILD, "score.mid")
    dry_path = os.path.join(BUILD, "music_dry.wav")
    out_path = os.path.join(BUILD, "music.wav")
    build_midi(mid_path)
    subprocess.run(["fluidsynth", "-ni", "-q", "-g", "0.5", "-r", str(SR),
                    "-o", "synth.reverb.room-size=0.8", "-o", "synth.reverb.damp=0.4",
                    "-o", "synth.reverb.width=1.0", "-o", "synth.reverb.level=0.7",
                    "-F", dry_path, SF2, mid_path], check=True)
    import wave
    with wave.open(dry_path) as w:
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        x = raw.reshape(-1, w.getnchannels()).astype(np.float32) / 32768
    total = int(total_duration() * SR)
    x = np.pad(x, ((0, max(0, total - len(x))), (0, 0)))[:total]
    y = hall_reverb(x, SR)
    fade = int(1.5 * SR)
    y[-fade:] *= np.linspace(1, 0, fade)[:, None] ** 2
    y /= np.abs(y).max() + 1e-9
    y *= 0.89
    with wave.open(out_path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((y * 32767).astype(np.int16).tobytes())
    audio_dir = os.path.join(ROOT, "assets", "audio")
    os.makedirs(audio_dir, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", out_path, "-c:a", "flac", "-compression_level", "8",
                    os.path.join(audio_dir, "score.flac")], check=True)
    shutil.copy(mid_path, os.path.join(audio_dir, "score.mid"))
    print("wrote", out_path, f"{len(y) / SR:.2f}s")


if __name__ == "__main__":
    main()
