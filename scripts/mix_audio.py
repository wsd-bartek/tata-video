"""Mix voice-over + score into build/mix.wav and assets/audio/mix.flac.

The voice is the master: it is brought to about -17 LUFS with a plain gain
(no dynamic normalisation, so the performance stays untouched) and the music
sits well below it, ducking further whenever a word is spoken (sidechain
compression keyed by the voice). A final limiter keeps peaks below -1.5 dBFS.

Usage: python3 scripts/mix_audio.py
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
from timeline import VO_OFFSET, total_duration  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VO = os.path.join(ROOT, "assets", "voiceover", "voiceover.mp3")
MUSIC = os.path.join(ROOT, "build", "music.wav")
OUT = os.path.join(ROOT, "build", "mix.wav")

VOICE_TARGET = -17.0   # LUFS
MUSIC_GAIN_DB = -2.0   # music bed before ducking
VOICE_CHAIN = "highpass=f=70,lowpass=f=15500,acompressor=threshold=-24dB:ratio=2:attack=8:release=160"


def integrated_loudness(path, chain):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-af", f"{chain},ebur128=framelog=quiet",
                        "-f", "null", "-"], capture_output=True, text=True, check=True)
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)[-1])


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    gain = VOICE_TARGET - integrated_loudness(VO, f"aresample=48000,{VOICE_CHAIN}")
    delay = int(VO_OFFSET * 1000)
    dur = f"{total_duration():.3f}"
    graph = (
        f"[1:a]aresample=48000,{VOICE_CHAIN},volume={gain:.2f}dB,"
        f"alimiter=limit=0.79:attack=3:release=50:level=false,"
        f"pan=stereo|c0=c0|c1=c0,adelay={delay}|{delay},apad=whole_dur={dur},atrim=duration={dur},"
        f"asplit=2[vo][key];"
        f"[0:a]aresample=48000,volume={MUSIC_GAIN_DB}dB,apad=whole_dur={dur},atrim=duration={dur}[mus];"
        f"[mus][key]sidechaincompress=threshold=0.08:ratio=2:attack=120:release=1000:knee=6[duck];"
        f"[vo][duck]amix=inputs=2:normalize=0:duration=first,"
        f"alimiter=limit=0.84:attack=5:release=60:level=false[out]"
    )
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", MUSIC, "-i", VO, "-filter_complex", graph,
                    "-map", "[out]", "-ar", "48000", "-c:a", "pcm_s16le", OUT], check=True)
    flac = os.path.join(ROOT, "assets", "audio", "mix.flac")
    os.makedirs(os.path.dirname(flac), exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", OUT, "-c:a", "flac", "-compression_level", "8", flac],
                   check=True)
    print(f"wrote {OUT} and {flac} (voice gain {gain:+.1f} dB)")


if __name__ == "__main__":
    main()
