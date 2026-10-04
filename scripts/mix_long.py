"""Mix the long film: place every narration clip at its film time, music ducked underneath.

Each clip is brought to -17 LUFS on its own (recordings came from different sessions),
gets tiny fades, and is laid on the timeline from the config. The music sits under it
with sidechain ducking; a final limiter keeps peaks below -1.5 dBFS.

Usage: python3 scripts/mix_long.py regie/private/film_config.py build/long/music_long.wav build/long/mix_long.wav
"""
import os
import re
import subprocess
import sys
import tempfile
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from film import load_config, path_of  # noqa: E402

SR = 48000
TARGET = -17.0
MUSIC_GAIN_DB = -2.0
VOICE_CHAIN = "highpass=f=70,lowpass=f=15500,acompressor=threshold=-24dB:ratio=2:attack=8:release=160"


def decode(path, t0, t1):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-to", f"{t1:.3f}", "-i", path,
                          "-af", VOICE_CHAIN, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).copy()


def loudness(x):
    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
        write_wav(tmp.name, x[:, None])
        r = subprocess.run(["ffmpeg", "-hide_banner", "-i", tmp.name, "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
                           capture_output=True, text=True, check=True)
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)[-1])


def write_wav(path, x):
    x = np.clip(x, -1, 1)
    with wave.open(path, "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype(np.int16).tobytes())


def main():
    cfg = load_config(sys.argv[1])
    music, out = sys.argv[2], sys.argv[3]
    total = int(cfg.END * SR)
    vo = np.zeros(total, np.float32)
    clips = list(cfg.CLIPS) + [(cfg.CREDIT[0], cfg.CREDIT[1], cfg.CREDIT[2], cfg.CREDIT[3], None)]
    for key, f, a, b, _gap in clips:
        x = decode(path_of(f), a, b)
        gain = 10 ** ((TARGET - loudness(x)) / 20)
        x *= gain
        fi, fo = int(0.015 * SR), int(0.08 * SR)
        x[:fi] *= np.linspace(0, 1, fi)
        x[-fo:] *= np.linspace(1, 0, fo)
        start = int(cfg.V[key] * SR)
        n = min(len(x), total - start)
        vo[start:start + n] += x[:n]
        print(f"  {key:6s} at {cfg.V[key]:7.2f}s  {len(x) / SR:5.2f}s  gain {20 * np.log10(gain):+5.1f} dB")
    peak = np.abs(vo).max()
    if peak > 0.89:
        vo *= 0.89 / peak
    vo_path = out.replace(".wav", "_vo.wav")
    write_wav(vo_path, np.repeat(vo[:, None], 2, axis=1))

    # music automation: extra dips where the score swells under important lines
    with wave.open(music) as w:
        mus = np.frombuffer(w.readframes(w.getnframes()), np.int16).reshape(-1, 2).astype(np.float32) / 32768
    gain_db = np.zeros(len(mus), np.float32)
    for t0, t1, db, ramp in getattr(cfg, "MUSIC_AUTOMATION", []):
        t = np.arange(len(mus), dtype=np.float32) / SR
        up = np.clip((t - (t0 - ramp)) / ramp, 0, 1)
        down = np.clip(((t1 + ramp) - t) / ramp, 0, 1)
        gain_db = np.minimum(gain_db, db * np.minimum(up, down))
    mus *= (10 ** (gain_db / 20))[:, None]
    music = out.replace(".wav", "_music_auto.wav")
    write_wav(music, mus)
    dur = f"{cfg.END:.3f}"
    graph = (f"[1:a]asplit=2[vo][key];"
             f"[0:a]aresample={SR},volume={MUSIC_GAIN_DB}dB,apad=whole_dur={dur},atrim=duration={dur}[mus];"
             f"[mus][key]sidechaincompress=threshold=0.08:ratio=2:attack=120:release=1000:knee=6[duck];"
             f"[vo][duck]amix=inputs=2:normalize=0:duration=first,volume=1.6dB,"
             f"alimiter=limit=0.84:attack=5:release=60:level=false[out]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", music, "-i", vo_path, "-filter_complex", graph,
                    "-map", "[out]", "-ar", str(SR), "-c:a", "pcm_s16le", out], check=True)
    print("wrote", out)


if __name__ == "__main__":
    main()
