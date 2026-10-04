"""Score for the long film: strings, piano, horns - one mood per chapter.

Reads MUSIC (sections with chord changes and a mood) from the film config and renders
a General-MIDI score with FluidSynth, then adds a hall reverb.

Usage: python3 scripts/compose_long.py regie/private/film_config.py build/long/music_long.wav
"""
import os
import subprocess
import sys
import wave

import mido
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from compose_music import hall_reverb  # noqa: E402
from film import load_config  # noqa: E402

SF2 = "/usr/share/sounds/sf2/FluidR3_GM.sf2"
TPB, SR = 480, 48000
PC = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8,
      "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}

# channel: (program, volume, pan)
CH = {"piano": (0, 0, 84, 60), "pad": (1, 49, 96, 64), "high": (2, 48, 74, 82), "cello": (3, 42, 86, 48),
      "bass": (4, 43, 82, 58), "pizz": (5, 45, 70, 72), "horn": (6, 60, 64, 54), "timp": (7, 47, 70, 64)}

# mood -> texture settings
MOODS = {
    "dark":      dict(pad=52, high=0, bass=60, piano="sparse", cello_ostinato=False, pizz=False, horn=0),
    "nostalgic": dict(pad=60, high=0, bass=58, piano="arp8", cello_ostinato=False, pizz=False, horn=0),
    "moving":    dict(pad=66, high=30, bass=66, piano="arp12", cello_ostinato=True, pizz=False, horn=0),
    "love":      dict(pad=70, high=40, bass=58, piano="melody", cello_ostinato=False, pizz=False, horn=0),
    "warm":      dict(pad=82, high=55, bass=66, piano="arp8", cello_ostinato=False, pizz=False, horn=0),
    "light":     dict(pad=50, high=30, bass=54, piano="arp8", cello_ostinato=False, pizz=True, horn=0),
    "journey":   dict(pad=74, high=50, bass=72, piano="arp12", cello_ostinato=True, pizz=False, horn=44),
    "silence":   dict(pad=26, high=0, bass=0, piano="sparse", cello_ostinato=False, pizz=False, horn=0),
    "golden":    dict(pad=100, high=86, bass=78, piano="arp12", cello_ostinato=False, pizz=False, horn=70),
    "wishes":    dict(pad=78, high=56, bass=64, piano="arp8", cello_ostinato=False, pizz=False, horn=0),
    "finale":    dict(pad=110, high=96, bass=86, piano="arp12", cello_ostinato=False, pizz=False, horn=84, timp=True),
    "coda":      dict(pad=100, high=74, bass=72, piano="coda", cello_ostinato=False, pizz=False, horn=58),
}


def parse(sym):
    """'Dm', 'C/E', 'Bb/F', 'F/A', 'Csus4', 'Eb' -> (pitch classes, bass pc)."""
    main, _, slash = sym.partition("/")
    root = main[:2] if len(main) > 1 and main[1] in "b#" else main[:1]
    qual = main[len(root):]
    r = PC[root]
    if qual.startswith("m") and not qual.startswith("maj"):
        ints = [0, 3, 7]
    elif qual == "sus4":
        ints = [0, 5, 7]
    else:
        ints = [0, 4, 7]
    pcs = [(r + i) % 12 for i in ints]
    bass = PC[slash] if slash else r
    return pcs, bass


def voice(pcs, lo, hi, prev, n=4):
    """Pick n chord tones in [lo, hi] closest to the previous voicing."""
    cands = sorted({p for p in range(lo, hi + 1) if p % 12 in pcs})
    best, best_cost = None, 1e9
    for i in range(len(cands) - n + 1):
        v = cands[i:i + n]
        if not set(x % 12 for x in v) >= set(pcs):
            continue
        cost = sum(abs(a - b) for a, b in zip(v, prev)) if prev else abs(sum(v) / n - (lo + hi) / 2)
        if cost < best_cost:
            best, best_cost = v, cost
    return best or cands[:n]


def closest(pc, lo, hi, prev):
    opts = [p for p in range(lo, hi + 1) if p % 12 == pc]
    return min(opts, key=lambda p: abs(p - prev)) if prev else opts[len(opts) // 2]


def build(cfg, path):
    end = cfg.MUSIC_END
    sections = cfg.MUSIC
    ev = []
    sounding = {}   # (channel, pitch) -> index of its pending note_off in ev

    def note(ch, t0, t1, p, vel):
        on, off = round(t0 * TPB), round(t1 * TPB)
        key = (ch, int(p))
        prev = sounding.get(key)
        if prev is not None and ev[prev][0] > on:
            # same pitch still ringing (legato overlap): end it exactly where the new one starts,
            # otherwise its late note_off would cut the new note short
            ev[prev] = (on,) + ev[prev][1:]
        ev.append((on, 1, ch, mido.Message("note_on", channel=ch, note=int(p), velocity=int(vel))))
        ev.append((off, 0, ch, mido.Message("note_off", channel=ch, note=int(p), velocity=0)))
        sounding[key] = len(ev) - 1

    def cc(ch, t, c, val):
        ev.append((round(t * TPB), 0, ch, mido.Message("control_change", channel=ch, control=c,
                                                       value=int(max(0, min(127, val))))))

    for name, (ch, prog, vol, pan) in CH.items():
        ev.append((0, 0, ch, mido.Message("program_change", channel=ch, program=prog)))
        cc(ch, 0, 7, vol)
        cc(ch, 0, 10, pan)
        cc(ch, 0, 91, 100)
        cc(ch, 0, 93, 0)

    # expression curves: ramp between section levels
    levels = {k: [] for k in ("pad", "high", "bass", "horn")}
    for s in sections:
        m = MOODS[s["mood"]]
        for k in levels:
            levels[k].append((s["t"], m.get(k, 0)))
    for k, chans in (("pad", ["pad"]), ("high", ["high"]), ("bass", ["bass", "cello"]), ("horn", ["horn"])):
        pts = levels[k] + [(end - 0.3, 0)]
        for (t0, a), (t1, b) in zip(pts, pts[1:]):
            ramp = min(2.0, (t1 - t0) * 0.5)
            t = t0
            while t < t1:
                u = min(1.0, (t - t0) / ramp) if ramp > 0 else 1.0
                prev = pts[max(0, pts.index((t0, a)) - 1)][1] if t0 > 0 else 0
                val = prev + (a - prev) * u if t - t0 < ramp else a
                for c in chans:
                    cc(CH[c][0], t, 11, val)
                t += 0.1

    rng = np.random.default_rng(3)
    prev_pad, prev_bass, prev_high = None, None, None
    all_chords = []
    for si, s in enumerate(sections):
        s_end = sections[si + 1]["t"] if si + 1 < len(sections) else end - 0.5
        for ci, (off, sym) in enumerate(s["chords"]):
            t0 = s["t"] + off
            t1 = s["t"] + s["chords"][ci + 1][0] if ci + 1 < len(s["chords"]) else s_end
            all_chords.append((t0, t1, sym, s["mood"]))

    for i, (t0, t1, sym, mood) in enumerate(all_chords):
        m = MOODS[mood]
        pcs, bpc = parse(sym)
        leg = 0.15
        pad = voice(pcs, 50, 69, prev_pad)
        prev_pad = pad
        if m["pad"] > 0:
            for p in pad:
                note(CH["pad"][0], t0, t1 + leg, p, 74)
        if m["high"] > 0:
            hv = voice(pcs, 67, 84, prev_high, n=3)
            prev_high = hv
            for p in hv:
                note(CH["high"][0], t0, t1 + leg, p, 66)
        if m["bass"] > 0:
            b = closest(bpc, 31, 45, prev_bass)
            prev_bass = b
            note(CH["bass"][0], t0, t1 + leg, b, 74)
            if m["cello_ostinato"]:
                step = 0.42 if mood == "journey" else 0.5
                t, k = t0, 0
                while t < t1 - 0.05:
                    p = b + 12 + (7 if k % 4 == 2 else 0)
                    note(CH["cello"][0], t, t + step * 0.9, p, 60 + (8 if k % 4 == 0 else 0))
                    t += step
                    k += 1
            else:
                note(CH["cello"][0], t0, t1 + leg, b + 12, 66)
        if m["horn"] > 0 and (t1 - t0) > 1.5:
            hn = voice(pcs, 53, 67, None, n=2)
            for p in hn:
                note(CH["horn"][0], t0 + 0.05, t1 + leg, p, 58)
        if m.get("timp"):
            t = t0
            while t < t1:                                 # soft timpani roll under the finale
                note(CH["timp"][0], t, t + 0.12, closest(bpc, 38, 50, None), 34 + 30 * (t - t0) / max(1, t1 - t0))
                t += 0.11
        if m["pizz"]:
            tones = sorted(voice(pcs, 55, 74, None, n=4))
            t, k = t0, 0
            while t < t1 - 0.05:
                note(CH["pizz"][0], t, t + 0.3, tones[[0, 2, 1, 3][k % 4]], 58 + int(rng.integers(-4, 5)))
                t += 0.5
                k += 1

        # piano
        style = m["piano"]
        ptones = sorted(voice(pcs, 60, 79, None, n=4))
        if style == "sparse":
            for k, t in enumerate(np.arange(t0 + 0.6, t1 - 0.4, 2.2)):
                note(CH["piano"][0], t, t + 2.4, ptones[[3, 1, 2, 0][k % 4]], 40 + int(rng.integers(-3, 4)))
        elif style in ("arp8", "arp12"):
            step = 0.5 if style == "arp8" else 0.34
            cc(CH["piano"][0], t0, 64, 0)
            cc(CH["piano"][0], t0 + 0.03, 64, 127)
            t, k = t0, 0
            while t < t1 - 0.05:
                p = ptones[[0, 2, 1, 3, 2, 1][k % 6]]
                note(CH["piano"][0], t, t + step * 2, p, 38 + (8 if k % 6 == 0 else 0) + int(rng.integers(-3, 4)))
                t += step
                k += 1
        elif style == "melody":
            top = ptones[-1] + 12 if ptones[-1] < 74 else ptones[-1]
            note(CH["piano"][0], t0, t0 + 1.6, top, 50)
            note(CH["piano"][0], t0 + 1.6, t1, ptones[2] + 12 if ptones[2] < 72 else ptones[2], 42)
            for k, t in enumerate(np.arange(t0, t1 - 0.2, 0.75)):
                note(CH["piano"][0], t, t + 1.2, ptones[[0, 1][k % 2]] - 12, 32)
        elif style == "coda":
            for j, p in enumerate(sorted(set(ptones + [ptones[0] - 12]))):
                note(CH["piano"][0], t0 + j * 0.09, t1, p, 46 - j * 2)
    # final falling notes over the coda
    coda_t = [s["t"] for s in sections if s["mood"] == "coda"][0]
    for t, p in [(coda_t + 1.5, 72), (coda_t + 3.6, 69), (coda_t + 6.0, 65), (coda_t + 9.0, 60)]:
        note(CH["piano"][0], t, end - 0.3, p, 42)
    cc(CH["piano"][0], end - 0.2, 64, 0)

    mid = mido.MidiFile(ticks_per_beat=TPB)
    tracks = {}
    for _, (ch, *_r) in CH.items():
        tr = mido.MidiTrack()
        tr.append(mido.MetaMessage("set_tempo", tempo=1_000_000, time=0))
        mid.tracks.append(tr)
        tracks[ch] = tr
    ev.sort(key=lambda e: (e[0], e[1]))
    last = {ch: 0 for ch in tracks}
    for tick, _, ch, msg in ev:
        tracks[ch].append(msg.copy(time=tick - last[ch]))
        last[ch] = tick
    mid.save(path)


def main():
    cfg = load_config(sys.argv[1])
    out = sys.argv[2]
    os.makedirs(os.path.dirname(out), exist_ok=True)
    midp, dry = out.replace(".wav", ".mid"), out.replace(".wav", "_dry.wav")
    build(cfg, midp)
    subprocess.run(["fluidsynth", "-ni", "-q", "-g", "0.45", "-r", str(SR), "-o", "synth.reverb.room-size=0.85",
                    "-o", "synth.reverb.damp=0.4", "-o", "synth.reverb.width=1.0", "-o", "synth.reverb.level=0.7",
                    "-F", dry, SF2, midp], check=True)
    with wave.open(dry) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).reshape(-1, w.getnchannels()).astype(np.float32) / 32768
    total = int(cfg.MUSIC_END * SR)
    x = np.pad(x, ((0, max(0, total - len(x))), (0, 0)))[:total]
    y = hall_reverb(x, SR, seconds=3.6, wet=0.30)
    fade = int(2.5 * SR)
    y[-fade:] *= np.linspace(1, 0, fade)[:, None] ** 2
    y = y / (np.abs(y).max() + 1e-9) * 0.89
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((y * 32767).astype(np.int16).tobytes())
    print("wrote", out, f"{len(y) / SR:.1f}s")


if __name__ == "__main__":
    main()
