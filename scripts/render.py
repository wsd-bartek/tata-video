"""Render the film: camera moves, per-chapter grade, 35mm look, titles, sound.

Inputs (see assets/plates/manifest.json and scripts/fetch_assets.py):
  assets/plates/<shot>.png   generated stills
  assets/clips/<shot>.mp4    generated moving plates (optional per shot)
  assets/photos/<name>.jpg   real family photos - override the shot that names them
  assets/audio/mix.flac      final sound mix (scripts/mix_audio.py)

Usage:
  python3 scripts/render.py                       # full film -> build/film.mp4
  python3 scripts/render.py --stills 3 24.5 ...   # single finished frames (film seconds)
  python3 scripts/render.py --range 20 26         # only part of the film
"""
import argparse
import glob
import json
import math
import multiprocessing as mp
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

sys.path.insert(0, os.path.dirname(__file__))
import timeline as TL  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A = lambda *p: os.path.join(ROOT, "assets", *p)  # noqa: E731
W, H, FPS = TL.WIDTH, TL.HEIGHT, TL.FPS

# --------------------------------------------------------------------------
# Looks. exposure, lift (faded blacks), gamma, contrast, saturation,
# shadow / highlight tint (added RGB), blue_desat (0..1, calms saturated blues)
# --------------------------------------------------------------------------
GRADES = {
    # 1981: cold, dark, fog - only the horizon carries a little warmth
    "dawn": dict(exposure=0.80, lift=0.030, gamma=1.12, contrast=1.06, sat=0.55,
                 shadow=(-0.010, 0.000, 0.022), highlight=(0.030, 0.012, -0.012)),
    "dawn_warm": dict(exposure=0.86, lift=0.032, gamma=1.08, contrast=1.04, sat=0.62,
                      shadow=(-0.006, 0.002, 0.018), highlight=(0.050, 0.022, -0.020)),
    # the years: earthy, faded print
    "memory": dict(exposure=0.96, lift=0.045, gamma=1.02, contrast=0.96, sat=0.64,
                   shadow=(0.018, 0.010, -0.004), highlight=(0.035, 0.016, -0.022)),
    # 2008: noticeably warmer, softer
    "warm": dict(exposure=1.00, lift=0.034, gamma=0.98, contrast=0.98, sat=0.80,
                 shadow=(0.022, 0.008, -0.010), highlight=(0.055, 0.022, -0.035)),
    "warm_soft": dict(exposure=1.02, lift=0.038, gamma=0.97, contrast=0.98, sat=0.70,
                      shadow=(0.026, 0.012, -0.010), highlight=(0.075, 0.035, -0.045)),
    # 2026: bright and golden
    "golden": dict(exposure=1.06, lift=0.040, gamma=0.95, contrast=0.97, sat=0.90,
                   shadow=(0.030, 0.016, -0.008), highlight=(0.055, 0.030, -0.030)),
    "golden_close": dict(exposure=1.04, lift=0.036, gamma=0.97, contrast=1.00, sat=0.72,
                         shadow=(0.020, 0.010, 0.000), highlight=(0.050, 0.026, -0.030),
                         blue_desat=0.85),
    "hope": dict(exposure=1.03, lift=0.038, gamma=0.96, contrast=1.00, sat=0.85,
                 shadow=(0.000, 0.008, 0.020), highlight=(0.050, 0.028, -0.025)),
}

TEXT_COLOR = (246, 237, 221)
LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)
GRAIN = 0.024          # grain strength (fraction of full scale, in the midtones)
VIGNETTE = 0.42
HALATION = (0.20, 0.075, 0.03)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def smooth(u):
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def lerp(a, b, u):
    return a + (b - a) * u


def find(pattern):
    hits = sorted(glob.glob(pattern))
    return hits[0] if hits else None


def scale_img(img, k):
    """Multiply every channel by k (fades, flicker) - a C-side point op."""
    if abs(k - 1) < 1e-4:
        return img
    lut = [min(255, int(v * k + 0.5)) for v in range(256)] * 3
    return img.point(lut)


def grade_fn(x, g):
    """The grade as a pure RGB -> RGB function (evaluated on a 3D LUT lattice)."""
    if g.get("blue_desat"):
        # pull saturated blues (e.g. a bright fleece) towards neutral
        lum = (x @ LUMA)[..., None]
        blue = np.clip((x[..., 2] - np.maximum(x[..., 0], x[..., 1])) * 4.0, 0, 1)
        k = (g["blue_desat"] * blue)[..., None]
        x = x * (1 - k) + lum * k
    x = x * g["exposure"]
    lc = np.clip(x @ LUMA, 0, 1)[..., None]
    x = x + (1 - lc) * np.array(g["shadow"], np.float32) + lc * np.array(g["highlight"], np.float32)
    lum = (x @ LUMA)[..., None]
    x = lum + g["sat"] * (x - lum)
    x = 0.5 + (x - 0.5) * g["contrast"]
    knee = 0.80   # soft highlight shoulder, like a print stock
    x = np.where(x > knee, knee + (1 - knee) * np.tanh(np.maximum(x - knee, 0) / (1 - knee)), x)
    x = np.clip(x, 0, 1) ** g["gamma"]
    return g["lift"] + (0.965 - g["lift"]) * x


def grade_lut(g, size=33):
    lin = np.linspace(0, 1, size, dtype=np.float32)
    b, gg, r = np.meshgrid(lin, lin, lin, indexing="ij")     # red changes fastest
    rgb = np.stack([r, gg, b], axis=-1).reshape(-1, 3)
    out = np.clip(grade_fn(rgb, g), 0, 1).astype(np.float32)
    return ImageFilter.Color3DLUT(size, out.reshape(-1).tolist())


def load_photo(path):
    """Real photo -> 16:9 picture. Portrait photos sit on a soft blurred copy."""
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    if abs(im.width / im.height - W / H) < 0.12:
        return im
    bg = ImageOps.fit(im, (W, H), Image.LANCZOS).filter(ImageFilter.GaussianBlur(40))
    bg = Image.blend(bg, Image.new("RGB", bg.size, (0, 0, 0)), 0.45)
    fg = ImageOps.contain(im, (int(W * 0.94), int(H * 0.94)), Image.LANCZOS)
    bg.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    return bg


def read_clip(path):
    """Decode a clip to an array of RGB frames (native size)."""
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height,r_frame_rate",
                            "-of", "json", path], capture_output=True, check=True, text=True)
    st = json.loads(probe.stdout)["streams"][0]
    w, h = st["width"], st["height"]
    num, den = st["r_frame_rate"].split("/")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3), float(num) / float(den)


# --------------------------------------------------------------------------
# shots
# --------------------------------------------------------------------------
class Shot:
    def __init__(self, spec, index):
        self.spec = spec
        self.id = spec["id"]
        self.start = TL.film(spec["start"])
        self.end = TL.film(spec["end"])
        nxt = TL.SHOTS[index + 1]["id"] if index + 1 < len(TL.SHOTS) else None
        self.d_in = TL.DISSOLVE.get(self.id, 0.0)
        self.d_out = TL.DISSOLVE.get(nxt, 0.0) if nxt else 0.0
        self.vis_start = self.start - self.d_in / 2
        self.vis_end = self.end + self.d_out / 2
        self.lut = grade_lut(GRADES[spec["grade"]])
        self.frames = None
        self.still = None

        photo = spec.get("photo")
        photo_path = find(A("photos", photo + ".*")) if photo else None
        clip_path = find(A("clips", self.id + ".mp4"))
        plate_path = find(A("plates", self.id + ".*"))
        if photo_path and not photo_path.endswith(".md"):
            self.still, self.source = load_photo(photo_path), photo_path
        elif spec["kind"] == "video" and clip_path:
            (self.frames, self.clip_fps), self.source = read_clip(clip_path), clip_path
        elif plate_path:
            self.still, self.source = Image.open(plate_path).convert("RGB"), plate_path
        else:
            raise SystemExit(f"missing picture for {self.id}")
        if self.still is not None:
            zmax = max(spec["move"][0], spec["move"][1])
            # pre-scale so the move neither upsamples much nor aliases
            s = max(W * zmax * 1.02 / self.still.width, H * zmax * 1.02 / self.still.height)
            self.still = self.still.resize((round(self.still.width * s), round(self.still.height * s)),
                                           Image.LANCZOS)

    def alpha(self, t):
        a = 1.0
        if self.d_in > 0 and t < self.start + self.d_in / 2:
            a = smooth((t - self.vis_start) / self.d_in)
        if self.d_out > 0 and t > self.end - self.d_out / 2:
            a = min(a, 1 - smooth((t - (self.end - self.d_out / 2)) / self.d_out))
        return a

    def source_image(self, t):
        if self.frames is None:
            return self.still
        # real time; if the shot is longer than the clip, slow it down evenly
        clip_len = (len(self.frames) - 1) / self.clip_fps
        speed = min(1.0, clip_len / (self.vis_end - self.vis_start))
        pos = min(max((t - self.vis_start) * speed * self.clip_fps, 0.0), len(self.frames) - 1.0)
        i, frac = int(pos), pos - int(pos)
        a = Image.fromarray(self.frames[i])
        if frac < 0.02 or i + 1 >= len(self.frames):
            return a
        return Image.blend(a, Image.fromarray(self.frames[i + 1]), frac)  # smooth slow motion

    def render(self, t, weave):
        img = self.source_image(t)
        z0, z1, px0, px1, py0, py1 = self.spec["move"]
        u = min(1.0, max(0.0, (t - self.vis_start) / max(1e-6, self.vis_end - self.vis_start)))
        scale = max(W / img.width, H / img.height) * lerp(z0, z1, u)
        mx, my = (img.width - W / scale) / 2, (img.height - H / scale) / 2
        cx = img.width / 2 + lerp(px0, px1, u) * mx + weave[0] / scale
        cy = img.height / 2 + lerp(py0, py1, u) * my + weave[1] / scale
        inv = 1 / scale
        out = img.transform((W, H), Image.AFFINE, (inv, 0, cx - W / 2 * inv, 0, inv, cy - H / 2 * inv),
                            resample=Image.BILINEAR)
        return out.filter(self.lut)


# --------------------------------------------------------------------------
# film look
# --------------------------------------------------------------------------
class Look:
    def __init__(self, seed=11):
        rng = np.random.default_rng(seed)
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2) / math.sqrt(2)
        v = 1 - VIGNETTE * np.clip(r, 0, 1) ** 2.4
        self.vignette = Image.fromarray((v * 255 + 0.5).astype(np.uint8)).convert("RGB")
        # grain bank: half-res noise upsampled -> organic ~2px grain (unit variance)
        self.grain = []
        for _ in range(24):
            n = rng.standard_normal((H // 2, W // 2)).astype(np.float32)
            im = Image.fromarray(np.clip(n * 40 + 128, 0, 255).astype(np.uint8)).resize((W, H), Image.BICUBIC)
            self.grain.append(((np.asarray(im, np.float32) - 128) / 40).astype(np.float16))
        self.chroma = []
        for _ in range(6):
            n = rng.standard_normal((H // 4, W // 4, 3)).astype(np.float32) * (GRAIN * 0.25 * 255)
            im = Image.fromarray(np.clip(n * 4 + 128, 0, 255).astype(np.uint8)).resize((W, H), Image.BICUBIC)
            self.chroma.append(((np.asarray(im, np.int16) - 128) // 4).astype(np.int16))
        lv = np.arange(256, dtype=np.float32) / 255
        self.grain_amp = (GRAIN * 255 * (0.35 + 0.65 * 4 * lv * (1 - lv))).astype(np.float32)

    @staticmethod
    def halation(img):
        """Red-orange halation + gentle bloom around highlights (computed at 1/8 size)."""
        small = img.resize((W // 8, H // 8), Image.BILINEAR)
        s = np.asarray(small, np.float32) / 255
        hi = np.clip((s @ LUMA - 0.68) / 0.32, 0, 1)
        g = np.asarray(Image.fromarray((hi * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(6)),
                       np.float32)[..., None] / 255
        bloom = np.asarray(small.filter(ImageFilter.GaussianBlur(10)), np.float32) / 255
        glow = g * np.array(HALATION, np.float32) + 0.07 * bloom * np.clip(g, 0.2, 1)
        glow = Image.fromarray((np.clip(glow, 0, 1) * 255).astype(np.uint8)).resize((W, H), Image.BILINEAR)
        return ImageChops.add(img, glow)

    def finish(self, img, frame_no):
        img = self.halation(img)
        img = ImageChops.multiply(img, self.vignette)
        lum = np.asarray(img.convert("L"))
        n = self.grain[frame_no % len(self.grain)] * self.grain_amp[lum]
        x = np.asarray(img, np.int16) + n.astype(np.int16)[..., None] + self.chroma[(frame_no * 7) % 6]
        return np.clip(x, 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------
# titles
# --------------------------------------------------------------------------
def text_mask(text, font_name, size, tracking, y_frac):
    font = ImageFont.truetype(A("fonts", font_name), size)
    track = tracking * size
    widths = [font.getlength(c) for c in text]
    total = sum(widths) + track * (len(text) - 1)
    bbox = font.getbbox(text)
    x, y0 = (W - total) / 2, H * y_frac - (bbox[1] + bbox[3]) / 2
    mask = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(mask)
    for c, w in zip(text, widths):
        d.text((x, y0), c, font=font, fill=255)
        x += w + track
    return mask


class Title:
    def __init__(self, spec, start, end, fade):
        self.start, self.end, self.fade = start, end, fade
        mask = text_mask(spec["text"], spec["font"], spec["size"], spec["tracking"], spec["y"])
        b, pad = mask.getbbox(), 60
        self.box = (max(0, b[0] - pad), max(0, b[1] - pad), min(W, b[2] + pad), min(H, b[3] + pad))
        self.mask = mask.crop(self.box)
        self.shadow = self.mask.filter(ImageFilter.GaussianBlur(9))
        self.color = Image.new("RGB", self.mask.size, TEXT_COLOR)

    def opacity(self, t):
        if t < self.start or t > self.end:
            return 0.0
        return min(smooth((t - self.start) / self.fade), smooth((self.end - t) / self.fade))

    def draw(self, img, t):
        a = self.opacity(t)
        if a <= 0:
            return img
        blur = 5.0 * (1 - smooth((t - self.start) / self.fade))   # focus pull while appearing
        m = self.mask.filter(ImageFilter.GaussianBlur(blur)) if blur > 0.3 else self.mask
        region = img.crop(self.box)
        shade = self.shadow.point([int(255 - 0.40 * a * v) for v in range(256)]).convert("RGB")
        region = ImageChops.multiply(region, shade)                  # soft shadow for legibility
        region = Image.composite(self.color, region, m.point([int(v * a) for v in range(256)]))
        img.paste(region, self.box[:2])
        return img


def build_titles():
    titles = [Title(s, TL.film(s["start"]), TL.film(s["end"]), s["fade"]) for s in TL.TITLES]
    ec = TL.END_CARD
    for s in TL.END_TITLES:
        titles.append(Title(s, TL.film(ec["start"]) + s["offset"], TL.film(ec["end"]), ec["fade"]))
    return titles


def title_dim(t):
    """How much to darken the picture centre while the dates / 45 LAT titles are up."""
    a = 0.0
    for s in TL.TITLES:
        st, en, f = TL.film(s["start"]) - 0.4, TL.film(s["end"]) + 0.4, s["fade"] + 0.4
        if st <= t <= en:
            a = max(a, min(smooth((t - st) / f), smooth((en - t) / f)))
    return a


# --------------------------------------------------------------------------
# frame assembly
# --------------------------------------------------------------------------
STATE = {}


def init_state():
    shots = [Shot(s, i) for i, s in enumerate(TL.SHOTS)]
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    centre = np.exp(-(((xx - W / 2) / (W * 0.38)) ** 2 + ((yy - H / 2) / (H * 0.30)) ** 2))
    STATE.update(shots=shots, look=Look(), titles=build_titles(),
                 centre=Image.fromarray((centre * 255).astype(np.uint8)))
    for s in shots:
        print(f"  {s.id:18s} {s.vis_start:6.2f}-{s.vis_end:6.2f}s  {os.path.relpath(s.source, ROOT)}",
              file=sys.stderr)


def weave_at(t):
    """Subtle gate weave, in output pixels."""
    return (0.55 * math.sin(2 * math.pi * 0.61 * t + 0.3) + 0.25 * math.sin(2 * math.pi * 1.73 * t + 1.1),
            0.45 * math.sin(2 * math.pi * 0.47 * t + 2.0) + 0.20 * math.sin(2 * math.pi * 2.11 * t + 0.7))


def render_frame(frame_no):
    t = frame_no / FPS
    weave = weave_at(t)
    img = None
    for s in STATE["shots"]:
        if s.vis_start <= t <= s.vis_end:
            a = s.alpha(t)
            if a <= 0:
                continue
            pic = s.render(t, weave)
            img = pic if img is None else Image.blend(img, pic, a)
    if img is None:
        img = Image.new("RGB", (W, H), (0, 0, 0))
    # fades + a breath of print flicker
    k = smooth((t - TL.FADE_IN[0]) / (TL.FADE_IN[1] - TL.FADE_IN[0]))
    fb0, fb1 = TL.film(TL.FADE_TO_BLACK[0]), TL.film(TL.FADE_TO_BLACK[1])
    k = min(k, 1 - smooth((t - fb0) / (fb1 - fb0)))
    k *= 1 + 0.006 * np.random.default_rng(1000 + frame_no).standard_normal()
    img = scale_img(img, k)
    dim = title_dim(t)
    if dim > 0:
        shade = STATE["centre"].point([int(255 - 0.22 * dim * v) for v in range(256)]).convert("RGB")
        img = ImageChops.multiply(img, shade)
    for title in STATE["titles"]:
        img = title.draw(img, t)
    return STATE["look"].finish(img, frame_no)


def render_film(out, workers, audio, t0=0.0, t1=None, preset="slow"):
    first = int(round(t0 * FPS))
    last = int(round((t1 if t1 is not None else TL.total_duration()) * FPS))
    n = last - first
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-"]
    if audio and os.path.exists(audio):
        cmd += ["-ss", f"{first / FPS:.3f}", "-i", audio, "-map", "0:v", "-map", "1:a",
                "-c:a", "aac", "-b:a", "320k", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", preset, "-crf", "17", "-tune", "grain",
            "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709", "-movflags", "+faststart", out]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    with mp.get_context("fork").Pool(workers) as pool:
        for i, frame in enumerate(pool.imap(render_frame, range(first, last), chunksize=4)):
            enc.stdin.write(frame.tobytes())
            if i % 48 == 0:
                print(f"frame {i}/{n}", file=sys.stderr, flush=True)
    enc.stdin.close()
    if enc.wait() != 0:
        raise SystemExit("ffmpeg failed")
    print("wrote", out, file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "film.mp4"))
    ap.add_argument("--audio", default=os.path.join(ROOT, "assets", "audio", "mix.flac"))
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--stills", type=float, nargs="*", help="film seconds to export as JPEG")
    ap.add_argument("--still-width", type=int, default=960)
    ap.add_argument("--range", type=float, nargs=2, metavar=("T0", "T1"), help="render only this film span")
    ap.add_argument("--preset", default="slow", help="x264 preset")
    args = ap.parse_args()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    init_state()
    if args.stills:
        for t in args.stills:
            img = Image.fromarray(render_frame(int(round(t * FPS))))
            if args.still_width != W:
                img = img.resize((args.still_width, round(args.still_width * H / W)), Image.LANCZOS)
            p = os.path.join(os.path.dirname(os.path.abspath(args.out)), f"frame_{t:06.2f}.jpg")
            img.save(p, quality=82)
            print("wrote", p, file=sys.stderr)
        return
    t0, t1 = args.range if args.range else (0.0, None)
    render_film(args.out, args.workers, args.audio, t0, t1, args.preset)


if __name__ == "__main__":
    main()
